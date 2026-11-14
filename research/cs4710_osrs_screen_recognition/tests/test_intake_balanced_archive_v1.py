from __future__ import annotations

import csv
import functools
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image
from PIL.PngImagePlugin import PngInfo


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
MODULE_PATH = SRC_DIR / "intake_balanced_archive_v1.py"
sys.path.insert(0, str(SRC_DIR))
try:
    SPEC = importlib.util.spec_from_file_location(
        "intake_balanced_archive_v1",
        MODULE_PATH,
    )
    assert SPEC is not None and SPEC.loader is not None
    intake_balanced_archive_v1 = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(intake_balanced_archive_v1)
finally:
    sys.path.pop(0)


PREFIX = "session_004_selected_60"
SESSION = "session_004"
LABELS = intake_balanced_archive_v1.LABEL_ORDER
SELECTION_HEADERS = intake_balanced_archive_v1.SELECTION_MANIFEST_HEADERS


def _selection_rows() -> list[dict[str, str]]:
    rows = []
    index = 1
    for label in LABELS:
        for _ in range(10):
            rows.append(
                {
                    "filename": f"candidate_{index:03d}.png",
                    "label": label,
                    "source_session": SESSION,
                    "selection_reason": "synthetic provenance only",
                }
            )
            index += 1
    return rows


@functools.lru_cache(maxsize=None)
def _png_bytes(
    index: int,
    mode: str = "RGB",
    size: tuple[int, int] = (2560, 1440),
    compress_level: int = 9,
    metadata: bool = False,
    shared_color: bool = False,
    outside_crop_marker: int = 0,
) -> bytes:
    if mode == "RGB":
        color = (
            17 if shared_color else index % 251,
            31 if shared_color else (index * 3) % 251,
            47 if shared_color else (index * 7) % 251,
        )
    elif mode == "L":
        color = index % 251
    else:
        raise AssertionError(f"unsupported synthetic mode: {mode}")
    image = Image.new(mode, size, color)
    if outside_crop_marker and mode == "RGB" and size == (2560, 1440):
        image.putpixel((2400, 1400), (outside_crop_marker, 1, 2))
    output = io.BytesIO()
    pnginfo = None
    if metadata:
        pnginfo = PngInfo()
        pnginfo.add_text("SyntheticField", "synthetic-only")
    image.save(
        output,
        format="PNG",
        compress_level=compress_level,
        pnginfo=pnginfo,
    )
    image.close()
    return output.getvalue()


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (2560, 1440), (1, 2, 3))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=10)
    image.close()
    return output.getvalue()


