from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from gamma.config import settings


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Transcribe exported Shana TTS asset clips and update manifests.",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=repo_root / "assets" / "voice_datasets" / "shana",
        help="Exported Shana asset directory containing manifests/shana_all.jsonl.",
    )
    parser.add_argument("--language", default="ja", help="Whisper language hint.")
    parser.add_argument("--model", default=settings.stt_model, help="faster-whisper model name.")
    parser.add_argument("--device", default=settings.stt_device, help="Inference device.")
    parser.add_argument("--device-index", type=int, default=settings.stt_device_index)
    parser.add_argument("--compute-type", default=settings.stt_compute_type)
    parser.add_argument("--beam-size", type=int, default=5)
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
            row["transcript_error"] = f"missing clip: {row['clip_path']}"
            continue
        transcript = transcribe_clip(model, clip_path, args)
        row["text"] = transcript
        row["text_needs_review"] = True
        row["text_source"] = f"faster-whisper:{args.model}"
        row.pop("transcript_error", None)
        updated += 1
        print(f"[{index}/{len(rows)}] {row['clip_id']}: {transcript}")

    write_jsonl(all_manifest, rows)
    by_name = {
        "clean": [row for row in rows if row.get("label") == "Shana"],
        "light_noise": [row for row in rows if row.get("label") == "Shana-light-noise"],
        "heavy_noise": [row for row in rows if row.get("label") == "Shana-heavy-noise"],
    }
    for name, grouped_rows in by_name.items():
        write_jsonl(manifest_dir / f"{name}.jsonl", grouped_rows)

    readme_path = dataset_dir / "README.md"
    if readme_path.exists():
        text = readme_path.read_text(encoding="utf-8")
        lines = []
        for line in text.splitlines():
            if line.startswith("Manifest rows with transcript text:"):
                line = f"Manifest rows with transcript text: {sum(1 for row in rows if row.get('text'))}"
            elif line.startswith("Manifest rows needing transcript review:"):
                line = f"Manifest rows needing transcript review: {sum(1 for row in rows if row.get('text_needs_review'))}"
            lines.append(line)
        readme_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    print(f"updated={updated} manifest={all_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
