from __future__ import annotations

import csv
import importlib.util
import json
import os
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "assign_grouped_split_v1.py"
)
SPEC = importlib.util.spec_from_file_location("assign_grouped_split_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
assign_grouped_split_v1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assign_grouped_split_v1)

HEADERS = assign_grouped_split_v1.MANIFEST_HEADERS
LABELS = assign_grouped_split_v1.APPROVED_LABELS


def _synthetic_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    index = 1
    for label in LABELS:
        for label_index in range(10):
            if label == "bank_open" and label_index < 2:
                group_id = "synthetic_bank_pair"
            elif label == "dialogue_or_choice_open" and label_index < 2:
                group_id = "synthetic_dialogue_pair"
            elif label == "grand_exchange_open" and label_index < 3:
                group_id = "synthetic_ge_triple"
            elif label == "grand_exchange_open" and label_index < 5:
                group_id = "synthetic_ge_pair"
            else:
                group_id = f"synthetic_group_{index:04d}"
            rows.append(
                {
                    "image_id": f"synthetic_img_{index:04d}",
                    "relative_image_path": f"reviewed/synthetic_img_{index:04d}.png",
                    "label": label,
                    "capture_session_id": "synthetic_session",
                    "source_group_id": group_id,
                    "inclusion_status": "included",
                    "exclusion_reason": "",
                    "resolution": "2287x1369",
                    "aspect_ratio": "2287:1369",
                    "client_type": "runelite",
                    "display_mode": "maximized_windowed",
                    "ui_layout_or_scale": "synthetic_fixed_profile",
                    "crop_policy": "fixed_game_canvas_v1",
                    "camera_angle_bucket": "",
                    "zoom_bucket": "",
                    "location_or_activity_bucket": "",
                    "overlay_notes": "none",
                    "redaction_method": "none",
                    "redaction_checked": "true",
                    "source_consent": "owner_captured",
                    "split": "unassigned",
                    "notes": "",
                }
            )
            index += 1
    return rows


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_manifest(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


class GroupedSplitV1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.input = self.root / "accepted.csv"
        self.output = self.root / "split.csv"
        self.audit = self.root / "split_audit.json"
        self.rows = _synthetic_rows()
        _write_manifest(self.input, self.rows)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _assign(
        self,
        *,
        rows: list[dict[str, str]] | None = None,
        output: Path | None = None,
        audit: Path | None = None,
        dry_run: bool = False,
        seed: int = 4710,
    ) -> dict[str, object]:
        if rows is not None:
            _write_manifest(self.input, rows)
        return assign_grouped_split_v1.assign_grouped_split(
            self.input,
            output or self.output,
            audit or self.audit,
            seed=seed,
            dry_run=dry_run,
        )

    def _installed_rows(self) -> list[dict[str, str]]:
        headers, rows = _read_manifest(self.output)
        self.assertEqual(headers, HEADERS)
        return rows

    def test_valid_grouped_split_has_canonical_output(self) -> None:
        summary = self._assign()
        rows = self._installed_rows()
        self.assertEqual(summary["row_count"], 60)
        self.assertEqual(len(rows), 60)
        self.assertTrue(self.audit.is_file())

    def test_exact_overall_36_12_12_totals(self) -> None:
        self._assign()
        counts = Counter(row["split"] for row in self._installed_rows())
        self.assertEqual(
            counts, Counter({"train": 36, "validation": 12, "test": 12})
        )

    def test_exact_per_class_6_2_2_counts(self) -> None:
        self._assign()
        rows = self._installed_rows()
        for label in LABELS:
            counts = Counter(row["split"] for row in rows if row["label"] == label)
            self.assertEqual(
                counts, Counter({"train": 6, "validation": 2, "test": 2})
            )

    def test_seed_4710_is_deterministic(self) -> None:
        first = self._assign(dry_run=True)
        second = self._assign(
            output=self.root / "second.csv",
            audit=self.root / "second.json",
            dry_run=True,
        )
        self.assertEqual(first["output_sha256"], second["output_sha256"])
        self.assertEqual(
            first["per_class_split_counts"], second["per_class_split_counts"]
        )

    def test_algorithm_rejects_a_nonfixed_seed(self) -> None:
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "requires seed=4710"
        ):
            self._assign(seed=7, dry_run=True)

    def test_dry_run_creates_no_output(self) -> None:
        summary = self._assign(dry_run=True)
        self.assertTrue(summary["dry_run"])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.audit.exists())

    def test_non_singleton_groups_stay_intact_and_in_training(self) -> None:
        self._assign()
        rows = self._installed_rows()
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            grouped[row["source_group_id"]].append(row)
        non_singletons = {
            group_id: group_rows
            for group_id, group_rows in grouped.items()
            if len(group_rows) > 1
        }
        self.assertEqual(len(non_singletons), 4)
        self.assertTrue(
            all(
                {row["split"] for row in group_rows} == {"train"}
                for group_rows in non_singletons.values()
            )
        )

    def test_validation_and_test_use_only_singleton_groups(self) -> None:
        self._assign()
        rows = self._installed_rows()
        group_sizes = Counter(row["source_group_id"] for row in rows)
        evaluation_rows = [
            row for row in rows if row["split"] in {"validation", "test"}
        ]
        self.assertTrue(
            all(group_sizes[row["source_group_id"]] == 1 for row in evaluation_rows)
        )

    def test_blank_source_group_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[0]["source_group_id"] = ""
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "nonblank source-group"
        ):
            self._assign(rows=rows)

    def test_mixed_label_source_group_is_rejected(self) -> None:
        rows = _synthetic_rows()
        bank_group = next(
            row["source_group_id"] for row in rows if row["label"] == "bank_open"
        )
        normal_row = next(
            row for row in rows if row["label"] == "normal_gameplay"
        )
        normal_row["source_group_id"] = bank_group
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "mixes accepted labels"
        ):
            self._assign(rows=rows)

    def test_already_assigned_input_split_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[0]["split"] = "train"
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "split=unassigned"
        ):
            self._assign(rows=rows)

    def test_duplicate_image_id_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[1]["image_id"] = rows[0]["image_id"]
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "duplicate image IDs"
        ):
            self._assign(rows=rows)

    def test_duplicate_reviewed_path_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[1]["relative_image_path"] = rows[0]["relative_image_path"]
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "duplicate reviewed paths"
        ):
            self._assign(rows=rows)

    def test_unsupported_label_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[0]["label"] = "unsupported"
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "unsupported label"
        ):
            self._assign(rows=rows)

    def test_non_included_row_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[0]["inclusion_status"] = "excluded"
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "must be included"
        ):
            self._assign(rows=rows)

    def test_privacy_unchecked_row_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[0]["redaction_checked"] = "false"
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "privacy checked"
        ):
            self._assign(rows=rows)

    def test_non_owner_captured_row_is_rejected(self) -> None:
        rows = _synthetic_rows()
        rows[0]["source_consent"] = "third_party"
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "owner captured"
        ):
            self._assign(rows=rows)

    def test_impossible_exact_split_is_rejected(self) -> None:
        rows = _synthetic_rows()
        bank_rows = [row for row in rows if row["label"] == "bank_open"]
        for pair_index in range(4):
            for row in bank_rows[pair_index * 2 : pair_index * 2 + 2]:
                row["source_group_id"] = f"synthetic_impossible_pair_{pair_index}"
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "exact preferred split"
        ):
            self._assign(rows=rows)

    def test_destination_overwrite_is_rejected(self) -> None:
        self.output.write_text("sentinel\n", encoding="utf-8")
        with self.assertRaisesRegex(
            assign_grouped_split_v1.GroupedSplitError, "already exists"
        ):
            self._assign()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "sentinel\n")
        self.assertFalse(self.audit.exists())

    def test_output_differs_only_in_split(self) -> None:
        self._assign()
        output_rows = self._installed_rows()
        for before, after in zip(self.rows, output_rows, strict=True):
            self.assertNotEqual(before["split"], after["split"])
            self.assertEqual(
                {key: before[key] for key in HEADERS if key != "split"},
                {key: after[key] for key in HEADERS if key != "split"},
            )

    def test_audit_accurately_matches_output(self) -> None:
        summary = self._assign()
        output_rows = self._installed_rows()
        audit = json.loads(self.audit.read_text(encoding="utf-8"))
        self.assertEqual(audit["random_seed"], 4710)
        self.assertEqual(audit["algorithm_version"], "grouped_split_v1")
        self.assertEqual(audit["output_manifest"]["sha256"], summary["output_sha256"])
        self.assertEqual(
            audit["split_counts"],
            dict(sorted(Counter(row["split"] for row in output_rows).items())),
        )
        self.assertTrue(audit["no_source_group_crosses_splits"])
        expected_ids = {
            split: [
                row["image_id"] for row in output_rows if row["split"] == split
            ]
            for split in ("train", "validation", "test")
        }
        self.assertEqual(audit["image_ids_by_split"], expected_ids)

    def test_install_failure_leaves_no_partial_files(self) -> None:
        real_link = os.link
        calls = 0

        def fail_second_link(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic audit-install failure")
            real_link(source, destination)

        with mock.patch.object(
            assign_grouped_split_v1.os, "link", side_effect=fail_second_link
        ):
            with self.assertRaisesRegex(
                assign_grouped_split_v1.GroupedSplitError,
                "could not be installed atomically",
            ):
                self._assign()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.audit.exists())
        self.assertEqual(list(self.root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
