from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from faster_whisper import WhisperModel

from .config import settings


MEDIA_EXTENSIONS = {
    ".aac",
    ".ac3",
    ".flac",
    ".m4a",
    ".mka",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
DEFAULT_EXCLUDE_PATTERNS = ("recap",)
_CANCEL_EVENT: threading.Event | None = None


@dataclass(slots=True)
class SegmentCandidate:
    source_path: Path
    clip_path: Path
    episode_id: str
    clip_id: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    text: str
    language: str
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "clip_path": str(self.clip_path),
            "episode_id": self.episode_id,
            "clip_id": self.clip_id,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
            "text": self.text,
            "language": self.language,
            "avg_logprob": self.avg_logprob,
            "no_speech_prob": self.no_speech_prob,
            "compression_ratio": self.compression_ratio,
        }


def set_cancel_event(cancel_event: threading.Event | None) -> None:
    global _CANCEL_EVENT
    _CANCEL_EVENT = cancel_event


def is_cancelled() -> bool:
    return _CANCEL_EVENT is not None and _CANCEL_EVENT.is_set()


def raise_if_cancelled() -> None:
    if is_cancelled():
        raise SystemExit(130)


def _subprocess_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_hidden_subprocess(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        creationflags=_subprocess_creationflags(),
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a review-first TTS dataset from episode audio. "
            "This extracts speech clips plus transcripts, but it does not auto-identify Shana."
        )
    )
    parser.add_argument("input_path", help="Media file or directory containing episodes.")
    parser.add_argument(
        "--out-dir",
        default="data/tts_dataset_candidates",
        help="Output directory for clips and manifests.",
    )
    parser.add_argument(
        "--language",
        default="ja",
        help="Whisper language hint, for example 'ja'. Use 'auto' to let Whisper detect it.",
    )
    parser.add_argument(
        "--model",
        default=settings.stt_model,
        help="faster-whisper model name to use for segmentation.",
    )
    parser.add_argument(
        "--device",
        default=settings.stt_device,
        help="Inference device, for example 'cpu' or 'cuda'.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=settings.stt_device_index,
        help="GPU index for faster-whisper when using CUDA.",
    )
    parser.add_argument(
        "--compute-type",
        default=settings.stt_compute_type,
        help="faster-whisper compute type, for example 'int8' or 'float16'.",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=5,
        help="Beam size passed to faster-whisper.",
    )
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=1.2,
        help="Discard segments shorter than this many seconds.",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=8.0,
        help="Discard segments longer than this many seconds.",
    )
    parser.add_argument(
        "--merge-gap-seconds",
        type=float,
        default=0.35,
        help="Merge adjacent Whisper segments when the gap is below this threshold.",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=2,
        help="Discard transcribed segments shorter than this many characters.",
    )
    parser.add_argument(
        "--max-no-speech-prob",
        type=float,
        default=0.6,
        help="Discard segments with a no-speech probability above this threshold.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="ffmpeg executable to use for clip extraction.",
    )
    parser.add_argument(
        "--ffprobe-bin",
        default="ffprobe",
        help="ffprobe executable to use for stream inspection.",
    )
    parser.add_argument(
        "--audio-stream-index",
        type=int,
        default=None,
        help="Audio stream index inside the container. Use this to force the Japanese track on dual-audio releases.",
    )
    parser.add_argument(
        "--audio-stream-lang",
        default=None,
        help="Preferred audio stream language tag, for example 'jpn' or 'ja'.",
    )
    parser.add_argument(
        "--exclude-pattern",
        action="append",
        default=[],
        help=(
            "Case-insensitive filename substring to exclude. "
            "Can be passed multiple times. Defaults already exclude recap files."
        ),
    )
    parser.add_argument(
        "--limit-files",
        type=int,
        default=0,
        help="If non-zero, only process this many input files.",
    )
    parser.add_argument(
        "--vocals-only",
        action="store_true",
        help=(
            "Run Demucs vocal separation before transcription and clip extraction. "
            "Requires the 'demucs' package to be installed. "
            "Strips music, SFX, and background noise so Whisper and the extracted clips contain vocals only."
        ),
    )
    parser.add_argument(
        "--demucs-model",
        default="htdemucs",
        help="Demucs model to use for vocal separation (default: htdemucs).",
    )
    return parser.parse_args(argv)


