"""Validate and create a private balanced-archive CS 4710 draft intake."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

import crop_v1
from build_accepted_manifest_v1 import sha256_file
from intake_session_v1 import (
    APPROVED_LABELS,
    MANIFEST_HEADERS,
    REVIEWED_ASPECT_RATIO,
)


AUDIT_SCHEMA_VERSION = "balanced_archive_draft_intake_v1"
SELECTION_MANIFEST_HEADERS = (
    "filename",
    "label",
    "source_session",
    "selection_reason",
)
LABEL_ORDER = (
    "normal_gameplay",
    "bank_open",
    "dialogue_or_choice_open",
    "shop_open",
    "grand_exchange_open",
    "unknown_other",
)
EXPECTED_PNG_COUNT = 60
EXPECTED_PER_LABEL = 10
EXPECTED_SOURCE_MODE = "RGB"
ID_ASSIGNMENT_RULE = (
    "Assign consecutive image IDs in verified selection_manifest.csv row order."
)
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
SESSION_PATTERN = re.compile(r"^session_[0-9]{3}$")


class BalancedArchiveIntakeError(RuntimeError):
    """Raised when a balanced archive cannot be safely admitted as a draft."""


def _is_existing(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _pixel_sha256(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(image.tobytes())
    return digest.hexdigest()


def _validate_archive_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or "\\" in name or "\x00" in name:
        raise BalancedArchiveIntakeError("archive contains an unsafe member path")
    posix_path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
    ):
        raise BalancedArchiveIntakeError(
            "archive contains an absolute or drive-qualified member path"
        )
    if any(part in {"", ".", ".."} for part in name.split("/")):
        raise BalancedArchiveIntakeError("archive contains path traversal")
    if info.is_dir():
        raise BalancedArchiveIntakeError("archive must not contain directory entries")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
        raise BalancedArchiveIntakeError("archive contains a symbolic-link member")
    if info.flag_bits & 0x1:
        raise BalancedArchiveIntakeError("archive contains an encrypted member")

    for part in posix_path.parts:
        lowered = part.casefold()
        if (
            part.startswith(".")
            or part.startswith("~")
            or part.endswith("~")
            or lowered == "__macosx"
            or lowered in {"thumbs.db", "desktop.ini"}
            or lowered.endswith((".tmp", ".temp", ".swp", ".swo"))
        ):
            raise BalancedArchiveIntakeError(
                "archive contains a hidden or temporary member"
            )
    if posix_path.suffix not in {".png", ".csv"}:
        raise BalancedArchiveIntakeError(
            "archive contains an unexpected file extension"
        )
    return posix_path


def _inspect_archive_layout(
    archive: zipfile.ZipFile,
    session_id: str,
) -> tuple[
    str,
    dict[str, zipfile.ZipInfo],
    zipfile.ZipInfo,
    zipfile.ZipInfo,
]:
    infos = archive.infolist()
    if len(infos) != EXPECTED_PNG_COUNT + 2:
        raise BalancedArchiveIntakeError("archive must contain exactly 62 entries")

    by_path: dict[str, zipfile.ZipInfo] = {}
    canonical_paths: set[str] = set()
    parsed_paths: list[PurePosixPath] = []
    for info in infos:
        parsed = _validate_archive_member(info)
        if info.filename in by_path or parsed.as_posix().casefold() in canonical_paths:
            raise BalancedArchiveIntakeError("archive contains a duplicate member path")
        by_path[info.filename] = info
        canonical_paths.add(parsed.as_posix().casefold())
        parsed_paths.append(parsed)

    expected_prefix = f"{session_id}_selected_60"
    if any(
        len(path.parts) != 2 or path.parts[0] != expected_prefix
        for path in parsed_paths
    ):
        raise BalancedArchiveIntakeError(
            "archive has an unexpected top-level layout"
        )

    selection_name = f"{expected_prefix}/selection_manifest.csv"
    excluded_name = (
        f"{expected_prefix}/excluded_from_balanced_selection.csv"
    )
    try:
        selection_info = by_path[selection_name]
        excluded_info = by_path[excluded_name]
    except KeyError as exc:
        raise BalancedArchiveIntakeError(
            "archive is missing a required provenance CSV"
        ) from exc

    png_infos = {
        path: info for path, info in by_path.items() if path.endswith(".png")
    }
    csv_paths = {path for path in by_path if path.endswith(".csv")}
    if len(png_infos) != EXPECTED_PNG_COUNT:
        raise BalancedArchiveIntakeError("archive must contain exactly 60 PNG files")
    if csv_paths != {selection_name, excluded_name}:
        raise BalancedArchiveIntakeError(
            "archive contains unexpected or missing CSV files"
        )
    return expected_prefix, png_infos, selection_info, excluded_info


def _read_selection_manifest(
    contents: bytes,
    *,
    session_id: str,
    png_infos: dict[str, zipfile.ZipInfo],
    prefix: str,
) -> list[dict[str, str]]:
    try:
        text = contents.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = tuple(reader.fieldnames or ())
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise BalancedArchiveIntakeError(
            "selection manifest could not be decoded"
        ) from exc
    if headers != SELECTION_MANIFEST_HEADERS:
        raise BalancedArchiveIntakeError(
            "selection manifest has incorrect columns"
        )
    if len(rows) != EXPECTED_PNG_COUNT:
        raise BalancedArchiveIntakeError(
            "selection manifest must contain exactly 60 rows"
        )
    if any(
        None in row or any(value is None for value in row.values())
        for row in rows
    ):
        raise BalancedArchiveIntakeError(
            "selection manifest contains incomplete or extra columns"
        )

    filenames: list[str] = []
    counts: Counter[str] = Counter()
    for row in rows:
        filename = row["filename"]
        if (
            not filename
            or PurePosixPath(filename).name != filename
            or PureWindowsPath(filename).name != filename
            or not filename.endswith(".png")
        ):
            raise BalancedArchiveIntakeError(
                "selection manifest contains an unsafe PNG filename"
            )
        if row["label"] not in APPROVED_LABELS:
            raise BalancedArchiveIntakeError(
                "selection manifest contains an unsupported label"
            )
        if row["source_session"] != session_id:
            raise BalancedArchiveIntakeError(
                "selection manifest contains the wrong source_session"
            )
        filenames.append(filename)
        counts[row["label"]] += 1

    if len(filenames) != len(set(filenames)):
        raise BalancedArchiveIntakeError(
            "selection manifest contains a duplicate filename"
        )
    if counts != Counter({label: EXPECTED_PER_LABEL for label in LABEL_ORDER}):
        raise BalancedArchiveIntakeError(
            "selection manifest must contain exactly 10 rows per label"
        )

    manifested_paths = {f"{prefix}/{filename}" for filename in filenames}
    archive_png_paths = set(png_infos)
    missing = manifested_paths - archive_png_paths
    extra = archive_png_paths - manifested_paths
    if missing:
        raise BalancedArchiveIntakeError(
            "selection manifest references a missing PNG"
        )
    if extra:
        raise BalancedArchiveIntakeError(
            "archive contains an extra or unmanifested PNG"
        )
    return rows


def _validate_excluded_csv(contents: bytes) -> None:
    try:
        rows = list(csv.reader(io.StringIO(contents.decode("utf-8-sig"), newline="")))
    except (UnicodeError, csv.Error) as exc:
        raise BalancedArchiveIntakeError(
            "excluded-selection CSV could not be decoded"
        ) from exc
    if not rows:
        raise BalancedArchiveIntakeError("excluded-selection CSV must not be empty")


def _output_layout(
    data_root: Path,
    session_id: str,
    first_image_index: int,
    rows: Sequence[dict[str, str]],
) -> dict[str, Any]:
    if not data_root.is_dir():
        raise BalancedArchiveIntakeError("data root must be an existing directory")
    reports_dir = data_root.parent / "reports"
    required_parents = (
        data_root / "raw",
        data_root / "reviewed",
        data_root / "manifests",
        reports_dir,
    )
    if any(not path.is_dir() for path in required_parents):
        raise BalancedArchiveIntakeError(
            "raw, reviewed, manifests, and reports directories must already exist"
        )

    image_ids = [
        f"img_{index:04d}"
        for index in range(first_image_index, first_image_index + len(rows))
    ]
    reviewed_paths = [
        data_root / "reviewed" / f"{image_id}.png" for image_id in image_ids
    ]
    raw_dir = data_root / "raw" / session_id
    selection_path = (
        data_root / "manifests" / f"{session_id}_selection_manifest.csv"
    )
    excluded_path = (
        data_root
        / "manifests"
        / f"{session_id}_excluded_from_balanced_selection.csv"
    )
    draft_path = data_root / "manifests" / f"{session_id}_draft.csv"
    audit_path = data_root / "manifests" / f"{session_id}_audit.json"
    contact_sheet_path = (
        reports_dir / f"{session_id}_review_contact_sheet.png"
    )
    accepted_path = data_root / "manifests" / f"{session_id}.csv"
    forbidden_feasibility_path = (
        data_root / "manifests" / "chat_open_feasibility_60.csv"
    )

    paths_to_protect = [
        raw_dir,
        *reviewed_paths,
        selection_path,
        excluded_path,
        draft_path,
        audit_path,
        contact_sheet_path,
        accepted_path,
        forbidden_feasibility_path,
    ]
    if any(_is_existing(path) for path in paths_to_protect):
        raise BalancedArchiveIntakeError(
            "one or more destination or forbidden outputs already exist"
        )
    resolved = [path.resolve(strict=False) for path in paths_to_protect]
    if len(resolved) != len(set(resolved)):
        raise BalancedArchiveIntakeError("planned output paths must be unique")

    return {
        "image_ids": image_ids,
        "reviewed_paths": reviewed_paths,
        "raw_dir": raw_dir,
        "selection_manifest": selection_path,
        "excluded_manifest": excluded_path,
        "draft_manifest": draft_path,
        "audit": audit_path,
        "contact_sheet": contact_sheet_path,
    }


def _extract_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> str:
    digest = hashlib.sha256()
    try:
        with archive.open(info, "r") as source, destination.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                output.write(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BalancedArchiveIntakeError(
            f"archive member could not be extracted: {PurePosixPath(info.filename).name}"
        ) from exc
    return digest.hexdigest()


def _validate_source_png(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            source_format = image.format
            source_dimensions = image.size
            source_mode = image.mode
            metadata_keys = sorted(image.info)
            image.verify()
        with Image.open(path) as image:
            image.load()
            decoded_pixel_sha256 = _pixel_sha256(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BalancedArchiveIntakeError(
            f"source image could not be decoded: {path.name}"
        ) from exc
    if source_format != "PNG":
        raise BalancedArchiveIntakeError(
            f"source image did not decode as PNG: {path.name}"
        )
    if source_mode != EXPECTED_SOURCE_MODE:
        raise BalancedArchiveIntakeError(
            f"source image must use RGB mode: {path.name}"
        )
    if source_dimensions != crop_v1.EXPECTED_SOURCE_SIZE:
        raise BalancedArchiveIntakeError(
            f"source image has wrong dimensions: {path.name}"
        )
    if metadata_keys:
        raise BalancedArchiveIntakeError(
            f"source image contains unexpected PNG metadata: {path.name}"
        )
    return {
        "source_dimensions": list(source_dimensions),
        "source_mode": source_mode,
        "source_metadata_keys": metadata_keys,
        "decoded_source_pixel_sha256": decoded_pixel_sha256,
    }


def _validate_reviewed_png(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            image.load()
            reviewed_format = image.format
            reviewed_dimensions = image.size
            reviewed_mode = image.mode
            metadata_keys = sorted(image.info)
            reviewed_pixel_sha256 = _pixel_sha256(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BalancedArchiveIntakeError(
            f"reviewed image could not be decoded: {path.name}"
        ) from exc
    if reviewed_format != "PNG":
        raise BalancedArchiveIntakeError("reviewed image did not decode as PNG")
    if reviewed_dimensions != crop_v1.EXPECTED_OUTPUT_SIZE:
        raise BalancedArchiveIntakeError("reviewed image has wrong dimensions")
    if reviewed_mode != EXPECTED_SOURCE_MODE:
        raise BalancedArchiveIntakeError("reviewed image must use RGB mode")
    if metadata_keys:
        raise BalancedArchiveIntakeError(
            "reviewed image contains unexpected PNG metadata"
        )
    return {
        "reviewed_dimensions": list(reviewed_dimensions),
        "reviewed_mode": reviewed_mode,
        "reviewed_metadata_keys": metadata_keys,
        "reviewed_sha256": sha256_file(path),
        "reviewed_pixel_sha256": reviewed_pixel_sha256,
    }


def _require_unique(values: Sequence[str], description: str) -> None:
    if len(values) != len(set(values)):
        raise BalancedArchiveIntakeError(f"duplicate {description} detected")


def _draft_row(
    image_id: str,
    label: str,
    session_id: str,
) -> dict[str, str]:
    return {
        "image_id": image_id,
        "relative_image_path": f"reviewed/{image_id}.png",
        "label": label,
        "capture_session_id": session_id,
        # Grouping evidence has not been reviewed. Blank is the canonical
        # draft representation; acceptance validation happens later.
        "source_group_id": "",
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
        "camera_angle_bucket": "",
        "zoom_bucket": "",
        "location_or_activity_bucket": "",
        "overlay_notes": "fixed_profile_v1",
        "redaction_method": "none",
        "redaction_checked": "false",
        "source_consent": "owner_captured",
        "split": "unassigned",
        "notes": "standardized_chat_open_feasibility_set",
    }


def _write_draft_manifest(
    path: Path,
    rows: Sequence[dict[str, str]],
) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=MANIFEST_HEADERS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != MANIFEST_HEADERS:
            raise BalancedArchiveIntakeError("staged draft schema validation failed")
        if list(reader) != list(rows):
            raise BalancedArchiveIntakeError("staged draft row validation failed")


def _write_contact_sheet(
    path: Path,
    records: Sequence[dict[str, Any]],
) -> None:
    columns = 5
    thumbnail_size = (340, 204)
    horizontal_gap = 16
    vertical_gap = 12
    margin = 20
    section_header_height = 30
    caption_height = 20
    rows_per_label = 2
    section_height = (
        section_header_height
        + rows_per_label
        * (thumbnail_size[1] + caption_height + vertical_gap)
        + vertical_gap
    )
    sheet_width = (
        margin * 2
        + columns * thumbnail_size[0]
        + (columns - 1) * horizontal_gap
    )
    sheet_height = margin * 2 + len(LABEL_ORDER) * section_height
    sheet = Image.new("RGB", (sheet_width, sheet_height), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    try:
        for label_index, label in enumerate(LABEL_ORDER):
            label_records = [record for record in records if record["label"] == label]
            if len(label_records) != EXPECTED_PER_LABEL:
                raise BalancedArchiveIntakeError(
                    "contact-sheet label grouping is incomplete"
                )
            section_top = margin + label_index * section_height
            draw.text(
                (margin, section_top),
                label,
                fill=(255, 255, 255),
                font=font,
            )
            image_top = section_top + section_header_height
            for index, record in enumerate(label_records):
                row_index, column_index = divmod(index, columns)
                left = margin + column_index * (
                    thumbnail_size[0] + horizontal_gap
                )
                top = image_top + row_index * (
                    thumbnail_size[1] + caption_height + vertical_gap
                )
                with Image.open(record["staged_reviewed_path"]) as reviewed:
                    reviewed.load()
                    thumbnail = reviewed.copy()
                try:
                    thumbnail.thumbnail(
                        thumbnail_size,
                        Image.Resampling.LANCZOS,
                    )
                    tile_left = left + (thumbnail_size[0] - thumbnail.width) // 2
                    tile_top = top + (thumbnail_size[1] - thumbnail.height) // 2
                    sheet.paste(thumbnail, (tile_left, tile_top))
                finally:
                    thumbnail.close()
                draw.text(
                    (left, top + thumbnail_size[1] + 2),
                    f"{record['image_id']} | {record['label']}",
                    fill=(255, 255, 255),
                    font=font,
                )
        sheet.save(path, format="PNG")
    except (OSError, ValueError) as exc:
        raise BalancedArchiveIntakeError(
            "review contact sheet could not be created"
        ) from exc
    finally:
        sheet.close()

    try:
        with Image.open(path) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.size != (sheet_width, sheet_height)
                or image.info
            ):
                raise BalancedArchiveIntakeError(
                    "staged review contact sheet validation failed"
                )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BalancedArchiveIntakeError(
            "staged review contact sheet could not be decoded"
        ) from exc


def _audit_document(
    *,
    archive_path: Path,
    archive_sha256: str,
    archive_prefix: str,
    session_id: str,
    class_counts: Counter[str],
    records: Sequence[dict[str, Any]],
    output_layout: dict[str, Any],
) -> dict[str, Any]:
    public_records = []
    for index, record in enumerate(records, start=1):
        public_records.append(
            {
                "source_filename": record["source_filename"],
                "image_id": record["image_id"],
                "candidate_label": record["label"],
                "manifest_row_index": index,
                "source_file_sha256": record["source_file_sha256"],
                "decoded_source_pixel_sha256": record[
                    "decoded_source_pixel_sha256"
                ],
                "reviewed_file_sha256": record["reviewed_sha256"],
                "reviewed_pixel_sha256": record["reviewed_pixel_sha256"],
                "source_dimensions": record["source_dimensions"],
                "reviewed_dimensions": record["reviewed_dimensions"],
                "source_mode": record["source_mode"],
                "reviewed_mode": record["reviewed_mode"],
            }
        )

    outputs = {
        "raw_session_directory": str(output_layout["raw_dir"]),
        "reviewed_image_range": (
            f"{output_layout['image_ids'][0]}.png through "
            f"{output_layout['image_ids'][-1]}.png"
        ),
        "selection_manifest": str(output_layout["selection_manifest"]),
        "excluded_selection_manifest": str(output_layout["excluded_manifest"]),
        "draft_manifest": str(output_layout["draft_manifest"]),
        "audit_json": str(output_layout["audit"]),
        "review_contact_sheet": str(output_layout["contact_sheet"]),
    }
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "archive": {
            "path": str(archive_path),
            "sha256": archive_sha256,
            "entry_count": EXPECTED_PNG_COUNT + 2,
            "png_count": EXPECTED_PNG_COUNT,
            "csv_count": 2,
            "top_level_prefix": archive_prefix,
        },
        "capture_session_id": session_id,
        "crop_policy": crop_v1.CROP_POLICY,
        "crop_coordinates": list(crop_v1.CROP_BOX),
        "expected_source_dimensions": list(crop_v1.EXPECTED_SOURCE_SIZE),
        "expected_reviewed_dimensions": list(crop_v1.EXPECTED_OUTPUT_SIZE),
        "reviewed_aspect_ratio": REVIEWED_ASPECT_RATIO,
        "id_assignment": {
            "rule": ID_ASSIGNMENT_RULE,
            "first_image_id": output_layout["image_ids"][0],
            "last_image_id": output_layout["image_ids"][-1],
        },
        "class_counts": {
            label: class_counts[label] for label in LABEL_ORDER
        },
        "technical_validation": {
            "archive_sha256_verified": True,
            "safe_member_paths_verified": True,
            "archive_layout_verified": True,
            "selection_manifest_verified": True,
            "all_source_files_decode_as_metadata_free_rgb_png": True,
            "all_source_dimensions_verified": True,
            "fixed_crop_api_reused": True,
            "all_reviewed_files_decode_as_metadata_free_rgb_png": True,
            "all_reviewed_dimensions_verified": True,
            "destination_overwrite_checks_passed": True,
        },
        "duplicate_checks": {
            "duplicate_source_file_sha256_count": 0,
            "duplicate_decoded_source_pixel_hash_count": 0,
            "duplicate_reviewed_file_sha256_count": 0,
            "duplicate_reviewed_pixel_hash_count": 0,
        },
        "outputs": outputs,
        "pending_review_state": {
            "inclusion_status": "needs_review",
            "redaction_checked": False,
            "split": "unassigned",
            "candidate_labels_finally_reviewed": False,
            "privacy_review_complete": False,
            "source_group_id": "",
            "source_group_status": "pending_grouping_and_leakage_review",
            "accepted_manifest_created": False,
        },
        "images": public_records,
    }


def _write_audit(path: Path, audit: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BalancedArchiveIntakeError(
            "staged audit JSON validation failed"
        ) from exc
    if parsed != audit:
        raise BalancedArchiveIntakeError("staged audit JSON content changed")


def _install_outputs(
    *,
    staged_raw_dir: Path,
    staged_reviewed_paths: Sequence[Path],
    staged_selection_manifest: Path,
    staged_excluded_manifest: Path,
    staged_draft_manifest: Path,
    staged_audit: Path,
    staged_contact_sheet: Path,
    output_layout: dict[str, Any],
) -> None:
    installed_files: list[Path] = []
    created_directories: list[Path] = []
    file_pairs = [
        *zip(
            staged_reviewed_paths,
            output_layout["reviewed_paths"],
            strict=True,
        ),
        (
            staged_selection_manifest,
            output_layout["selection_manifest"],
        ),
        (
            staged_excluded_manifest,
            output_layout["excluded_manifest"],
        ),
        (staged_draft_manifest, output_layout["draft_manifest"]),
        (staged_audit, output_layout["audit"]),
        (staged_contact_sheet, output_layout["contact_sheet"]),
    ]
    try:
        output_layout["raw_dir"].mkdir()
        created_directories.append(output_layout["raw_dir"])
        for staged in sorted(staged_raw_dir.iterdir(), key=lambda path: path.name):
            destination = output_layout["raw_dir"] / staged.name
            os.link(staged, destination)
            installed_files.append(destination)
        for staged, destination in file_pairs:
            os.link(staged, destination)
            installed_files.append(destination)
    except (FileExistsError, OSError) as exc:
        rollback_errors: list[OSError] = []
        for path in reversed(installed_files):
            try:
                path.unlink()
            except OSError as rollback_exc:
                rollback_errors.append(rollback_exc)
        for path in reversed(created_directories):
            try:
                path.rmdir()
            except OSError as rollback_exc:
                rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise BalancedArchiveIntakeError(
                "intake installation failed and transactional rollback was incomplete"
            ) from exc
        raise BalancedArchiveIntakeError(
            "intake installation failed; newly created outputs were rolled back"
        ) from exc


def run_balanced_archive_intake(
    archive_path: str | os.PathLike[str],
    expected_sha256: str,
    data_root: str | os.PathLike[str],
    *,
    session_id: str,
    first_image_index: int,
    validate_only: bool = False,
) -> dict[str, Any]:
    """Validate and optionally install one complete balanced draft intake."""

    archive_path = Path(archive_path)
    data_root = Path(data_root)
    if not SESSION_PATTERN.fullmatch(session_id):
        raise BalancedArchiveIntakeError("session must use the session_NNN form")
    if first_image_index < 1:
        raise BalancedArchiveIntakeError("first image index must be positive")
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise BalancedArchiveIntakeError(
            "expected SHA-256 must contain exactly 64 hexadecimal characters"
        )
    if not archive_path.is_file():
        raise BalancedArchiveIntakeError("archive does not exist")

    actual_archive_sha256 = sha256_file(archive_path)
    if actual_archive_sha256 != expected_sha256.lower():
        raise BalancedArchiveIntakeError("archive SHA-256 does not match")

    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BalancedArchiveIntakeError("archive is not a readable ZIP file") from exc

    with archive:
        prefix, png_infos, selection_info, excluded_info = _inspect_archive_layout(
            archive,
            session_id,
        )
        try:
            selection_bytes = archive.read(selection_info)
            excluded_bytes = archive.read(excluded_info)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise BalancedArchiveIntakeError(
                "archive provenance CSV could not be read"
            ) from exc
        selection_rows = _read_selection_manifest(
            selection_bytes,
            session_id=session_id,
            png_infos=png_infos,
            prefix=prefix,
        )
        _validate_excluded_csv(excluded_bytes)
        output_layout = _output_layout(
            data_root,
            session_id,
            first_image_index,
            selection_rows,
        )
        if archive_path.resolve(strict=False) in {
            path.resolve(strict=False)
            for path in (
                output_layout["selection_manifest"],
                output_layout["excluded_manifest"],
                output_layout["draft_manifest"],
                output_layout["audit"],
                output_layout["contact_sheet"],
                *output_layout["reviewed_paths"],
            )
        }:
            raise BalancedArchiveIntakeError(
                "archive path must differ from every output path"
            )

        with tempfile.TemporaryDirectory(
            prefix=f".{session_id}_balanced_intake.",
            dir=data_root.parent,
        ) as temp_dir:
            stage_root = Path(temp_dir)
            staged_raw_dir = stage_root / "raw"
            staged_reviewed_dir = stage_root / "reviewed"
            staged_raw_dir.mkdir()
            staged_reviewed_dir.mkdir()

            records: list[dict[str, Any]] = []
            staged_reviewed_paths: list[Path] = []
            seen_source_files: set[str] = set()
            seen_source_pixels: set[str] = set()
            seen_reviewed_files: set[str] = set()
            seen_reviewed_pixels: set[str] = set()
            for image_id, selection_row in zip(
                output_layout["image_ids"],
                selection_rows,
                strict=True,
            ):
                source_filename = selection_row["filename"]
                source_info = png_infos[f"{prefix}/{source_filename}"]
                staged_source = staged_raw_dir / source_filename
                source_file_sha256 = _extract_member(
                    archive,
                    source_info,
                    staged_source,
                )
                if source_file_sha256 in seen_source_files:
                    raise BalancedArchiveIntakeError(
                        "duplicate compressed source bytes detected"
                    )
                seen_source_files.add(source_file_sha256)
                source_record = _validate_source_png(staged_source)
                source_pixel_hash = source_record["decoded_source_pixel_sha256"]
                if source_pixel_hash in seen_source_pixels:
                    raise BalancedArchiveIntakeError(
                        "duplicate decoded source pixels detected"
                    )
                seen_source_pixels.add(source_pixel_hash)
                staged_reviewed = staged_reviewed_dir / f"{image_id}.png"
                try:
                    crop_v1.crop_image(staged_source, staged_reviewed)
                except crop_v1.CropError as exc:
                    raise BalancedArchiveIntakeError(
                        f"fixed crop failed for {image_id}"
                    ) from exc
                reviewed_record = _validate_reviewed_png(staged_reviewed)
                if reviewed_record["reviewed_sha256"] in seen_reviewed_files:
                    raise BalancedArchiveIntakeError(
                        "duplicate reviewed file bytes detected"
                    )
                seen_reviewed_files.add(reviewed_record["reviewed_sha256"])
                if (
                    reviewed_record["reviewed_pixel_sha256"]
                    in seen_reviewed_pixels
                ):
                    raise BalancedArchiveIntakeError(
                        "duplicate reviewed pixels detected"
                    )
                seen_reviewed_pixels.add(
                    reviewed_record["reviewed_pixel_sha256"]
                )
                records.append(
                    {
                        "source_filename": source_filename,
                        "image_id": image_id,
                        "label": selection_row["label"],
                        "source_file_sha256": source_file_sha256,
                        **source_record,
                        **reviewed_record,
                        "staged_reviewed_path": staged_reviewed,
                    }
                )
                staged_reviewed_paths.append(staged_reviewed)

            _require_unique(
                [record["source_file_sha256"] for record in records],
                "compressed source bytes",
            )
            _require_unique(
                [
                    record["decoded_source_pixel_sha256"]
                    for record in records
                ],
                "decoded source pixels",
            )
            _require_unique(
                [record["reviewed_sha256"] for record in records],
                "reviewed file bytes",
            )
            _require_unique(
                [record["reviewed_pixel_sha256"] for record in records],
                "reviewed pixels",
            )

            draft_rows = [
                _draft_row(record["image_id"], record["label"], session_id)
                for record in records
            ]
            staged_selection_manifest = stage_root / "selection_manifest.csv"
            staged_excluded_manifest = stage_root / "excluded_selection.csv"
            staged_draft_manifest = stage_root / "draft.csv"
            staged_audit = stage_root / "audit.json"
            staged_contact_sheet = stage_root / "contact_sheet.png"
            staged_selection_manifest.write_bytes(selection_bytes)
            staged_excluded_manifest.write_bytes(excluded_bytes)
            if staged_selection_manifest.read_bytes() != selection_bytes:
                raise BalancedArchiveIntakeError(
                    "selection-manifest provenance bytes changed during staging"
                )
            if staged_excluded_manifest.read_bytes() != excluded_bytes:
                raise BalancedArchiveIntakeError(
                    "excluded-manifest provenance bytes changed during staging"
                )
            _write_draft_manifest(staged_draft_manifest, draft_rows)
            _write_contact_sheet(staged_contact_sheet, records)
            class_counts = Counter(record["label"] for record in records)
            audit = _audit_document(
                archive_path=archive_path,
                archive_sha256=actual_archive_sha256,
                archive_prefix=prefix,
                session_id=session_id,
                class_counts=class_counts,
                records=records,
                output_layout=output_layout,
            )
            _write_audit(staged_audit, audit)

            if not validate_only:
                _install_outputs(
                    staged_raw_dir=staged_raw_dir,
                    staged_reviewed_paths=staged_reviewed_paths,
                    staged_selection_manifest=staged_selection_manifest,
                    staged_excluded_manifest=staged_excluded_manifest,
                    staged_draft_manifest=staged_draft_manifest,
                    staged_audit=staged_audit,
                    staged_contact_sheet=staged_contact_sheet,
                    output_layout=output_layout,
                )

    return {
        "archive_sha256": actual_archive_sha256,
        "capture_session_id": session_id,
        "archive_entry_count": EXPECTED_PNG_COUNT + 2,
        "raw_count": EXPECTED_PNG_COUNT,
        "reviewed_count": EXPECTED_PNG_COUNT,
        "first_image_id": output_layout["image_ids"][0],
        "last_image_id": output_layout["image_ids"][-1],
        "class_counts": {label: class_counts[label] for label in LABEL_ORDER},
        "inclusion_status": "needs_review",
        "redaction_checked": False,
        "split": "unassigned",
        "validate_only": validate_only,
        "outputs_installed": not validate_only,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and optionally create a private balanced CS 4710 "
            "draft intake."
        )
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument("--first-image-index", required=True, type=int)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = run_balanced_archive_intake(
            args.archive,
            args.expected_sha256,
            args.data_root,
            session_id=args.session,
            first_image_index=args.first_image_index,
            validate_only=args.validate_only,
        )
    except BalancedArchiveIntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
