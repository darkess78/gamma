from __future__ import annotations

import argparse
import shutil
import sys
import threading
from pathlib import Path
from typing import Sequence

from .run_prepare_tts_dataset import DEFAULT_EXCLUDE_PATTERNS, main as prepare_dataset_main

_CANCEL_EVENT: threading.Event | None = None


def set_cancel_event(cancel_event: threading.Event | None) -> None:
    global _CANCEL_EVENT
    _CANCEL_EVENT = cancel_event


def is_cancelled() -> bool:
    return _CANCEL_EVENT is not None and _CANCEL_EVENT.is_set()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy episode media from a source directory or share to a local staging folder, "
            "then optionally run dataset preparation on the staged copy."
        )
    )
    parser.add_argument("source_path", help="Source media directory, including UNC shares.")
    parser.add_argument(
        "--staging-dir",
        default="data/source_media/shakugan_no_shana",
        help="Local directory where source files will be copied.",
    )
    parser.add_argument(
        "--dataset-out-dir",
        default="data/shana_dataset",
        help="Output directory for extracted clips and manifests.",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Run the dataset preparation step after staging completes.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite staged files even when size and modified time match.",
    )
    parser.add_argument(
        "--exclude-pattern",
        action="append",
        default=[],
        help=(
            "Case-insensitive filename substring to exclude from staging. "
            "Can be passed multiple times. Defaults already exclude recap files."
        ),
    )
    parser.add_argument(
        "--limit-files",
        type=int,
        default=0,
        help="If non-zero, only copy this many files from the source.",
    )
    parser.add_argument(
        "--prepare-args",
        nargs=argparse.REMAINDER,
        help=(
            "Extra arguments passed through to gamma.run_prepare_tts_dataset. "
            "Use after '--prepare-args', for example: --prepare-args --language ja --limit-files 2"
        ),
    )
    return parser.parse_args(argv)


def should_copy(source_path: Path, destination_path: Path, overwrite: bool) -> bool:
    if overwrite or not destination_path.exists():
        return True
    source_stat = source_path.stat()
    destination_stat = destination_path.stat()
    if source_stat.st_size != destination_stat.st_size:
        return True
    if int(source_stat.st_mtime) != int(destination_stat.st_mtime):
        return True
    return False


def should_exclude_path(path: Path, exclude_patterns: Sequence[str]) -> bool:
    lowered = path.name.lower()
    return any(pattern.strip().lower() in lowered for pattern in exclude_patterns if pattern.strip())


def copy_tree(
    source_root: Path,
    destination_root: Path,
    overwrite: bool,
    limit_files: int,
    exclude_patterns: Sequence[str],
) -> tuple[int, int]:
    copied = 0
    skipped = 0
    files = sorted(
        path for path in source_root.rglob("*") if path.is_file() and not should_exclude_path(path, exclude_patterns)
    )
    if limit_files > 0:
        files = files[:limit_files]

    for index, source_path in enumerate(files, start=1):
        if is_cancelled():
            print("Staging cancelled before copying the next file.")
            break
        relative_path = source_path.relative_to(source_root)
        destination_path = destination_root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if should_copy(source_path, destination_path, overwrite=overwrite):
            print(f"[{index}/{len(files)}] copy {relative_path}")
            shutil.copy2(source_path, destination_path)
            copied += 1
        else:
            print(f"[{index}/{len(files)}] skip {relative_path}")
            skipped += 1
    return copied, skipped


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    source_root = Path(args.source_path).expanduser()
    staging_root = Path(args.staging_dir).expanduser().resolve()
    dataset_out_dir = Path(args.dataset_out_dir).expanduser().resolve()

    if not source_root.exists():
        raise SystemExit(f"source path not found: {source_root}")
    if not source_root.is_dir():
        raise SystemExit(f"source path is not a directory: {source_root}")

    staging_root.mkdir(parents=True, exist_ok=True)
    exclude_patterns = tuple(dict.fromkeys(DEFAULT_EXCLUDE_PATTERNS + tuple(args.exclude_pattern)))
    copied, skipped = copy_tree(
        source_root=source_root,
        destination_root=staging_root,
        overwrite=args.overwrite,
        limit_files=args.limit_files,
        exclude_patterns=exclude_patterns,
    )
    print(
        "Staging complete. "
        f"copied={copied} skipped={skipped} destination={staging_root} "
        f"excluded={', '.join(exclude_patterns)}"
    )

    if is_cancelled():
        print("Pipeline cancelled after staging.")
        return 130

    if not args.prepare:
        print("Dataset prep not started. Re-run with --prepare to continue into clip extraction.")
        return 0

    forwarded_args = [str(staging_root), "--out-dir", str(dataset_out_dir)]
    if args.prepare_args:
        forwarded_args.extend(args.prepare_args)
    print(f"Starting dataset prep from {staging_root} into {dataset_out_dir}")
    return prepare_dataset_main(forwarded_args)


if __name__ == "__main__":
    raise SystemExit(main())