def should_exclude_path(path: Path, exclude_patterns: Sequence[str]) -> bool:
    lowered = path.name.lower()
    return any(pattern.strip().lower() in lowered for pattern in exclude_patterns if pattern.strip())


def discover_media_files(input_path: Path, exclude_patterns: Sequence[str]) -> list[Path]:
    if input_path.is_file():
        return [] if should_exclude_path(input_path, exclude_patterns) else [input_path]
    return sorted(
        path
        for path in input_path.rglob("*")
        if path.is_file()
        and path.suffix.lower() in MEDIA_EXTENSIONS
        and not should_exclude_path(path, exclude_patterns)
    )


def sanitize_stem(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in value)
    collapsed = "_".join(part for part in cleaned.split("_") if part)
    return collapsed.lower() or "episode"


def ensure_binary(binary_name: str) -> None:
    if shutil.which(binary_name):
        return
    raise SystemExit(f"required executable was not found: {binary_name}")


def ensure_ffmpeg(ffmpeg_bin: str, ffprobe_bin: str) -> None:
    if shutil.which(ffmpeg_bin):
        ensure_binary(ffprobe_bin)
        return
    raise SystemExit(f"ffmpeg executable was not found: {ffmpeg_bin}\nInstall ffmpeg or pass --ffmpeg-bin with the correct path.")


def build_model(args: argparse.Namespace) -> WhisperModel:
    model_name = str(args.model).strip().lower()
    language_name = str(args.language).strip().lower()
    if model_name.endswith(".en") and language_name not in {"", "auto", "en", "eng", "english"}:
        raise SystemExit(
            f"Model '{args.model}' is English-only and cannot be used with language '{args.language}'. "
            "Use a multilingual Whisper model such as 'base', 'small', 'medium', or 'large-v3'."
        )
    return WhisperModel(
        args.model,
        device=args.device,
        device_index=args.device_index,
        compute_type=args.compute_type,
    )


def merge_transcript_segments(raw_segments: Iterable[object], gap_threshold: float) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for raw in raw_segments:
        text = raw.text.strip()
        if not text:
            continue
        current = {
            "start": float(raw.start),
            "end": float(raw.end),
            "text": text,
            "avg_logprob": _to_optional_float(getattr(raw, "avg_logprob", None)),
            "no_speech_prob": _to_optional_float(getattr(raw, "no_speech_prob", None)),
            "compression_ratio": _to_optional_float(getattr(raw, "compression_ratio", None)),
        }
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        gap = current["start"] - previous["end"]
        if gap <= gap_threshold:
            previous["end"] = current["end"]
            previous["text"] = f"{previous['text']} {current['text']}".strip()
            previous["avg_logprob"] = _mean_optional(previous["avg_logprob"], current["avg_logprob"])
            previous["no_speech_prob"] = _mean_optional(previous["no_speech_prob"], current["no_speech_prob"])
            previous["compression_ratio"] = _mean_optional(previous["compression_ratio"], current["compression_ratio"])
            continue
        merged.append(current)
    return merged


def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean_optional(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return (left + right) / 2.0


def extract_clip(ffmpeg_bin: str, source_path: Path, clip_path: Path, start_seconds: float, end_seconds: float) -> None:
    raise_if_cancelled()
    command = [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-to",
        f"{end_seconds:.3f}",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(clip_path),
    ]
    completed = _run_hidden_subprocess(command)
    if completed.returncode == 0:
        return
    raise RuntimeError(
        f"ffmpeg failed for {source_path.name} [{start_seconds:.3f}, {end_seconds:.3f}]: "
        f"{completed.stderr.strip() or completed.stdout.strip()}"
    )


def inspect_audio_streams(ffprobe_bin: str, source_path: Path) -> list[dict[str, object]]:
    raise_if_cancelled()
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index:stream_tags=language,title",
        "-of",
        "json",
        str(source_path),
    ]
    completed = _run_hidden_subprocess(command)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {source_path}: {completed.stderr.strip() or completed.stdout.strip()}")
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        return []
    return [stream for stream in streams if isinstance(stream, dict)]


