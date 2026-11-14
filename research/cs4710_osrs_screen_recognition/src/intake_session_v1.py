"""Create private draft intake artifacts for an approved CS 4710 session."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NamedTuple, Sequence

from PIL import Image, UnidentifiedImageError

import crop_v1


CAPTURE_SESSION_ID = "session_001"
AUDIT_SCHEMA_VERSION = "1.0"
REVIEWED_ASPECT_RATIO = "2287:1369"
APPROVED_LABELS = frozenset(
    {
        "normal_gameplay",
        "bank_open",
        "dialogue_or_choice_open",
        "shop_open",
        "grand_exchange_open",
        "unknown_other",
    }
)

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


class IntakePlanItem(NamedTuple):
    raw_name: str
    reviewed_name: str
    image_id: str
    label: str
    source_group_id: str
    camera_angle_bucket: str = ""
    zoom_bucket: str = ""
    location_or_activity_bucket: str = ""


SESSION_PLANS: Mapping[str, tuple[IntakePlanItem, ...]] = {
    "session_001": (
        IntakePlanItem(
            "normal_gameplay_001.png",
            "img_0001.png",
            "img_0001",
            "normal_gameplay",
            "source_group_0001",
        ),
        IntakePlanItem(
            "bank_open_001.png",
            "img_0002.png",
            "img_0002",
            "bank_open",
            "source_group_0002",
        ),
        IntakePlanItem(
            "dialogue_or_choice_open_001.png",
            "img_0003.png",
            "img_0003",
            "dialogue_or_choice_open",
            "source_group_0003",
        ),
        IntakePlanItem(
            "shop_open_002.png",
            "img_0004.png",
            "img_0004",
            "shop_open",
            "source_group_0004",
        ),
        IntakePlanItem(
            "grand_exchange_open_002.png",
            "img_0005.png",
            "img_0005",
            "grand_exchange_open",
            "source_group_0005",
        ),
        IntakePlanItem(
            "unknown_other_001.png",
            "img_0006.png",
            "img_0006",
            "unknown_other",
            "source_group_0006",
        ),
    ),
    "session_002": (
        IntakePlanItem(
            "normal_gameplay_001.png",
            "img_0007.png",
            "img_0007",
            "normal_gameplay",
            "source_group_0007",
            "north_facing",
        ),
        IntakePlanItem(
            "bank_open_001.png",
            "img_0008.png",
            "img_0008",
            "bank_open",
            "source_group_0008",
            "north_facing",
        ),
        IntakePlanItem(
            "dialogue_or_choice_open_001.png",
            "img_0009.png",
            "img_0009",
            "dialogue_or_choice_open",
            "source_group_0009",
            "north_facing",
        ),
        IntakePlanItem(
            "shop_open_001.png",
            "img_0010.png",
            "img_0010",
            "shop_open",
            "source_group_0010",
            "north_facing",
        ),
        IntakePlanItem(
            "grand_exchange_open_001.png",
            "img_0011.png",
            "img_0011",
            "grand_exchange_open",
            "source_group_0011",
            "north_facing",
        ),
        IntakePlanItem(
            "unknown_other_001.png",
            "img_0012.png",
            "img_0012",
            "unknown_other",
            "source_group_0012",
            "north_facing",
        ),
    ),
    "session_003": (
        IntakePlanItem(
            "normal_gameplay_001.png",
            "img_0013.png",
            "img_0013",
            "normal_gameplay",
            "source_group_0013",
            "north_facing",
        ),
        IntakePlanItem(
            "bank_open_001.png",
            "img_0014.png",
            "img_0014",
            "bank_open",
            "source_group_0014",
            "north_facing",
        ),
        IntakePlanItem(
            "dialogue_or_choice_open_001.png",
            "img_0015.png",
            "img_0015",
            "dialogue_or_choice_open",
            "source_group_0015",
            "north_facing",
        ),
        IntakePlanItem(
            "shop_open_001.png",
            "img_0016.png",
            "img_0016",
            "shop_open",
            "source_group_0016",
            "north_facing",
        ),
        IntakePlanItem(
            "grand_exchange_open_001.png",
            "img_0017.png",
            "img_0017",
            "grand_exchange_open",
            "source_group_0017",
            "north_facing",
        ),
        IntakePlanItem(
            "unknown_other_001.png",
            "img_0018.png",
            "img_0018",
            "unknown_other",
            "source_group_0018",
            "north_facing",
        ),
    ),
}

# Preserve the original session_001 import contract for existing callers.
INTAKE_PLAN = SESSION_PLANS[CAPTURE_SESSION_ID]

CAMERA_ANGLE_BUCKETS = frozenset(
    {
        "",
        "north_facing",
        "east_facing",
        "south_facing",
        "west_facing",
        "mixed_or_unclear",
        "not_applicable",
    }
)
ZOOM_BUCKETS = frozenset({"", "near", "medium", "far", "not_applicable"})


class PriorDatasetState(NamedTuple):
    session_ids: frozenset[str] = frozenset()
    accepted_examples: frozenset[tuple[str, str]] = frozenset()
    image_ids: frozenset[str] = frozenset()
    reviewed_paths: frozenset[str] = frozenset()
    source_group_ids: frozenset[str] = frozenset()
    source_hashes: frozenset[str] = frozenset()
    reviewed_hashes: frozenset[str] = frozenset()


class IntakeError(RuntimeError):
    """Raised when a complete session intake cannot be created safely."""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_session_plan(session_id: str) -> tuple[IntakePlanItem, ...]:
    """Return one explicit approved plan, rejecting arbitrary session names."""

    try:
        return SESSION_PLANS[session_id]
    except KeyError as exc:
        raise IntakeError(f"unknown capture session: {session_id}") from exc


def session_artifact_names(session_id: str) -> tuple[str, str]:
    """Return the fixed private draft and audit filenames for a known session."""

    get_session_plan(session_id)
    return f"{session_id}_draft.csv", f"{session_id}_audit.json"


def validate_intake_plan(
    plan: Sequence[IntakePlanItem] = INTAKE_PLAN,
) -> None:
    """Validate the fixed six-example mapping without touching the filesystem."""

    if len(plan) != 6:
        raise IntakeError("the intake plan must contain exactly six examples")

    raw_names = [item.raw_name for item in plan]
    reviewed_names = [item.reviewed_name for item in plan]
    image_ids = [item.image_id for item in plan]
    source_group_ids = [item.source_group_id for item in plan]
    labels = [item.label for item in plan]

    for description, values in (
        ("source names", raw_names),
        ("reviewed names", reviewed_names),
        ("image IDs", image_ids),
        ("source group IDs", source_group_ids),
    ):
        if len(set(values)) != len(values):
            raise IntakeError(f"intake plan contains duplicate {description}")

    if set(labels) != APPROVED_LABELS or len(labels) != len(APPROVED_LABELS):
        raise IntakeError("intake plan must contain each approved label exactly once")

    for item in plan:
        if Path(item.raw_name).name != item.raw_name or not item.raw_name.lower().endswith(
            ".png"
        ):
            raise IntakeError("intake source names must be simple PNG filenames")
        if (
            Path(item.reviewed_name).name != item.reviewed_name
            or not item.reviewed_name.lower().endswith(".png")
        ):
            raise IntakeError("reviewed names must be simple PNG filenames")
        if item.camera_angle_bucket not in CAMERA_ANGLE_BUCKETS:
            raise IntakeError("intake plan contains an unsupported camera angle bucket")
        if item.zoom_bucket not in ZOOM_BUCKETS:
            raise IntakeError("intake plan contains an unsupported zoom bucket")


def validate_session_plans(
    plans: Mapping[str, Sequence[IntakePlanItem]] = SESSION_PLANS,
) -> None:
    """Validate every approved plan and identifiers shared across plans."""

    if not plans:
        raise IntakeError("at least one capture-session plan is required")

    image_ids: list[str] = []
    reviewed_names: list[str] = []
    source_group_ids: list[str] = []
    for session_id, plan in plans.items():
        if session_id not in SESSION_PLANS:
            if (
                Path(session_id).name != session_id
                or not session_id.startswith("session_")
            ):
                raise IntakeError("capture-session IDs must be safe opaque names")
        validate_intake_plan(plan)
        image_ids.extend(item.image_id for item in plan)
        reviewed_names.extend(item.reviewed_name for item in plan)
        source_group_ids.extend(item.source_group_id for item in plan)

    for description, values in (
        ("image IDs across session plans", image_ids),
        ("reviewed paths across session plans", reviewed_names),
        ("source group IDs across session plans", source_group_ids),
    ):
        if len(set(values)) != len(values):
            raise IntakeError(f"duplicate {description}")


def load_prior_dataset_state(
    *,
    manifest_paths: Sequence[str | os.PathLike[str]] = (),
    audit_paths: Sequence[str | os.PathLike[str]] = (),
    current_session_id: str,
) -> PriorDatasetState:
    """Load safe identifiers and hashes from explicitly supplied prior records."""

    session_ids: set[str] = set()
    accepted_examples: set[tuple[str, str]] = set()
    image_ids: set[str] = set()
    reviewed_paths: set[str] = set()
    source_group_ids: set[str] = set()
    source_hashes: set[str] = set()
    reviewed_hashes: set[str] = set()

    for manifest_value in manifest_paths:
        manifest_path = Path(manifest_value)
        try:
            with manifest_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = tuple(reader.fieldnames or ())
                rows = list(reader)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise IntakeError("a prior manifest could not be read") from exc
        if headers != MANIFEST_HEADERS:
            raise IntakeError("a prior manifest has an unexpected schema")
        for row in rows:
            session_id = row["capture_session_id"]
            if session_id == current_session_id:
                continue
            relative_path = row["relative_image_path"]
            if Path(relative_path).is_absolute():
                raise IntakeError("a prior manifest contains an absolute reviewed path")
            session_ids.add(session_id)
            image_ids.add(row["image_id"])
            reviewed_paths.add(relative_path)
            source_group_ids.add(row["source_group_id"])
            if row["inclusion_status"] == "included":
                accepted_examples.add((session_id, row["image_id"]))

    for audit_value in audit_paths:
        audit_path = Path(audit_value)
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise IntakeError("a prior audit JSON file could not be read") from exc
        if not isinstance(audit, dict) or not isinstance(audit.get("images"), list):
            raise IntakeError("a prior audit JSON file has an unexpected schema")
        session_id = audit.get("capture_session_id")
        if not isinstance(session_id, str):
            raise IntakeError("a prior audit JSON file has no capture-session ID")
        if session_id == current_session_id:
            continue
        session_ids.add(session_id)
        for record in audit["images"]:
            if not isinstance(record, dict):
                raise IntakeError("a prior audit image record is invalid")
            source_hash = record.get("source_sha256")
            reviewed_hash = record.get("reviewed_sha256")
            if not isinstance(source_hash, str) or not isinstance(reviewed_hash, str):
                raise IntakeError("a prior audit image record has invalid hashes")
            source_hashes.add(source_hash)
            reviewed_hashes.add(reviewed_hash)

    return PriorDatasetState(
        session_ids=frozenset(session_ids),
        accepted_examples=frozenset(accepted_examples),
        image_ids=frozenset(image_ids),
        reviewed_paths=frozenset(reviewed_paths),
        source_group_ids=frozenset(source_group_ids),
        source_hashes=frozenset(source_hashes),
        reviewed_hashes=frozenset(reviewed_hashes),
    )


def _merge_prior_hashes(
    prior_state: PriorDatasetState,
    *,
    source_hashes: Sequence[str],
    reviewed_hashes: Sequence[str],
) -> PriorDatasetState:
    return prior_state._replace(
        source_hashes=prior_state.source_hashes.union(source_hashes),
        reviewed_hashes=prior_state.reviewed_hashes.union(reviewed_hashes),
    )


def validate_cross_session_plan(
    plan: Sequence[IntakePlanItem],
    prior_state: PriorDatasetState,
) -> None:
    """Reject identifier/path collisions with explicitly supplied prior data."""

    collisions = (
        ("image IDs", {item.image_id for item in plan} & prior_state.image_ids),
        (
            "reviewed paths",
            {
                str(PurePosixPath("reviewed") / item.reviewed_name)
                for item in plan
            }
            & prior_state.reviewed_paths,
        ),
        (
            "source group IDs",
            {item.source_group_id for item in plan} & prior_state.source_group_ids,
        ),
    )
    for description, values in collisions:
        if values:
            raise IntakeError(f"cross-session duplicate {description} are not allowed")


def _validate_destination_layout(
    source_dir: Path,
    reviewed_dir: Path,
    manifest_path: Path,
    audit_path: Path,
    plan: Sequence[IntakePlanItem],
    *,
    overwrite: bool,
) -> list[Path]:
    if not source_dir.exists():
        raise IntakeError("source directory does not exist")
    if not source_dir.is_dir():
        raise IntakeError("source directory is not a directory")
    if reviewed_dir.exists() and not reviewed_dir.is_dir():
        raise IntakeError("reviewed destination is not a directory")
    if not reviewed_dir.parent.is_dir():
        raise IntakeError("reviewed destination parent does not exist")
    if not manifest_path.parent.is_dir():
        raise IntakeError("manifest destination parent does not exist")
    if not audit_path.parent.is_dir():
        raise IntakeError("audit destination parent does not exist")
    if manifest_path.suffix.lower() != ".csv":
        raise IntakeError("manifest destination must have a .csv extension")
    if audit_path.suffix.lower() != ".json":
        raise IntakeError("audit destination must have a .json extension")

    destination_paths = [
        *(reviewed_dir / item.reviewed_name for item in plan),
        manifest_path,
        audit_path,
    ]
    resolved_destinations = [_resolved(path) for path in destination_paths]
    if len(set(resolved_destinations)) != len(resolved_destinations):
        raise IntakeError("planned output paths must be unique")

    resolved_sources = {_resolved(source_dir / item.raw_name) for item in plan}
    if resolved_sources.intersection(resolved_destinations):
        raise IntakeError("source and output paths must be different")

    device_ids = {
        reviewed_dir.parent.stat().st_dev,
        manifest_path.parent.stat().st_dev,
        audit_path.parent.stat().st_dev,
    }
    if len(device_ids) != 1:
        raise IntakeError("all intake destinations must be on one filesystem")

    existing = [path for path in destination_paths if path.exists()]
    if existing and not overwrite:
        raise IntakeError("one or more intake outputs already exist; use --overwrite")
    return destination_paths


def _read_source(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise IntakeError(f"planned source is missing: {path.name}")
    if not path.is_file():
        raise IntakeError(f"planned source is not a regular file: {path.name}")
    if path.suffix.lower() != ".png":
        raise IntakeError(f"planned source is not a PNG: {path.name}")

    try:
        with Image.open(path) as image:
            source_format = image.format
            source_dimensions = image.size
            source_mode = image.mode
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise IntakeError(f"planned source could not be decoded: {path.name}") from exc

    if source_format != "PNG":
        raise IntakeError(f"planned source did not decode as PNG: {path.name}")
    if source_dimensions != crop_v1.EXPECTED_SOURCE_SIZE:
        raise IntakeError(
            f"planned source has wrong dimensions: {path.name}; expected "
            f"{crop_v1.EXPECTED_SOURCE_WIDTH}x{crop_v1.EXPECTED_SOURCE_HEIGHT}"
        )
    if source_mode not in crop_v1.SUPPORTED_MODES:
        raise IntakeError(f"planned source has unsupported mode: {path.name}")

    return {
        "source_path": path,
        "source_sha256": _sha256(path),
        "source_dimensions": list(source_dimensions),
        "source_mode": source_mode,
    }


def _verify_reviewed_image(path: Path, expected_mode: str) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            image.load()
            reviewed_format = image.format
            reviewed_dimensions = image.size
            reviewed_mode = image.mode
            metadata_keys = set(image.info)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise IntakeError("a staged reviewed image could not be decoded") from exc

    if reviewed_format != "PNG":
        raise IntakeError("a staged reviewed image did not decode as PNG")
    if reviewed_dimensions != crop_v1.EXPECTED_OUTPUT_SIZE:
        raise IntakeError("a staged reviewed image has incorrect dimensions")
    if reviewed_mode != expected_mode:
        raise IntakeError("a staged reviewed image did not preserve source mode")
    if metadata_keys:
        raise IntakeError("a staged reviewed image contains unexpected PNG metadata")

    return {
        "reviewed_sha256": _sha256(path),
        "reviewed_dimensions": list(reviewed_dimensions),
        "reviewed_mode": reviewed_mode,
    }


def _manifest_row(item: IntakePlanItem, capture_session_id: str) -> dict[str, str]:
    return {
        "image_id": item.image_id,
        "relative_image_path": str(PurePosixPath("reviewed") / item.reviewed_name),
        "label": item.label,
        "capture_session_id": capture_session_id,
        "source_group_id": item.source_group_id,
        "inclusion_status": "needs_review",
        "exclusion_reason": "",
        "resolution": (
            f"{crop_v1.EXPECTED_OUTPUT_WIDTH}x{crop_v1.EXPECTED_OUTPUT_HEIGHT}"
        ),
        "aspect_ratio": REVIEWED_ASPECT_RATIO,
        "client_type": "runelite",
        "display_mode": "maximized_windowed",
        "ui_layout_or_scale": "resizable_classic_stretched_90pct",
        "crop_policy": crop_v1.CROP_POLICY,
        "camera_angle_bucket": item.camera_angle_bucket,
        "zoom_bucket": item.zoom_bucket,
        "location_or_activity_bucket": item.location_or_activity_bucket,
        "overlay_notes": "fixed_profile_v1",
        "redaction_method": "none",
        "redaction_checked": "false",
        "source_consent": "owner_captured",
        "split": "unassigned",
        "notes": "",
    }


def _write_manifest(
    path: Path,
    plan: Sequence[IntakePlanItem],
    capture_session_id: str,
) -> list[dict[str, str]]:
    rows = [_manifest_row(item, capture_session_id) for item in plan]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _write_audit(
    path: Path,
    plan: Sequence[IntakePlanItem],
    source_records: Sequence[dict[str, Any]],
    reviewed_records: Sequence[dict[str, Any]],
    capture_session_id: str,
    prior_state: PriorDatasetState,
) -> dict[str, Any]:
    images = []
    for index, (item, source, reviewed) in enumerate(
        zip(plan, source_records, reviewed_records, strict=True), start=1
    ):
        images.append(
            {
                "image_id": item.image_id,
                "label": item.label,
                "source_sha256": source["source_sha256"],
                "reviewed_sha256": reviewed["reviewed_sha256"],
                "source_dimensions": source["source_dimensions"],
                "reviewed_dimensions": reviewed["reviewed_dimensions"],
                "source_mode": source["source_mode"],
                "reviewed_mode": reviewed["reviewed_mode"],
                "exact_duplicate_status": "unique",
                "manifest_row_index": index,
            }
        )

    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "capture_session_id": capture_session_id,
        "crop_policy": crop_v1.CROP_POLICY,
        "crop_coordinates": list(crop_v1.CROP_BOX),
        "expected_source_dimensions": list(crop_v1.EXPECTED_SOURCE_SIZE),
        "expected_reviewed_dimensions": list(crop_v1.EXPECTED_OUTPUT_SIZE),
        "reviewed_aspect_ratio": REVIEWED_ASPECT_RATIO,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "images": images,
    }
    if capture_session_id != CAPTURE_SESSION_ID or prior_state.session_ids:
        audit["cross_session_summary"] = {
            "prior_session_ids_checked": sorted(prior_state.session_ids),
            "prior_accepted_examples_checked": len(prior_state.accepted_examples),
            "duplicate_image_ids_found": 0,
            "duplicate_reviewed_paths_found": 0,
            "duplicate_source_group_ids_found": 0,
            "exact_duplicate_source_hashes_found": 0,
            "exact_duplicate_reviewed_hashes_found": 0,
        }
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit


def _validate_staged_manifest(path: Path, expected_rows: Sequence[dict[str, str]]) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        rows = list(reader)
    if headers != MANIFEST_HEADERS:
        raise IntakeError("staged manifest header validation failed")
    if rows != list(expected_rows):
        raise IntakeError("staged manifest row validation failed")


def _validate_staged_audit(path: Path, expected: dict[str, Any]) -> None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntakeError("staged audit JSON validation failed") from exc
    if parsed != expected:
        raise IntakeError("staged audit JSON content validation failed")
    serialized = path.read_text(encoding="utf-8")
    if any(key in serialized for key in ("source_path", "raw_name", "absolute_path")):
        raise IntakeError("staged audit JSON contains a prohibited path field")


def _install_complete_set(
    staged_outputs: Sequence[tuple[Path, Path]],
    stage_root: Path,
    *,
    overwrite: bool,
) -> None:
    installed: list[Path] = []
    if not overwrite:
        try:
            for staged, destination in staged_outputs:
                os.link(staged, destination)
                installed.append(destination)
        except FileExistsError as exc:
            for destination in reversed(installed):
                destination.unlink(missing_ok=True)
            raise IntakeError("an intake output appeared during installation") from exc
        except OSError as exc:
            for destination in reversed(installed):
                destination.unlink(missing_ok=True)
            raise IntakeError("the complete intake output set could not be installed") from exc
        return

    backup_dir = stage_root / "backups"
    backup_dir.mkdir()
    backups: dict[Path, Path] = {}
    try:
        for index, (_, destination) in enumerate(staged_outputs):
            if destination.exists():
                backup = backup_dir / f"backup_{index:02d}"
                os.replace(destination, backup)
                backups[destination] = backup
        for staged, destination in staged_outputs:
            os.replace(staged, destination)
            installed.append(destination)
    except OSError as exc:
        for destination in reversed(installed):
            destination.unlink(missing_ok=True)
        restoration_failed = False
        for destination, backup in backups.items():
            if backup.exists():
                try:
                    os.replace(backup, destination)
                except OSError:
                    restoration_failed = True
        if restoration_failed:
            raise IntakeError("intake installation failed and rollback was incomplete") from exc
        raise IntakeError("intake installation failed; prior outputs were restored") from exc


def run_intake(
    source_dir: str | os.PathLike[str],
    reviewed_dir: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    audit_path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    session_id: str = CAPTURE_SESSION_ID,
    plan: Sequence[IntakePlanItem] | None = None,
    prior_manifest_paths: Sequence[str | os.PathLike[str]] = (),
    prior_audit_paths: Sequence[str | os.PathLike[str]] = (),
    prior_source_hashes: Sequence[str] = (),
    prior_reviewed_hashes: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and install one complete six-example private draft intake."""

    source_dir = Path(source_dir)
    reviewed_dir = Path(reviewed_dir)
    manifest_path = Path(manifest_path)
    audit_path = Path(audit_path)

    validate_session_plans()
    selected_plan = get_session_plan(session_id) if plan is None else tuple(plan)
    validate_intake_plan(selected_plan)
    prior_state = load_prior_dataset_state(
        manifest_paths=prior_manifest_paths,
        audit_paths=prior_audit_paths,
        current_session_id=session_id,
    )
    prior_state = _merge_prior_hashes(
        prior_state,
        source_hashes=prior_source_hashes,
        reviewed_hashes=prior_reviewed_hashes,
    )
    validate_cross_session_plan(selected_plan, prior_state)
    destination_paths = _validate_destination_layout(
        source_dir,
        reviewed_dir,
        manifest_path,
        audit_path,
        selected_plan,
        overwrite=overwrite,
    )

    source_paths = [source_dir / item.raw_name for item in selected_plan]
    if len({_resolved(path) for path in source_paths}) != len(source_paths):
        raise IntakeError("planned source paths must be unique")
    source_records = [_read_source(path) for path in source_paths]
    source_hashes = [record["source_sha256"] for record in source_records]
    if len(set(source_hashes)) != len(source_hashes):
        raise IntakeError("exact duplicate source images are not allowed")
    if set(source_hashes) & prior_state.source_hashes:
        raise IntakeError("an exact duplicate prior-session source is not allowed")

    reviewed_dir_created = False
    with tempfile.TemporaryDirectory(
        prefix=f".{session_id}_intake.", dir=reviewed_dir.parent
    ) as temp_dir:
        stage_root = Path(temp_dir)
        staged_reviewed_dir = stage_root / "reviewed"
        staged_reviewed_dir.mkdir()

        reviewed_records: list[dict[str, Any]] = []
        staged_images: list[Path] = []
        for item, source in zip(selected_plan, source_records, strict=True):
            staged_image = staged_reviewed_dir / item.reviewed_name
            try:
                crop_v1.crop_image(source["source_path"], staged_image)
            except crop_v1.CropError as exc:
                raise IntakeError(f"crop failed for {item.image_id}") from exc
            reviewed_records.append(
                _verify_reviewed_image(staged_image, source["source_mode"])
            )
            staged_images.append(staged_image)

        reviewed_hashes = [record["reviewed_sha256"] for record in reviewed_records]
        if len(set(reviewed_hashes)) != len(reviewed_hashes):
            raise IntakeError("exact duplicate reviewed images are not allowed")
        if set(reviewed_hashes) & prior_state.reviewed_hashes:
            raise IntakeError("an exact duplicate prior-session reviewed image is not allowed")

        staged_manifest = stage_root / "manifest.csv"
        staged_audit = stage_root / "audit.json"
        manifest_rows = _write_manifest(staged_manifest, selected_plan, session_id)
        audit = _write_audit(
            staged_audit,
            selected_plan,
            source_records,
            reviewed_records,
            session_id,
            prior_state,
        )
        _validate_staged_manifest(staged_manifest, manifest_rows)
        _validate_staged_audit(staged_audit, audit)

        if not reviewed_dir.exists():
            reviewed_dir.mkdir()
            reviewed_dir_created = True

        staged_outputs = [
            *(
                zip(
                    staged_images,
                    destination_paths[: len(selected_plan)],
                    strict=True,
                )
            ),
            (staged_manifest, manifest_path),
            (staged_audit, audit_path),
        ]
        try:
            _install_complete_set(
                staged_outputs,
                stage_root,
                overwrite=overwrite,
            )
        except IntakeError:
            if reviewed_dir_created:
                try:
                    reviewed_dir.rmdir()
                except OSError:
                    pass
            raise

    return {
        "capture_session_id": session_id,
        "crop_policy": crop_v1.CROP_POLICY,
        "reviewed_count": len(selected_plan),
        "reviewed_dimensions": list(crop_v1.EXPECTED_OUTPUT_SIZE),
        "reviewed_aspect_ratio": REVIEWED_ASPECT_RATIO,
        "inclusion_status": "needs_review",
        "redaction_checked": False,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one complete approved private CS 4710 draft intake."
    )
    parser.add_argument(
        "--session",
        default=CAPTURE_SESSION_ID,
        help="explicit approved capture session (default: session_001)",
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--reviewed-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument(
        "--manifests-dir",
        type=Path,
        help="directory for safely derived <session>_draft.csv and <session>_audit.json",
    )
    parser.add_argument(
        "--prior-manifest",
        type=Path,
        action="append",
        default=[],
        help="explicit prior accepted/draft manifest to check; may be repeated",
    )
    parser.add_argument(
        "--prior-audit-json",
        type=Path,
        action="append",
        default=[],
        help="explicit prior audit JSON to check for exact hashes; may be repeated",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="transactionally replace the complete expected output set",
    )
    return parser.parse_args(argv)


def _selected_artifact_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    manifest_name, audit_name = session_artifact_names(args.session)
    explicit = args.manifest is not None or args.audit_json is not None
    if explicit:
        if args.manifest is None or args.audit_json is None:
            raise IntakeError("--manifest and --audit-json must be supplied together")
        if args.manifests_dir is not None:
            raise IntakeError(
                "--manifests-dir cannot be combined with explicit artifact paths"
            )
        if args.manifest.name != manifest_name or args.audit_json.name != audit_name:
            raise IntakeError("session artifact filenames do not match --session")
        return args.manifest, args.audit_json

    manifests_dir = (
        args.manifests_dir
        if args.manifests_dir is not None
        else args.reviewed_dir.parent / "manifests"
    )
    return manifests_dir / manifest_name, manifests_dir / audit_name


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest_path, audit_path = _selected_artifact_paths(args)
        summary = run_intake(
            args.source_dir,
            args.reviewed_dir,
            manifest_path,
            audit_path,
            overwrite=args.overwrite,
            session_id=args.session,
            prior_manifest_paths=args.prior_manifest,
            prior_audit_paths=args.prior_audit_json,
        )
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
