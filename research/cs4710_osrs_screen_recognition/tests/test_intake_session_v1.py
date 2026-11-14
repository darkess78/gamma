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
from unittest import mock

from PIL import Image


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
MODULE_PATH = SRC_DIR / "intake_session_v1.py"
sys.path.insert(0, str(SRC_DIR))
try:
    SPEC = importlib.util.spec_from_file_location("intake_session_v1", MODULE_PATH)
    assert SPEC is not None and SPEC.loader is not None
    intake_session_v1 = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(intake_session_v1)
finally:
    sys.path.pop(0)


EXPECTED_HEADERS = (
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

EXPECTED_MAPPING = (
    ("normal_gameplay_001.png", "img_0001.png", "img_0001"),
    ("bank_open_001.png", "img_0002.png", "img_0002"),
    ("dialogue_or_choice_open_001.png", "img_0003.png", "img_0003"),
    ("shop_open_002.png", "img_0004.png", "img_0004"),
    ("grand_exchange_open_002.png", "img_0005.png", "img_0005"),
    ("unknown_other_001.png", "img_0006.png", "img_0006"),
)

EXPECTED_SESSION_002_MAPPING = (
    (
        "normal_gameplay_001.png",
        "img_0007.png",
        "img_0007",
        "source_group_0007",
    ),
    ("bank_open_001.png", "img_0008.png", "img_0008", "source_group_0008"),
    (
        "dialogue_or_choice_open_001.png",
        "img_0009.png",
        "img_0009",
        "source_group_0009",
    ),
    ("shop_open_001.png", "img_0010.png", "img_0010", "source_group_0010"),
    (
        "grand_exchange_open_001.png",
        "img_0011.png",
        "img_0011",
        "source_group_0011",
    ),
    (
        "unknown_other_001.png",
        "img_0012.png",
        "img_0012",
        "source_group_0012",
    ),
)

EXPECTED_SESSION_003_MAPPING = (
    (
        "normal_gameplay_001.png",
        "img_0013.png",
        "img_0013",
        "normal_gameplay",
        "source_group_0013",
    ),
    (
        "bank_open_001.png",
        "img_0014.png",
        "img_0014",
        "bank_open",
        "source_group_0014",
    ),
    (
        "dialogue_or_choice_open_001.png",
        "img_0015.png",
        "img_0015",
        "dialogue_or_choice_open",
        "source_group_0015",
    ),
    (
        "shop_open_001.png",
        "img_0016.png",
        "img_0016",
        "shop_open",
        "source_group_0016",
    ),
    (
        "grand_exchange_open_001.png",
        "img_0017.png",
        "img_0017",
        "grand_exchange_open",
        "source_group_0017",
    ),
    (
        "unknown_other_001.png",
        "img_0018.png",
        "img_0018",
        "unknown_other",
        "source_group_0018",
    ),
)


def _paths(
    root: Path,
    session_id: str = "session_001",
) -> tuple[Path, Path, Path, Path]:
    source_dir = root / "source"
    reviewed_dir = root / "reviewed"
    manifests_dir = root / "manifests"
    source_dir.mkdir()
    manifests_dir.mkdir()
    return (
        source_dir,
        reviewed_dir,
        manifests_dir / f"{session_id}_draft.csv",
        manifests_dir / f"{session_id}_audit.json",
    )


def _save_sources(
    source_dir: Path,
    *,
    mixed_modes: bool = False,
    plan: tuple[intake_session_v1.IntakePlanItem, ...] | None = None,
) -> None:
    selected_plan = intake_session_v1.INTAKE_PLAN if plan is None else plan
    for index, item in enumerate(selected_plan, start=1):
        mode = "RGBA" if mixed_modes and index % 2 == 0 else "RGB"
        if mode == "RGBA":
            color = (index, index + 20, index + 40, index + 60)
        else:
            color = (index, index + 20, index + 40)
        image = Image.new(mode, (2560, 1440), color)
        image.putpixel((index, 23 + index), color)
        image.save(source_dir / item.raw_name, format="PNG")
        image.close()


def _read_manifest(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


class IntakePlanTest(unittest.TestCase):
    def test_plan_contains_exactly_six_unique_image_ids(self) -> None:
        image_ids = [item.image_id for item in intake_session_v1.INTAKE_PLAN]
        self.assertEqual(len(image_ids), 6)
        self.assertEqual(len(set(image_ids)), 6)
        intake_session_v1.validate_intake_plan()

    def test_plan_contains_exactly_the_six_approved_labels(self) -> None:
        labels = [item.label for item in intake_session_v1.INTAKE_PLAN]
        self.assertEqual(len(labels), 6)
        self.assertEqual(set(labels), intake_session_v1.APPROVED_LABELS)

    def test_raw_names_map_to_expected_opaque_reviewed_names(self) -> None:
        mapping = tuple(
            (item.raw_name, item.reviewed_name, item.image_id)
            for item in intake_session_v1.INTAKE_PLAN
        )
        self.assertEqual(mapping, EXPECTED_MAPPING)

    def test_session_001_plan_remains_the_legacy_plan(self) -> None:
        self.assertIs(
            intake_session_v1.INTAKE_PLAN,
            intake_session_v1.SESSION_PLANS["session_001"],
        )
        self.assertEqual(intake_session_v1.CAPTURE_SESSION_ID, "session_001")

    def test_session_002_has_the_approved_six_row_mapping(self) -> None:
        plan = intake_session_v1.SESSION_PLANS["session_002"]
        mapping = tuple(
            (
                item.raw_name,
                item.reviewed_name,
                item.image_id,
                item.source_group_id,
            )
            for item in plan
        )
        self.assertEqual(mapping, EXPECTED_SESSION_002_MAPPING)

    def test_session_003_has_the_approved_six_row_mapping(self) -> None:
        plan = intake_session_v1.SESSION_PLANS["session_003"]
        mapping = tuple(
            (
                item.raw_name,
                item.reviewed_name,
                item.image_id,
                item.label,
                item.source_group_id,
            )
            for item in plan
        )
        self.assertEqual(mapping, EXPECTED_SESSION_003_MAPPING)
        self.assertEqual(len(plan), 6)
        self.assertEqual(
            [item.image_id for item in plan],
            [f"img_{index:04d}" for index in range(13, 19)],
        )
        self.assertEqual(
            [item.reviewed_name for item in plan],
            [f"img_{index:04d}.png" for index in range(13, 19)],
        )
        self.assertEqual(
            [item.source_group_id for item in plan],
            [f"source_group_{index:04d}" for index in range(13, 19)],
        )
        self.assertEqual(
            [item.label for item in plan],
            [row[3] for row in EXPECTED_SESSION_003_MAPPING],
        )

    def test_all_sessions_contain_one_example_per_approved_label(self) -> None:
        for plan in intake_session_v1.SESSION_PLANS.values():
            self.assertEqual(len(plan), 6)
            self.assertEqual({item.label for item in plan}, intake_session_v1.APPROVED_LABELS)

    def test_all_session_plans_have_18_unique_ids_paths_and_groups(self) -> None:
        plans = tuple(intake_session_v1.SESSION_PLANS.values())
        image_ids = [item.image_id for plan in plans for item in plan]
        reviewed_names = [item.reviewed_name for plan in plans for item in plan]
        source_group_ids = [item.source_group_id for plan in plans for item in plan]
        self.assertEqual(len(image_ids), len(set(image_ids)), 18)
        self.assertEqual(len(reviewed_names), len(set(reviewed_names)), 18)
        self.assertEqual(len(source_group_ids), len(set(source_group_ids)), 18)
        intake_session_v1.validate_session_plans()

    def test_unknown_session_is_rejected(self) -> None:
        with self.assertRaisesRegex(intake_session_v1.IntakeError, "unknown"):
            intake_session_v1.get_session_plan("session_999")

    def test_session_specific_artifact_names(self) -> None:
        self.assertEqual(
            intake_session_v1.session_artifact_names("session_002"),
            ("session_002_draft.csv", "session_002_audit.json"),
        )
        self.assertEqual(
            intake_session_v1.session_artifact_names("session_003"),
            ("session_003_draft.csv", "session_003_audit.json"),
        )

    def test_duplicate_image_ids_across_plans_are_rejected(self) -> None:
        session_002 = list(intake_session_v1.SESSION_PLANS["session_002"])
        session_002[0] = session_002[0]._replace(image_id="img_0001")
        with self.assertRaisesRegex(intake_session_v1.IntakeError, "image IDs"):
            intake_session_v1.validate_session_plans(
                {
                    "session_001": intake_session_v1.SESSION_PLANS["session_001"],
                    "session_002": tuple(session_002),
                }
            )

    def test_duplicate_reviewed_paths_across_plans_are_rejected(self) -> None:
        session_002 = list(intake_session_v1.SESSION_PLANS["session_002"])
        session_002[0] = session_002[0]._replace(reviewed_name="img_0001.png")
        with self.assertRaisesRegex(intake_session_v1.IntakeError, "reviewed paths"):
            intake_session_v1.validate_session_plans(
                {
                    "session_001": intake_session_v1.SESSION_PLANS["session_001"],
                    "session_002": tuple(session_002),
                }
            )

    def test_duplicate_source_group_ids_across_plans_are_rejected(self) -> None:
        session_002 = list(intake_session_v1.SESSION_PLANS["session_002"])
        session_002[0] = session_002[0]._replace(
            source_group_id="source_group_0001"
        )
        with self.assertRaisesRegex(intake_session_v1.IntakeError, "source group IDs"):
            intake_session_v1.validate_session_plans(
                {
                    "session_001": intake_session_v1.SESSION_PLANS["session_001"],
                    "session_002": tuple(session_002),
                }
            )

    def test_session_003_collision_with_any_prior_plan_is_rejected(self) -> None:
        replacements = (
            ("image_id", "img_0007", "image IDs"),
            ("reviewed_name", "img_0007.png", "reviewed paths"),
            ("source_group_id", "source_group_0007", "source group IDs"),
        )
        for field, value, message in replacements:
            with self.subTest(field=field):
                session_003 = list(intake_session_v1.SESSION_PLANS["session_003"])
                session_003[0] = session_003[0]._replace(**{field: value})
                plans = dict(intake_session_v1.SESSION_PLANS)
                plans["session_003"] = tuple(session_003)
                with self.assertRaisesRegex(intake_session_v1.IntakeError, message):
                    intake_session_v1.validate_session_plans(plans)


class SuccessfulRgbIntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.source_dir, cls.reviewed_dir, cls.manifest, cls.audit = _paths(cls.root)
        _save_sources(cls.source_dir)
        cls.summary = intake_session_v1.run_intake(
            cls.source_dir,
            cls.reviewed_dir,
            cls.manifest,
            cls.audit,
        )
        cls.headers, cls.rows = _read_manifest(cls.manifest)
        cls.audit_data = json.loads(cls.audit.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_successful_six_image_rgb_intake(self) -> None:
        self.assertEqual(self.summary["reviewed_count"], 6)
        self.assertEqual(
            sorted(path.name for path in self.reviewed_dir.iterdir()),
            [f"img_{index:04d}.png" for index in range(1, 7)],
        )
        for path in self.reviewed_dir.iterdir():
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.mode, "RGB")

    def test_reviewed_dimensions_are_exact(self) -> None:
        for path in self.reviewed_dir.iterdir():
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.size, (2287, 1369))

    def test_manifest_has_approved_22_headers(self) -> None:
        self.assertEqual(self.headers, EXPECTED_HEADERS)
        self.assertEqual(len(self.headers), 22)
        self.assertEqual(len(set(self.headers)), 22)

    def test_manifest_has_exactly_six_rows(self) -> None:
        self.assertEqual(len(self.rows), 6)

    def test_manifest_rows_are_pending_review(self) -> None:
        self.assertEqual({row["inclusion_status"] for row in self.rows}, {"needs_review"})

    def test_manifest_rows_have_unchecked_redaction(self) -> None:
        self.assertEqual({row["redaction_checked"] for row in self.rows}, {"false"})

    def test_manifest_rows_have_unassigned_split(self) -> None:
        self.assertEqual({row["split"] for row in self.rows}, {"unassigned"})

    def test_audit_contains_expected_safe_technical_keys(self) -> None:
        self.assertEqual(
            set(self.audit_data),
            {
                "schema_version",
                "capture_session_id",
                "crop_policy",
                "crop_coordinates",
                "expected_source_dimensions",
                "expected_reviewed_dimensions",
                "reviewed_aspect_ratio",
                "generated_at_utc",
                "images",
            },
        )
        self.assertEqual(len(self.audit_data["images"]), 6)
        expected_image_keys = {
            "image_id",
            "label",
            "source_sha256",
            "reviewed_sha256",
            "source_dimensions",
            "reviewed_dimensions",
            "source_mode",
            "reviewed_mode",
            "exact_duplicate_status",
            "manifest_row_index",
        }
        for record in self.audit_data["images"]:
            self.assertEqual(set(record), expected_image_keys)

    def test_audit_contains_no_absolute_source_paths(self) -> None:
        serialized = self.audit.read_text(encoding="utf-8")
        self.assertNotIn(str(self.source_dir.resolve()), serialized)
        self.assertNotIn("source_path", serialized)
        self.assertNotIn("raw_name", serialized)


class SuccessfulSession002IntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.source_dir, cls.reviewed_dir, cls.manifest, cls.audit = _paths(
            cls.root, "session_002"
        )
        cls.plan = intake_session_v1.SESSION_PLANS["session_002"]
        _save_sources(cls.source_dir, plan=cls.plan)
        cls.summary = intake_session_v1.run_intake(
            cls.source_dir,
            cls.reviewed_dir,
            cls.manifest,
            cls.audit,
            session_id="session_002",
        )
        cls.headers, cls.rows = _read_manifest(cls.manifest)
        cls.audit_data = json.loads(cls.audit.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_complete_session_002_intake_succeeds(self) -> None:
        self.assertEqual(self.summary["capture_session_id"], "session_002")
        self.assertEqual(self.summary["reviewed_count"], 6)
        self.assertEqual(
            sorted(path.name for path in self.reviewed_dir.iterdir()),
            [f"img_{index:04d}.png" for index in range(7, 13)],
        )
        self.assertEqual(len(self.rows), 6)
        self.assertEqual(self.audit_data["capture_session_id"], "session_002")
        self.assertEqual(len(self.audit_data["images"]), 6)

    def test_session_002_manifest_uses_expected_ids_paths_and_groups(self) -> None:
        self.assertEqual(
            [row["image_id"] for row in self.rows],
            [f"img_{index:04d}" for index in range(7, 13)],
        )
        self.assertEqual(
            [row["relative_image_path"] for row in self.rows],
            [f"reviewed/img_{index:04d}.png" for index in range(7, 13)],
        )
        self.assertEqual(
            [row["source_group_id"] for row in self.rows],
            [f"source_group_{index:04d}" for index in range(7, 13)],
        )
        self.assertEqual(
            {row["camera_angle_bucket"] for row in self.rows},
            {"north_facing"},
        )

    def test_session_002_audit_has_cross_session_summary(self) -> None:
        self.assertEqual(
            self.audit_data["cross_session_summary"],
            {
                "prior_session_ids_checked": [],
                "prior_accepted_examples_checked": 0,
                "duplicate_image_ids_found": 0,
                "duplicate_reviewed_paths_found": 0,
                "duplicate_source_group_ids_found": 0,
                "exact_duplicate_source_hashes_found": 0,
                "exact_duplicate_reviewed_hashes_found": 0,
            },
        )

    def test_session_002_does_not_overwrite_session_001_reviewed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, manifest, audit = _paths(root, "session_002")
            _save_sources(source_dir, plan=self.plan)
            reviewed_dir.mkdir()
            sentinels = []
            for index in range(1, 7):
                path = reviewed_dir / f"img_{index:04d}.png"
                path.write_bytes(f"session-001-{index}\n".encode())
                sentinels.append((path, path.read_bytes()))

            intake_session_v1.run_intake(
                source_dir,
                reviewed_dir,
                manifest,
                audit,
                session_id="session_002",
            )

            for path, expected in sentinels:
                self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(len(list(reviewed_dir.iterdir())), 12)


class Session002CliTest(unittest.TestCase):
    def test_cli_selects_session_002_and_derives_artifact_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, _, _ = _paths(root, "session_002")
            plan = intake_session_v1.SESSION_PLANS["session_002"]
            _save_sources(source_dir, plan=plan)
            manifests_dir = root / "manifests"

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--session",
                    "session_002",
                    "--source-dir",
                    str(source_dir),
                    "--reviewed-dir",
                    str(reviewed_dir),
                    "--manifests-dir",
                    str(manifests_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["capture_session_id"], "session_002")
            self.assertTrue((manifests_dir / "session_002_draft.csv").is_file())
            self.assertTrue((manifests_dir / "session_002_audit.json").is_file())


class SuccessfulSession003IntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.source_dir, cls.reviewed_dir, cls.manifest, cls.audit = _paths(
            cls.root, "session_003"
        )
        cls.plan = intake_session_v1.SESSION_PLANS["session_003"]
        _save_sources(cls.source_dir, plan=cls.plan)
        cls.summary = intake_session_v1.run_intake(
            cls.source_dir,
            cls.reviewed_dir,
            cls.manifest,
            cls.audit,
            session_id="session_003",
        )
        cls.headers, cls.rows = _read_manifest(cls.manifest)
        cls.audit_data = json.loads(cls.audit.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_complete_session_003_rgb_intake_succeeds(self) -> None:
        self.assertEqual(self.summary["capture_session_id"], "session_003")
        self.assertEqual(self.summary["reviewed_count"], 6)
        self.assertEqual(
            sorted(path.name for path in self.reviewed_dir.iterdir()),
            [f"img_{index:04d}.png" for index in range(13, 19)],
        )
        for path in self.reviewed_dir.iterdir():
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (2287, 1369))
                self.assertEqual(image.mode, "RGB")
                self.assertFalse(image.info)

    def test_session_003_manifest_uses_approved_mapping_and_review_state(self) -> None:
        self.assertEqual(self.headers, EXPECTED_HEADERS)
        self.assertEqual(
            [row["image_id"] for row in self.rows],
            [f"img_{index:04d}" for index in range(13, 19)],
        )
        self.assertEqual(
            [row["relative_image_path"] for row in self.rows],
            [f"reviewed/img_{index:04d}.png" for index in range(13, 19)],
        )
        self.assertEqual(
            [row["label"] for row in self.rows],
            [row[3] for row in EXPECTED_SESSION_003_MAPPING],
        )
        self.assertEqual(
            [row["source_group_id"] for row in self.rows],
            [f"source_group_{index:04d}" for index in range(13, 19)],
        )
        self.assertEqual({row["capture_session_id"] for row in self.rows}, {"session_003"})
        self.assertEqual({row["inclusion_status"] for row in self.rows}, {"needs_review"})
        self.assertEqual({row["redaction_checked"] for row in self.rows}, {"false"})
        self.assertEqual({row["split"] for row in self.rows}, {"unassigned"})

    def test_session_003_audit_has_six_safe_records(self) -> None:
        self.assertEqual(self.audit_data["capture_session_id"], "session_003")
        self.assertEqual(len(self.audit_data["images"]), 6)
        self.assertEqual(
            [record["image_id"] for record in self.audit_data["images"]],
            [f"img_{index:04d}" for index in range(13, 19)],
        )
        self.assertNotIn(str(self.source_dir.resolve()), self.audit.read_text())

    def test_session_003_does_not_overwrite_prior_reviewed_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, manifest, audit = _paths(root, "session_003")
            _save_sources(source_dir, plan=self.plan)
            reviewed_dir.mkdir()
            sentinels = []
            for index in range(1, 13):
                path = reviewed_dir / f"img_{index:04d}.png"
                path.write_bytes(f"prior-session-{index}\n".encode())
                sentinels.append((path, path.read_bytes()))

            intake_session_v1.run_intake(
                source_dir,
                reviewed_dir,
                manifest,
                audit,
                session_id="session_003",
            )

            for path, expected in sentinels:
                self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(len(list(reviewed_dir.iterdir())), 18)


class Session003CliTest(unittest.TestCase):
    def test_cli_selects_session_003_and_derives_artifact_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, _, _ = _paths(root, "session_003")
            plan = intake_session_v1.SESSION_PLANS["session_003"]
            _save_sources(source_dir, plan=plan)
            manifests_dir = root / "manifests"

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--session",
                    "session_003",
                    "--source-dir",
                    str(source_dir),
                    "--reviewed-dir",
                    str(reviewed_dir),
                    "--manifests-dir",
                    str(manifests_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["capture_session_id"], "session_003")
            self.assertTrue((manifests_dir / "session_003_draft.csv").is_file())
            self.assertTrue((manifests_dir / "session_003_audit.json").is_file())


class MixedModeIntakeTest(unittest.TestCase):
    def test_successful_mixed_rgb_rgba_intake_preserves_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir, mixed_modes=True)

            intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)

            modes = []
            for item in intake_session_v1.INTAKE_PLAN:
                with Image.open(reviewed_dir / item.reviewed_name) as image:
                    image.load()
                    modes.append(image.mode)
            self.assertEqual(modes, ["RGB", "RGBA", "RGB", "RGBA", "RGB", "RGBA"])

    def test_successful_session_003_mixed_rgb_rgba_intake_preserves_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, manifest, audit = _paths(root, "session_003")
            plan = intake_session_v1.SESSION_PLANS["session_003"]
            _save_sources(source_dir, mixed_modes=True, plan=plan)

            intake_session_v1.run_intake(
                source_dir,
                reviewed_dir,
                manifest,
                audit,
                session_id="session_003",
            )

            modes = []
            for item in plan:
                with Image.open(reviewed_dir / item.reviewed_name) as image:
                    image.load()
                    modes.append(image.mode)
            self.assertEqual(modes, ["RGB", "RGBA", "RGB", "RGBA", "RGB", "RGBA"])


