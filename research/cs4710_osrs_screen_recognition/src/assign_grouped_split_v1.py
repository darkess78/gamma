"""Assign a deterministic, leakage-safe grouped split to an accepted manifest."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence


MANIFEST_HEADERS = (
    "image_id",
    "relative_image_path",
    "label",
    "capture_session_id",
    "source_group_id",
    "inclusion_status",
    "exclusion_reason",
    "resolution",
    "aspect_ratio",
    "client_type",
    "display_mode",
    "ui_layout_or_scale",
    "crop_policy",
    "camera_angle_bucket",
    "zoom_bucket",
    "location_or_activity_bucket",
    "overlay_notes",
    "redaction_method",
    "redaction_checked",
    "source_consent",
    "split",
    "notes",
)
APPROVED_LABELS = (
    "bank_open",
    "dialogue_or_choice_open",
    "grand_exchange_open",
    "normal_gameplay",
    "shop_open",
    "unknown_other",
)
SPLIT_TARGETS = {"train": 6, "validation": 2, "test": 2}
OVERALL_TARGETS = {"train": 36, "validation": 12, "test": 12}
DEFAULT_SEED = 4710
ALGORITHM_VERSION = "grouped_split_v1"
ALGORITHM_DESCRIPTION = (
    "Sort labels and source-group IDs; initialize one local random.Random(seed); "
    "within each label assign every non-singleton group to train, shuffle the "
    "remaining sorted singleton groups, fill train to 6 images, then assign "
    "2 singleton images to validation and 2 to test."
)


class GroupedSplitError(RuntimeError):
    """Raised when a grouped split cannot be prepared or installed safely."""


def sha256_bytes(contents: bytes) -> str:
    """Return the SHA-256 digest for in-memory bytes."""

    return hashlib.sha256(contents).hexdigest()


def _read_manifest(
    path: Path,
) -> tuple[list[dict[str, str]], bytes]:
    if not path.is_file() or path.is_symlink():
        raise GroupedSplitError(f"input manifest must be a regular file: {path}")
    try:
        original = path.read_bytes()
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise GroupedSplitError(f"input manifest could not be read: {path}") from exc
    if headers != MANIFEST_HEADERS:
        raise GroupedSplitError("manifest must use the exact canonical 22-column schema")
    if not rows:
        raise GroupedSplitError("input manifest must not be empty")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise GroupedSplitError("manifest contains an incomplete or extra column")
    return rows, original


def _require_unique(
    rows: Sequence[dict[str, str]], key: str, description: str
) -> None:
    values = [row[key] for row in rows]
    if len(values) != len(set(values)):
        raise GroupedSplitError(f"duplicate {description} are not allowed")


def _validate_rows(
    rows: Sequence[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    _require_unique(rows, "image_id", "image IDs")
    _require_unique(rows, "relative_image_path", "reviewed paths")

    approved = set(APPROVED_LABELS)
    for row in rows:
        if row["label"] not in approved:
            raise GroupedSplitError("manifest contains an unsupported label")
        if row["inclusion_status"] != "included":
            raise GroupedSplitError("every row must be included")
        if row["redaction_checked"] != "true":
            raise GroupedSplitError("every row must be privacy checked")
        if row["source_consent"] != "owner_captured":
            raise GroupedSplitError("every row must be owner captured")
        if not row["source_group_id"]:
            raise GroupedSplitError("every row must have a nonblank source-group ID")
        if row["split"] != "unassigned":
            raise GroupedSplitError("every input row must have split=unassigned")

    class_counts = Counter(row["label"] for row in rows)
    expected_counts = Counter({label: 10 for label in APPROVED_LABELS})
    if class_counts != expected_counts:
        raise GroupedSplitError(
            "exact 6/2/2 splitting requires exactly 10 rows for every approved label"
        )

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["source_group_id"]].append(row)
    for group_id, group_rows in groups.items():
        if len({row["label"] for row in group_rows}) != 1:
            raise GroupedSplitError(
                f"source group mixes accepted labels: {group_id}"
            )
    return dict(groups)


def _assign_groups(
    groups: dict[str, list[dict[str, str]]],
    *,
    seed: int,
) -> dict[str, str]:
    rng = random.Random(seed)
    assignments: dict[str, str] = {}

    for label in sorted(APPROVED_LABELS):
        label_group_ids = sorted(
            group_id
            for group_id, group_rows in groups.items()
            if group_rows[0]["label"] == label
        )
        non_singleton_ids = [
            group_id for group_id in label_group_ids if len(groups[group_id]) > 1
        ]
        singleton_ids = [
            group_id for group_id in label_group_ids if len(groups[group_id]) == 1
        ]
        non_singleton_image_count = sum(
            len(groups[group_id]) for group_id in non_singleton_ids
        )
        train_singletons_needed = (
            SPLIT_TARGETS["train"] - non_singleton_image_count
        )
        if train_singletons_needed < 0:
            raise GroupedSplitError(
                f"exact preferred split is impossible for {label}: "
                "non-singleton groups exceed the train target"
            )
        if len(singleton_ids) != train_singletons_needed + 4:
            raise GroupedSplitError(
                f"exact preferred split is impossible for {label}: "
                "four singleton evaluation images are required"
            )

        shuffled_singletons = list(singleton_ids)
        rng.shuffle(shuffled_singletons)
        train_singletons = shuffled_singletons[:train_singletons_needed]
        validation_singletons = shuffled_singletons[
            train_singletons_needed : train_singletons_needed + 2
        ]
        test_singletons = shuffled_singletons[train_singletons_needed + 2 :]

        for group_id in non_singleton_ids + train_singletons:
            assignments[group_id] = "train"
        for group_id in validation_singletons:
            assignments[group_id] = "validation"
        for group_id in test_singletons:
            assignments[group_id] = "test"

    if set(assignments) != set(groups):
        raise GroupedSplitError("internal error: not every source group was assigned")
    return assignments


def _apply_assignments(
    rows: Sequence[dict[str, str]],
    assignments: dict[str, str],
) -> list[dict[str, str]]:
    output_rows: list[dict[str, str]] = []
    for row in rows:
        updated = dict(row)
        updated["split"] = assignments[row["source_group_id"]]
        output_rows.append(updated)
        changed = {
            key for key in MANIFEST_HEADERS if row[key] != updated[key]
        }
        if changed != {"split"}:
            raise GroupedSplitError("output must differ from input only in split")
    return output_rows


def _csv_bytes(rows: Sequence[dict[str, str]]) -> bytes:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=MANIFEST_HEADERS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        handle.seek(0)
        return handle.read().encode("utf-8")


def _validate_output(
    input_rows: Sequence[dict[str, str]],
    output_rows: Sequence[dict[str, str]],
) -> None:
    if len(output_rows) != 60:
        raise GroupedSplitError("output must contain exactly 60 rows")
    if [row["image_id"] for row in output_rows] != [
        row["image_id"] for row in input_rows
    ]:
        raise GroupedSplitError("output image ordering changed")
    for before, after in zip(input_rows, output_rows, strict=True):
        if any(
            before[key] != after[key]
            for key in MANIFEST_HEADERS
            if key != "split"
        ):
            raise GroupedSplitError("output differs from input outside split")

    split_counts = Counter(row["split"] for row in output_rows)
    if split_counts != Counter(OVERALL_TARGETS):
        raise GroupedSplitError("output does not have exact 36/12/12 split counts")
    for label in sorted(APPROVED_LABELS):
        counts = Counter(
            row["split"] for row in output_rows if row["label"] == label
        )
        if counts != Counter(SPLIT_TARGETS):
            raise GroupedSplitError(
                f"output does not have exact 6/2/2 counts for {label}"
            )

    group_splits: dict[str, set[str]] = defaultdict(set)
    for row in output_rows:
        group_splits[row["source_group_id"]].add(row["split"])
    crossing = [
        group_id for group_id, splits in group_splits.items() if len(splits) != 1
    ]
    if crossing:
        raise GroupedSplitError("one or more source groups cross splits")


def _audit_document(
    input_path: Path,
    output_path: Path,
    input_bytes: bytes,
    output_bytes: bytes,
    output_rows: Sequence[dict[str, str]],
    groups: dict[str, list[dict[str, str]]],
    assignments: dict[str, str],
    *,
    seed: int,
) -> dict[str, Any]:
    class_counts = Counter(row["label"] for row in output_rows)
    split_counts = Counter(row["split"] for row in output_rows)
    per_class = {
        label: dict(
            sorted(
                Counter(
                    row["split"] for row in output_rows if row["label"] == label
                ).items()
            )
        )
        for label in sorted(APPROVED_LABELS)
    }
    non_singletons = {
        group_id: {
            "label": groups[group_id][0]["label"],
            "group_size": len(groups[group_id]),
            "image_ids": sorted(row["image_id"] for row in groups[group_id]),
            "split": assignments[group_id],
        }
        for group_id in sorted(groups)
        if len(groups[group_id]) > 1
    }
    ids_by_split = {
        split: [
            row["image_id"] for row in output_rows if row["split"] == split
        ]
        for split in ("train", "validation", "test")
    }
    group_splits = {
        group_id: sorted(
            {row["split"] for row in output_rows if row["source_group_id"] == group_id}
        )
        for group_id in sorted(groups)
    }
    capture_sessions = sorted(
        {row["capture_session_id"] for row in output_rows}
    )
    return {
        "schema_version": "1.0",
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm": ALGORITHM_DESCRIPTION,
        "random_seed": seed,
        "input_manifest": {
            "path": input_path.as_posix(),
            "sha256": sha256_bytes(input_bytes),
        },
        "output_manifest": {
            "path": output_path.as_posix(),
            "sha256": sha256_bytes(output_bytes),
        },
        "class_counts": dict(sorted(class_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "per_class_split_counts": per_class,
        "source_group_count": len(groups),
        "non_singleton_group_assignments": non_singletons,
        "source_group_splits": group_splits,
        "no_source_group_crosses_splits": all(
            len(splits) == 1 for splits in group_splits.values()
        ),
        "image_ids_by_split": ids_by_split,
        "capture_session_ids": capture_sessions,
        "capture_session_limitation": (
            "All data came from capture_session_id=session_004."
            if capture_sessions == ["session_004"]
            else "All rows come from the capture sessions listed above."
        ),
        "evaluation_scope": (
            "This is source-group-aware splitting, not capture-session-held-out "
            "evaluation."
        ),
    }


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_destinations(
    input_path: Path,
    output_path: Path,
    audit_path: Path,
    *,
    dry_run: bool,
) -> None:
    resolved = [
        path.resolve(strict=False)
        for path in (input_path, output_path, audit_path)
    ]
    if len(set(resolved)) != 3:
        raise GroupedSplitError("input, output, and audit paths must be distinct")
    if output_path.suffix.lower() != ".csv":
        raise GroupedSplitError("output manifest path must end in .csv")
    if audit_path.suffix.lower() != ".json":
        raise GroupedSplitError("audit path must end in .json")
    for path in (output_path, audit_path):
        if not path.parent.is_dir():
            raise GroupedSplitError(f"output directory does not exist: {path.parent}")
        if path.is_symlink():
            raise GroupedSplitError("output destinations must not be symbolic links")
        if not dry_run and path.exists():
            raise GroupedSplitError(f"destination already exists: {path}")


def _stage_bytes(destination: Path, contents: bytes) -> Path:
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
            return Path(handle.name)
    except OSError as exc:
        raise GroupedSplitError(f"could not stage output for {destination}") from exc


def _install_outputs(
    output_path: Path,
    audit_path: Path,
    output_bytes: bytes,
    audit_bytes: bytes,
) -> None:
    staged_output = _stage_bytes(output_path, output_bytes)
    staged_audit = _stage_bytes(audit_path, audit_bytes)
    installed: list[Path] = []
    try:
        if staged_output.read_bytes() != output_bytes:
            raise GroupedSplitError("staged manifest verification failed")
        if json.loads(staged_audit.read_text(encoding="utf-8")) != json.loads(
            audit_bytes.decode("utf-8")
        ):
            raise GroupedSplitError("staged audit verification failed")
        for staged, destination in (
            (staged_output, output_path),
            (staged_audit, audit_path),
        ):
            try:
                os.link(staged, destination)
            except FileExistsError as exc:
                raise GroupedSplitError(
                    f"destination appeared during installation: {destination}"
                ) from exc
            except OSError as exc:
                raise GroupedSplitError(
                    f"output could not be installed atomically: {destination}"
                ) from exc
            installed.append(destination)
    except Exception:
        for path in reversed(installed):
            path.unlink(missing_ok=True)
        raise
    finally:
        staged_output.unlink(missing_ok=True)
        staged_audit.unlink(missing_ok=True)


def assign_grouped_split(
    input_manifest: str | os.PathLike[str],
    output_manifest: str | os.PathLike[str],
    audit_path: str | os.PathLike[str],
    *,
    seed: int = DEFAULT_SEED,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate, assign, and optionally install a grouped 6/2/2 split."""

    if seed != DEFAULT_SEED:
        raise GroupedSplitError(f"{ALGORITHM_VERSION} requires seed={DEFAULT_SEED}")
    input_path = Path(input_manifest)
    output_path = Path(output_manifest)
    audit = Path(audit_path)
    _validate_destinations(input_path, output_path, audit, dry_run=dry_run)
    rows, input_bytes = _read_manifest(input_path)
    groups = _validate_rows(rows)
    assignments = _assign_groups(groups, seed=seed)
    output_rows = _apply_assignments(rows, assignments)
    _validate_output(rows, output_rows)
    output_bytes = _csv_bytes(output_rows)
    document = _audit_document(
        input_path,
        output_path,
        input_bytes,
        output_bytes,
        output_rows,
        groups,
        assignments,
        seed=seed,
    )
    audit_bytes = _json_bytes(document)

    if not dry_run:
        _install_outputs(output_path, audit, output_bytes, audit_bytes)

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "dry_run": dry_run,
        "input_sha256": sha256_bytes(input_bytes),
        "output_sha256": sha256_bytes(output_bytes),
        "row_count": len(output_rows),
        "source_group_count": len(groups),
        "split_counts": document["split_counts"],
        "per_class_split_counts": document["per_class_split_counts"],
        "no_source_group_crosses_splits": document[
            "no_source_group_crosses_splits"
        ],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Assign a deterministic source-group-aware split to an accepted "
            "CS 4710 OSRS manifest."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--dry-run",
        "--validate-only",
        dest="dry_run",
        action="store_true",
        help="validate and summarize without creating either output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = assign_grouped_split(
            args.input,
            args.output,
            args.audit,
            seed=args.seed,
            dry_run=args.dry_run,
        )
    except GroupedSplitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