def _language_candidates(value: str) -> set[str]:
    lowered = value.strip().lower()
    aliases = {lowered}
    if lowered == "ja":
        aliases.update({"jpn", "japanese"})
    elif lowered == "jpn":
        aliases.update({"ja", "japanese"})
    elif lowered == "en":
        aliases.update({"eng", "english"})
    elif lowered == "eng":
        aliases.update({"en", "english"})
    return aliases


def choose_audio_stream(ffprobe_bin: str, source_path: Path, args: argparse.Namespace) -> int:
    streams = inspect_audio_streams(ffprobe_bin, source_path)
    if not streams:
        raise RuntimeError(f"no audio streams found in {source_path}")

    if args.audio_stream_index is not None:
        selected = args.audio_stream_index
        if any(int(stream.get("index", -1)) == selected for stream in streams):
            return selected
        raise RuntimeError(f"requested audio stream index {selected} was not found in {source_path}")

    preferred_language = args.audio_stream_lang
    if not preferred_language and args.language != "auto":
        preferred_language = args.language

    if preferred_language:
        aliases = _language_candidates(preferred_language)
        for stream in streams:
            tags = stream.get("tags", {})
            if not isinstance(tags, dict):
                continue
            language = str(tags.get("language", "")).strip().lower()
            title = str(tags.get("title", "")).strip().lower()
            if language in aliases or any(alias in title for alias in aliases):
                return int(stream.get("index", 0))

    return int(streams[0].get("index", 0))


def extract_working_audio(
    ffmpeg_bin: str,
    source_path: Path,
    target_path: Path,
    audio_stream_index: int,
) -> None:
    raise_if_cancelled()
    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source_path),
        "-map",
        f"0:{audio_stream_index}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(target_path),
    ]
    completed = _run_hidden_subprocess(command)
    if completed.returncode == 0:
        return
    raise RuntimeError(f"ffmpeg audio extraction failed for {source_path}: {completed.stderr.strip() or completed.stdout.strip()}")


def _extract_audio_hq(
    ffmpeg_bin: str,
    source_path: Path,
    target_path: Path,
    audio_stream_index: int,
) -> None:
    """Extract full-quality audio (44.1 kHz stereo WAV) suitable for Demucs vocal separation."""
    raise_if_cancelled()
    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source_path),
        "-map",
        f"0:{audio_stream_index}",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(target_path),
    ]
    completed = _run_hidden_subprocess(command)
    if completed.returncode == 0:
        return
    raise RuntimeError(
        f"ffmpeg HQ audio extraction failed for {source_path}: "
        f"{completed.stderr.strip() or completed.stdout.strip()}"
    )


def _separate_vocals(
    hq_audio_path: Path,
    demucs_out_dir: Path,
    demucs_model: str,
) -> Path:
    """Run Demucs vocal separation via Python API and return the path to vocals.wav."""
    raise_if_cancelled()
    try:
        from demucs.separate import main as demucs_main  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "Demucs is not installed. Install it with: pip install demucs"
        )
    _sink = io.StringIO()
    try:
        with redirect_stdout(_sink), redirect_stderr(_sink):
            demucs_main([
                "--two-stems=vocals",
                "--name", demucs_model,
                "--out", str(demucs_out_dir),
                str(hq_audio_path),
            ])
    except SystemExit as exc:
        code = exc.code
        if code not in (None, 0):
            raise RuntimeError(
                f"Demucs separation failed for {hq_audio_path.name} (exit code {code}):\n{_sink.getvalue().strip()}"
            )
    vocals_path = demucs_out_dir / demucs_model / hq_audio_path.stem / "vocals.wav"
    if not vocals_path.exists():
        raise RuntimeError(
            f"Demucs completed but vocals.wav not found at expected path: {vocals_path}"
        )
    return vocals_path


