"""Validate and merge accepted private CS 4710 OSRS session manifests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence

from PIL import Image, UnidentifiedImageError


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
EXPECTED_REVIEWED_SIZE = (2287, 1369)
EXPECTED_REVIEWED_MODES = frozenset({"RGB", "RGBA"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AcceptedManifestError(RuntimeError):
    """Raised when accepted manifests cannot be combined safely."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise AcceptedManifestError(f"manifest does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise AcceptedManifestError(f"manifest could not be read: {path}") from exc
    if headers != MANIFEST_HEADERS:
        raise AcceptedManifestError("manifest must use the exact 22-column schema")
    if not rows:
        raise AcceptedManifestError("accepted session manifests must not be empty")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise AcceptedManifestError("manifest contains an incomplete or extra column")
    return rows


def _validate_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise AcceptedManifestError("reviewed paths must use nonempty POSIX-relative paths")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise AcceptedManifestError("absolute reviewed paths are not allowed")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise AcceptedManifestError("reviewed path traversal is not allowed")
    relative = PurePosixPath(value)
    if relative.suffix.lower() != ".png":
        raise AcceptedManifestError("reviewed image paths must end in .png")
    return relative


def _validate_rows(
    rows_by_manifest: Sequence[tuple[Path, list[dict[str, str]]]],
) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    manifest_sessions: set[str] = set()
    for path, rows in rows_by_manifest:
        sessions = {row["capture_session_id"] for row in rows}
        if len(sessions) != 1 or "" in sessions:
            raise AcceptedManifestError(
                f"each input must contain exactly one capture session: {path}"
            )
        session_id = next(iter(sessions))
        if session_id in manifest_sessions:
            raise AcceptedManifestError("capture sessions must not repeat across manifests")
        manifest_sessions.add(session_id)
        for row in rows:
            if row["label"] not in APPROVED_LABELS:
                raise AcceptedManifestError("manifest contains an unsupported label")
            if row["inclusion_status"] != "included":
                raise AcceptedManifestError("every row must be included")
            if row["exclusion_reason"]:
                raise AcceptedManifestError("included rows must have no exclusion reason")
            if row["redaction_checked"] != "true":
                raise AcceptedManifestError("every row must be redaction checked")
            if row["source_consent"] != "owner_captured":
                raise AcceptedManifestError("every row must be owner captured")
            if row["split"] != "unassigned":
                if not row["source_group_id"]:
                    raise AcceptedManifestError(
                        "assigned splits require a resolved source-group ID"
                    )
                raise AcceptedManifestError("every row must have an unassigned split")
            if row["resolution"] != "2287x1369":
                raise AcceptedManifestError("every row must use the reviewed v1 resolution")
            if row["aspect_ratio"] != "2287:1369":
                raise AcceptedManifestError("every row must use the reviewed v1 aspect ratio")
            if row["crop_policy"] != "fixed_game_canvas_v1":
                raise AcceptedManifestError("every row must use fixed_game_canvas_v1")
            _validate_relative_path(row["relative_image_path"])
            combined.append(dict(row))

    for description, key in (
        ("image IDs", "image_id"),
        ("reviewed paths", "relative_image_path"),
    ):
        values = [row[key] for row in combined]
        if len(values) != len(set(values)):
            raise AcceptedManifestError(f"duplicate {description} are not allowed")
    return combined


def _resolve_reviewed_files(
    rows: Sequence[dict[str, str]], data_root: Path
) -> dict[str, Path]:
    if not data_root.is_dir():
        raise AcceptedManifestError("data root must be an existing directory")
    resolved_root = data_root.resolve(strict=True)
    files: dict[str, Path] = {}
    for row in rows:
        relative = _validate_relative_path(row["relative_image_path"])
        candidate = data_root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except FileNotFoundError as exc:
            raise AcceptedManifestError(
                f"reviewed image does not exist: {row['relative_image_path']}"
            ) from exc
        except ValueError as exc:
            raise AcceptedManifestError("reviewed image resolves outside the data root") from exc
        if not resolved.is_file():
            raise AcceptedManifestError("reviewed image must resolve to a regular file")
        files[row["image_id"]] = resolved
    return files


