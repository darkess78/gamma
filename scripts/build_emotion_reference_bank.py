"""Build candidate emotion reference clips for Shana's Qwen voice.

Her live profile clones only her timbre (x-vector mode) from a flat clip, and
the Qwen Base model ignores text emotion instructions. This script makes
English clips in her own timbre whose *delivery* carries an emotion, scores
how much each still sounds like her, and renders probe lines with the best
candidates in ICL mode (the mode the live backend uses for an emotion
reference), so a person can pick the clips by ear.

Stages, run in order on Gamma PC:

  .venv-tts/bin/python scripts/build_emotion_reference_bank.py generate --out DIR
  .venv/bin/python     scripts/build_emotion_reference_bank.py transcribe --out DIR
  .venv-tts/bin/python scripts/build_emotion_reference_bank.py probe --out DIR

`generate` and `probe` need torch + qwen_tts (.venv-tts); `transcribe` needs
faster-whisper (.venv). Results: DIR/manifest.json and DIR/review.html.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "qwen_shana_dataset_xvec_soft"
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
EMOTION_MODEL = "superb/wav2vec2-base-superb-er"

# The text drives most of the delivery in clone mode, so each line is written
# to pull its emotion out of her flat voice. Lines run 4-7 seconds, a good
# length for an ICL reference.
LINES: dict[str, list[str]] = {
    "neutral": [
        "I checked the list again, and there are only two things left to finish today.",
        "That's fine. We can start with the easier part and come back to the rest later.",
        "The window has been open all morning, so the room is a little cold now.",
        "I read the instructions twice. The second step is the one people usually skip.",
        "If you want, I can keep track of it for you until you get back.",
        "It's not complicated. You just have to do it in the right order.",
    ],
    "happy": [
        "Oh, you actually remembered! That really makes me happy, you know.",
        "This is so nice. I didn't expect today to turn out this well.",
        "Melon bread again? You know me too well. Thank you, really.",
        "See? I told you it would work. I'm really glad it did.",
        "Hearing that from you makes me smile. Okay, I'm in a good mood now.",
        "That went perfectly! Let's do it like that every time from now on.",
    ],
    "teasing": [
        "Oh? You're asking me for help again? I thought you had it all figured out.",
        "Wow, three tries and you finally got it. Should I be impressed?",
        "You're blushing. Don't worry, I won't tell anyone. Probably.",
        "Hmm, so you do need me after all. I knew it.",
        "That's cute. Wrong, but cute. Want me to show you how it's done?",
        "If you keep making that face, I'm going to start laughing.",
    ],
    "concerned": [
        "Hey, are you okay? You sound really tired today.",
        "You don't have to push yourself so hard. Take a break, alright?",
        "That sounds like a lot to deal with. Do you want to talk about it?",
        "Did you eat anything today? Please don't skip meals again.",
        "I'm worried about you. Just tell me if something's wrong, okay?",
        "It's late. Please get some sleep, you can finish this tomorrow.",
    ],
    "excited": [
        "Wait, really? It's finally out? Let's try it right now!",
        "No way, we actually won! Did you see that? That was amazing!",
        "Okay, okay, I have an idea, and you're going to love it!",
        "It's snowing outside! Come look, it's everywhere already!",
        "We did it! All of it! I can't believe it actually worked!",
        "Hurry up, it's starting! Come on, we're going to miss it!",
    ],
    "embarrassed": [
        "W-wait, don't look at me like that. It's not what you think.",
        "I didn't make it for you or anything. I just had extra, that's all.",
        "Um... thank you. You didn't have to say all that, though.",
        "Stop laughing! Anyone could have tripped there. It wasn't funny.",
        "I, um, might have forgotten. Just a little. Sorry.",
        "Don't say that so loudly! Someone might hear you.",
    ],
    "annoyed": [
        "Seriously? I told you three times already. Pay attention this time.",
        "No. I'm not doing that again. Figure it out yourself.",
        "You're late. Again. Do you have any idea how long I waited?",
        "Ugh, stop poking me. I'm trying to concentrate here.",
        "That's not what I said, and you know it. Don't twist my words.",
        "Fine. Do whatever you want. Just don't come complaining to me later.",
    ],
}

# Wav2vec2-superb-er labels: neu, hap, ang, sad. Only a weak signal.
EMOTION_TARGET_LABEL = {
    "neutral": "neu", "happy": "hap", "teasing": "hap", "excited": "hap",
    "annoyed": "ang", "concerned": "sad", "embarrassed": "sad",
}

PROBE_LINES = [
    "Okay, listen. I'll explain it once, so you'd better remember it this time.",
    "Thanks for waiting. I think we're almost done with everything now.",
]


def _load_profile(profile_id: str) -> dict[str, Any]:
    for name in ("voices.local.toml", "voices.toml", "voices.presets.toml"):
        path = ROOT / "config" / name
        if not path.is_file():
            continue
        profiles = tomllib.loads(path.read_text(encoding="utf-8")).get("profiles", {})
        if profile_id in profiles:
            return profiles[profile_id]
    raise SystemExit(f"profile {profile_id!r} not found in config/voices*.toml")


def _live_params(profile: dict[str, Any], emotion: str) -> dict[str, Any]:
    """The generation kwargs her live profile would send for this emotion."""
    extra = dict(profile.get("qwen_tts_extra_json") or {})
    speeds = extra.pop("speed_by_emotion", {}) or {}
    extra.pop("output_peak_by_emotion", None)
    extra.pop("output_peak", None)
    extra.pop("x_vector_only_mode", None)
    if emotion in speeds or "default" in speeds:
        extra["speed"] = speeds.get(emotion, speeds.get("default"))
    return extra


# Sampling variants: the live settings are very conservative (top_k 6), which
# keeps her timbre but flattens delivery. Looser sampling finds livelier takes;
# the similarity score decides whether they are still her.
VARIANTS: dict[str, dict[str, Any]] = {
    "live": {},
    "loose": {"temperature": 0.6, "top_k": 30, "top_p": 0.85},
    "wild": {"temperature": 0.8, "top_k": 50, "top_p": 0.95},
}


def _manifest_path(out: Path) -> Path:
    return out / "manifest.json"


def _read_manifest(out: Path) -> dict[str, Any]:
    return json.loads(_manifest_path(out).read_text(encoding="utf-8"))


def _write_manifest(out: Path, manifest: dict[str, Any]) -> None:
    _manifest_path(out).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


class _Voice:
    """Qwen Base model plus her speaker embedding, loaded once."""

    def __init__(self, device: str, profile: dict[str, Any]) -> None:
        import torch
        from qwen_tts import Qwen3TTSModel

        self.torch = torch
        self.model = Qwen3TTSModel.from_pretrained(
            MODEL_ID, device_map=device, dtype=torch.bfloat16, local_files_only=True,
        )
        ref = ROOT / str(profile["qwen_tts_reference_audio"])
        self.ref_path = ref
        self.prompt = self.model.create_voice_clone_prompt(ref_audio=str(ref), x_vector_only_mode=True)
        self.embedding = self._as_vector(self.prompt[0].ref_spk_embedding)

    def _as_vector(self, value: Any):
        tensor = value if hasattr(value, "detach") else self.torch.as_tensor(value)
        return tensor.detach().float().flatten().cpu()

    def similarity(self, wav_path: Path) -> float:
        item = self.model.create_voice_clone_prompt(ref_audio=str(wav_path), x_vector_only_mode=True)[0]
        other = self._as_vector(item.ref_spk_embedding)
        return float(self.torch.nn.functional.cosine_similarity(self.embedding, other, dim=0))

    def generate(self, texts: list[str], params: dict[str, Any], *, seed: int, prompt=None) -> tuple[list[Any], int]:
        self.torch.manual_seed(seed)
        voice_prompt = prompt if prompt is not None else self.prompt
        wavs, sr = self.model.generate_voice_clone(
            text=texts, language=["English"] * len(texts), voice_clone_prompt=voice_prompt * len(texts), **params,
        )
        return list(wavs), sr


def _save_wav(path: Path, audio: Any, sr: int, peak: float = 0.58) -> float:
    import numpy as np
    import soundfile as sf

    data = audio.cpu().float().numpy() if hasattr(audio, "cpu") else np.asarray(audio, dtype=np.float32)
    data = np.asarray(data, dtype=np.float32).flatten()
    top = float(np.max(np.abs(data))) if data.size else 0.0
    if top > 0:
        data = data * (peak / top)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr, subtype="PCM_16")
    return round(len(data) / sr, 2)


def _prosody(path: Path) -> dict[str, float]:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(path), sr=16000)
    f0, voiced, _ = librosa.pyin(y, fmin=120, fmax=700, sr=sr)
    f0 = f0[voiced & ~np.isnan(f0)] if f0 is not None else np.array([])
    if f0.size < 5:
        return {"f0_median_hz": 0.0, "f0_spread_st": 0.0}
    semitones = 12 * np.log2(f0 / np.median(f0))
    return {
        "f0_median_hz": round(float(np.median(f0)), 1),
        "f0_spread_st": round(float(np.percentile(semitones, 90) - np.percentile(semitones, 10)), 2),
    }


def _emotion_scorer():
    from transformers import pipeline

    classify = pipeline("audio-classification", model=EMOTION_MODEL, device=-1, model_kwargs={"local_files_only": True})

    def score(path: Path) -> dict[str, float]:
        import librosa

        y, _ = librosa.load(str(path), sr=16000)
        return {row["label"]: round(float(row["score"]), 3) for row in classify(y, top_k=4)}

    return score


def stage_generate(args: argparse.Namespace) -> None:
    out: Path = args.out
    profile = _load_profile(args.profile)
    voice = _Voice(args.device, profile)
    emotions = args.emotions or list(LINES)
    takes: list[dict[str, Any]] = []
    for emotion in emotions:
        lines = LINES[emotion]
        base = _live_params(profile, emotion)
        for variant, overrides in VARIANTS.items():
            params = {**base, **overrides}
            for seed in range(args.seeds):
                wavs, sr = voice.generate(lines, params, seed=1000 * seed + 7)
                for index, (text, audio) in enumerate(zip(lines, wavs)):
                    take_id = f"{emotion}_{index:02d}_{variant}_s{seed}"
                    path = out / "takes" / f"{take_id}.wav"
                    duration = _save_wav(path, audio, sr)
                    takes.append({
                        "id": take_id, "emotion": emotion, "line": index, "variant": variant,
                        "seed": seed, "text": text, "path": str(path.relative_to(out)),
                        "duration_s": duration, "params": params,
                    })
                print(f"generated {emotion}/{variant}/seed{seed}", flush=True)
    score_emotion = _emotion_scorer()
    for take in takes:
        path = out / take["path"]
        take["speaker_similarity"] = round(voice.similarity(path), 4)
        take["emotion_probs"] = score_emotion(path)
        take.update(_prosody(path))
    _write_manifest(out, {
        "profile": args.profile, "reference": str(voice.ref_path.relative_to(ROOT)),
        "model": MODEL_ID, "takes": takes,
    })
    print(f"wrote {len(takes)} takes to {out}", flush=True)


def _normalize_words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def stage_transcribe(args: argparse.Namespace) -> None:
    from faster_whisper import WhisperModel

    out: Path = args.out
    manifest = _read_manifest(out)
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    for take in manifest["takes"]:
        segments, _ = model.transcribe(str(out / take["path"]), language="en", beam_size=3)
        heard = " ".join(segment.text.strip() for segment in segments).strip()
        take["asr_text"] = heard
        take["asr_match"] = round(difflib.SequenceMatcher(
            None, _normalize_words(take["text"]), _normalize_words(heard)).ratio(), 3)
    _write_manifest(out, manifest)
    print(f"transcribed {len(manifest['takes'])} takes", flush=True)


def _rank(manifest: dict[str, Any], min_asr: float) -> dict[str, list[dict[str, Any]]]:
    """Keep takes that still sound like her and say the line; rank by emotion strength."""
    takes = manifest["takes"]
    baseline = sorted(t["speaker_similarity"] for t in takes if t["emotion"] == "neutral" and t["variant"] == "live")
    # The floor is what her own live neutral takes reach, minus a little slack.
    floor = (baseline[len(baseline) // 10] if baseline else 0.0) - 0.02
    neutral_spread = sorted(t["f0_spread_st"] for t in takes if t["emotion"] == "neutral")
    spread_ref = neutral_spread[len(neutral_spread) // 2] if neutral_spread else 1.0
    manifest["similarity_floor"] = round(floor, 4)
    ranked: dict[str, list[dict[str, Any]]] = {}
    for take in takes:
        label = EMOTION_TARGET_LABEL[take["emotion"]]
        take["passes"] = take["speaker_similarity"] >= floor and take.get("asr_match", 1.0) >= min_asr
        expressive = take["f0_spread_st"] / max(spread_ref, 0.5)
        if take["emotion"] == "neutral":
            take["rank_score"] = round(take["speaker_similarity"] * 2 + take["emotion_probs"].get(label, 0.0), 4)
        else:
            take["rank_score"] = round(
                take["speaker_similarity"] * 2 + take["emotion_probs"].get(label, 0.0) + 0.25 * min(expressive, 2.0), 4)
        if take["passes"]:
            ranked.setdefault(take["emotion"], []).append(take)
    for rows in ranked.values():
        rows.sort(key=lambda t: t["rank_score"], reverse=True)
    return ranked


def stage_probe(args: argparse.Namespace) -> None:
    out: Path = args.out
    manifest = _read_manifest(out)
    profile = _load_profile(manifest["profile"])
    ranked = _rank(manifest, args.min_asr)
    voice = _Voice(args.device, profile)
    probes: list[dict[str, Any]] = []
    for emotion, rows in ranked.items():
        params = _live_params(profile, emotion)
        for take in rows[: args.top]:
            # Exactly what the live backend sends for an emotion reference.
            prompt = voice.model.create_voice_clone_prompt(
                ref_audio=str(out / take["path"]), ref_text=take["text"], x_vector_only_mode=False)
            wavs, sr = voice.generate(PROBE_LINES, params, seed=11, prompt=prompt)
            take["probes"] = []
            for index, audio in enumerate(wavs):
                path = out / "probes" / f"{take['id']}_p{index}.wav"
                _save_wav(path, audio, sr)
                similarity = round(voice.similarity(path), 4)
                take["probes"].append({"path": str(path.relative_to(out)), "speaker_similarity": similarity})
            take["probe_similarity"] = round(min(p["speaker_similarity"] for p in take["probes"]), 4)
            probes.append(take)
            print(f"probed {take['id']} -> {take['probe_similarity']}", flush=True)
    _write_manifest(out, manifest)
    (out / "review.html").write_text(_review_html(manifest, ranked, args.top), encoding="utf-8")
    print(f"probed {len(probes)} candidates; open {out / 'review.html'}", flush=True)


def _review_html(manifest: dict[str, Any], ranked: dict[str, list[dict[str, Any]]], top: int) -> str:
    def audio(path: str) -> str:
        return f'<audio controls preload="none" src="{html.escape(path)}"></audio>'

    sections = [
        f"<p>Reference: {html.escape(manifest['reference'])}. Similarity floor (her live neutral takes): "
        f"{manifest['similarity_floor']}. Probes are new lines spoken with the candidate as the ICL reference: "
        f"that is how she will sound live with this clip. Judge the probes first.</p>",
        f"<h2>Her current voice (live settings, neutral)</h2>"
        + "".join(audio(t["path"]) for t in manifest["takes"][:3] if t["emotion"] == "neutral" and t["variant"] == "live"),
    ]
    for emotion, rows in ranked.items():
        sections.append(f"<h2>{html.escape(emotion)}</h2>")
        for take in rows[:top]:
            probe_cells = "".join(
                f"<div>probe {i + 1} (sim {p['speaker_similarity']}) {audio(p['path'])}</div>"
                for i, p in enumerate(take.get("probes", []))
            )
            emotion_probs = ", ".join(f"{k} {v}" for k, v in take["emotion_probs"].items())
            sections.append(
                f'<div class="take"><b>{html.escape(take["id"])}</b> "{html.escape(take["text"])}"<br>'
                f"sim {take['speaker_similarity']} · probe sim {take.get('probe_similarity', '-')} · "
                f"asr {take.get('asr_match', '-')} · pitch spread {take['f0_spread_st']} st · {emotion_probs}"
                f"<div>clip {audio(take['path'])}</div>{probe_cells}</div>"
            )
        dropped = sum(1 for t in manifest["takes"] if t["emotion"] == emotion and not t["passes"])
        sections.append(f"<p class='muted'>{dropped} takes for {emotion} failed the similarity or transcript check.</p>")
    return (
        "<!doctype html><meta charset='utf-8'><title>Emotion clip review</title>"
        "<style>body{font:15px system-ui;max-width:900px;margin:24px auto;padding:0 16px;background:#fff;color:#111}"
        ".take{border:1px solid #ddd;border-radius:8px;padding:10px;margin:10px 0}audio{height:32px;vertical-align:middle}"
        ".muted{color:#666}@media(prefers-color-scheme:dark){body{background:#151515;color:#eee}.take{border-color:#333}}</style>"
        "<h1>Shana emotion clip candidates</h1>" + "".join(sections)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["generate", "transcribe", "probe"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--top", type=int, default=4, help="candidates per emotion to probe")
    parser.add_argument("--min-asr", type=float, default=0.85)
    parser.add_argument("--emotions", nargs="*", choices=list(LINES))
    args = parser.parse_args(argv)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    {"generate": stage_generate, "transcribe": stage_transcribe, "probe": stage_probe}[args.stage](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