def process_file(
    model: WhisperModel,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    source_path: Path,
    out_dir: Path,
    args: argparse.Namespace,
) -> list[SegmentCandidate]:
    raise_if_cancelled()
    episode_id = sanitize_stem(source_path.stem)
    clips_dir = out_dir / "clips" / episode_id
    clips_dir.mkdir(parents=True, exist_ok=True)

    selected_stream_index = choose_audio_stream(ffprobe_bin, source_path, args)
    print(f"    using audio stream index {selected_stream_index} for {source_path.name}")

    language = None if args.language == "auto" else args.language
    vocals_only = getattr(args, "vocals_only", False)
    demucs_model = getattr(args, "demucs_model", "htdemucs")

    with tempfile.TemporaryDirectory(prefix="gamma-tts-dataset-") as temp_dir:
        temp_path = Path(temp_dir)

        if vocals_only:
            hq_audio_path = temp_path / f"{episode_id}_hq.wav"
            print(f"    extracting HQ audio for Demucs ({source_path.name})...")
            _extract_audio_hq(ffmpeg_bin, source_path, hq_audio_path, selected_stream_index)
            demucs_out_dir = temp_path / "demucs_out"
            demucs_out_dir.mkdir()
            print(f"    running Demucs ({demucs_model}) vocal separation — this may take a while...")
            vocals_path = _separate_vocals(hq_audio_path, demucs_out_dir, demucs_model)
            print(f"    vocal separation complete, transcribing vocals...")
            transcription_source = vocals_path
            clip_source = vocals_path
        else:
            working_audio_path = temp_path / f"{episode_id}.wav"
            extract_working_audio(ffmpeg_bin, source_path, working_audio_path, selected_stream_index)
            transcription_source = working_audio_path
            clip_source = source_path

        segments, info = model.transcribe(
            str(transcription_source),
            language=language,
            vad_filter=True,
            beam_size=args.beam_size,
            condition_on_previous_text=False,
        )
        raise_if_cancelled()
        merged_segments = merge_transcript_segments(segments, gap_threshold=args.merge_gap_seconds)
        effective_language = info.language or args.language
        print(f"    transcript language={effective_language} merged_segments={len(merged_segments)}")

        candidates: list[SegmentCandidate] = []
        rejected_short = 0
        rejected_long = 0
        rejected_text = 0
        rejected_no_speech = 0
        for index, segment in enumerate(merged_segments, start=1):
            raise_if_cancelled()
            start_seconds = float(segment["start"])
            end_seconds = float(segment["end"])
            duration_seconds = max(end_seconds - start_seconds, 0.0)
            text = str(segment["text"]).strip()
            no_speech_prob = _to_optional_float(segment["no_speech_prob"])

            if duration_seconds < args.min_seconds:
                rejected_short += 1
                continue
            if duration_seconds > args.max_seconds:
                rejected_long += 1
                continue
            if len(text) < args.min_chars:
                rejected_text += 1
                continue
            if no_speech_prob is not None and no_speech_prob > args.max_no_speech_prob:
                rejected_no_speech += 1
                continue

            clip_id = f"{episode_id}_{index:04d}"
            clip_path = clips_dir / f"{clip_id}.wav"
            extract_clip(ffmpeg_bin, clip_source, clip_path, start_seconds, end_seconds)
            print(
                "    candidate speech "
                f"{clip_id} start={start_seconds:.2f}s end={end_seconds:.2f}s "
                f"dur={duration_seconds:.2f}s no_speech={no_speech_prob if no_speech_prob is not None else 'n/a'} "
                f"text={text[:80]!r}"
            )
            candidates.append(
                SegmentCandidate(
                    source_path=source_path,
                    clip_path=clip_path,
                    episode_id=episode_id,
                    clip_id=clip_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    duration_seconds=duration_seconds,
                    text=text,
                    language=effective_language,
                    avg_logprob=_to_optional_float(segment["avg_logprob"]),
                    no_speech_prob=no_speech_prob,
                    compression_ratio=_to_optional_float(segment["compression_ratio"]),
                )
            )
        print(
            "    file summary "
            f"accepted={len(candidates)} rejected_short={rejected_short} rejected_long={rejected_long} "
            f"rejected_text={rejected_text} rejected_no_speech={rejected_no_speech}"
        )
    return candidates


