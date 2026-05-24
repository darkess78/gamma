from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from gamma.config import settings


PUNCTUATION_RE = re.compile(r"[\s、。,.!?！？…・「」『』（）()\[\]{}\"']")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Cross-check Shana TTS transcripts with a larger Whisper model.",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=repo_root / "assets" / "voice_datasets" / "shana",
        help="Exported Shana asset directory containing manifests/shana_all.jsonl.",
    )
    parser.add_argument("--language", default="ja", help="Whisper language hint.")
    parser.add_argument("--model", default="large-v3", help="faster-whisper model name.")
    parser.add_argument("--device", default=settings.stt_device, help="Inference device.")
    parser.add_argument("--device-index", type=int, default=settings.stt_device_index)
    parser.add_argument("--compute-type", default=settings.stt_compute_type)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument(
        "--candidate-min-score",
        type=float,
        default=0.82,
        help="Minimum normalized similarity score for training_candidate=true.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return PUNCTUATION_RE.sub("", normalized).strip()


def agreement(primary: str, check: str) -> tuple[str, float]:
    primary_normalized = normalize_text(primary)
    check_normalized = normalize_text(check)
    if not primary_normalized or not check_normalized:
        return "empty", 0.0
    if primary_normalized == check_normalized:
        return "match", 1.0
    score = difflib.SequenceMatcher(None, primary_normalized, check_normalized).ratio()
    if score >= 0.82:
        return "close", round(score, 4)
    return "different", round(score, 4)


def transcribe_clip(model: WhisperModel, path: Path, args: argparse.Namespace) -> str:
    language = None if args.language == "auto" else args.language
    segments, _info = model.transcribe(
        str(path),
        language=language,
        vad_filter=False,
        beam_size=args.beam_size,
        condition_on_previous_text=False,
    )
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


def update_readme(dataset_dir: Path, rows: list[dict[str, Any]]) -> None:
    readme_path = dataset_dir / "README.md"
    if not readme_path.exists():
        return
    text = readme_path.read_text(encoding="utf-8")
    counts = Counter(str(row.get("text_agreement") or "unchecked") for row in rows)
    checked = sum(1 for row in rows if row.get("text_check"))
    candidates = sum(1 for row in rows if row.get("training_candidate"))
    needs_review = sum(1 for row in rows if row.get("text_needs_review"))
    replacement = (
        f"Large-model transcript checks: {checked}\n\n"
        f"Training candidate rows: {candidates}\n\n"
        "Transcript agreement:\n"
        f"- `match`: {counts.get('match', 0)}\n"
        f"- `close`: {counts.get('close', 0)}\n"
        f"- `different`: {counts.get('different', 0)}\n"
        f"- `empty`: {counts.get('empty', 0)}\n"
    )
    marker = "Large-model transcript checks:"
    if marker in text:
        before = text.split(marker, 1)[0].rstrip()
        suffix_marker = "Labels:\n"
        after = text.split(suffix_marker, 1)[1] if suffix_marker in text else ""
        text = before + "\n\n" + replacement + "\n" + suffix_marker + after
    else:
        text = text.replace("Labels:\n", replacement + "\nLabels:\n", 1)
    text = re.sub(
        r"Manifest rows needing transcript review: \d+",
        f"Manifest rows needing transcript review: {needs_review}",
        text,
        count=1,
    )
    readme_path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    dataset_dir = args.dataset_dir.expanduser().resolve()
    manifest_dir = dataset_dir / "manifests"
    all_manifest = manifest_dir / "shana_all.jsonl"
    if not all_manifest.exists():
        raise SystemExit(f"manifest not found: {all_manifest}")

    repo_root = Path(__file__).resolve().parents[1]
    rows = load_jsonl(all_manifest)
    model = WhisperModel(
        args.model,
        device=args.device,
        device_index=args.device_index,
        compute_type=args.compute_type,
    )

    updated = 0
    for index, row in enumerate(rows, start=1):
        clip_path = repo_root / str(row["clip_path"])
        if not clip_path.exists():
            row["transcript_check_error"] = f"missing clip: {row['clip_path']}"
            continue
        check_text = transcribe_clip(model, clip_path, args)
        agreement_name, score = agreement(str(row.get("text") or ""), check_text)
        row["text_check"] = check_text
        row["text_check_source"] = f"faster-whisper:{args.model}"
        row["text_agreement"] = agreement_name
        row["text_agreement_score"] = score
        row["text_needs_review"] = agreement_name != "match"
        row["training_candidate"] = (
            row.get("label") == "Shana"
            and bool(row.get("text"))
            and bool(check_text)
            and score >= args.candidate_min_score
        )
        row.pop("transcript_check_error", None)
        updated += 1
        print(
            f"[{index}/{len(rows)}] {row['clip_id']}: "
            f"{agreement_name} {score:.4f} | {check_text}"
        )

    write_jsonl(all_manifest, rows)
    by_name = {
        "clean": [row for row in rows if row.get("label") == "Shana"],
        "light_noise": [row for row in rows if row.get("label") == "Shana-light-noise"],
        "heavy_noise": [row for row in rows if row.get("label") == "Shana-heavy-noise"],
    }
    for name, grouped_rows in by_name.items():
        write_jsonl(manifest_dir / f"{name}.jsonl", grouped_rows)
    update_readme(dataset_dir, rows)

    counts = Counter(str(row.get("text_agreement") or "unchecked") for row in rows)
    print(
        "updated="
        f"{updated} match={counts.get('match', 0)} close={counts.get('close', 0)} "
        f"different={counts.get('different', 0)} empty={counts.get('empty', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
