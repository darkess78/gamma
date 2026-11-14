from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from collections import Counter
from pathlib import Path
from unittest import mock

import joblib
import numpy as np
from PIL import Image
from sklearn.exceptions import ConvergenceWarning
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "model_baseline_v1.py"
)
SPEC = importlib.util.spec_from_file_location("model_baseline_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)

HEADERS = baseline.MANIFEST_HEADERS
LABELS = baseline.LABEL_ORDER


def _rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    index = 19
    for label in LABELS:
        for label_index in range(10):
            if label == "bank_open" and label_index < 2:
                group = "synthetic_group_bank_pair_1"
            elif label == "dialogue_or_choice_open" and label_index < 2:
                group = "synthetic_group_dialogue_pair"
            elif label == "grand_exchange_open" and label_index < 3:
                group = "synthetic_group_ge_triple"
            elif label == "grand_exchange_open" and label_index < 5:
                group = "synthetic_group_ge_pair"
            elif label == "shop_open" and label_index < 2:
                group = "synthetic_group_shop_pair"
            else:
                group = f"synthetic_group_{index:04d}"
            if label_index < 6:
                split = "train"
            elif label_index < 8:
                split = "validation"
            else:
                split = "test"
            rows.append(
                {
                    "image_id": f"img_{index:04d}",
                    "relative_image_path": f"reviewed/img_{index:04d}.png",
                    "label": label,
                    "capture_session_id": "session_004",
                    "source_group_id": group,
                    "inclusion_status": "included",
                    "exclusion_reason": "",
                    "resolution": "2287x1369",
                    "aspect_ratio": "2287:1369",
                    "client_type": "runelite",
                    "display_mode": "maximized_windowed",
                    "ui_layout_or_scale": "resizable_classic_stretched_90pct",
                    "crop_policy": "fixed_game_canvas_v1",
                    "camera_angle_bucket": "",
                    "zoom_bucket": "",
                    "location_or_activity_bucket": "",
                    "overlay_notes": "fixed_profile_v1",
                    "redaction_method": "none",
                    "redaction_checked": "true",
                    "source_consent": "owner_captured",
                    "split": split,
                    "notes": "synthetic",
                }
            )
            index += 1
    assert len({row["source_group_id"] for row in rows}) == 54
    return rows


def _write_manifest(path: Path, rows: list[dict[str, str]], headers=HEADERS) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=headers, lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_synthetic_images(data_root: Path, rows: list[dict[str, str]]) -> None:
    reviewed = data_root / "reviewed"
    reviewed.mkdir(parents=True)
    label_colors = {
        label: (
            20 + label_index * 35,
            230 - label_index * 25,
            40 + label_index * 27,
        )
        for label_index, label in enumerate(LABELS)
    }
    for index, row in enumerate(rows):
        base = label_colors[row["label"]]
        color = (base[0], base[1], min(255, base[2] + index % 5))
        Image.new("RGB", (2287, 1369), color).save(
            data_root / row["relative_image_path"],
            format="PNG",
            compress_level=9,
        )


def _write_audit(
    path: Path,
    split_manifest: Path,
    accepted_manifest: Path,
    rows: list[dict[str, str]],
) -> None:
    split_counts = Counter(row["split"] for row in rows)
    class_counts = Counter(row["label"] for row in rows)
    document = {
        "random_seed": 4710,
        "input_manifest": {
            "path": accepted_manifest.name,
            "sha256": baseline.sha256_file(accepted_manifest),
        },
        "output_manifest": {
            "path": split_manifest.name,
            "sha256": baseline.sha256_file(split_manifest),
        },
        "class_counts": {key: class_counts[key] for key in sorted(class_counts)},
        "split_counts": {key: split_counts[key] for key in sorted(split_counts)},
        "per_class_split_counts": {
            label: {
                key: value
                for key, value in sorted(
                    Counter(
                        row["split"] for row in rows if row["label"] == label
                    ).items()
                )
            }
            for label in LABELS
        },
        "source_group_count": 54,
        "no_source_group_crosses_splits": True,
        "image_ids_by_split": {
            split: sorted(
                row["image_id"] for row in rows if row["split"] == split
            )
            for split in ("train", "validation", "test")
        },
        "capture_session_ids": ["session_004"],
    }
    path.write_text(json.dumps(document), encoding="utf-8")


class SyntheticDataset:
    def __init__(self, root: Path, *, images: bool = True) -> None:
        self.root = root
        self.data_root = root / "data"
        self.manifests = self.data_root / "manifests"
        self.manifests.mkdir(parents=True)
        self.rows = _rows()
        self.accepted = self.manifests / "accepted.csv"
        accepted_rows = [dict(row, split="unassigned") for row in self.rows]
        _write_manifest(self.accepted, accepted_rows)
        self.split = self.manifests / "synthetic_split_v1.csv"
        _write_manifest(self.split, self.rows)
        self.audit = self.manifests / "synthetic_split_v1_audit.json"
        _write_audit(self.audit, self.split, self.accepted, self.rows)
        if images:
            _write_synthetic_images(self.data_root, self.rows)
        else:
            (self.data_root / "reviewed").mkdir()


def _build_frozen_selection_artifacts(
    root: Path, dataset: SyntheticDataset
) -> Path:
    output = root / "selection"
    for relative in ("features", "models", "metrics", "plots"):
        (output / relative).mkdir(parents=True)
    train_rows = sorted(
        (row for row in dataset.rows if row["split"] == "train"),
        key=lambda row: row["image_id"],
    )
    validation_rows = sorted(
        (row for row in dataset.rows if row["split"] == "validation"),
        key=lambda row: row["image_id"],
    )
    X_train = np.zeros((36, baseline.FEATURE_DIMENSION), dtype=np.float32)
    X_validation = np.zeros((12, baseline.FEATURE_DIMENSION), dtype=np.float32)
    for index, row in enumerate(train_rows):
        X_train[index, LABELS.index(row["label"])] = 1.0
    for index, row in enumerate(validation_rows):
        X_validation[index, LABELS.index(row["label"])] = 1.0
    arrays = {
        "X_train": X_train,
        "y_train": np.asarray([row["label"] for row in train_rows], dtype=np.str_),
        "train_image_ids": np.asarray(
            [row["image_id"] for row in train_rows], dtype=np.str_
        ),
        "X_validation": X_validation,
        "y_validation": np.asarray(
            [row["label"] for row in validation_rows], dtype=np.str_
        ),
        "validation_image_ids": np.asarray(
            [row["image_id"] for row in validation_rows], dtype=np.str_
        ),
        "label_order": np.asarray(LABELS, dtype=np.str_),
        "feature_shape": np.asarray(baseline.FEATURE_SHAPE, dtype=np.int64),
        "preprocessing_version": np.asarray(
            baseline.PREPROCESSING_VERSION, dtype=np.str_
        ),
    }
    feature_path = output / "features/train_validation_features.npz"
    baseline.write_deterministic_npz(feature_path, arrays)

    small_X = X_train[:, : len(LABELS)]
    model = Pipeline(
        [
            ("standard_scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    C=0.01,
                    solver="lbfgs",
                    max_iter=5000,
                    tol=1e-4,
                    class_weight=None,
                    random_state=4710,
                ),
            ),
        ]
    )
    model.fit(small_X, arrays["y_train"])
    model.gamma_baseline_version_ = baseline.BASELINE_VERSION
    model.gamma_preprocessing_version_ = baseline.PREPROCESSING_VERSION
    model.gamma_training_image_ids_ = tuple(arrays["train_image_ids"].tolist())
    model.gamma_fit_scope_ = "training rows only; no validation refit"
    model.gamma_selected_identifier_ = baseline.FROZEN_SELECTED_IDENTIFIER
    model.gamma_selected_C_ = baseline.FROZEN_SELECTED_C
    model_path = output / "models/selected_train_only_model.joblib"
    joblib.dump(model, model_path, compress=3)

    y_validation = arrays["y_validation"]
    predictions = np.asarray(
        (
            "normal_gameplay",
            "grand_exchange_open",
            "bank_open",
            "bank_open",
            "dialogue_or_choice_open",
            "shop_open",
            "shop_open",
            "shop_open",
            "grand_exchange_open",
            "grand_exchange_open",
            "dialogue_or_choice_open",
            "shop_open",
        )
    )
    selected_metrics = baseline.calculate_metrics(
        y_validation, predictions, arrays["validation_image_ids"]
    )
    candidates = []
    for specification in baseline.candidate_specifications():
        candidates.append(
            {
                **specification,
                "hyperparameters": {},
                "status": "success",
                "warnings": [],
                "metrics": selected_metrics,
            }
        )
    validation_document = {
        "schema_version": "1.0",
        "baseline_version": baseline.BASELINE_VERSION,
        "label_order": list(LABELS),
        "train_image_ids": arrays["train_image_ids"].tolist(),
        "validation_image_ids": arrays["validation_image_ids"].tolist(),
        "test_images_decoded_or_evaluated": False,
        "preprocessing": baseline.preprocessing_configuration(),
        "candidate_grid": baseline.candidate_specifications(),
        "learned_candidates": candidates,
        "selected_learned_model_identifier": baseline.FROZEN_SELECTED_IDENTIFIER,
        "selected_C": baseline.FROZEN_SELECTED_C,
        "selection_rule": baseline.SELECTION_RULE,
        "final_test_evaluation": "pending",
    }
    results_path = output / "metrics/validation_results.json"
    baseline._write_json(results_path, validation_document)
    plot_path = output / "plots/validation_confusion_matrix.png"
    baseline._plot_confusion_matrix(plot_path, selected_metrics["confusion_matrix"])

    audit_document = {
        "schema_version": "1.0",
        "baseline_version": baseline.BASELINE_VERSION,
        "input_manifest": {
            "path": dataset.split.as_posix(),
            "sha256": baseline.sha256_file(dataset.split),
        },
        "split_audit": {
            "path": dataset.audit.as_posix(),
            "sha256": baseline.sha256_file(dataset.audit),
        },
        "random_seed": 4710,
        "preprocessing_version": baseline.PREPROCESSING_VERSION,
        "counts": {
            "train": 36,
            "validation": 12,
            "per_class": {
                label: {"train": 6, "validation": 2} for label in LABELS
            },
        },
        "feature_array_hashes": {
            name: {
                "sha256_uncompressed_c_order_bytes": baseline.array_sha256(
                    arrays[name]
                ),
                "dtype": str(arrays[name].dtype),
                "shape": list(arrays[name].shape),
            }
            for name in baseline.FEATURE_ARRAY_NAMES
        },
        "test_exclusion": {
            "test_images_opened": 0,
            "test_images_decoded": False,
            "test_features_created": 0,
            "test_rows_evaluated": 0,
        },
        "output_artifacts": {
            "features": {"sha256": baseline.sha256_file(feature_path)},
            "selected_model": {"sha256": baseline.sha256_file(model_path)},
            "validation_results": {"sha256": baseline.sha256_file(results_path)},
            "confusion_matrix": {"sha256": baseline.sha256_file(plot_path)},
        },
        "learned_candidates_attempted": 8,
        "successful_learned_candidates": 8,
        "failed_learned_candidates": 0,
        "selected_configuration": {
            "identifier": baseline.FROZEN_SELECTED_IDENTIFIER,
            "family": "logistic_regression",
            "C": baseline.FROZEN_SELECTED_C,
        },
        "final_test_evaluation": "pending",
    }
    baseline._write_json(
        output / "metrics/selection_audit.json", audit_document
    )
    return output


