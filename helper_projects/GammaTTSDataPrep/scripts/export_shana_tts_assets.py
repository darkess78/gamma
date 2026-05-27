from __future__ import annotations

import argparse
import csv
import json
import shutil
import wave
from pathlib import Path
from typing import Any


LABEL_DIRS = {
    "Shana": "clean",
    "Shana-light-noise": "light_noise",
    "Shana-heavy-noise": "heavy_noise",
}


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Export reviewed Shana TTS clips into a small repo asset folder."
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=repo_root / "data" / "tts_data_prep" / "shana_dataset",
        help="Reviewed dataset directory containing labels.json and manifest.jsonl.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=repo_root / "assets" / "voice_datasets" / "shana",
        help="Output directory for Shana-only clips and manifests.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the existing output directory before exporting.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            clip_id = str(row.get("clip_id", "")).strip()
            if clip_id:
                rows[clip_id] = row
    return rows


def load_manifests(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    rows = load_manifest(dataset_dir / "manifest.jsonl")
    segments_path = dataset_dir / "segments.csv"
    if segments_path.exists():
        with segments_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                clip_id = str(row.get("clip_id", "")).strip()
                if not clip_id:
                    continue
                rows.setdefault(clip_id, {}).update(
                    {
                        "clip_id": clip_id,
                        "duration_seconds": parse_float(row.get("duration_seconds")),
                        "episode_id": row.get("episode_id"),
                        "language": row.get("language") or "ja",
                        "text": row.get("text") or "",
                    }
                )
    manifest_dir = dataset_dir / "exports" / "manifests"
    if manifest_dir.exists():
        for path in sorted(manifest_dir.glob("*.jsonl")):
            rows.update(load_manifest(path))
    return rows


def parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def base_clip_id(clip_id: str) -> str:
    marker = "_trim_"
    if marker not in clip_id:
        return clip_id
    return clip_id.split(marker, 1)[0]


def wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
        if rate <= 0:
            return None
        return round(frames / rate, 3)
    except (wave.Error, OSError):
        return None


def relative_to_repo(path: Path) -> str:
    repo_root = Path(__file__).resolve().parents[3]
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    dataset_dir = args.dataset_dir.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[3]

    if not dataset_dir.exists():
        raise SystemExit(f"dataset directory not found: {dataset_dir}")
    labels_path = dataset_dir / "labels.json"
    if not labels_path.exists():
        raise SystemExit(f"labels file not found: {labels_path}")

    if args.clean and out_dir.exists():
        if repo_root not in out_dir.parents:
            raise SystemExit(f"refusing to clean output outside repo: {out_dir}")
        shutil.rmtree(out_dir)

    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    manifest_rows = load_manifests(dataset_dir)
    exported_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []

    for clip_id, label_row in sorted(labels.items()):
        label = label_row.get("label")
        target_subdir = LABEL_DIRS.get(label)
        if target_subdir is None:
            continue

        source_clip = Path(str(label_row.get("clip_path", ""))).expanduser()
        if not source_clip.exists():
            missing_rows.append(
                {
                    "clip_id": clip_id,
                    "label": label,
                    "missing_clip_path": str(source_clip),
                }
            )
            continue

        target_clip = out_dir / target_subdir / source_clip.name
        target_clip.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_clip, target_clip)

        manifest_row = manifest_rows.get(clip_id, {})
        source_manifest_row = manifest_rows.get(base_clip_id(clip_id), {})
        duration_seconds = manifest_row.get("duration_seconds") or label_row.get("duration_seconds")
        if duration_seconds is None:
            duration_seconds = wav_duration_seconds(source_clip)
        text = manifest_row.get("text") or label_row.get("text") or ""
        text_source = "manifest"
        text_needs_review = False
        if not manifest_row.get("text") and label_row.get("text"):
            text_source = "label_metadata"
        if not text and source_manifest_row.get("text"):
            text = source_manifest_row.get("text", "")
            text_source = "base_clip_manifest"
            text_needs_review = clip_id != base_clip_id(clip_id)
        elif not text:
            text_source = "missing"
            text_needs_review = True
        source_episode = Path(str(label_row.get("source_path", ""))).name
        exported_rows.append(
            {
                "clip_id": clip_id,
                "clip_path": relative_to_repo(target_clip),
                "duration_seconds": duration_seconds,
                "episode_id": (
                    manifest_row.get("episode_id")
                    or label_row.get("episode_id")
                    or source_manifest_row.get("episode_id")
                ),
                "label": label,
                "language": (
                    manifest_row.get("language")
                    or label_row.get("language")
                    or source_manifest_row.get("language")
                    or "ja"
                ),
                "source_episode": source_episode,
                "text": text,
                "text_needs_review": text_needs_review,
                "text_source": text_source,
            }
        )

    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in LABEL_DIRS}
    for row in exported_rows:
        groups[str(row["label"])].append(row)

    manifest_dir = out_dir / "manifests"
    write_jsonl(manifest_dir / "shana_all.jsonl", exported_rows)
    for label, rows in groups.items():
        write_jsonl(manifest_dir / f"{LABEL_DIRS[label]}.jsonl", rows)
    write_jsonl(manifest_dir / "missing.jsonl", missing_rows)

    text_ready = sum(1 for row in exported_rows if row.get("text"))
    needs_text_review = sum(1 for row in exported_rows if row.get("text_needs_review"))
    readme = (
        "# Shana TTS Dataset\n\n"
        "Reviewed Shana-only clips exported from `data/tts_data_prep/shana_dataset`.\n"
        "This excludes clips labeled `Not Shana` or `Reject`.\n\n"
        f"Exported clips: {len(exported_rows)}\n\n"
        f"Manifest rows with transcript text: {text_ready}\n\n"
        f"Manifest rows needing transcript review: {needs_text_review}\n\n"
        "Labels:\n"
        "- `clean`: clips labeled `Shana`\n"
        "- `light_noise`: clips labeled `Shana-light-noise`\n"
        "- `heavy_noise`: clips labeled `Shana-heavy-noise`\n\n"
        "The WAV files are reviewed by speaker label, but many rows still need accurate transcript text before "
        "they should be used for supervised TTS training.\n\n"
        "Use `helper_projects/GammaTTSDataPrep/scripts/export_shana_tts_assets.py --clean` "
        "to refresh this folder after labeling more clips.\n"
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")

    print(f"exported={len(exported_rows)} missing={len(missing_rows)} out_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