def write_outputs(out_dir: Path, candidates: Sequence[SegmentCandidate], args: argparse.Namespace) -> None:
    manifest_path = out_dir / "manifest.jsonl"
    csv_path = out_dir / "segments.csv"
    review_path = out_dir / "REVIEW.md"

    with manifest_path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate.as_dict(), ensure_ascii=False) + "\n")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "clip_id",
                "episode_id",
                "source_path",
                "clip_path",
                "start_seconds",
                "end_seconds",
                "duration_seconds",
                "language",
                "text",
                "avg_logprob",
                "no_speech_prob",
                "compression_ratio",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate.as_dict())

    review_lines = [
        "# TTS Dataset Review",
        "",
        "This dataset is candidate dialogue only. It does not auto-identify Shana.",
        "",
        "Recommended review workflow:",
        "1. Listen to each clip in `clips/` and delete or relabel anything that is not Shana.",
        "2. Remove clips with music bleed, overlapping speakers, screams, whispers, or strong effects.",
        "3. Keep clips with clean solo dialogue and accurate Japanese transcripts.",
        "4. Use the filtered set as your GPT-SoVITS training input or as reference audio.",
        "",
        "Run settings:",
        f"- input: `{args.input_path}`",
        f"- language: `{args.language}`",
        f"- model: `{args.model}`",
        f"- audio_stream_index: `{args.audio_stream_index}`",
        f"- audio_stream_lang: `{args.audio_stream_lang}`",
        f"- exclude_patterns: `{', '.join(DEFAULT_EXCLUDE_PATTERNS + tuple(args.exclude_pattern))}`",
        f"- min_seconds: `{args.min_seconds}`",
        f"- max_seconds: `{args.max_seconds}`",
        f"- merge_gap_seconds: `{args.merge_gap_seconds}`",
        f"- max_no_speech_prob: `{args.max_no_speech_prob}`",
        "",
        f"Generated clips: `{len(candidates)}`",
    ]
    review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    input_path = Path(args.input_path).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not input_path.exists():
        raise SystemExit(f"input path not found: {input_path}")

    ensure_ffmpeg(args.ffmpeg_bin, args.ffprobe_bin)
    exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + tuple(args.exclude_pattern)
    media_files = discover_media_files(input_path, exclude_patterns=exclude_patterns)
    if not media_files:
        raise SystemExit(f"no media files found under: {input_path}")
    if args.limit_files > 0:
        media_files = media_files[: args.limit_files]

    out_dir.mkdir(parents=True, exist_ok=True)
    model = build_model(args)

    all_candidates: list[SegmentCandidate] = []
    for index, media_file in enumerate(media_files, start=1):
        if is_cancelled():
            print("Dataset preparation cancelled before processing the next file.")
            return 130
        print(f"[{index}/{len(media_files)}] processing {media_file}")
        all_candidates.extend(process_file(model, args.ffmpeg_bin, args.ffprobe_bin, media_file, out_dir, args))

    if is_cancelled():
        print("Dataset preparation cancelled before writing outputs.")
        return 130
    write_outputs(out_dir, all_candidates, args)
    print(f"Wrote {len(all_candidates)} clips to {out_dir}")
    print(f"Review candidates in {out_dir / 'REVIEW.md'} before using them for training.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