def _frozen_contract_patch(
    dataset: SyntheticDataset, selection: Path
) -> mock._patch:
    hashes = {
        relative: baseline.sha256_file(selection / relative)
        for relative in baseline.FROZEN_SELECTION_ARTIFACT_HASHES
    }
    return mock.patch.multiple(
        baseline,
        FROZEN_SPLIT_MANIFEST_SHA256=baseline.sha256_file(dataset.split),
        FROZEN_SPLIT_AUDIT_SHA256=baseline.sha256_file(dataset.audit),
        FROZEN_SELECTION_ARTIFACT_HASHES=hashes,
        _verify_final_output_policy=_synthetic_final_output_policy,
    )


def _synthetic_final_output_policy(output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise baseline.BaselineError(
            f"final-test output directory already exists: {output}"
        )
    if not output.parent.is_dir():
        raise baseline.BaselineError(
            f"final-test output parent directory does not exist: {output.parent}"
        )
    if output.name != "final_test":
        raise baseline.BaselineError(
            "final-test output directory must be named final_test"
        )
    return None


class ManifestValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dataset = SyntheticDataset(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _rewrite(self, rows: list[dict[str, str]], headers=HEADERS) -> Path:
        path = self.dataset.manifests / "case.csv"
        _write_manifest(path, rows, headers)
        return path

    def test_valid_canonical_manifest_and_audit(self) -> None:
        result = baseline.validate_manifest(self.dataset.split)
        self.assertEqual(result["split_counts"], {"test": 12, "train": 36, "validation": 12})
        self.assertEqual(result["source_group_count"], 54)
        self.assertIsNotNone(result["audit"])

    def test_validation_does_not_decode_any_image(self) -> None:
        with mock.patch.object(
            baseline.Image, "open", side_effect=AssertionError("decoded")
        ):
            baseline.validate_manifest(self.dataset.split)

    def test_wrong_schema_rejected(self) -> None:
        with self.assertRaisesRegex(baseline.BaselineError, "22-column"):
            baseline.validate_manifest(
                self._rewrite(self.dataset.rows, HEADERS[:-1])
            )

    def test_duplicate_image_id_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[1]["image_id"] = rows[0]["image_id"]
        with self.assertRaisesRegex(baseline.BaselineError, "duplicate image IDs"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_duplicate_reviewed_path_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[1]["relative_image_path"] = rows[0]["relative_image_path"]
        with self.assertRaisesRegex(baseline.BaselineError, "duplicate reviewed"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_unsupported_label_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[0]["label"] = "unsupported"
        with self.assertRaisesRegex(baseline.BaselineError, "unsupported label"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_non_included_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[0]["inclusion_status"] = "needs_review"
        with self.assertRaisesRegex(baseline.BaselineError, "inclusion_status"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_privacy_unchecked_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[0]["redaction_checked"] = "false"
        with self.assertRaisesRegex(baseline.BaselineError, "redaction_checked"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_non_owner_captured_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[0]["source_consent"] = "unknown"
        with self.assertRaisesRegex(baseline.BaselineError, "owner_captured"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_blank_source_group_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[0]["source_group_id"] = ""
        with self.assertRaisesRegex(baseline.BaselineError, "nonblank"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_source_group_crossing_splits_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[6]["source_group_id"] = rows[0]["source_group_id"]
        with self.assertRaisesRegex(baseline.BaselineError, "crosses splits"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_wrong_per_class_counts_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[0]["label"] = LABELS[1]
        with self.assertRaisesRegex(baseline.BaselineError, "10 rows per class"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_wrong_total_split_counts_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        rows[8]["split"] = "validation"
        with self.assertRaisesRegex(baseline.BaselineError, "36/12/12"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_missing_image_path_rejected(self) -> None:
        missing = self.dataset.data_root / self.dataset.rows[0]["relative_image_path"]
        missing.unlink()
        with self.assertRaisesRegex(baseline.BaselineError, "does not exist"):
            baseline.validate_manifest(self.dataset.split)

    def test_historical_image_ids_rejected(self) -> None:
        rows = [dict(row) for row in self.dataset.rows]
        old_path = self.dataset.data_root / rows[0]["relative_image_path"]
        new_path = old_path.with_name("img_0001.png")
        old_path.rename(new_path)
        rows[0]["image_id"] = "img_0001"
        rows[0]["relative_image_path"] = "reviewed/img_0001.png"
        with self.assertRaisesRegex(baseline.BaselineError, "img_0019 through img_0078"):
            baseline.validate_manifest(self._rewrite(rows))

    def test_split_audit_mismatch_rejected(self) -> None:
        audit = json.loads(self.dataset.audit.read_text(encoding="utf-8"))
        audit["random_seed"] = 1
        self.dataset.audit.write_text(json.dumps(audit), encoding="utf-8")
        with self.assertRaisesRegex(baseline.BaselineError, "random seed"):
            baseline.validate_manifest(self.dataset.split)

    def test_accepted_manifest_must_remain_unassigned(self) -> None:
        rows = [dict(row, split="unassigned") for row in self.dataset.rows]
        rows[0]["split"] = "train"
        _write_manifest(self.dataset.accepted, rows)
        _write_audit(
            self.dataset.audit,
            self.dataset.split,
            self.dataset.accepted,
            self.dataset.rows,
        )
        with self.assertRaisesRegex(baseline.BaselineError, "remain 60 unassigned"):
            baseline.validate_manifest(self.dataset.split)


class PreprocessingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _image(
        self,
        name: str = "image.png",
        *,
        size: tuple[int, int] = (2287, 1369),
        mode: str = "RGB",
        color=(255, 0, 0),
        format: str = "PNG",
    ) -> Path:
        path = self.root / name
        Image.new(mode, size, color).save(path, format=format)
        return path

    def test_canvas_shape_centered_padding_rgb_order_and_range(self) -> None:
        feature = baseline.load_preprocessed_image(
            self._image(color=(255, 128, 64))
        )
        canvas = feature.reshape(baseline.FEATURE_SHAPE)
        self.assertEqual(feature.shape, (18432,))
        self.assertEqual(feature.dtype, np.float32)
        self.assertTrue(np.all((feature >= 0) & (feature <= 1)))
        self.assertTrue(np.all(canvas[:3] == 0))
        self.assertTrue(np.all(canvas[60:] == 0))
        np.testing.assert_allclose(
            canvas[3, 0], np.array([1.0, 128 / 255, 64 / 255], dtype=np.float32)
        )

    def test_aspect_ratio_fit_is_96_by_57_and_not_cropped(self) -> None:
        path = self._image(color=(0, 0, 0))
        with Image.open(path) as source:
            source.paste((255, 0, 0), (0, 0, 100, 1369))
            source.paste((0, 255, 0), (2187, 0, 2287, 1369))
            source.save(path)
        canvas = baseline.load_preprocessed_image(path).reshape(
            baseline.FEATURE_SHAPE
        )
        nonblack = np.argwhere(np.any(canvas != 0, axis=2))
        self.assertTrue(np.any(nonblack[:, 1] == 0))
        self.assertTrue(np.any(nonblack[:, 1] == 95))
        self.assertGreaterEqual(nonblack[:, 0].min(), 3)
        self.assertLessEqual(nonblack[:, 0].max(), 59)

    def test_deterministic_flattened_output(self) -> None:
        path = self._image(color=(9, 18, 27))
        first = baseline.load_preprocessed_image(path)
        second = baseline.load_preprocessed_image(path)
        np.testing.assert_array_equal(first, second)

    def test_wrong_dimensions_rejected(self) -> None:
        with self.assertRaisesRegex(baseline.BaselineError, "dimensions"):
            baseline.load_preprocessed_image(self._image(size=(100, 100)))

    def test_wrong_mode_rejected(self) -> None:
        with self.assertRaisesRegex(baseline.BaselineError, "not RGB"):
            baseline.load_preprocessed_image(
                self._image(mode="L", color=12)
            )


class FeatureModelUnitTest(unittest.TestCase):
    def test_exact_candidate_grid_and_order(self) -> None:
        specs = baseline.candidate_specifications()
        self.assertEqual(len(specs), 8)
        self.assertEqual(
            [(item["family"], item["C"]) for item in specs],
            [
                (family, c_value)
                for family in ("logistic_regression", "linear_svm")
                for c_value in (0.01, 0.1, 1.0, 10.0)
            ],
        )
        logistic = baseline._candidate_pipeline(specs[0])["classifier"]
        svm = baseline._candidate_pipeline(specs[4])["classifier"]
        self.assertEqual(logistic.solver, "lbfgs")
        self.assertEqual(logistic.max_iter, 5000)
        self.assertEqual(logistic.tol, 1e-4)
        self.assertIsNone(logistic.class_weight)
        self.assertEqual(logistic.random_state, 4710)
        self.assertEqual(svm.dual, "auto")
        self.assertEqual(svm.max_iter, 10000)
        self.assertEqual(svm.tol, 1e-4)
        self.assertIsNone(svm.class_weight)
        self.assertEqual(svm.random_state, 4710)

    def test_train_only_standard_scaler_fitting(self) -> None:
        X_train = np.array([[0.0, 0.0], [2.0, 2.0]] * 3, dtype=np.float32)
        y_train = np.asarray(LABELS)
        X_validation = np.full((6, 2), 1000.0, dtype=np.float32)
        y_validation = np.asarray(LABELS)
        ids = np.asarray([f"img_{index:04d}" for index in range(1, 7)])
        with mock.patch.object(
            baseline, "candidate_specifications", return_value=[
                {
                    "identifier": "logistic_regression_C_1.0",
                    "family": "logistic_regression",
                    "family_name": "Logistic Regression",
                    "C": 1.0,
                }
            ]
        ):
            _, models = baseline.fit_learned_candidates(
                X_train, y_train, X_validation, y_validation, ids
            )
        scaler = models["logistic_regression_C_1.0"]["standard_scaler"]
        np.testing.assert_allclose(scaler.mean_, [1.0, 1.0])
        self.assertEqual(scaler.n_samples_seen_, 6)

    def test_convergence_warning_marks_candidate_failed(self) -> None:
        class WarningPipeline:
            def get_params(self, deep=True):
                return {}

            def fit(self, X, y):
                warnings.warn("synthetic", ConvergenceWarning)
                return self

        specification = {
            "identifier": "logistic_regression_C_1.0",
            "family": "logistic_regression",
            "family_name": "Logistic Regression",
            "C": 1.0,
        }
        with (
            mock.patch.object(
                baseline, "candidate_specifications", return_value=[specification]
            ),
            mock.patch.object(
                baseline, "_candidate_pipeline", return_value=WarningPipeline()
            ),
        ):
            results, models = baseline.fit_learned_candidates(
                np.zeros((2, 2)),
                np.asarray(["a", "b"]),
                np.zeros((1, 2)),
                np.asarray(["a"]),
                np.asarray(["img_1"]),
            )
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("ConvergenceWarning", results[0]["failure_reason"])
        self.assertEqual(models, {})

    def test_metric_label_order_zero_division_and_unknown_counts(self) -> None:
        truth = np.asarray(LABELS + ("unknown_other",))
        predictions = np.asarray(
            (
                "normal_gameplay",
                "bank_open",
                "dialogue_or_choice_open",
                "shop_open",
                "grand_exchange_open",
                "normal_gameplay",
                "unknown_other",
            )
        )
        metrics = baseline.calculate_metrics(
            truth, predictions, [f"img_{index}" for index in range(7)]
        )
        self.assertEqual(list(metrics["per_class"]), list(LABELS))
        self.assertEqual(len(metrics["confusion_matrix"]), 6)
        unknown = metrics["unknown_other_analysis"]
        self.assertEqual(unknown["actual_unknown_other"], 2)
        self.assertEqual(unknown["unknown_other_false_rejections"], 1)
        self.assertEqual(unknown["unknown_other_false_acceptances"], 0)

    def test_model_selection_tie_break_order(self) -> None:
        def item(identifier, family, c_value, f1=0.8, accuracy=0.8):
            return {
                "identifier": identifier,
                "family": family,
                "C": c_value,
                "status": "success",
                "metrics": {"macro_f1": f1, "accuracy": accuracy},
            }

        candidates = [
            item("linear_svm_C_0.1", "linear_svm", 0.1),
            item("logistic_regression_C_0.1", "logistic_regression", 0.1),
            item("logistic_regression_C_1.0", "logistic_regression", 1.0),
            item("better_accuracy", "linear_svm", 10.0, f1=0.8, accuracy=0.9),
            item("best_f1", "linear_svm", 10.0, f1=0.9, accuracy=0.1),
        ]
        self.assertEqual(
            baseline.select_learned_candidate(candidates)["identifier"], "best_f1"
        )
        self.assertEqual(
            baseline.select_learned_candidate(candidates[:3])["identifier"],
            "logistic_regression_C_0.1",
        )

    def test_nearest_template_cosine_and_deterministic_tie(self) -> None:
        train = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        labels = np.asarray(["bank_open", "normal_gameplay", "shop_open"])
        ids = np.asarray(["img_0020", "img_0019", "img_0021"])
        validation = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        result = baseline.nearest_template_comparison(
            train,
            labels,
            ids,
            validation,
            np.asarray(["normal_gameplay", "shop_open"]),
            np.asarray(["img_0070", "img_0071"]),
        )
        self.assertEqual(
            result["matches"][0]["nearest_training_image_id"], "img_0019"
        )
        self.assertEqual(result["matches"][0]["predicted_label"], "normal_gameplay")
        self.assertAlmostEqual(result["matches"][1]["cosine_score"], 1.0)


class CliSafetyTest(unittest.TestCase):
    def test_select_without_exclude_test_rejected(self) -> None:
        parser = baseline._parser()
        args = parser.parse_args(
            ["select", "--manifest", "m.csv", "--output-dir", "model_baseline_v1"]
        )
        with self.assertRaisesRegex(baseline.BaselineError, "--exclude-test"):
            baseline.run_selection(
                args.manifest, args.output_dir, exclude_test=args.exclude_test
            )

    def test_unsupported_seed_rejected(self) -> None:
        with self.assertRaisesRegex(baseline.BaselineError, "seed=4710"):
            baseline.run_selection(
                "m.csv",
                "model_baseline_v1",
                seed=1,
                exclude_test=True,
            )

    def test_destination_overwrite_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "model_baseline_v1"
            output.mkdir()
            with self.assertRaisesRegex(baseline.BaselineError, "already exists"):
                baseline.run_selection("m.csv", output, exclude_test=True)

    def test_failure_cleanup_removes_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "model_baseline_v1"
            with (
                mock.patch.object(
                    baseline, "validate_manifest", side_effect=RuntimeError("synthetic")
                ),
            ):
                with self.assertRaises(RuntimeError):
                    baseline.run_selection("m.csv", output, exclude_test=True)
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".model_baseline_v1.staging.*")), [])


class EndToEndSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.dataset = SyntheticDataset(cls.root)
        cls.output = cls.root / "model_baseline_v1"
        cls.opened: list[str] = []
        original = baseline.load_preprocessed_image

        def recording_loader(path: Path) -> np.ndarray:
            cls.opened.append(path.stem)
            return original(path)

        def fast_candidate_fit(
            X_train,
            y_train,
            X_validation,
            y_validation,
            validation_image_ids,
        ):
            results = []
            models = {}
            for specification in baseline.candidate_specifications():
                pipeline = Pipeline(
                    [
                        ("standard_scaler", StandardScaler()),
                        ("classifier", DummyClassifier(strategy="most_frequent")),
                    ]
                )
                pipeline.fit(X_train, y_train)
                predictions = pipeline.predict(X_validation)
                results.append(
                    {
                        **specification,
                        "hyperparameters": {
                            "pipeline": [
                                "StandardScaler",
                                specification["family_name"],
                            ],
                            "scaler_fit_scope": "training rows only",
                            "C": specification["C"],
                        },
                        "status": "success",
                        "warnings": [],
                        "metrics": baseline.calculate_metrics(
                            y_validation,
                            predictions,
                            validation_image_ids,
                        ),
                    }
                )
                models[specification["identifier"]] = pipeline
            return results, models

        with (
            mock.patch.object(
                baseline, "load_preprocessed_image", side_effect=recording_loader
            ),
            mock.patch.object(baseline, "_verify_output_policy", return_value=None),
            mock.patch.object(
                baseline,
                "fit_learned_candidates",
                side_effect=fast_candidate_fit,
            ),
        ):
            cls.summary = baseline.run_selection(
                cls.dataset.split,
                cls.output,
                exclude_test=True,
            )
        cls.validation = json.loads(
            (cls.output / "metrics/validation_results.json").read_text(
                encoding="utf-8"
            )
        )
        cls.audit = json.loads(
            (cls.output / "metrics/selection_audit.json").read_text(
                encoding="utf-8"
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_select_opens_exactly_train_and_validation_never_test(self) -> None:
        expected = {
            row["image_id"]
            for row in self.dataset.rows
            if row["split"] in {"train", "validation"}
        }
        test_ids = {
            row["image_id"] for row in self.dataset.rows if row["split"] == "test"
        }
        self.assertEqual(len(self.opened), 48)
        self.assertEqual(set(self.opened), expected)
        self.assertTrue(test_ids.isdisjoint(self.opened))

    def test_feature_artifact_has_no_test_data(self) -> None:
        with np.load(
            self.output / "features/train_validation_features.npz",
            allow_pickle=False,
        ) as archive:
            self.assertEqual(archive["X_train"].shape, (36, 18432))
            self.assertEqual(archive["X_validation"].shape, (12, 18432))
            self.assertEqual(archive["X_train"].dtype, np.float32)
            ids = set(archive["train_image_ids"]) | set(
                archive["validation_image_ids"]
            )
        test_ids = {
            row["image_id"] for row in self.dataset.rows if row["split"] == "test"
        }
        self.assertTrue(test_ids.isdisjoint(ids))

    def test_eight_candidates_nearest_and_complete_metrics(self) -> None:
        candidates = self.validation["learned_candidates"]
        self.assertEqual(len(candidates), 8)
        for candidate in candidates:
            if candidate["status"] == "success":
                self.assertIn("macro_f1", candidate["metrics"])
                self.assertEqual(len(candidate["metrics"]["confusion_matrix"]), 6)
            else:
                self.assertIn("failure_reason", candidate)
        self.assertIn("metrics", self.validation["nearest_template"])

    def test_selected_pipeline_remains_train_only(self) -> None:
        model = joblib.load(
            self.output / "models/selected_train_only_model.joblib"
        )
        self.assertIsInstance(model, Pipeline)
        self.assertEqual(len(model.gamma_training_image_ids_), 36)
        self.assertEqual(
            model["standard_scaler"].n_samples_seen_,
            36,
        )
        self.assertIn("no validation refit", model.gamma_fit_scope_)

    def test_expected_artifact_set_and_hashes(self) -> None:
        actual = {
            path.relative_to(self.output).as_posix()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            actual,
            {
                "features/train_validation_features.npz",
                "models/selected_train_only_model.joblib",
                "metrics/validation_results.json",
                "metrics/selection_audit.json",
                "plots/validation_confusion_matrix.png",
            },
        )
        for record in self.audit["output_artifacts"].values():
            path = self.output / Path(record["path"]).relative_to(
                self.output.name
            )
            self.assertEqual(baseline.sha256_file(path), record["sha256"])

    def test_no_test_id_in_serialized_metadata(self) -> None:
        test_ids = {
            row["image_id"] for row in self.dataset.rows if row["split"] == "test"
        }
        text = json.dumps(
            {"validation": self.validation, "audit": self.audit},
            sort_keys=True,
        )
        model = joblib.load(
            self.output / "models/selected_train_only_model.joblib"
        )
        for test_id in test_ids:
            self.assertNotIn(test_id, text)
            self.assertNotIn(test_id, model.gamma_training_image_ids_)

    def test_audit_feature_hashes_match_arrays(self) -> None:
        with np.load(
            self.output / "features/train_validation_features.npz",
            allow_pickle=False,
        ) as archive:
            for name in archive.files:
                self.assertEqual(
                    baseline.array_sha256(archive[name]),
                    self.audit["feature_array_hashes"][name][
                        "sha256_uncompressed_c_order_bytes"
                    ],
                )


class FrozenFinalPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.dataset = SyntheticDataset(cls.root)
        cls.selection = _build_frozen_selection_artifacts(cls.root, cls.dataset)
        cls.output = cls.root / "final_test"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_correct_frozen_selection_accepted_without_decode_fit_or_write(self) -> None:
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(
                baseline,
                "load_preprocessed_image",
                side_effect=AssertionError("decoded"),
            ),
            mock.patch.object(
                baseline,
                "_fit_frozen_final_pipeline",
                side_effect=AssertionError("fitted"),
            ),
        ):
            result = baseline.preflight_final_test(
                self.dataset.split,
                self.selection,
                self.output,
                confirm_frozen_selection=True,
            )
        self.assertEqual(result["fitting_count"], 48)
        self.assertEqual(result["test_count"], 12)
        self.assertEqual(result["test_images_decoded"], 0)
        self.assertEqual(result["models_fitted"], 0)
        self.assertEqual(result["artifacts_written"], 0)
        self.assertFalse(self.output.exists())

    def test_altered_selection_artifact_hash_rejected(self) -> None:
        copied = self.root / "altered-selection"
        shutil.copytree(self.selection, copied)
        contract = _frozen_contract_patch(self.dataset, copied)
        path = copied / "metrics/validation_results.json"
        path.write_bytes(path.read_bytes() + b" ")
        with contract:
            with self.assertRaisesRegex(baseline.BaselineError, "hash differs"):
                baseline.preflight_final_test(
                    self.dataset.split,
                    copied,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_wrong_selected_model_family_rejected(self) -> None:
        original = joblib.load(
            self.selection / "models/selected_train_only_model.joblib"
        )
        wrong = Pipeline(
            [
                ("standard_scaler", StandardScaler()),
                (
                    "classifier",
                    LinearSVC(
                        C=0.01,
                        dual="auto",
                        max_iter=10000,
                        tol=1e-4,
                        class_weight=None,
                        random_state=4710,
                    ),
                ),
            ]
        )
        X = np.tile(np.eye(6), (6, 1))
        wrong.fit(X, np.asarray(LABELS * 6))
        for name, value in original.__dict__.items():
            if name.startswith("gamma_"):
                setattr(wrong, name, value)
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(baseline.joblib, "load", return_value=wrong),
        ):
            with self.assertRaisesRegex(baseline.BaselineError, "family"):
                baseline.preflight_final_test(
                    self.dataset.split,
                    self.selection,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_wrong_selected_C_rejected(self) -> None:
        wrong = joblib.load(
            self.selection / "models/selected_train_only_model.joblib"
        )
        wrong.set_params(classifier__C=0.1)
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(baseline.joblib, "load", return_value=wrong),
        ):
            with self.assertRaisesRegex(baseline.BaselineError, "wrong C"):
                baseline.preflight_final_test(
                    self.dataset.split,
                    self.selection,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_preprocessing_mismatch_rejected(self) -> None:
        copied = self.root / "preprocessing-mismatch"
        shutil.copytree(self.selection, copied)
        results_path = copied / "metrics/validation_results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        results["preprocessing"]["canvas_width"] = 95
        baseline._write_json(results_path, results)
        audit_path = copied / "metrics/selection_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["output_artifacts"]["validation_results"][
            "sha256"
        ] = baseline.sha256_file(results_path)
        baseline._write_json(audit_path, audit)
        with _frozen_contract_patch(self.dataset, copied):
            with self.assertRaisesRegex(baseline.BaselineError, "preprocessing"):
                baseline.preflight_final_test(
                    self.dataset.split,
                    copied,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_split_manifest_hash_mismatch_rejected(self) -> None:
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(
                baseline, "FROZEN_SPLIT_MANIFEST_SHA256", "0" * 64
            ),
        ):
            with self.assertRaisesRegex(baseline.BaselineError, "split-manifest"):
                baseline.preflight_final_test(
                    self.dataset.split,
                    self.selection,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_split_audit_hash_mismatch_rejected(self) -> None:
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(baseline, "FROZEN_SPLIT_AUDIT_SHA256", "0" * 64),
        ):
            with self.assertRaisesRegex(baseline.BaselineError, "split-audit"):
                baseline.preflight_final_test(
                    self.dataset.split,
                    self.selection,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_missing_confirmation_flag_rejected_by_parser_and_function(self) -> None:
        parser = baseline._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "final-test",
                    "--manifest",
                    "m.csv",
                    "--selection-artifacts",
                    "selection",
                    "--output-dir",
                    "final_test",
                ]
            )
        with self.assertRaisesRegex(
            baseline.BaselineError, "--confirm-frozen-selection"
        ):
            baseline.preflight_final_test(
                self.dataset.split, self.selection, self.output
            )

    def test_unsupported_seed_rejected(self) -> None:
        with self.assertRaisesRegex(baseline.BaselineError, "seed=4710"):
            baseline.preflight_final_test(
                self.dataset.split,
                self.selection,
                self.output,
                seed=1,
                confirm_frozen_selection=True,
            )


class FrozenFinalFitUnitTest(unittest.TestCase):
    def test_convergence_warning_is_fatal(self) -> None:
        class WarningPipeline:
            def fit(self, X, y):
                warnings.warn("synthetic", ConvergenceWarning)
                return self

        with mock.patch.object(
            baseline, "_frozen_final_pipeline", return_value=WarningPipeline()
        ):
            with self.assertRaisesRegex(
                baseline.BaselineError, "ConvergenceWarning"
            ):
                baseline._fit_frozen_final_pipeline(
                    np.zeros((6, 2)), np.asarray(LABELS)
                )

    def test_frozen_pipeline_configuration(self) -> None:
        pipeline = baseline._frozen_final_pipeline()
        classifier = pipeline["classifier"]
        self.assertIsInstance(classifier, LogisticRegression)
        self.assertEqual(classifier.C, 0.01)
        self.assertEqual(classifier.solver, "lbfgs")
        self.assertEqual(classifier.max_iter, 5000)
        self.assertEqual(classifier.tol, 1e-4)
        self.assertIsNone(classifier.class_weight)
        self.assertEqual(classifier.random_state, 4710)


class EndToEndFinalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.dataset = SyntheticDataset(cls.root)
        cls.selection = _build_frozen_selection_artifacts(cls.root, cls.dataset)
        cls.output = cls.root / "final_test"
        cls.opened: list[tuple[str, bool]] = []
        cls.fit_completed = False
        original_loader = baseline.load_preprocessed_image
        original_fit = baseline._fit_frozen_final_pipeline

        def recording_loader(path: Path) -> np.ndarray:
            cls.opened.append((path.stem, cls.fit_completed))
            if (
                path.stem
                in {
                    row["image_id"]
                    for row in cls.dataset.rows
                    if row["split"] == "test"
                }
                and not cls.fit_completed
            ):
                raise AssertionError("test decoded before fit")
            return original_loader(path)

        def recording_fit(X, y):
            result = original_fit(X, y)
            cls.fit_completed = True
            return result

        with (
            _frozen_contract_patch(cls.dataset, cls.selection),
            mock.patch.object(
                baseline, "load_preprocessed_image", side_effect=recording_loader
            ),
            mock.patch.object(
                baseline, "_fit_frozen_final_pipeline", side_effect=recording_fit
            ),
            mock.patch.object(
                baseline,
                "fit_learned_candidates",
                side_effect=AssertionError("candidate fitting ran"),
            ),
            mock.patch.object(
                baseline,
                "nearest_template_comparison",
                side_effect=AssertionError("alternative candidate ran"),
            ),
        ):
            cls.summary = baseline.run_final_test(
                cls.dataset.split,
                cls.selection,
                cls.output,
                confirm_frozen_selection=True,
            )
        cls.results = json.loads(
            (cls.output / "metrics/final_test_results.json").read_text(
                encoding="utf-8"
            )
        )
        cls.audit = json.loads(
            (cls.output / "metrics/final_test_audit.json").read_text(
                encoding="utf-8"
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_exact_decode_order_and_test_isolation(self) -> None:
        fitting_ids = sorted(
            row["image_id"]
            for row in self.dataset.rows
            if row["split"] in {"train", "validation"}
        )
        test_ids = sorted(
            row["image_id"]
            for row in self.dataset.rows
            if row["split"] == "test"
        )
        self.assertEqual([item[0] for item in self.opened[:48]], fitting_ids)
        self.assertTrue(all(not item[1] for item in self.opened[:48]))
        self.assertEqual([item[0] for item in self.opened[48:]], test_ids)
        self.assertTrue(all(item[1] for item in self.opened[48:]))
        self.assertEqual(len(self.opened), 60)

    def test_final_model_fit_scope_and_no_test_metadata(self) -> None:
        model = joblib.load(
            self.output / "models/final_train_validation_model.joblib"
        )
        self.assertEqual(int(model["standard_scaler"].n_samples_seen_), 48)
        self.assertEqual(len(model.gamma_fitting_image_ids_), 48)
        self.assertFalse(hasattr(model, "gamma_test_image_ids_"))
        test_ids = {
            row["image_id"]
            for row in self.dataset.rows
            if row["split"] == "test"
        }
        self.assertTrue(test_ids.isdisjoint(model.gamma_fitting_image_ids_))

    def test_metrics_and_private_artifacts_complete(self) -> None:
        metrics = self.results["test_metrics"]
        self.assertEqual(self.results["label_order"], list(LABELS))
        self.assertEqual(set(metrics["per_class"]), set(LABELS))
        self.assertEqual(len(metrics["predictions"]), 12)
        self.assertEqual(sum(map(sum, metrics["confusion_matrix"])), 12)
        self.assertEqual(
            {value["support"] for value in metrics["per_class"].values()}, {2}
        )
        unknown = metrics["unknown_other_analysis"]
        self.assertEqual(unknown["actual_unknown_other"], 2)
        self.assertIn("unknown_other_false_rejections", unknown)
        self.assertIn("unknown_other_false_acceptances", unknown)
        actual = {
            path.relative_to(self.output).as_posix()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, set(baseline.FINAL_ARTIFACT_PATHS))

    def test_audit_hashes_and_execution_evidence(self) -> None:
        mapping = {
            "final_model": "models/final_train_validation_model.joblib",
            "results_json": "metrics/final_test_results.json",
            "confusion_matrix": "plots/final_test_confusion_matrix.png",
        }
        for key, relative in mapping.items():
            self.assertEqual(
                self.audit["output_artifacts"][key]["sha256"],
                baseline.sha256_file(self.output / relative),
            )
        evidence = self.audit["execution_ordering_evidence"]
        self.assertEqual(evidence["fitting_images_opened_before_fit"], 48)
        self.assertEqual(evidence["test_images_opened_during_fitting"], 0)
        self.assertEqual(evidence["test_images_opened_after_fitting"], 12)
        self.assertEqual(evidence["models_producing_test_predictions"], 1)
        self.assertEqual(evidence["test_prediction_calls"], 1)
        self.assertEqual(evidence["post_test_fit_calls"], 0)

    def test_overwrite_refusal_occurs_before_test_decoding(self) -> None:
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(
                baseline,
                "load_preprocessed_image",
                side_effect=AssertionError("decoded"),
            ),
        ):
            with self.assertRaisesRegex(baseline.BaselineError, "already exists"):
                baseline.run_final_test(
                    self.dataset.split,
                    self.selection,
                    self.output,
                    confirm_frozen_selection=True,
                )

    def test_transactional_failure_leaves_no_partial_output(self) -> None:
        failed_output = self.root / "failure" / "final_test"
        failed_output.parent.mkdir()
        with (
            _frozen_contract_patch(self.dataset, self.selection),
            mock.patch.object(
                baseline,
                "_fit_frozen_final_pipeline",
                side_effect=baseline.BaselineError("synthetic fit failure"),
            ),
        ):
            with self.assertRaisesRegex(baseline.BaselineError, "synthetic"):
                baseline.run_final_test(
                    self.dataset.split,
                    self.selection,
                    failed_output,
                    confirm_frozen_selection=True,
                )
        self.assertFalse(failed_output.exists())
        self.assertEqual(
            list(failed_output.parent.glob(".final_test.staging.*")), []
        )


if __name__ == "__main__":
    unittest.main()