def _csv_bytes(
    rows: list[dict[str, str]],
    headers: tuple[str, ...] = SELECTION_HEADERS,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=headers,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _write_archive(
    path: Path,
    *,
    rows: list[dict[str, str]] | None = None,
    selection_headers: tuple[str, ...] = SELECTION_HEADERS,
    path_overrides: dict[str, str] | None = None,
    image_overrides: dict[str, bytes] | None = None,
    info_overrides: dict[str, zipfile.ZipInfo] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    selected_rows = _selection_rows() if rows is None else rows
    path_overrides = path_overrides or {}
    image_overrides = image_overrides or {}
    info_overrides = info_overrides or {}
    selection_path = f"{PREFIX}/selection_manifest.csv"
    excluded_path = f"{PREFIX}/excluded_from_balanced_selection.csv"
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = []
    for index, row in enumerate(_selection_rows(), start=1):
        original_name = f"{PREFIX}/{row['filename']}"
        member_name = path_overrides.get(original_name, original_name)
        member = info_overrides.get(original_name, member_name)
        contents = image_overrides.get(row["filename"], _png_bytes(index))
        entries.append((member, contents))
    entries.extend(
        [
            (
                path_overrides.get(selection_path, selection_path),
                _csv_bytes(selected_rows, selection_headers),
            ),
            (
                path_overrides.get(excluded_path, excluded_path),
                b"filename,label,reason\nsynthetic.png,normal_gameplay,synthetic\n",
            ),
        ]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(
            path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for member, contents in entries:
                archive.writestr(member, contents)
    return hashlib.sha256(path.read_bytes()).hexdigest(), selected_rows


def _make_data_root(root: Path) -> Path:
    data_root = root / "data"
    (data_root / "raw").mkdir(parents=True)
    (data_root / "reviewed").mkdir()
    (data_root / "manifests").mkdir()
    (root / "reports").mkdir()
    return data_root


def _run(
    archive: Path,
    archive_hash: str,
    data_root: Path,
    *,
    validate_only: bool = True,
) -> dict[str, object]:
    return intake_balanced_archive_v1.run_balanced_archive_intake(
        archive,
        archive_hash,
        data_root,
        session_id=SESSION,
        first_image_index=19,
        validate_only=validate_only,
    )


def _assert_no_outputs(test: unittest.TestCase, data_root: Path) -> None:
    test.assertEqual(list((data_root / "raw").iterdir()), [])
    test.assertEqual(list((data_root / "reviewed").iterdir()), [])
    test.assertEqual(list((data_root / "manifests").iterdir()), [])
    test.assertEqual(list((data_root.parent / "reports").iterdir()), [])
    test.assertEqual(
        list(data_root.parent.glob(".session_004_balanced_intake.*")),
        [],
    )


class ValidBalancedArchiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.archive = cls.root / "balanced.zip"
        cls.archive_hash, cls.selection_rows = _write_archive(cls.archive)
        cls.data_root = _make_data_root(cls.root)
        cls.summary = _run(
            cls.archive,
            cls.archive_hash,
            cls.data_root,
            validate_only=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_valid_balanced_archive_validation(self) -> None:
        self.assertEqual(self.summary["archive_entry_count"], 62)
        self.assertEqual(self.summary["raw_count"], 60)
        self.assertEqual(self.summary["reviewed_count"], 60)
        self.assertEqual(
            self.summary["class_counts"],
            {label: 10 for label in LABELS},
        )
        self.assertTrue(self.summary["validate_only"])
        self.assertFalse(self.summary["outputs_installed"])

    def test_validate_only_creates_no_persistent_output(self) -> None:
        _assert_no_outputs(self, self.data_root)


class InstalledDraftIntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.archive = cls.root / "balanced.zip"
        cls.archive_hash, cls.selection_rows = _write_archive(cls.archive)
        cls.data_root = _make_data_root(cls.root)
        cls.summary = _run(
            cls.archive,
            cls.archive_hash,
            cls.data_root,
            validate_only=False,
        )
        cls.draft_path = cls.data_root / "manifests/session_004_draft.csv"
        with cls.draft_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            cls.headers = tuple(reader.fieldnames or ())
            cls.rows = list(reader)
        cls.audit = json.loads(
            (
                cls.data_root / "manifests/session_004_audit.json"
            ).read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_deterministic_id_mapping_uses_selection_row_order(self) -> None:
        self.assertEqual(
            [row["image_id"] for row in self.rows],
            [f"img_{index:04d}" for index in range(19, 79)],
        )
        self.assertEqual(
            [
                (record["source_filename"], record["image_id"])
                for record in self.audit["images"]
            ],
            [
                (selection["filename"], f"img_{index:04d}")
                for index, selection in enumerate(
                    self.selection_rows,
                    start=19,
                )
            ],
        )
        self.assertEqual(
            self.audit["id_assignment"]["rule"],
            intake_balanced_archive_v1.ID_ASSIGNMENT_RULE,
        )

    def test_draft_status_remains_pending(self) -> None:
        self.assertEqual(
            self.headers,
            intake_balanced_archive_v1.MANIFEST_HEADERS,
        )
        self.assertEqual(len(self.rows), 60)
        self.assertEqual(
            {row["inclusion_status"] for row in self.rows},
            {"needs_review"},
        )
        self.assertEqual(
            {row["redaction_checked"] for row in self.rows},
            {"false"},
        )
        self.assertEqual({row["split"] for row in self.rows}, {"unassigned"})
        self.assertEqual({row["source_group_id"] for row in self.rows}, {""})
        self.assertEqual(
            {row["source_consent"] for row in self.rows},
            {"owner_captured"},
        )

    def test_all_approved_outputs_are_complete(self) -> None:
        self.assertEqual(
            sorted(path.name for path in (self.data_root / "raw/session_004").iterdir()),
            sorted(row["filename"] for row in self.selection_rows),
        )
        self.assertEqual(
            sorted(path.name for path in (self.data_root / "reviewed").iterdir()),
            [f"img_{index:04d}.png" for index in range(19, 79)],
        )
        self.assertEqual(len(self.audit["images"]), 60)
        self.assertEqual(
            self.audit["class_counts"],
            {label: 10 for label in LABELS},
        )
        contact_sheet = self.root / "reports/session_004_review_contact_sheet.png"
        with Image.open(contact_sheet) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")
            self.assertFalse(image.info)

    def test_provenance_csv_bytes_are_preserved(self) -> None:
        with zipfile.ZipFile(self.archive) as archive:
            expected_selection = archive.read(
                f"{PREFIX}/selection_manifest.csv"
            )
            expected_excluded = archive.read(
                f"{PREFIX}/excluded_from_balanced_selection.csv"
            )
        self.assertEqual(
            (
                self.data_root
                / "manifests/session_004_selection_manifest.csv"
            ).read_bytes(),
            expected_selection,
        )
        self.assertEqual(
            (
                self.data_root
                / "manifests/session_004_excluded_from_balanced_selection.csv"
            ).read_bytes(),
            expected_excluded,
        )

    def test_no_accepted_or_feasibility_manifest_is_created(self) -> None:
        self.assertFalse(
            (self.data_root / "manifests/session_004.csv").exists()
        )
        self.assertFalse(
            (
                self.data_root
                / "manifests/chat_open_feasibility_60.csv"
            ).exists()
        )


class ArchiveAndManifestFailureTest(unittest.TestCase):
    def _case(
        self,
        *,
        rows: list[dict[str, str]] | None = None,
        selection_headers: tuple[str, ...] = SELECTION_HEADERS,
        path_overrides: dict[str, str] | None = None,
        image_overrides: dict[str, bytes] | None = None,
        info_overrides: dict[str, zipfile.ZipInfo] | None = None,
        expected_error: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "case.zip"
            archive_hash, _ = _write_archive(
                archive,
                rows=rows,
                selection_headers=selection_headers,
                path_overrides=path_overrides,
                image_overrides=image_overrides,
                info_overrides=info_overrides,
            )
            data_root = _make_data_root(root)
            with self.assertRaisesRegex(
                intake_balanced_archive_v1.BalancedArchiveIntakeError,
                expected_error,
            ):
                _run(archive, archive_hash, data_root)
            _assert_no_outputs(self, data_root)

    def test_wrong_archive_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "case.zip"
            _write_archive(archive)
            data_root = _make_data_root(root)
            with self.assertRaisesRegex(
                intake_balanced_archive_v1.BalancedArchiveIntakeError,
                "SHA-256",
            ):
                _run(archive, "0" * 64, data_root)
            _assert_no_outputs(self, data_root)

    def test_path_traversal(self) -> None:
        original = f"{PREFIX}/candidate_060.png"
        self._case(
            path_overrides={original: f"{PREFIX}/../escape.png"},
            expected_error="traversal",
        )

    def test_absolute_or_drive_qualified_member(self) -> None:
        original = f"{PREFIX}/candidate_060.png"
        for unsafe in ("/absolute.png", "C:/drive.png"):
            with self.subTest(unsafe=unsafe):
                self._case(
                    path_overrides={original: unsafe},
                    expected_error="absolute or drive-qualified",
                )

    def test_duplicate_zip_member_path(self) -> None:
        self._case(
            path_overrides={
                f"{PREFIX}/candidate_060.png": f"{PREFIX}/candidate_001.png"
            },
            expected_error="duplicate member",
        )

    def test_symlink_member(self) -> None:
        original = f"{PREFIX}/candidate_060.png"
        info = zipfile.ZipInfo(original)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self._case(
            info_overrides={original: info},
            expected_error="symbolic-link",
        )

    def test_unexpected_file_type(self) -> None:
        excluded = f"{PREFIX}/excluded_from_balanced_selection.csv"
        self._case(
            path_overrides={excluded: f"{PREFIX}/unexpected.txt"},
            expected_error="unexpected file extension",
        )

    def test_missing_selection_manifest(self) -> None:
        selection = f"{PREFIX}/selection_manifest.csv"
        self._case(
            path_overrides={selection: f"{PREFIX}/other.csv"},
            expected_error="missing a required provenance CSV",
        )

    def test_incorrect_manifest_columns(self) -> None:
        self._case(
            selection_headers=("filename", "label", "source_session"),
            expected_error="incorrect columns",
        )

    def test_duplicate_manifest_filename(self) -> None:
        rows = _selection_rows()
        rows[1]["filename"] = rows[0]["filename"]
        self._case(rows=rows, expected_error="duplicate filename")

    def test_missing_png(self) -> None:
        rows = _selection_rows()
        rows[0]["filename"] = "missing.png"
        self._case(rows=rows, expected_error="missing PNG")

    def test_extra_unmanifested_png(self) -> None:
        rows = _selection_rows()
        png_infos = {
            f"{PREFIX}/{row['filename']}": object() for row in rows
        }
        png_infos[f"{PREFIX}/extra.png"] = object()
        with self.assertRaisesRegex(
            intake_balanced_archive_v1.BalancedArchiveIntakeError,
            "extra or unmanifested PNG",
        ):
            intake_balanced_archive_v1._read_selection_manifest(
                _csv_bytes(rows),
                session_id=SESSION,
                png_infos=png_infos,
                prefix=PREFIX,
            )

    def test_unsupported_label(self) -> None:
        rows = _selection_rows()
        rows[0]["label"] = "not_a_label"
        self._case(rows=rows, expected_error="unsupported label")

    def test_wrong_per_class_count(self) -> None:
        rows = _selection_rows()
        rows[0]["label"] = "bank_open"
        self._case(rows=rows, expected_error="10 rows per label")

    def test_wrong_source_session(self) -> None:
        rows = _selection_rows()
        rows[0]["source_session"] = "session_005"
        self._case(rows=rows, expected_error="wrong source_session")


class ImageValidationFailureTest(unittest.TestCase):
    _case = ArchiveAndManifestFailureTest._case

    def test_non_png_or_corrupt_image(self) -> None:
        for contents in (_jpeg_bytes(), b"not an image"):
            with self.subTest(kind=contents[:4]):
                self._case(
                    image_overrides={"candidate_001.png": contents},
                    expected_error="could not be decoded|did not decode as PNG",
                )

    def test_wrong_image_dimensions(self) -> None:
        self._case(
            image_overrides={
                "candidate_001.png": _png_bytes(1, size=(2559, 1440))
            },
            expected_error="wrong dimensions",
        )

    def test_wrong_image_mode(self) -> None:
        self._case(
            image_overrides={"candidate_001.png": _png_bytes(1, mode="L")},
            expected_error="RGB mode",
        )

    def test_unexpected_png_metadata(self) -> None:
        self._case(
            image_overrides={
                "candidate_001.png": _png_bytes(1, metadata=True)
            },
            expected_error="metadata",
        )

    def test_duplicate_source_bytes(self) -> None:
        duplicate = _png_bytes(1)
        self._case(
            image_overrides={
                "candidate_001.png": duplicate,
                "candidate_002.png": duplicate,
            },
            expected_error="duplicate compressed source bytes",
        )

    def test_duplicate_source_pixels_with_different_encoding(self) -> None:
        self._case(
            image_overrides={
                "candidate_001.png": _png_bytes(
                    1,
                    compress_level=9,
                    shared_color=True,
                ),
                "candidate_002.png": _png_bytes(
                    2,
                    compress_level=1,
                    shared_color=True,
                ),
            },
            expected_error="duplicate decoded source pixels",
        )

    def test_duplicate_reviewed_pixels(self) -> None:
        self._case(
            image_overrides={
                "candidate_001.png": _png_bytes(
                    1,
                    shared_color=True,
                    outside_crop_marker=1,
                ),
                "candidate_002.png": _png_bytes(
                    2,
                    shared_color=True,
                    outside_crop_marker=2,
                ),
            },
            expected_error="duplicate reviewed",
        )


class DestinationAndTransactionTest(unittest.TestCase):
    def _valid_case(self) -> tuple[
        tempfile.TemporaryDirectory[str],
        Path,
        Path,
        str,
    ]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        archive = root / "case.zip"
        archive_hash, _ = _write_archive(archive)
        data_root = _make_data_root(root)
        return temp_dir, data_root, archive, archive_hash

    def test_reviewed_id_collision(self) -> None:
        temp_dir, data_root, archive, archive_hash = self._valid_case()
        try:
            collision = data_root / "reviewed/img_0019.png"
            collision.write_bytes(b"sentinel")
            with self.assertRaisesRegex(
                intake_balanced_archive_v1.BalancedArchiveIntakeError,
                "already exist",
            ):
                _run(archive, archive_hash, data_root)
            self.assertEqual(collision.read_bytes(), b"sentinel")
        finally:
            temp_dir.cleanup()

    def test_destination_overwrite_refusal(self) -> None:
        temp_dir, data_root, archive, archive_hash = self._valid_case()
        try:
            collision = data_root / "manifests/session_004_draft.csv"
            collision.write_bytes(b"sentinel")
            with self.assertRaisesRegex(
                intake_balanced_archive_v1.BalancedArchiveIntakeError,
                "already exist",
            ):
                _run(archive, archive_hash, data_root)
            self.assertEqual(collision.read_bytes(), b"sentinel")
        finally:
            temp_dir.cleanup()

    def test_install_failure_cleanup_leaves_no_partial_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = _make_data_root(root)
            stage = root / "stage"
            staged_raw = stage / "raw"
            staged_reviewed = stage / "reviewed"
            staged_raw.mkdir(parents=True)
            staged_reviewed.mkdir()
            for name in ("one.png", "two.png"):
                (staged_raw / name).write_bytes(name.encode())
            staged_reviewed_path = staged_reviewed / "img_0019.png"
            staged_reviewed_path.write_bytes(b"reviewed")
            staged_files = {}
            for key, name in (
                ("selection_manifest", "selection.csv"),
                ("excluded_manifest", "excluded.csv"),
                ("draft_manifest", "draft.csv"),
                ("audit", "audit.json"),
                ("contact_sheet", "contact.png"),
            ):
                staged_files[key] = stage / name
                staged_files[key].write_bytes(name.encode())
            output_layout = {
                "raw_dir": data_root / "raw/session_004",
                "reviewed_paths": [data_root / "reviewed/img_0019.png"],
                "selection_manifest": data_root
                / "manifests/session_004_selection_manifest.csv",
                "excluded_manifest": data_root
                / "manifests/session_004_excluded_from_balanced_selection.csv",
                "draft_manifest": data_root / "manifests/session_004_draft.csv",
                "audit": data_root / "manifests/session_004_audit.json",
                "contact_sheet": root
                / "reports/session_004_review_contact_sheet.png",
            }
            real_link = os.link
            call_count = 0

            def fail_on_third(source: Path, destination: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 3:
                    raise OSError("synthetic install failure")
                real_link(source, destination)

            with mock.patch.object(
                intake_balanced_archive_v1.os,
                "link",
                side_effect=fail_on_third,
            ):
                with self.assertRaisesRegex(
                    intake_balanced_archive_v1.BalancedArchiveIntakeError,
                    "rolled back",
                ):
                    intake_balanced_archive_v1._install_outputs(
                        staged_raw_dir=staged_raw,
                        staged_reviewed_paths=[staged_reviewed_path],
                        staged_selection_manifest=staged_files[
                            "selection_manifest"
                        ],
                        staged_excluded_manifest=staged_files[
                            "excluded_manifest"
                        ],
                        staged_draft_manifest=staged_files["draft_manifest"],
                        staged_audit=staged_files["audit"],
                        staged_contact_sheet=staged_files["contact_sheet"],
                        output_layout=output_layout,
                    )
            _assert_no_outputs(self, data_root)


if __name__ == "__main__":
    unittest.main()
