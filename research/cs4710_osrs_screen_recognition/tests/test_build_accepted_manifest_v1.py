from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
MODULE_PATH = SRC_DIR / "build_accepted_manifest_v1.py"
SPEC = importlib.util.spec_from_file_location("build_accepted_manifest_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
build_accepted_manifest_v1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_accepted_manifest_v1)

HEADERS = build_accepted_manifest_v1.MANIFEST_HEADERS
LABELS = (
    "normal_gameplay",
    "bank_open",
    "dialogue_or_choice_open",
    "shop_open",
    "grand_exchange_open",
    "unknown_other",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row(index: int, session_id: str, label: str) -> dict[str, str]:
    return {
        "image_id": f"img_{index:04d}",
        "relative_image_path": f"reviewed/img_{index:04d}.png",
        "label": label,
        "capture_session_id": session_id,
        "source_group_id": f"source_group_{index:04d}",
        "inclusion_status": "included",
        "exclusion_reason": "",
        "resolution": "2287x1369",
        "aspect_ratio": "2287:1369",
        "client_type": "runelite",
        "display_mode": "maximized_windowed",
        "ui_layout_or_scale": "resizable_classic_stretched_90pct",
        "crop_policy": "fixed_game_canvas_v1",
        "camera_angle_bucket": "north_facing",
        "zoom_bucket": "",
        "location_or_activity_bucket": "",
        "overlay_notes": "fixed_profile_v1",
        "redaction_method": "none",
        "redaction_checked": "true",
        "source_consent": "owner_captured",
        "split": "unassigned",
        "notes": "",
    }


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_manifest(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def _save_image(
    path: Path,
    index: int,
    *,
    size: tuple[int, int] = (2287, 1369),
    metadata: bool = False,
) -> None:
    image = Image.new(
        "RGB",
        size,
        (index % 251, (index * 7) % 251, (index * 13) % 251),
    )
    image.putpixel((0, 0), ((index * 17) % 251, 1, 2))
    pnginfo = None
    if metadata:
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("synthetic_test", "must be rejected")
    image.save(path, format="PNG", pnginfo=pnginfo)
    image.close()


def _write_audit(
    path: Path,
    session_id: str,
    rows: list[dict[str, str]],
    data_root: Path,
    *,
    mismatched_hash: bool = False,
    balanced_archive_schema: bool = False,
) -> None:
    images = []
    for row in rows:
        reviewed_hash = _sha256(data_root / row["relative_image_path"])
        if mismatched_hash and not images:
            reviewed_hash = "0" * 64
        record = {
            "image_id": row["image_id"],
            "reviewed_dimensions": [2287, 1369],
            "reviewed_mode": "RGB",
        }
        if balanced_archive_schema:
            record.update(
                {
                    "candidate_label": row["label"],
                    "source_file_sha256": hashlib.sha256(
                        f"source-{row['image_id']}".encode()
                    ).hexdigest(),
                    "reviewed_file_sha256": reviewed_hash,
                }
            )
        else:
            record.update(
                {
                    "label": row["label"],
                    "source_sha256": hashlib.sha256(
                        f"source-{row['image_id']}".encode()
                    ).hexdigest(),
                    "reviewed_sha256": reviewed_hash,
                }
            )
        images.append(record)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "capture_session_id": session_id,
                "images": images,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _make_valid_three_session_fixture(
    root: Path,
) -> tuple[Path, list[Path], list[Path], list[dict[str, str]]]:
    data_root = root / "data"
    reviewed = data_root / "reviewed"
    manifests_dir = root / "manifests"
    reviewed.mkdir(parents=True)
    manifests_dir.mkdir()
    manifest_paths: list[Path] = []
    audit_paths: list[Path] = []
    all_rows: list[dict[str, str]] = []
    for session_number in range(1, 4):
        session_id = f"session_{session_number:03d}"
        start = 1 + (session_number - 1) * 6
        rows = [
            _row(index, session_id, label)
            for index, label in zip(range(start, start + 6), LABELS, strict=True)
        ]
        for row in rows:
            _save_image(
                data_root / row["relative_image_path"],
                int(row["image_id"].split("_")[1]),
            )
        manifest = manifests_dir / f"{session_id}.csv"
        audit = manifests_dir / f"{session_id}_audit.json"
        _write_manifest(manifest, rows)
        _write_audit(audit, session_id, rows, data_root)
        manifest_paths.append(manifest)
        audit_paths.append(audit)
        all_rows.extend(rows)
    return data_root, manifest_paths, audit_paths, all_rows


class ImportSafetyTest(unittest.TestCase):
    def test_import_has_no_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = (
                "import importlib.util; "
                f"p={str(MODULE_PATH)!r}; "
                "s=importlib.util.spec_from_file_location('builder_import_test', p); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            self.assertEqual(list(root.iterdir()), [])


class SuccessfulThreeSessionMergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        (
            cls.data_root,
            cls.manifest_paths,
            cls.audit_paths,
            cls.expected_rows,
        ) = _make_valid_three_session_fixture(cls.root)
        cls.source_hashes_before = {
            path: _sha256(path)
            for path in [*cls.manifest_paths, *cls.audit_paths]
            + sorted((cls.data_root / "reviewed").iterdir())
        }
        cls.output = cls.root / "accepted_18.csv"
        cls.summary = build_accepted_manifest_v1.build_accepted_manifest(
            cls.manifest_paths,
            cls.output,
            cls.data_root,
            audit_paths=cls.audit_paths,
        )
        cls.headers, cls.rows = _read_manifest(cls.output)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_three_valid_six_row_manifests_merge_into_18_rows(self) -> None:
        self.assertEqual(self.summary["manifest_count"], 3)
        self.assertEqual(self.summary["row_count"], 18)
        self.assertEqual(len(self.rows), 18)

    def test_exact_22_column_order_is_preserved(self) -> None:
        self.assertEqual(self.headers, HEADERS)
        self.assertEqual(len(self.headers), 22)

    def test_input_manifest_and_row_order_are_preserved(self) -> None:
        self.assertEqual(self.rows, self.expected_rows)
        self.assertEqual(
            [row["image_id"] for row in self.rows],
            [f"img_{index:04d}" for index in range(1, 19)],
        )

    def test_source_manifests_audits_and_images_are_unchanged(self) -> None:
        after = {path: _sha256(path) for path in self.source_hashes_before}
        self.assertEqual(after, self.source_hashes_before)

    def test_successful_cli_run_writes_exactly_18_valid_rows(self) -> None:
        output = self.root / "cli_accepted_18.csv"
        command = [sys.executable, str(MODULE_PATH)]
        for path in self.manifest_paths:
            command.extend(("--manifest", str(path)))
        for path in self.audit_paths:
            command.extend(("--audit", str(path)))
        command.extend(("--data-root", str(self.data_root), "--output", str(output)))
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["row_count"], 18)
        headers, rows = _read_manifest(output)
        self.assertEqual(headers, HEADERS)
        self.assertEqual(rows, self.expected_rows)


class RejectionAndTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_root = self.root / "data"
        (self.data_root / "reviewed").mkdir(parents=True)
        self.manifests_dir = self.root / "manifests"
        self.manifests_dir.mkdir()
        self.output = self.root / "output.csv"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _manifest(self, name: str, rows: list[dict[str, str]]) -> Path:
        path = self.manifests_dir / name
        _write_manifest(path, rows)
        return path

    def _valid_single(self) -> tuple[dict[str, str], Path]:
        row = _row(1, "session_001", LABELS[0])
        image_path = self.data_root / row["relative_image_path"]
        _save_image(image_path, 1)
        return row, self._manifest("session_001.csv", [row])

    def _build(self, paths: list[Path], **kwargs):
        return build_accepted_manifest_v1.build_accepted_manifest(
            paths,
            self.output,
            self.data_root,
            **kwargs,
        )

    def test_duplicate_image_id_is_rejected(self) -> None:
        first = _row(1, "session_001", LABELS[0])
        second = _row(2, "session_002", LABELS[1])
        second["image_id"] = first["image_id"]
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "image IDs"
        ):
            self._build(
                [
                    self._manifest("one.csv", [first]),
                    self._manifest("two.csv", [second]),
                ]
            )

    def test_duplicate_reviewed_path_is_rejected(self) -> None:
        first = _row(1, "session_001", LABELS[0])
        second = _row(2, "session_002", LABELS[1])
        second["relative_image_path"] = first["relative_image_path"]
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "reviewed paths"
        ):
            self._build(
                [
                    self._manifest("one.csv", [first]),
                    self._manifest("two.csv", [second]),
                ]
            )

    def test_repeated_nonblank_source_group_id_is_valid(self) -> None:
        first = _row(1, "session_001", LABELS[0])
        second = _row(2, "session_001", LABELS[1])
        second["source_group_id"] = first["source_group_id"]
        _save_image(self.data_root / first["relative_image_path"], 1)
        _save_image(self.data_root / second["relative_image_path"], 2)
        summary = self._build(
            [self._manifest("repeated-source-group.csv", [first, second])]
        )
        self.assertTrue(summary["source_groups_resolved"])
        self.assertTrue(summary["split_ready"])

    def test_blank_source_group_with_unassigned_split_is_valid(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["source_group_id"] = ""
        _save_image(self.data_root / row["relative_image_path"], 1)
        summary = self._build([self._manifest("pending-group.csv", [row])])
        self.assertFalse(summary["source_groups_resolved"])
        self.assertFalse(summary["split_ready"])

    def test_blank_source_group_with_assigned_split_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["source_group_id"] = ""
        row["split"] = "train"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError,
            "assigned splits require a resolved source-group ID",
        ):
            self._build([self._manifest("blank-group-assigned-split.csv", [row])])

    def test_draft_needs_review_row_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["inclusion_status"] = "needs_review"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "included"
        ):
            self._build([self._manifest("draft.csv", [row])])

    def test_unchecked_redaction_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["redaction_checked"] = "false"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "redaction checked"
        ):
            self._build([self._manifest("unchecked.csv", [row])])

    def test_non_owner_captured_row_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["source_consent"] = "third_party"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "owner captured"
        ):
            self._build([self._manifest("consent.csv", [row])])

    def test_assigned_split_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["split"] = "train"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "unassigned split"
        ):
            self._build([self._manifest("split.csv", [row])])

    def test_incorrect_manifest_schema_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        path = self.manifests_dir / "wrong-schema.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            headers = HEADERS[:-1]
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            writer.writerow({key: row[key] for key in headers})
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError,
            "exact 22-column schema",
        ):
            self._build([path])

    def test_missing_reviewed_file_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "does not exist"
        ):
            self._build([self._manifest("missing.csv", [row])])

    def test_absolute_reviewed_path_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["relative_image_path"] = "/tmp/reviewed.png"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "absolute"
        ):
            self._build([self._manifest("absolute.csv", [row])])

    def test_reviewed_path_traversal_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        row["relative_image_path"] = "reviewed/../outside.png"
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "traversal"
        ):
            self._build([self._manifest("traversal.csv", [row])])

    def test_wrong_image_size_is_rejected_when_validation_is_enabled(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        _save_image(self.data_root / row["relative_image_path"], 1, size=(64, 64))
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "wrong dimensions"
        ):
            self._build(
                [self._manifest("wrong-size.csv", [row])], validate_images=True
            )

    def test_metadata_bearing_png_is_rejected(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        _save_image(self.data_root / row["relative_image_path"], 1, metadata=True)
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "metadata-free"
        ):
            self._build([self._manifest("metadata.csv", [row])])

    def test_audit_hash_mismatch_is_rejected(self) -> None:
        row, manifest = self._valid_single()
        audit = self.manifests_dir / "session_001_audit.json"
        _write_audit(
            audit,
            "session_001",
            [row],
            self.data_root,
            mismatched_hash=True,
        )
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError,
            "reviewed hash does not match",
        ):
            self._build([manifest], audit_paths=[audit])

    def test_balanced_archive_audit_schema_is_accepted(self) -> None:
        row, manifest = self._valid_single()
        audit = self.manifests_dir / "session_001_balanced_audit.json"
        _write_audit(
            audit,
            "session_001",
            [row],
            self.data_root,
            balanced_archive_schema=True,
        )
        summary = self._build([manifest], audit_paths=[audit])
        self.assertEqual(summary["source_hash_count"], 1)

    def test_output_overwrite_is_refused_by_default(self) -> None:
        _, manifest = self._valid_single()
        self.output.write_text("sentinel\n", encoding="utf-8")
        with self.assertRaisesRegex(
            build_accepted_manifest_v1.AcceptedManifestError, "already exists"
        ):
            self._build([manifest])
        self.assertEqual(self.output.read_text(encoding="utf-8"), "sentinel\n")

    def test_transactional_overwrite_succeeds_when_explicit(self) -> None:
        row, manifest = self._valid_single()
        self.output.write_text("sentinel\n", encoding="utf-8")
        self._build([manifest], overwrite=True)
        headers, rows = _read_manifest(self.output)
        self.assertEqual(headers, HEADERS)
        self.assertEqual(rows, [row])

    def test_failure_leaves_no_partial_output_or_temp_file(self) -> None:
        row = _row(1, "session_001", LABELS[0])
        manifest = self._manifest("missing.csv", [row])
        with self.assertRaises(build_accepted_manifest_v1.AcceptedManifestError):
            self._build([manifest])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".output.csv.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