def _validate_reviewed_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image_format = image.format
            image_size = image.size
            image_mode = image.mode
            metadata = dict(image.info)
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AcceptedManifestError("a reviewed image could not be decoded") from exc
    if image_format != "PNG":
        raise AcceptedManifestError("every reviewed image must decode as PNG")
    if image_size != EXPECTED_REVIEWED_SIZE:
        raise AcceptedManifestError("a reviewed image has the wrong dimensions")
    if image_mode not in EXPECTED_REVIEWED_MODES:
        raise AcceptedManifestError("a reviewed image has an unsupported mode")
    if metadata:
        raise AcceptedManifestError("reviewed PNG images must be metadata-free")


def _load_audit(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AcceptedManifestError(f"audit does not exist: {path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptedManifestError(f"audit JSON could not be read: {path}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("images"), list):
        raise AcceptedManifestError("audit JSON has an unexpected schema")
    if not isinstance(parsed.get("capture_session_id"), str):
        raise AcceptedManifestError("audit JSON has no capture-session ID")
    return parsed


def _audit_record_value(
    record: dict[str, Any],
    primary_key: str,
    alternate_key: str,
) -> Any:
    """Read one field from either supported intake-audit schema."""

    present = [key for key in (primary_key, alternate_key) if key in record]
    if not present:
        return None
    value = record[present[0]]
    if len(present) == 2 and record[present[1]] != value:
        raise AcceptedManifestError(
            f"audit contains conflicting {primary_key} and {alternate_key} values"
        )
    return value


def _validate_audits(
    rows: Sequence[dict[str, str]],
    audit_paths: Sequence[Path],
    reviewed_hashes: dict[str, str],
) -> tuple[set[str], set[str]]:
    expected_by_session: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        expected_by_session.setdefault(row["capture_session_id"], {})[
            row["image_id"]
        ] = row

    audits_by_session: dict[str, dict[str, Any]] = {}
    for path in audit_paths:
        audit = _load_audit(path)
        session_id = audit["capture_session_id"]
        if session_id in audits_by_session:
            raise AcceptedManifestError("duplicate capture-session audits are not allowed")
        audits_by_session[session_id] = audit
    if set(audits_by_session) != set(expected_by_session):
        raise AcceptedManifestError("audits must cover exactly the input capture sessions")

    source_hashes: set[str] = set()
    audit_reviewed_hashes: set[str] = set()
    for session_id, expected_rows in expected_by_session.items():
        audit = audits_by_session[session_id]
        records: dict[str, dict[str, Any]] = {}
        for record in audit["images"]:
            if not isinstance(record, dict) or not isinstance(record.get("image_id"), str):
                raise AcceptedManifestError("audit contains an invalid image record")
            image_id = record["image_id"]
            if image_id in records:
                raise AcceptedManifestError("audit contains a duplicate image ID")
            records[image_id] = record
        if set(records) != set(expected_rows):
            raise AcceptedManifestError("audit image IDs do not match the session manifest")
        for image_id, row in expected_rows.items():
            record = records[image_id]
            source_hash = _audit_record_value(
                record, "source_sha256", "source_file_sha256"
            )
            reviewed_hash = _audit_record_value(
                record, "reviewed_sha256", "reviewed_file_sha256"
            )
            audit_label = _audit_record_value(record, "label", "candidate_label")
            if (
                not isinstance(source_hash, str)
                or not SHA256_PATTERN.fullmatch(source_hash)
                or not isinstance(reviewed_hash, str)
                or not SHA256_PATTERN.fullmatch(reviewed_hash)
            ):
                raise AcceptedManifestError("audit contains an invalid SHA-256 value")
            if audit_label != row["label"]:
                raise AcceptedManifestError("audit label does not match the manifest")
            if record.get("reviewed_dimensions") != list(EXPECTED_REVIEWED_SIZE):
                raise AcceptedManifestError("audit reviewed dimensions are invalid")
            if record.get("reviewed_mode") not in EXPECTED_REVIEWED_MODES:
                raise AcceptedManifestError("audit reviewed mode is invalid")
            if reviewed_hash != reviewed_hashes[image_id]:
                raise AcceptedManifestError("audit reviewed hash does not match the file")
            if source_hash in source_hashes:
                raise AcceptedManifestError("audit contains an exact duplicate source hash")
            if reviewed_hash in audit_reviewed_hashes:
                raise AcceptedManifestError("audit contains an exact duplicate reviewed hash")
            source_hashes.add(source_hash)
            audit_reviewed_hashes.add(reviewed_hash)
    return source_hashes, audit_reviewed_hashes


def _write_manifest_atomic(
    output_path: Path,
    rows: Sequence[dict[str, str]],
    *,
    overwrite: bool,
) -> None:
    if output_path.suffix.lower() != ".csv":
        raise AcceptedManifestError("output path must end in .csv")
    if not output_path.parent.is_dir():
        raise AcceptedManifestError("output directory does not exist")
    if output_path.is_symlink():
        raise AcceptedManifestError("output path must not be a symbolic link")
    if output_path.exists() and not overwrite:
        raise AcceptedManifestError("output already exists; use --overwrite to replace it")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(
                handle, fieldnames=MANIFEST_HEADERS, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())

        staged_rows = _read_manifest(temp_path)
        if staged_rows != list(rows):
            raise AcceptedManifestError("staged output validation failed")
        try:
            if overwrite:
                os.replace(temp_path, output_path)
            else:
                os.link(temp_path, output_path)
                temp_path.unlink()
        except FileExistsError as exc:
            raise AcceptedManifestError("output appeared during installation") from exc
        except OSError as exc:
            raise AcceptedManifestError("output could not be installed atomically") from exc
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def build_accepted_manifest(
    manifest_paths: Sequence[str | os.PathLike[str]],
    output_path: str | os.PathLike[str],
    data_root: str | os.PathLike[str],
    *,
    audit_paths: Sequence[str | os.PathLike[str]] = (),
    validate_images: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Merge validated accepted manifests and atomically write one CSV."""

    if not manifest_paths:
        raise AcceptedManifestError("at least one input manifest is required")
    manifests = [Path(path) for path in manifest_paths]
    if len({path.resolve(strict=False) for path in manifests}) != len(manifests):
        raise AcceptedManifestError("input manifest paths must be unique")
    output = Path(output_path)
    if output.resolve(strict=False) in {
        path.resolve(strict=False) for path in manifests
    }:
        raise AcceptedManifestError("output path must differ from every input manifest")

    rows_by_manifest = [(path, _read_manifest(path)) for path in manifests]
    rows = _validate_rows(rows_by_manifest)
    reviewed_files = _resolve_reviewed_files(rows, Path(data_root))
    if validate_images:
        for path in reviewed_files.values():
            _validate_reviewed_image(path)
    reviewed_hashes = {
        image_id: sha256_file(path) for image_id, path in reviewed_files.items()
    }
    if len(reviewed_hashes) != len(set(reviewed_hashes.values())):
        raise AcceptedManifestError("exact duplicate reviewed image hashes are not allowed")

    source_hash_count: int | None = None
    if audit_paths:
        source_hashes, audit_reviewed_hashes = _validate_audits(
            rows,
            [Path(path) for path in audit_paths],
            reviewed_hashes,
        )
        if audit_reviewed_hashes != set(reviewed_hashes.values()):
            raise AcceptedManifestError("audit reviewed hashes do not cover every row")
        source_hash_count = len(source_hashes)

    _write_manifest_atomic(output, rows, overwrite=overwrite)
    source_groups_resolved = all(row["source_group_id"] for row in rows)
    return {
        "manifest_count": len(manifests),
        "row_count": len(rows),
        "reviewed_hash_count": len(reviewed_hashes),
        "source_hash_count": source_hash_count,
        "source_groups_resolved": source_groups_resolved,
        "split_ready": source_groups_resolved,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and merge accepted CS 4710 OSRS session manifests."
    )
    parser.add_argument("--manifest", action="append", required=True, type=Path)
    parser.add_argument("--audit", action="append", default=[], type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="atomically replace an existing output after full validation",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = build_accepted_manifest(
            args.manifest,
            args.output,
            args.data_root,
            audit_paths=args.audit,
            overwrite=args.overwrite,
        )
    except AcceptedManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