class IntakeFailureTest(unittest.TestCase):
    def test_missing_source_causes_total_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            (source_dir / intake_session_v1.INTAKE_PLAN[-1].raw_name).unlink()

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "missing"):
                intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_wrong_size_source_causes_total_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            bad_source = source_dir / intake_session_v1.INTAKE_PLAN[2].raw_name
            Image.new("RGB", (2559, 1440), (1, 2, 3)).save(bad_source, format="PNG")

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "wrong dimensions"):
                intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_unsupported_mode_causes_total_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            bad_source = source_dir / intake_session_v1.INTAKE_PLAN[3].raw_name
            Image.new("L", (2560, 1440), 1).save(bad_source, format="PNG")

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "unsupported mode"):
                intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_duplicate_source_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            first = source_dir / intake_session_v1.INTAKE_PLAN[0].raw_name
            second = source_dir / intake_session_v1.INTAKE_PLAN[1].raw_name
            second.write_bytes(first.read_bytes())

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "duplicate source"):
                intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_prior_session_exact_duplicate_source_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(
                Path(temp_dir), "session_002"
            )
            plan = intake_session_v1.SESSION_PLANS["session_002"]
            _save_sources(source_dir, plan=plan)
            prior_hash = hashlib.sha256(
                (source_dir / plan[0].raw_name).read_bytes()
            ).hexdigest()

            with self.assertRaisesRegex(
                intake_session_v1.IntakeError,
                "prior-session source",
            ):
                intake_session_v1.run_intake(
                    source_dir,
                    reviewed_dir,
                    manifest,
                    audit,
                    session_id="session_002",
                    prior_source_hashes=[prior_hash],
                )

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_prior_session_exact_duplicate_reviewed_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, manifest, audit = _paths(root, "session_003")
            plan = intake_session_v1.SESSION_PLANS["session_003"]
            _save_sources(source_dir, plan=plan)
            expected_reviewed = root / "expected_reviewed.png"
            intake_session_v1.crop_v1.crop_image(
                source_dir / plan[0].raw_name,
                expected_reviewed,
            )
            prior_hash = hashlib.sha256(expected_reviewed.read_bytes()).hexdigest()

            with self.assertRaisesRegex(
                intake_session_v1.IntakeError,
                "prior-session reviewed",
            ):
                intake_session_v1.run_intake(
                    source_dir,
                    reviewed_dir,
                    manifest,
                    audit,
                    session_id="session_003",
                    prior_reviewed_hashes=[prior_hash],
                )

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_session_003_missing_or_wrong_size_source_causes_total_failure(self) -> None:
        plan = intake_session_v1.SESSION_PLANS["session_003"]
        for failure in ("missing", "wrong dimensions"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source_dir, reviewed_dir, manifest, audit = _paths(root, "session_003")
                _save_sources(source_dir, plan=plan)
                bad_source = source_dir / plan[-1].raw_name
                if failure == "missing":
                    bad_source.unlink()
                else:
                    Image.new("RGB", (2559, 1440), (1, 2, 3)).save(
                        bad_source,
                        format="PNG",
                    )

                with self.assertRaisesRegex(intake_session_v1.IntakeError, failure):
                    intake_session_v1.run_intake(
                        source_dir,
                        reviewed_dir,
                        manifest,
                        audit,
                        session_id="session_003",
                    )

                self.assertFalse(reviewed_dir.exists())
                self.assertFalse(manifest.exists())
                self.assertFalse(audit.exists())

    def test_session_003_existing_destination_refusal_preserves_prior_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, manifest, audit = _paths(root, "session_003")
            plan = intake_session_v1.SESSION_PLANS["session_003"]
            _save_sources(source_dir, plan=plan)
            reviewed_dir.mkdir()
            existing_image = reviewed_dir / plan[0].reviewed_name
            existing_image.write_bytes(b"reviewed sentinel\n")
            manifest.write_bytes(b"manifest sentinel\n")

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "already exist"):
                intake_session_v1.run_intake(
                    source_dir,
                    reviewed_dir,
                    manifest,
                    audit,
                    session_id="session_003",
                )

            self.assertEqual(existing_image.read_bytes(), b"reviewed sentinel\n")
            self.assertEqual(manifest.read_bytes(), b"manifest sentinel\n")
            self.assertFalse(audit.exists())
            self.assertEqual(sorted(reviewed_dir.iterdir()), [existing_image])

    def test_transactional_session_003_failure_leaves_no_partial_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir, reviewed_dir, manifest, audit = _paths(root, "session_003")
            plan = intake_session_v1.SESSION_PLANS["session_003"]
            _save_sources(source_dir, plan=plan)
            real_crop = intake_session_v1.crop_v1.crop_image
            call_count = 0

            def fail_on_third(*args: object, **kwargs: object) -> object:
                nonlocal call_count
                call_count += 1
                if call_count == 3:
                    raise intake_session_v1.crop_v1.CropError("synthetic crop failure")
                return real_crop(*args, **kwargs)

            with mock.patch.object(
                intake_session_v1.crop_v1,
                "crop_image",
                side_effect=fail_on_third,
            ):
                with self.assertRaisesRegex(intake_session_v1.IntakeError, "crop failed"):
                    intake_session_v1.run_intake(
                        source_dir,
                        reviewed_dir,
                        manifest,
                        audit,
                        session_id="session_003",
                    )

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_session_002_failure_leaves_no_partial_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(
                Path(temp_dir), "session_002"
            )
            plan = intake_session_v1.SESSION_PLANS["session_002"]
            _save_sources(source_dir, plan=plan)
            (source_dir / plan[-1].raw_name).unlink()

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "missing"):
                intake_session_v1.run_intake(
                    source_dir,
                    reviewed_dir,
                    manifest,
                    audit,
                    session_id="session_002",
                )

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_existing_output_refusal_leaves_existing_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            reviewed_dir.mkdir()
            existing_image = reviewed_dir / "img_0001.png"
            existing_image.write_bytes(b"reviewed sentinel\n")
            manifest.write_bytes(b"manifest sentinel\n")

            with self.assertRaisesRegex(intake_session_v1.IntakeError, "already exist"):
                intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)

            self.assertEqual(existing_image.read_bytes(), b"reviewed sentinel\n")
            self.assertEqual(manifest.read_bytes(), b"manifest sentinel\n")
            self.assertFalse(audit.exists())
            self.assertEqual(sorted(reviewed_dir.iterdir()), [existing_image])

    def test_explicit_overwrite_replaces_the_complete_output_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            intake_session_v1.run_intake(source_dir, reviewed_dir, manifest, audit)
            expected_outputs = [
                *(reviewed_dir / item.reviewed_name for item in intake_session_v1.INTAKE_PLAN),
                manifest,
                audit,
            ]
            for path in expected_outputs:
                path.write_bytes(b"existing sentinel\n")

            intake_session_v1.run_intake(
                source_dir,
                reviewed_dir,
                manifest,
                audit,
                overwrite=True,
            )

            for path in expected_outputs[:6]:
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.size, (2287, 1369))
            headers, rows = _read_manifest(manifest)
            self.assertEqual(headers, EXPECTED_HEADERS)
            self.assertEqual(len(rows), 6)
            self.assertEqual(len(json.loads(audit.read_text(encoding="utf-8"))["images"]), 6)

    def test_crop_failure_leaves_no_partial_reviewed_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir, reviewed_dir, manifest, audit = _paths(Path(temp_dir))
            _save_sources(source_dir)
            real_crop = intake_session_v1.crop_v1.crop_image
            call_count = 0

            def fail_on_third(*args: object, **kwargs: object) -> object:
                nonlocal call_count
                call_count += 1
                if call_count == 3:
                    raise intake_session_v1.crop_v1.CropError("synthetic crop failure")
                return real_crop(*args, **kwargs)

            with mock.patch.object(
                intake_session_v1.crop_v1,
                "crop_image",
                side_effect=fail_on_third,
            ):
                with self.assertRaisesRegex(intake_session_v1.IntakeError, "crop failed"):
                    intake_session_v1.run_intake(
                        source_dir,
                        reviewed_dir,
                        manifest,
                        audit,
                    )

            self.assertFalse(reviewed_dir.exists())
            self.assertFalse(manifest.exists())
            self.assertFalse(audit.exists())

    def test_module_import_has_no_cli_or_filesystem_side_effects(self) -> None:
        code = (
            "import importlib.util,sys; "
            f"d={str(SRC_DIR)!r}; p={str(MODULE_PATH)!r}; "
            "sys.path.insert(0,d); "
            "s=importlib.util.spec_from_file_location('intake_import_check',p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=temp_dir,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            self.assertEqual(list(Path(temp_dir).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
