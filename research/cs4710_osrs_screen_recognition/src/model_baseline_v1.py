"""Train/validation-only baseline selection for CS 4710 OSRS recognition.

The test split is manifest metadata only in this module. The ``select`` command
passes only train and validation rows to the sole image-decoding boundary,
``load_preprocessed_image``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
import zipfile
from collections import Counter, defaultdict
from importlib import metadata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Sequence

import joblib
import numpy as np
from PIL import Image, UnidentifiedImageError
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC


BASELINE_VERSION = "model_baseline_v1"
RESULTS_SCHEMA_VERSION = "1.0"
AUDIT_SCHEMA_VERSION = "1.0"
FINAL_RESULTS_SCHEMA_VERSION = "1.0"
FINAL_AUDIT_SCHEMA_VERSION = "1.0"
PREPROCESSING_VERSION = "letterbox_rgb_96x64_lanczos_v1"
DEFAULT_SEED = 4710
CANVAS_WIDTH = 96
CANVAS_HEIGHT = 64
CHANNELS = 3
FEATURE_SHAPE = (CANVAS_HEIGHT, CANVAS_WIDTH, CHANNELS)
FEATURE_DIMENSION = CANVAS_WIDTH * CANVAS_HEIGHT * CHANNELS
EXPECTED_IMAGE_SIZE = (2287, 1369)
EXPECTED_IMAGE_MODE = "RGB"
LABEL_ORDER = (
    "normal_gameplay",
    "bank_open",
    "dialogue_or_choice_open",
    "shop_open",
    "grand_exchange_open",
    "unknown_other",
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
SPLIT_COUNTS = {"train": 36, "validation": 12, "test": 12}
PER_CLASS_SPLIT_COUNTS = {"train": 6, "validation": 2, "test": 2}
CANDIDATE_C_VALUES = (0.01, 0.1, 1.0, 10.0)
SELECTION_RULE = (
    "Highest validation macro F1; then highest validation accuracy; then "
    "smaller C; then Logistic Regression over Linear SVM; then lexical model "
    "identifier."
)
FEATURE_ARRAY_NAMES = (
    "X_train",
    "y_train",
    "train_image_ids",
    "X_validation",
    "y_validation",
    "validation_image_ids",
    "label_order",
    "feature_shape",
    "preprocessing_version",
)
FROZEN_SPLIT_MANIFEST_SHA256 = (
    "8aafe5d981fafc5c977cc0d082f7ab32110f4eaa77a5025fe96ce2218ed94367"
)
FROZEN_SPLIT_AUDIT_SHA256 = (
    "9457ff7f4bd2f84c3bb887897a4bf19bba365ed7cf6889c209bfd22679575361"
)
FROZEN_SELECTION_ARTIFACT_HASHES = {
    "features/train_validation_features.npz": (
        "da8b500c870eb9412e8deec1c065635035594f1afd1eb7f742c3d3c362e3818e"
    ),
    "models/selected_train_only_model.joblib": (
        "d2207a4c5d37c69484ef51aa7e5a073ab8fec31ee25f5ef5594c73ebbef6457e"
    ),
    "metrics/validation_results.json": (
        "f7fa63527934b5a85e16276f79e1d4ed2bc8d1632b46527cb4ab2cf47878561e"
    ),
    "metrics/selection_audit.json": (
        "26db2a5d39b645e7b13b7dba3a4fcadce7867b976faae96d6fac36784d03f1e3"
    ),
    "plots/validation_confusion_matrix.png": (
        "9746d783a958887874b53888a16088b9b25e635344f74b5eea1e3a307134c642"
    ),
}
FROZEN_SELECTED_IDENTIFIER = "logistic_regression_C_0.01"
FROZEN_SELECTED_C = 0.01
FINAL_ARTIFACT_PATHS = (
    "models/final_train_validation_model.joblib",
    "metrics/final_test_results.json",
    "metrics/final_test_audit.json",
    "plots/final_test_confusion_matrix.png",
)


class BaselineError(RuntimeError):
    """Raised when the baseline contract cannot be satisfied safely."""


def sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> tuple[list[dict[str, str]], bytes]:
    if not path.is_file() or path.is_symlink():
        raise BaselineError(f"manifest must be a regular file: {path}")
    try:
        contents = path.read_bytes()
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise BaselineError(f"manifest could not be read: {path}") from exc
    if headers != MANIFEST_HEADERS:
        raise BaselineError("manifest must use the exact canonical 22-column schema")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise BaselineError("manifest contains an incomplete or extra column")
    return rows, contents


def _validate_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or "\x00" in value:
        raise BaselineError("reviewed image paths must be nonempty POSIX paths")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise BaselineError("absolute reviewed image paths are not allowed")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise BaselineError("reviewed image path traversal is not allowed")
    if posix.suffix != ".png":
        raise BaselineError("reviewed image paths must use lowercase .png")
    return posix


def _data_root_for_manifest(manifest_path: Path) -> Path:
    if manifest_path.parent.name == "manifests":
        return manifest_path.parent.parent
    return manifest_path.parent


def _resolve_image_paths(
    manifest_path: Path, rows: Sequence[dict[str, str]]
) -> dict[str, Path]:
    data_root = _data_root_for_manifest(manifest_path)
    if not data_root.is_dir():
        raise BaselineError(f"manifest data root does not exist: {data_root}")
    resolved_root = data_root.resolve(strict=True)
    paths: dict[str, Path] = {}
    for row in rows:
        relative = _validate_relative_path(row["relative_image_path"])
        candidate = data_root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except FileNotFoundError as exc:
            raise BaselineError(
                f"reviewed image does not exist: {row['relative_image_path']}"
            ) from exc
        except ValueError as exc:
            raise BaselineError("reviewed image resolves outside the data root") from exc
        if not resolved.is_file():
            raise BaselineError("reviewed image must resolve to a regular file")
        if resolved.stem != row["image_id"]:
            raise BaselineError("reviewed image filename must match its opaque image ID")
        paths[row["image_id"]] = resolved
    return paths


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _validate_manifest_rows(rows: Sequence[dict[str, str]]) -> dict[str, Any]:
    if len(rows) != 60:
        raise BaselineError("split manifest must contain exactly 60 rows")

    image_ids = [row["image_id"] for row in rows]
    reviewed_paths = [row["relative_image_path"] for row in rows]
    if len(image_ids) != len(set(image_ids)):
        raise BaselineError("duplicate image IDs are not allowed")
    if len(reviewed_paths) != len(set(reviewed_paths)):
        raise BaselineError("duplicate reviewed paths are not allowed")
    expected_ids = {f"img_{index:04d}" for index in range(19, 79)}
    if set(image_ids) != expected_ids:
        raise BaselineError("image IDs must be exactly img_0019 through img_0078")

    approved_labels = set(LABEL_ORDER)
    for row in rows:
        if row["label"] not in approved_labels:
            raise BaselineError("manifest contains an unsupported label")
        if row["inclusion_status"] != "included":
            raise BaselineError("every row must have inclusion_status=included")
        if row["exclusion_reason"]:
            raise BaselineError("included rows must have a blank exclusion reason")
        if row["redaction_checked"] != "true":
            raise BaselineError("every row must have redaction_checked=true")
        if row["source_consent"] != "owner_captured":
            raise BaselineError("every row must have source_consent=owner_captured")
        if not row["source_group_id"].strip():
            raise BaselineError("every row must have a nonblank source_group_id")
        if row["split"] not in SPLIT_COUNTS:
            raise BaselineError("every row must be assigned to train, validation, or test")
        if row["capture_session_id"] != "session_004":
            raise BaselineError("every feasibility row must use capture_session_id=session_004")
        if row["resolution"] != "2287x1369":
            raise BaselineError("every row must use resolution=2287x1369")
        if row["aspect_ratio"] != "2287:1369":
            raise BaselineError("every row must use aspect_ratio=2287:1369")
        if row["crop_policy"] != "fixed_game_canvas_v1":
            raise BaselineError("every row must use crop_policy=fixed_game_canvas_v1")
        _validate_relative_path(row["relative_image_path"])

    class_counts = Counter(row["label"] for row in rows)
    if class_counts != Counter({label: 10 for label in LABEL_ORDER}):
        raise BaselineError("manifest must contain exactly 10 rows per class")
    split_counts = Counter(row["split"] for row in rows)
    if split_counts != Counter(SPLIT_COUNTS):
        raise BaselineError("manifest must contain exactly 36/12/12 split rows")
    per_class_split_counts: dict[str, dict[str, int]] = {}
    for label in LABEL_ORDER:
        counts = Counter(row["split"] for row in rows if row["label"] == label)
        if counts != Counter(PER_CLASS_SPLIT_COUNTS):
            raise BaselineError(f"{label} must contain exactly 6/2/2 split rows")
        per_class_split_counts[label] = _counter_dict(counts)

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["source_group_id"]].append(row)
    for group_id, group_rows in groups.items():
        splits = {row["split"] for row in group_rows}
        if len(splits) != 1:
            raise BaselineError(f"source group crosses splits: {group_id}")
        if len({row["label"] for row in group_rows}) != 1:
            raise BaselineError(f"source group mixes labels: {group_id}")
        split = next(iter(splits))
        if len(group_rows) > 1 and split != "train":
            raise BaselineError("all non-singleton source groups must be in train")
        if split in {"validation", "test"} and len(group_rows) != 1:
            raise BaselineError("validation and test must contain singleton groups only")
    if len(groups) != 54:
        raise BaselineError("manifest must contain exactly 54 source groups")

    ids_by_split = {
        split: sorted(row["image_id"] for row in rows if row["split"] == split)
        for split in ("train", "validation", "test")
    }
    return {
        "class_counts": _counter_dict(class_counts),
        "split_counts": _counter_dict(split_counts),
        "per_class_split_counts": per_class_split_counts,
        "source_group_count": len(groups),
        "ids_by_split": ids_by_split,
    }


def _load_json(path: Path, description: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise BaselineError(f"{description} must be a regular file: {path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"{description} could not be read: {path}") from exc
    if not isinstance(parsed, dict):
        raise BaselineError(f"{description} must contain a JSON object")
    return parsed


def _find_repo_root(start: Path) -> Path | None:
    resolved = start.resolve(strict=False)
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _resolve_audit_input_manifest(
    audit: dict[str, Any], audit_path: Path, manifest_path: Path
) -> Path:
    input_record = audit.get("input_manifest")
    if not isinstance(input_record, dict) or not isinstance(input_record.get("path"), str):
        raise BaselineError("split audit has no valid input_manifest record")
    recorded = Path(input_record["path"])
    if recorded.is_absolute():
        return recorded
    if len(recorded.parts) == 1:
        return audit_path.parent / recorded
    repo_root = _find_repo_root(manifest_path.parent)
    if repo_root is not None:
        return repo_root / recorded
    return audit_path.parent / recorded.name


def _reconcile_split_audit(
    manifest_path: Path,
    manifest_rows: Sequence[dict[str, str]],
    manifest_bytes: bytes,
    summary: dict[str, Any],
    audit_path: Path,
) -> dict[str, Any]:
    audit = _load_json(audit_path, "split audit")
    output_record = audit.get("output_manifest")
    if not isinstance(output_record, dict):
        raise BaselineError("split audit has no output_manifest record")
    if output_record.get("sha256") != sha256_bytes(manifest_bytes):
        raise BaselineError("split audit output-manifest hash does not match")
    if audit.get("random_seed") != DEFAULT_SEED:
        raise BaselineError("split audit has the wrong random seed")
    if audit.get("class_counts") != summary["class_counts"]:
        raise BaselineError("split audit class counts do not match")
    if audit.get("split_counts") != summary["split_counts"]:
        raise BaselineError("split audit split counts do not match")
    if audit.get("per_class_split_counts") != summary["per_class_split_counts"]:
        raise BaselineError("split audit per-class split counts do not match")
    if audit.get("source_group_count") != 54:
        raise BaselineError("split audit source-group count does not match")
    if audit.get("no_source_group_crosses_splits") is not True:
        raise BaselineError("split audit does not confirm source-group isolation")
    if audit.get("image_ids_by_split") != summary["ids_by_split"]:
        raise BaselineError("split audit image IDs do not match the manifest")
    if audit.get("capture_session_ids") != ["session_004"]:
        raise BaselineError("split audit capture-session identity does not match")

    accepted_path = _resolve_audit_input_manifest(audit, audit_path, manifest_path)
    accepted_rows, accepted_bytes = _read_manifest(accepted_path)
    input_record = audit["input_manifest"]
    if input_record.get("sha256") != sha256_bytes(accepted_bytes):
        raise BaselineError("split audit input-manifest hash does not match")
    if len(accepted_rows) != 60 or any(
        row["split"] != "unassigned" for row in accepted_rows
    ):
        raise BaselineError("accepted unsplit manifest must remain 60 unassigned rows")
    if [row["image_id"] for row in accepted_rows] != [
        row["image_id"] for row in manifest_rows
    ]:
        raise BaselineError("accepted and split manifest image ordering differs")
    for accepted, split in zip(accepted_rows, manifest_rows, strict=True):
        if any(
            accepted[key] != split[key] for key in MANIFEST_HEADERS if key != "split"
        ):
            raise BaselineError("accepted and split manifests differ outside split")
    return {
        "path": audit_path,
        "sha256": sha256_file(audit_path),
        "accepted_manifest_path": accepted_path,
        "accepted_manifest_sha256": sha256_bytes(accepted_bytes),
    }


def validate_manifest(
    manifest: str | os.PathLike[str],
    *,
    audit_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Validate manifest metadata and path existence without decoding images."""

    manifest_path = Path(manifest)
    rows, manifest_bytes = _read_manifest(manifest_path)
    summary = _validate_manifest_rows(rows)
    image_paths = _resolve_image_paths(manifest_path, rows)

    selected_audit: Path | None
    if audit_path is not None:
        selected_audit = Path(audit_path)
    else:
        sibling = manifest_path.with_name(f"{manifest_path.stem}_audit.json")
        selected_audit = sibling if sibling.exists() else None
    audit_summary = None
    if selected_audit is not None:
        audit_summary = _reconcile_split_audit(
            manifest_path,
            rows,
            manifest_bytes,
            summary,
            selected_audit,
        )
    return {
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "rows": rows,
        "image_paths": image_paths,
        "audit": audit_summary,
        **summary,
    }


def load_preprocessed_image(path: Path) -> np.ndarray:
    """Decode and letterbox one reviewed frame as row-major HWC RGB float32.

    The 96x64 canvas is black. Pixels are flattened in NumPy C order from
    shape ``(64, 96, 3)``: rows, then columns, with RGB channels innermost.
    """

    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise BaselineError(f"reviewed image is not PNG: {path.name}")
            if image.mode != EXPECTED_IMAGE_MODE:
                raise BaselineError(f"reviewed image is not RGB: {path.name}")
            if image.size != EXPECTED_IMAGE_SIZE:
                raise BaselineError(
                    f"reviewed image does not have dimensions 2287x1369: {path.name}"
                )
            source_width, source_height = image.size
            scale = min(
                CANVAS_WIDTH / source_width,
                CANVAS_HEIGHT / source_height,
            )
            resized_width = max(1, min(CANVAS_WIDTH, int(source_width * scale)))
            resized_height = max(1, min(CANVAS_HEIGHT, int(source_height * scale)))
            resized = image.resize(
                (resized_width, resized_height),
                resample=Image.Resampling.LANCZOS,
            )
            canvas = Image.new(
                "RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), color=(0, 0, 0)
            )
            offset = (
                (CANVAS_WIDTH - resized_width) // 2,
                (CANVAS_HEIGHT - resized_height) // 2,
            )
            canvas.paste(resized, offset)
            array = np.asarray(canvas, dtype=np.float32) / np.float32(255.0)
    except BaselineError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BaselineError(f"reviewed image could not be decoded: {path.name}") from exc
    if array.shape != FEATURE_SHAPE:
        raise BaselineError("internal preprocessing shape mismatch")
    flattened = np.ascontiguousarray(array.reshape(-1), dtype=np.float32)
    if flattened.shape != (FEATURE_DIMENSION,):
        raise BaselineError("internal flattened feature dimension mismatch")
    return flattened


def _rows_for_split(
    rows: Sequence[dict[str, str]], split: str
) -> list[dict[str, str]]:
    return sorted(
        (row for row in rows if row["split"] == split),
        key=lambda row: row["image_id"],
    )


def extract_train_validation_features(
    rows: Sequence[dict[str, str]],
    image_paths: dict[str, Path],
) -> dict[str, np.ndarray]:
    """Decode exactly the train and validation rows, never test rows."""

    train_rows = _rows_for_split(rows, "train")
    validation_rows = _rows_for_split(rows, "validation")

    def extract(selected_rows: Sequence[dict[str, str]]) -> np.ndarray:
        return np.stack(
            [
                load_preprocessed_image(image_paths[row["image_id"]])
                for row in selected_rows
            ]
        ).astype(np.float32, copy=False)

    return {
        "X_train": extract(train_rows),
        "y_train": np.asarray([row["label"] for row in train_rows], dtype=np.str_),
        "train_image_ids": np.asarray(
            [row["image_id"] for row in train_rows], dtype=np.str_
        ),
        "X_validation": extract(validation_rows),
        "y_validation": np.asarray(
            [row["label"] for row in validation_rows], dtype=np.str_
        ),
        "validation_image_ids": np.asarray(
            [row["image_id"] for row in validation_rows], dtype=np.str_
        ),
        "label_order": np.asarray(LABEL_ORDER, dtype=np.str_),
        "feature_shape": np.asarray(FEATURE_SHAPE, dtype=np.int64),
        "preprocessing_version": np.asarray(PREPROCESSING_VERSION, dtype=np.str_),
    }


def array_sha256(array: np.ndarray) -> str:
    """Hash the uncompressed C-order array bytes."""

    return sha256_bytes(np.ascontiguousarray(array).tobytes(order="C"))


def _npy_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.lib.format.write_array(buffer, np.asarray(array), allow_pickle=False)
    return buffer.getvalue()


def write_deterministic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """Write an NPZ with fixed member order and timestamps."""

    if tuple(arrays) != FEATURE_ARRAY_NAMES:
        raise BaselineError("feature artifact arrays are missing or out of order")
    with zipfile.ZipFile(
        path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in FEATURE_ARRAY_NAMES:
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            info.create_system = 3
            archive.writestr(info, _npy_bytes(arrays[name]), compress_type=zipfile.ZIP_DEFLATED)


def preprocessing_configuration() -> dict[str, Any]:
    return {
        "version": PREPROCESSING_VERSION,
        "input_format": "PNG",
        "input_mode": "RGB",
        "input_dimensions": [EXPECTED_IMAGE_SIZE[0], EXPECTED_IMAGE_SIZE[1]],
        "entire_reviewed_frame": True,
        "canvas_width": CANVAS_WIDTH,
        "canvas_height": CANVAS_HEIGHT,
        "feature_shape_hwc": list(FEATURE_SHAPE),
        "channel_order": "RGB",
        "flatten_order": "NumPy C order: row, column, channel",
        "padding_rgb": [0, 0, 0],
        "resize": "preserve aspect ratio and fit completely; no crop",
        "interpolation": "Pillow Image.Resampling.LANCZOS",
        "scaling": "float32 channel values divided by 255 into [0.0, 1.0]",
        "augmentation": "none",
        "feature_dimension": FEATURE_DIMENSION,
    }


def _unknown_other_analysis(
    y_true: Sequence[str], y_pred: Sequence[str]
) -> dict[str, Any]:
    actual_unknown = sum(value == "unknown_other" for value in y_true)
    other_actual = len(y_true) - actual_unknown
    correct = sum(
        actual == predicted == "unknown_other"
        for actual, predicted in zip(y_true, y_pred, strict=True)
    )
    false_rejections = sum(
        actual == "unknown_other" and predicted != "unknown_other"
        for actual, predicted in zip(y_true, y_pred, strict=True)
    )
    false_acceptances = sum(
        actual != "unknown_other" and predicted == "unknown_other"
        for actual, predicted in zip(y_true, y_pred, strict=True)
    )
    return {
        "actual_unknown_other": int(actual_unknown),
        "correct_unknown_other_predictions": int(correct),
        "unknown_other_false_rejections": int(false_rejections),
        "unknown_other_false_rejection_rate": (
            float(false_rejections / actual_unknown) if actual_unknown else None
        ),
        "non_unknown_other_actual": int(other_actual),
        "unknown_other_false_acceptances": int(false_acceptances),
        "unknown_other_false_acceptance_rate": (
            float(false_acceptances / other_actual) if other_actual else None
        ),
    }


def calculate_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    image_ids: Sequence[str],
) -> dict[str, Any]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=LABEL_ORDER,
        zero_division=0,
    )
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=LABEL_ORDER,
        average="macro",
        zero_division=0,
    )
    matrix = confusion_matrix(y_true, y_pred, labels=LABEL_ORDER)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(macro_precision),
        "macro_recall": float(macro_recall),
        "macro_f1": float(macro_f1),
        "per_class": {
            label: {
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
                "support": int(support[index]),
            }
            for index, label in enumerate(LABEL_ORDER)
        },
        "confusion_matrix": matrix.astype(int).tolist(),
        "predictions": [
            {"image_id": str(image_id), "predicted_label": str(predicted)}
            for image_id, predicted in zip(image_ids, y_pred, strict=True)
        ],
        "unknown_other_analysis": _unknown_other_analysis(y_true, y_pred),
    }


def candidate_specifications() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for family, family_name in (
        ("logistic_regression", "Logistic Regression"),
        ("linear_svm", "Linear SVM"),
    ):
        for c_value in CANDIDATE_C_VALUES:
            candidates.append(
                {
                    "identifier": f"{family}_C_{c_value}",
                    "family": family,
                    "family_name": family_name,
                    "C": c_value,
                }
            )
    return candidates


def _candidate_pipeline(specification: dict[str, Any]) -> Pipeline:
    common = {
        "C": specification["C"],
        "class_weight": None,
        "random_state": DEFAULT_SEED,
        "tol": 1e-4,
    }
    if specification["family"] == "logistic_regression":
        classifier = LogisticRegression(
            solver="lbfgs",
            max_iter=5000,
            **common,
        )
    elif specification["family"] == "linear_svm":
        classifier = LinearSVC(
            dual="auto",
            max_iter=10000,
            **common,
        )
    else:
        raise BaselineError("internal unsupported candidate family")
    return Pipeline(
        [
            ("standard_scaler", StandardScaler()),
            ("classifier", classifier),
        ]
    )


def fit_learned_candidates(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_validation: np.ndarray,
    y_validation: np.ndarray,
    validation_image_ids: np.ndarray,
) -> tuple[list[dict[str, Any]], dict[str, Pipeline]]:
    results: list[dict[str, Any]] = []
    successful_models: dict[str, Pipeline] = {}
    for specification in candidate_specifications():
        pipeline = _candidate_pipeline(specification)
        public_hyperparameters = {
            "pipeline": ["StandardScaler", specification["family_name"]],
            "scaler_fit_scope": "training rows only",
            "C": specification["C"],
            "class_weight": None,
            "random_state": DEFAULT_SEED,
            "tol": 1e-4,
            "solver": (
                "lbfgs"
                if specification["family"] == "logistic_regression"
                else None
            ),
            "dual": (
                "auto" if specification["family"] == "linear_svm" else None
            ),
            "max_iter": (
                5000
                if specification["family"] == "logistic_regression"
                else 10000
            ),
        }
        result: dict[str, Any] = {
            **specification,
            "hyperparameters": public_hyperparameters,
            "status": "pending",
            "warnings": [],
        }
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                start = time.perf_counter()
                pipeline.fit(X_train, y_train)
                fit_elapsed = time.perf_counter() - start
            result["warnings"] = [
                {
                    "category": warning.category.__name__,
                    "message": str(warning.message),
                }
                for warning in caught
            ]
            convergence = [
                warning
                for warning in caught
                if issubclass(warning.category, ConvergenceWarning)
            ]
            if convergence:
                result["status"] = "failed"
                result["failure_reason"] = (
                    "scikit-learn ConvergenceWarning during training"
                )
            else:
                predictions = pipeline.predict(X_validation)
                result["status"] = "success"
                result["metrics"] = calculate_metrics(
                    y_validation, predictions, validation_image_ids
                )
                result["fit_elapsed_seconds_informational"] = float(fit_elapsed)
                result["fit_elapsed_excluded_from_determinism_claim"] = True
                successful_models[specification["identifier"]] = pipeline
        except Exception as exc:  # candidate failures must remain visible
            result["status"] = "failed"
            result["failure_reason"] = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results, successful_models


def select_learned_candidate(
    candidate_results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    successful = [result for result in candidate_results if result["status"] == "success"]
    if not successful:
        raise BaselineError("no learned candidate completed successfully")
    return sorted(
        successful,
        key=lambda result: (
            -result["metrics"]["macro_f1"],
            -result["metrics"]["accuracy"],
            result["C"],
            0 if result["family"] == "logistic_regression" else 1,
            result["identifier"],
        ),
    )[0]


def nearest_template_comparison(
    X_train: np.ndarray,
    y_train: np.ndarray,
    train_image_ids: np.ndarray,
    X_validation: np.ndarray,
    y_validation: np.ndarray,
    validation_image_ids: np.ndarray,
) -> dict[str, Any]:
    order = np.argsort(train_image_ids, kind="stable")
    train_features = X_train[order]
    train_labels = y_train[order]
    train_ids = train_image_ids[order]
    train_norms = np.linalg.norm(train_features, axis=1, keepdims=True)
    train_unit = np.divide(
        train_features,
        train_norms,
        out=np.zeros_like(train_features),
        where=train_norms != 0,
    )
    validation_norms = np.linalg.norm(X_validation, axis=1, keepdims=True)
    validation_unit = np.divide(
        X_validation,
        validation_norms,
        out=np.zeros_like(X_validation),
        where=validation_norms != 0,
    )
    similarities = validation_unit @ train_unit.T
    nearest_indices = np.argmax(similarities, axis=1)
    predictions = train_labels[nearest_indices]
    nearest_ids = train_ids[nearest_indices]
    scores = similarities[np.arange(len(X_validation)), nearest_indices]
    return {
        "identifier": "nearest_template_cosine",
        "role": "comparison_only_not_selectable",
        "metrics": calculate_metrics(
            y_validation, predictions, validation_image_ids
        ),
        "matches": [
            {
                "validation_image_id": str(validation_id),
                "predicted_label": str(predicted),
                "nearest_training_image_id": str(nearest_id),
                "cosine_score": float(score),
            }
            for validation_id, predicted, nearest_id, score in zip(
                validation_image_ids,
                predictions,
                nearest_ids,
                scores,
                strict=True,
            )
        ],
        "tie_break": "ascending training image ID",
        "threshold": None,
    }


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_bytes(_json_bytes(document))


def _plot_confusion_matrix(
    path: Path,
    matrix: Sequence[Sequence[int]],
    *,
    title: str = "Selected learned model — validation set confusion matrix",
) -> None:
    previous_config = os.environ.get("MPLCONFIGDIR")
    with tempfile.TemporaryDirectory(prefix=".model_baseline_v1-matplotlib.") as config:
        os.environ["MPLCONFIGDIR"] = config
        try:
            import matplotlib

            matplotlib.use("Agg")
            from matplotlib import pyplot as plt

            values = np.asarray(matrix, dtype=int)
            figure, axis = plt.subplots(figsize=(10, 8), constrained_layout=True)
            image = axis.imshow(values, interpolation="nearest", cmap="Blues")
            figure.colorbar(image, ax=axis)
            axis.set(
                xticks=np.arange(len(LABEL_ORDER)),
                yticks=np.arange(len(LABEL_ORDER)),
                xticklabels=LABEL_ORDER,
                yticklabels=LABEL_ORDER,
                ylabel="Actual label",
                xlabel="Predicted label",
                title=title,
            )
            plt.setp(
                axis.get_xticklabels(),
                rotation=35,
                ha="right",
                rotation_mode="anchor",
            )
            threshold = values.max() / 2 if values.size else 0
            for row_index in range(values.shape[0]):
                for column_index in range(values.shape[1]):
                    axis.text(
                        column_index,
                        row_index,
                        str(int(values[row_index, column_index])),
                        ha="center",
                        va="center",
                        color=(
                            "white"
                            if values[row_index, column_index] > threshold
                            else "black"
                        ),
                    )
            figure.savefig(
                path,
                dpi=140,
                format="png",
                metadata={"Software": "matplotlib"},
            )
            plt.close(figure)
        finally:
            if previous_config is None:
                os.environ.pop("MPLCONFIGDIR", None)
            else:
                os.environ["MPLCONFIGDIR"] = previous_config


def _library_versions() -> dict[str, str]:
    package_names = {
        "numpy": "numpy",
        "Pillow": "Pillow",
        "scikit-learn": "scikit-learn",
        "scipy": "scipy",
        "matplotlib": "matplotlib",
        "joblib": "joblib",
    }
    return {
        "python": platform.python_version(),
        **{
            display: metadata.version(package)
            for display, package in package_names.items()
        },
    }


def _relative_display_path(path: Path, repo_root: Path | None) -> str:
    resolved = path.resolve(strict=False)
    if repo_root is not None:
        try:
            return resolved.relative_to(repo_root.resolve(strict=False)).as_posix()
        except ValueError:
            pass
    return path.name


def _verify_output_policy(output_dir: Path) -> Path | None:
    if output_dir.exists() or output_dir.is_symlink():
        raise BaselineError(f"output directory already exists: {output_dir}")
    if not output_dir.parent.is_dir():
        raise BaselineError(f"output parent directory does not exist: {output_dir.parent}")
    if output_dir.name != "model_baseline_v1":
        raise BaselineError("output directory must be named model_baseline_v1")
    repo_root = _find_repo_root(output_dir.parent)
    if repo_root is None:
        return None
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(output_dir / "privacy-check")],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise BaselineError("could not verify private output ignore policy") from exc
    if result.returncode != 0:
        raise BaselineError("intended output directory is not ignored by Git")
    return repo_root


def _validate_feature_arrays(arrays: dict[str, np.ndarray]) -> None:
    if tuple(arrays) != FEATURE_ARRAY_NAMES:
        raise BaselineError("feature artifact does not have the exact required fields")
    if arrays["X_train"].shape != (36, FEATURE_DIMENSION):
        raise BaselineError("feature artifact must contain exactly 36 train rows")
    if arrays["X_validation"].shape != (12, FEATURE_DIMENSION):
        raise BaselineError("feature artifact must contain exactly 12 validation rows")
    if arrays["X_train"].dtype != np.float32 or arrays["X_validation"].dtype != np.float32:
        raise BaselineError("feature arrays must be float32")
    if tuple(arrays["label_order"].tolist()) != LABEL_ORDER:
        raise BaselineError("feature artifact label order is invalid")
    if tuple(arrays["feature_shape"].tolist()) != FEATURE_SHAPE:
        raise BaselineError("feature artifact feature shape is invalid")
    if str(arrays["preprocessing_version"].item()) != PREPROCESSING_VERSION:
        raise BaselineError("feature artifact preprocessing version is invalid")
    combined_ids = set(arrays["train_image_ids"].tolist()) | set(
        arrays["validation_image_ids"].tolist()
    )
    if any(image_id in combined_ids for image_id in (f"img_{i:04d}" for i in range(1, 19))):
        raise BaselineError("historical image ID found in feature artifact")


def _validate_staged_artifacts(staging: Path, test_ids: set[str]) -> None:
    expected = {
        "features/train_validation_features.npz",
        "models/selected_train_only_model.joblib",
        "metrics/validation_results.json",
        "metrics/selection_audit.json",
        "plots/validation_confusion_matrix.png",
    }
    actual = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise BaselineError("staged artifact set is incomplete or contains extras")
    with np.load(
        staging / "features/train_validation_features.npz", allow_pickle=False
    ) as archive:
        arrays = {name: archive[name] for name in archive.files}
    _validate_feature_arrays(arrays)
    serialized_ids = set(arrays["train_image_ids"].tolist()) | set(
        arrays["validation_image_ids"].tolist()
    )
    if test_ids & serialized_ids:
        raise BaselineError("test image ID found in feature artifact")
    validation = _load_json(
        staging / "metrics/validation_results.json", "validation results"
    )
    audit = _load_json(staging / "metrics/selection_audit.json", "selection audit")
    text = json.dumps({"validation": validation, "audit": audit}, sort_keys=True)
    if any(test_id in text for test_id in test_ids):
        raise BaselineError("test image ID leaked into validation metadata")
    if validation.get("test_images_decoded_or_evaluated") is not False:
        raise BaselineError("validation results do not confirm test exclusion")
    if audit.get("test_exclusion", {}).get("test_images_decoded") is not False:
        raise BaselineError("selection audit does not confirm test exclusion")
    model = joblib.load(staging / "models/selected_train_only_model.joblib")
    if not isinstance(model, Pipeline):
        raise BaselineError("selected model artifact is not a scikit-learn Pipeline")
    if len(getattr(model, "gamma_training_image_ids_", ())) != 36:
        raise BaselineError("selected model does not record exactly 36 training IDs")
    if test_ids & set(model.gamma_training_image_ids_):
        raise BaselineError("test image ID found in selected model metadata")
    with Image.open(staging / "plots/validation_confusion_matrix.png") as plot:
        plot.verify()


def _selection_documents(
    validation: dict[str, Any],
    arrays: dict[str, np.ndarray],
    candidate_results: list[dict[str, Any]],
    nearest: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    train_ids = arrays["train_image_ids"].tolist()
    validation_ids = arrays["validation_image_ids"].tolist()
    return {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "baseline_version": BASELINE_VERSION,
        "label_order": list(LABEL_ORDER),
        "train_image_ids": train_ids,
        "validation_image_ids": validation_ids,
        "test_images_decoded_or_evaluated": False,
        "test_exclusion_statement": (
            "Test rows were read as manifest metadata only. Test image files were "
            "not opened, decoded, hashed, resized, featurized, or evaluated."
        ),
        "preprocessing": preprocessing_configuration(),
        "candidate_grid": candidate_specifications(),
        "learned_candidates": candidate_results,
        "candidate_failures": [
            {
                "identifier": result["identifier"],
                "failure_reason": result["failure_reason"],
                "warnings": result["warnings"],
            }
            for result in candidate_results
            if result["status"] == "failed"
        ],
        "nearest_template": nearest,
        "selected_learned_model_identifier": selected["identifier"],
        "selected_C": selected["C"],
        "selection_rule": SELECTION_RULE,
        "selected_model_validation_confusion_matrix": selected["metrics"][
            "confusion_matrix"
        ],
        "selected_model_validation_predictions": selected["metrics"]["predictions"],
        "unknown_other_analysis": selected["metrics"]["unknown_other_analysis"],
        "dataset_size_limitations": [
            "Only 36 training and 12 validation images are available.",
            "Validation contains only two examples per class.",
            "All images come from one capture session and one fixed capture profile.",
            "This is source-group-aware but not capture-session-held-out validation.",
            "Detailed validation metrics are selection evidence, not final test results.",
        ],
        "fit_scope": "Every scaler and classifier was fitted on training rows only.",
        "validation_scope": "Candidates were compared on validation rows only.",
        "final_test_evaluation": "pending",
        "manifest_validation": {
            "manifest_sha256": validation["manifest_sha256"],
            "split_counts": validation["split_counts"],
            "per_class_split_counts": validation["per_class_split_counts"],
            "source_group_count": validation["source_group_count"],
        },
    }


def run_selection(
    manifest: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    seed: int = DEFAULT_SEED,
    exclude_test: bool = False,
    audit_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if seed != DEFAULT_SEED:
        raise BaselineError(f"{BASELINE_VERSION} requires seed={DEFAULT_SEED}")
    if not exclude_test:
        raise BaselineError("select requires --exclude-test")

    manifest_path = Path(manifest)
    output = Path(output_dir)
    repo_root = _verify_output_policy(output)
    validation = validate_manifest(manifest_path, audit_path=audit_path)
    if validation["audit"] is None:
        raise BaselineError("select requires a reconciled split audit")

    arrays = extract_train_validation_features(
        validation["rows"], validation["image_paths"]
    )
    _validate_feature_arrays(arrays)
    candidate_results, successful_models = fit_learned_candidates(
        arrays["X_train"],
        arrays["y_train"],
        arrays["X_validation"],
        arrays["y_validation"],
        arrays["validation_image_ids"],
    )
    selected = select_learned_candidate(candidate_results)
    selected_model = successful_models[selected["identifier"]]
    selected_model.gamma_baseline_version_ = BASELINE_VERSION
    selected_model.gamma_preprocessing_version_ = PREPROCESSING_VERSION
    selected_model.gamma_training_image_ids_ = tuple(
        arrays["train_image_ids"].tolist()
    )
    selected_model.gamma_fit_scope_ = "training rows only; no validation refit"
    selected_model.gamma_selected_identifier_ = selected["identifier"]
    selected_model.gamma_selected_C_ = selected["C"]

    nearest = nearest_template_comparison(
        arrays["X_train"],
        arrays["y_train"],
        arrays["train_image_ids"],
        arrays["X_validation"],
        arrays["y_validation"],
        arrays["validation_image_ids"],
    )
    validation_document = _selection_documents(
        validation, arrays, candidate_results, nearest, selected
    )

    staging: Path | None = None
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{output.name}.staging.",
                dir=output.parent,
            )
        )
        for relative in ("features", "models", "metrics", "plots"):
            (staging / relative).mkdir()
        feature_path = staging / "features/train_validation_features.npz"
        model_path = staging / "models/selected_train_only_model.joblib"
        results_path = staging / "metrics/validation_results.json"
        audit_output_path = staging / "metrics/selection_audit.json"
        plot_path = staging / "plots/validation_confusion_matrix.png"

        write_deterministic_npz(feature_path, arrays)
        joblib.dump(selected_model, model_path, compress=3)
        _write_json(results_path, validation_document)
        _plot_confusion_matrix(
            plot_path, selected["metrics"]["confusion_matrix"]
        )

        final_artifact_paths = {
            "features": output / "features/train_validation_features.npz",
            "selected_model": output / "models/selected_train_only_model.joblib",
            "validation_results": output / "metrics/validation_results.json",
            "confusion_matrix": output / "plots/validation_confusion_matrix.png",
        }
        staged_artifact_paths = {
            "features": feature_path,
            "selected_model": model_path,
            "validation_results": results_path,
            "confusion_matrix": plot_path,
        }

        def artifact_display_path(path: Path) -> str:
            if repo_root is not None:
                return _relative_display_path(path, repo_root)
            return f"{output.name}/{path.relative_to(output).as_posix()}"

        audit_document = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "baseline_version": BASELINE_VERSION,
            "source_code": {
                "identifier": BASELINE_VERSION,
                "path": _relative_display_path(Path(__file__), repo_root),
                "sha256": sha256_file(Path(__file__)),
            },
            "input_manifest": {
                "path": _relative_display_path(manifest_path, repo_root),
                "sha256": validation["manifest_sha256"],
            },
            "split_audit": {
                "path": _relative_display_path(
                    validation["audit"]["path"], repo_root
                ),
                "sha256": validation["audit"]["sha256"],
            },
            "random_seed": seed,
            "library_versions": _library_versions(),
            "preprocessing_version": PREPROCESSING_VERSION,
            "feature_dimensions": {
                "canvas_hwc": list(FEATURE_SHAPE),
                "flattened": FEATURE_DIMENSION,
            },
            "feature_array_hashes": {
                name: {
                    "sha256_uncompressed_c_order_bytes": array_sha256(arrays[name]),
                    "dtype": str(arrays[name].dtype),
                    "shape": list(arrays[name].shape),
                }
                for name in FEATURE_ARRAY_NAMES
            },
            "counts": {
                "train": 36,
                "validation": 12,
                "per_class": {
                    label: {"train": 6, "validation": 2}
                    for label in LABEL_ORDER
                },
            },
            "test_exclusion": {
                "manifest_metadata_rows_seen": 12,
                "test_images_opened": 0,
                "test_images_decoded": False,
                "test_features_created": 0,
                "test_rows_evaluated": 0,
                "statement": (
                    "Test paths were existence-checked as metadata only; test image "
                    "files were never opened or decoded."
                ),
            },
            "output_artifacts": {
                key: {
                    "path": artifact_display_path(path),
                    "sha256": sha256_file(staged_artifact_paths[key]),
                }
                for key, path in final_artifact_paths.items()
            },
            "model_selection_rule": SELECTION_RULE,
            "learned_candidates_attempted": len(candidate_results),
            "successful_learned_candidates": sum(
                result["status"] == "success" for result in candidate_results
            ),
            "failed_learned_candidates": sum(
                result["status"] == "failed" for result in candidate_results
            ),
            "selected_configuration": {
                "identifier": selected["identifier"],
                "family": selected["family"],
                "C": selected["C"],
            },
            "selected_model_fit_scope": (
                "The persisted pipeline remains fitted on the 36 training rows "
                "only; it was not refitted on train+validation."
            ),
            "final_test_evaluation": "pending",
        }
        _write_json(audit_output_path, audit_document)

        test_ids = set(validation["ids_by_split"]["test"])
        _validate_staged_artifacts(staging, test_ids)
        if output.exists() or output.is_symlink():
            raise BaselineError("output directory appeared during staging")
        os.replace(staging, output)
        staging = None
    except BaselineError:
        raise
    except Exception as exc:
        raise BaselineError(f"selection failed before atomic installation: {exc}") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    return {
        "baseline_version": BASELINE_VERSION,
        "manifest_sha256": validation["manifest_sha256"],
        "train_feature_count": 36,
        "validation_feature_count": 12,
        "test_feature_count": 0,
        "test_images_opened": 0,
        "learned_candidates_attempted": len(candidate_results),
        "successful_learned_candidates": sum(
            result["status"] == "success" for result in candidate_results
        ),
        "failed_learned_candidates": sum(
            result["status"] == "failed" for result in candidate_results
        ),
        "selected_learned_model": selected["identifier"],
        "selected_C": selected["C"],
        "output_dir": _relative_display_path(output, repo_root),
        "final_test_evaluation": "pending",
    }


def frozen_final_configuration() -> dict[str, Any]:
    """Return the immutable configuration approved before test evaluation."""

    return {
        "preprocessing": {
            **preprocessing_configuration(),
            "resized_dimensions": [96, 57],
            "centered_padding": {
                "left_columns": 0,
                "top_rows": 3,
                "right_columns": 0,
                "bottom_rows": 4,
            },
        },
        "pipeline": ["StandardScaler", "LogisticRegression"],
        "scaler_fit_scope": "train + validation rows only",
        "classifier": {
            "family": "logistic_regression",
            "identifier": FROZEN_SELECTED_IDENTIFIER,
            "C": FROZEN_SELECTED_C,
            "solver": "lbfgs",
            "max_iter": 5000,
            "tol": 1e-4,
            "class_weight": None,
            "random_state": DEFAULT_SEED,
        },
        "augmentation": "none",
        "PCA": "none",
        "ROI_features": "none",
        "threshold_tuning": "none",
        "label_order": list(LABEL_ORDER),
    }


def _verify_final_output_policy(output_dir: Path) -> Path | None:
    if output_dir.exists() or output_dir.is_symlink():
        raise BaselineError(f"final-test output directory already exists: {output_dir}")
    if not output_dir.parent.is_dir():
        raise BaselineError(
            f"final-test output parent directory does not exist: {output_dir.parent}"
        )
    if output_dir.name != "final_test":
        raise BaselineError("final-test output directory must be named final_test")
    repo_root = _find_repo_root(output_dir.parent)
    if repo_root is None:
        return None
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(output_dir / "privacy-check")],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise BaselineError("could not verify private final-test ignore policy") from exc
    if result.returncode != 0:
        raise BaselineError("final-test output directory is not ignored by Git")
    return repo_root


def _load_feature_archive(path: Path) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            arrays = {name: archive[name] for name in archive.files}
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise BaselineError("selection feature artifact could not be loaded") from exc
    _validate_feature_arrays(arrays)
    return arrays


def _verify_frozen_classifier(classifier: Any) -> None:
    if not isinstance(classifier, LogisticRegression):
        raise BaselineError("frozen selected model family is not Logistic Regression")
    expected = {
        "C": FROZEN_SELECTED_C,
        "solver": "lbfgs",
        "max_iter": 5000,
        "tol": 1e-4,
        "class_weight": None,
        "random_state": DEFAULT_SEED,
    }
    actual = classifier.get_params()
    for name, value in expected.items():
        if actual.get(name) != value:
            raise BaselineError(f"frozen selected classifier has wrong {name}")


def _verify_selection_artifacts(
    selection_artifacts: Path,
    validation: dict[str, Any],
) -> dict[str, Any]:
    artifact_paths = {
        relative: selection_artifacts / relative
        for relative in FROZEN_SELECTION_ARTIFACT_HASHES
    }
    actual_hashes: dict[str, str] = {}
    for relative, expected_hash in FROZEN_SELECTION_ARTIFACT_HASHES.items():
        path = artifact_paths[relative]
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"missing frozen selection artifact: {relative}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise BaselineError(f"frozen selection artifact hash differs: {relative}")
        actual_hashes[relative] = actual_hash

    validation_results = _load_json(
        artifact_paths["metrics/validation_results.json"], "validation results"
    )
    selection_audit = _load_json(
        artifact_paths["metrics/selection_audit.json"], "selection audit"
    )
    arrays = _load_feature_archive(
        artifact_paths["features/train_validation_features.npz"]
    )
    selected_model = joblib.load(
        artifact_paths["models/selected_train_only_model.joblib"]
    )

    test_ids = set(validation["ids_by_split"]["test"])
    train_ids = validation["ids_by_split"]["train"]
    validation_ids = validation["ids_by_split"]["validation"]
    if arrays["train_image_ids"].tolist() != train_ids:
        raise BaselineError("selection feature training IDs differ from the split")
    if arrays["validation_image_ids"].tolist() != validation_ids:
        raise BaselineError("selection feature validation IDs differ from the split")
    labels_by_id = {row["image_id"]: row["label"] for row in validation["rows"]}
    if arrays["y_train"].tolist() != [labels_by_id[image_id] for image_id in train_ids]:
        raise BaselineError("selection feature training labels differ from the split")
    if arrays["y_validation"].tolist() != [
        labels_by_id[image_id] for image_id in validation_ids
    ]:
        raise BaselineError("selection feature validation labels differ from the split")
    if test_ids & (
        set(arrays["train_image_ids"].tolist())
        | set(arrays["validation_image_ids"].tolist())
    ):
        raise BaselineError("test image ID found in validation feature artifact")

    if validation_results.get("preprocessing") != preprocessing_configuration():
        raise BaselineError("frozen preprocessing configuration differs")
    if validation_results.get("label_order") != list(LABEL_ORDER):
        raise BaselineError("validation results label order differs")
    if validation_results.get("candidate_grid") != candidate_specifications():
        raise BaselineError("frozen candidate grid differs")
    candidates = validation_results.get("learned_candidates")
    if not isinstance(candidates, list) or len(candidates) != 8:
        raise BaselineError("selection did not evaluate exactly eight candidates")
    candidate_specs = [
        {
            key: candidate.get(key)
            for key in ("identifier", "family", "family_name", "C")
        }
        for candidate in candidates
    ]
    if candidate_specs != candidate_specifications():
        raise BaselineError("selection candidate configurations differ")
    for candidate in candidates:
        if candidate.get("status") != "success":
            raise BaselineError("all eight frozen candidates must have succeeded")
        predictions = candidate.get("metrics", {}).get("predictions", [])
        if [item.get("image_id") for item in predictions] != validation_ids:
            raise BaselineError("a candidate was not evaluated only on validation IDs")
    selected = select_learned_candidate(candidates)
    if (
        selected.get("identifier") != FROZEN_SELECTED_IDENTIFIER
        or selected.get("family") != "logistic_regression"
        or selected.get("C") != FROZEN_SELECTED_C
    ):
        raise BaselineError("validated selection does not produce the frozen model")
    if (
        validation_results.get("selected_learned_model_identifier")
        != FROZEN_SELECTED_IDENTIFIER
        or validation_results.get("selected_C") != FROZEN_SELECTED_C
    ):
        raise BaselineError("validation results selected model or C differs")
    selected_metrics = selected.get("metrics", {})
    if not np.isclose(selected_metrics.get("accuracy", np.nan), 2 / 3):
        raise BaselineError("selected validation accuracy differs")
    if not np.isclose(
        selected_metrics.get("macro_f1", np.nan), 0.6055555555555555
    ):
        raise BaselineError("selected validation macro F1 differs")
    if validation_results.get("final_test_evaluation") != "pending":
        raise BaselineError("validation results do not declare final test pending")
    if validation_results.get("test_images_decoded_or_evaluated") is not False:
        raise BaselineError("validation results do not confirm test exclusion")

    if selection_audit.get("final_test_evaluation") != "pending":
        raise BaselineError("selection audit does not declare final test pending")
    if selection_audit.get("learned_candidates_attempted") != 8:
        raise BaselineError("selection audit candidate count differs")
    if selection_audit.get("successful_learned_candidates") != 8:
        raise BaselineError("selection audit successful-candidate count differs")
    if selection_audit.get("failed_learned_candidates") != 0:
        raise BaselineError("selection audit reports candidate failures")
    if selection_audit.get("selected_configuration") != {
        "identifier": FROZEN_SELECTED_IDENTIFIER,
        "family": "logistic_regression",
        "C": FROZEN_SELECTED_C,
    }:
        raise BaselineError("selection audit selected configuration differs")
    if selection_audit.get("preprocessing_version") != PREPROCESSING_VERSION:
        raise BaselineError("selection audit preprocessing version differs")
    if selection_audit.get("random_seed") != DEFAULT_SEED:
        raise BaselineError("selection audit random seed differs")
    if selection_audit.get("input_manifest", {}).get("sha256") != validation[
        "manifest_sha256"
    ]:
        raise BaselineError("selection audit manifest hash differs")
    if selection_audit.get("split_audit", {}).get("sha256") != validation["audit"][
        "sha256"
    ]:
        raise BaselineError("selection audit split-audit hash differs")
    if selection_audit.get("counts") != {
        "train": 36,
        "validation": 12,
        "per_class": {
            label: {"train": 6, "validation": 2} for label in LABEL_ORDER
        },
    }:
        raise BaselineError("selection audit fitting counts differ")
    exclusion = selection_audit.get("test_exclusion", {})
    if not (
        exclusion.get("test_images_opened") == 0
        and exclusion.get("test_images_decoded") is False
        and exclusion.get("test_features_created") == 0
        and exclusion.get("test_rows_evaluated") == 0
    ):
        raise BaselineError("selection audit does not prove test exclusion")
    for name, record in selection_audit.get("feature_array_hashes", {}).items():
        if name not in arrays or record.get(
            "sha256_uncompressed_c_order_bytes"
        ) != array_sha256(arrays[name]):
            raise BaselineError("selection audit feature-array hashes differ")
    if set(selection_audit.get("feature_array_hashes", {})) != set(
        FEATURE_ARRAY_NAMES
    ):
        raise BaselineError("selection audit feature-array inventory differs")
    for key, relative in {
        "features": "features/train_validation_features.npz",
        "selected_model": "models/selected_train_only_model.joblib",
        "validation_results": "metrics/validation_results.json",
        "confusion_matrix": "plots/validation_confusion_matrix.png",
    }.items():
        if selection_audit.get("output_artifacts", {}).get(key, {}).get(
            "sha256"
        ) != actual_hashes[relative]:
            raise BaselineError("selection audit output-artifact hashes differ")

    serialized = json.dumps(
        {"validation_results": validation_results, "selection_audit": selection_audit},
        sort_keys=True,
    )
    if any(test_id in serialized for test_id in test_ids):
        raise BaselineError("test image ID leaked into selection JSON metadata")
    if not isinstance(selected_model, Pipeline):
        raise BaselineError("frozen selected model is not a Pipeline")
    if [name for name, _ in selected_model.steps] != [
        "standard_scaler",
        "classifier",
    ]:
        raise BaselineError("frozen selected pipeline steps differ")
    scaler = selected_model["standard_scaler"]
    if not isinstance(scaler, StandardScaler) or int(scaler.n_samples_seen_) != 36:
        raise BaselineError("frozen StandardScaler was not fitted on 36 rows")
    classifier = selected_model["classifier"]
    _verify_frozen_classifier(classifier)
    if set(classifier.classes_.tolist()) != set(LABEL_ORDER):
        raise BaselineError("frozen classifier classes differ")
    model_train_ids = tuple(
        getattr(selected_model, "gamma_training_image_ids_", ())
    )
    if len(model_train_ids) != 36 or list(model_train_ids) != train_ids:
        raise BaselineError("frozen model metadata does not record 36 training IDs")
    if test_ids & set(model_train_ids):
        raise BaselineError("test image ID found in selected-model metadata")
    if (
        getattr(selected_model, "gamma_baseline_version_", None) != BASELINE_VERSION
        or getattr(selected_model, "gamma_preprocessing_version_", None)
        != PREPROCESSING_VERSION
        or getattr(selected_model, "gamma_selected_identifier_", None)
        != FROZEN_SELECTED_IDENTIFIER
        or getattr(selected_model, "gamma_selected_C_", None) != FROZEN_SELECTED_C
    ):
        raise BaselineError("frozen selected-model metadata differs")
    return {
        "paths": artifact_paths,
        "hashes": actual_hashes,
        "validation_results": validation_results,
        "selection_audit": selection_audit,
        "arrays": arrays,
        "selected_model": selected_model,
    }


def preflight_final_test(
    manifest: str | os.PathLike[str],
    selection_artifacts: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    seed: int = DEFAULT_SEED,
    confirm_frozen_selection: bool = False,
    audit_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Validate frozen inputs without decoding images, fitting, or writing."""

    if not confirm_frozen_selection:
        raise BaselineError("final-test requires --confirm-frozen-selection")
    if seed != DEFAULT_SEED:
        raise BaselineError(f"{BASELINE_VERSION} final-test requires seed={DEFAULT_SEED}")
    output = Path(output_dir)
    repo_root = _verify_final_output_policy(output)
    validation = validate_manifest(manifest, audit_path=audit_path)
    if validation["audit"] is None:
        raise BaselineError("final-test requires a reconciled split audit")
    if validation["manifest_sha256"] != FROZEN_SPLIT_MANIFEST_SHA256:
        raise BaselineError("frozen split-manifest hash differs")
    if validation["audit"]["sha256"] != FROZEN_SPLIT_AUDIT_SHA256:
        raise BaselineError("frozen split-audit hash differs")
    selection = _verify_selection_artifacts(
        Path(selection_artifacts), validation
    )
    return {
        "baseline_version": BASELINE_VERSION,
        "manifest_sha256": validation["manifest_sha256"],
        "split_audit_sha256": validation["audit"]["sha256"],
        "selection_artifact_hashes": selection["hashes"],
        "selected_model": FROZEN_SELECTED_IDENTIFIER,
        "selected_C": FROZEN_SELECTED_C,
        "train_count": 36,
        "validation_count": 12,
        "fitting_count": 48,
        "test_count": 12,
        "test_images_decoded": 0,
        "models_fitted": 0,
        "artifacts_written": 0,
        "_repo_root": repo_root,
        "_validation": validation,
        "_selection": selection,
    }


def _frozen_final_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("standard_scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    C=FROZEN_SELECTED_C,
                    solver="lbfgs",
                    max_iter=5000,
                    tol=1e-4,
                    class_weight=None,
                    random_state=DEFAULT_SEED,
                ),
            ),
        ]
    )


def _fit_frozen_final_pipeline(
    X_fitting: np.ndarray,
    y_fitting: np.ndarray,
) -> tuple[Pipeline, list[dict[str, str]]]:
    pipeline = _frozen_final_pipeline()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pipeline.fit(X_fitting, y_fitting)
    warning_records = [
        {"category": warning.category.__name__, "message": str(warning.message)}
        for warning in caught
    ]
    if any(issubclass(warning.category, ConvergenceWarning) for warning in caught):
        raise BaselineError("ConvergenceWarning during frozen final fitting")
    return pipeline, warning_records


def _validate_final_staging(staging: Path) -> None:
    actual = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }
    if actual != set(FINAL_ARTIFACT_PATHS):
        raise BaselineError("staged final-test artifact set is incomplete or contains extras")
    model_path = staging / FINAL_ARTIFACT_PATHS[0]
    results_path = staging / FINAL_ARTIFACT_PATHS[1]
    audit_path = staging / FINAL_ARTIFACT_PATHS[2]
    plot_path = staging / FINAL_ARTIFACT_PATHS[3]
    model = joblib.load(model_path)
    if not isinstance(model, Pipeline):
        raise BaselineError("staged final model is not a Pipeline")
    if int(model["standard_scaler"].n_samples_seen_) != 48:
        raise BaselineError("staged final model scaler fitting count differs")
    _verify_frozen_classifier(model["classifier"])
    if len(getattr(model, "gamma_fitting_image_ids_", ())) != 48:
        raise BaselineError("staged final model fitting metadata differs")
    if hasattr(model, "gamma_test_image_ids_"):
        raise BaselineError("test IDs must not be stored in final model metadata")
    results = _load_json(results_path, "final test results")
    if len(results.get("test_metrics", {}).get("predictions", [])) != 12:
        raise BaselineError("staged final results must contain 12 predictions")
    matrix = results.get("test_metrics", {}).get("confusion_matrix", [])
    if len(matrix) != 6 or any(len(row) != 6 for row in matrix):
        raise BaselineError("staged final confusion matrix must be 6x6")
    if sum(sum(int(value) for value in row) for row in matrix) != 12:
        raise BaselineError("staged final confusion matrix must total 12")
    audit = _load_json(audit_path, "final test audit")
    expected_hashes = {
        "final_model": sha256_file(model_path),
        "results_json": sha256_file(results_path),
        "confusion_matrix": sha256_file(plot_path),
    }
    for key, expected_hash in expected_hashes.items():
        if audit.get("output_artifacts", {}).get(key, {}).get("sha256") != expected_hash:
            raise BaselineError("final audit output hash does not reconcile")
    ordering = audit.get("execution_ordering_evidence", {})
    if not (
        ordering.get("fitting_images_opened_before_fit") == 48
        and ordering.get("test_images_opened_during_fitting") == 0
        and ordering.get("test_images_opened_after_fitting") == 12
        and ordering.get("models_producing_test_predictions") == 1
        and ordering.get("test_prediction_calls") == 1
        and ordering.get("post_test_fit_calls") == 0
    ):
        raise BaselineError("final audit execution-ordering evidence differs")
    with Image.open(plot_path) as plot:
        plot.verify()


def run_final_test(
    manifest: str | os.PathLike[str],
    selection_artifacts: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    seed: int = DEFAULT_SEED,
    confirm_frozen_selection: bool = False,
    audit_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Refit the frozen pipeline once and evaluate the untouched test once."""

    preflight = preflight_final_test(
        manifest,
        selection_artifacts,
        output_dir,
        seed=seed,
        confirm_frozen_selection=confirm_frozen_selection,
        audit_path=audit_path,
    )
    output = Path(output_dir)
    repo_root = preflight["_repo_root"]
    validation = preflight["_validation"]
    selection = preflight["_selection"]
    fitting_rows = sorted(
        (
            row
            for row in validation["rows"]
            if row["split"] in {"train", "validation"}
        ),
        key=lambda row: row["image_id"],
    )
    test_rows = _rows_for_split(validation["rows"], "test")
    fitting_ids = [row["image_id"] for row in fitting_rows]
    test_ids = [row["image_id"] for row in test_rows]
    fitting_counts = Counter(row["label"] for row in fitting_rows)
    test_counts = Counter(row["label"] for row in test_rows)
    if len(fitting_rows) != 48 or fitting_counts != Counter(
        {label: 8 for label in LABEL_ORDER}
    ):
        raise BaselineError("final fitting set must contain 48 rows, eight per class")
    if len(test_rows) != 12 or test_counts != Counter(
        {label: 2 for label in LABEL_ORDER}
    ):
        raise BaselineError("final test set must contain 12 rows, two per class")

    staging: Path | None = None
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{output.name}.staging.",
                dir=output.parent,
            )
        )
        for relative in ("models", "metrics", "plots"):
            (staging / relative).mkdir()

        X_fitting = np.stack(
            [
                load_preprocessed_image(validation["image_paths"][row["image_id"]])
                for row in fitting_rows
            ]
        ).astype(np.float32, copy=False)
        y_fitting = np.asarray([row["label"] for row in fitting_rows], dtype=np.str_)
        if X_fitting.shape != (48, FEATURE_DIMENSION):
            raise BaselineError("final fitting feature array shape differs")
        if set(test_ids) & set(fitting_ids):
            raise BaselineError("test row entered the final fitting set")

        model, warning_records = _fit_frozen_final_pipeline(X_fitting, y_fitting)
        if int(model["standard_scaler"].n_samples_seen_) != 48:
            raise BaselineError("final StandardScaler was not fitted on 48 rows")
        _verify_frozen_classifier(model["classifier"])
        if set(model["classifier"].classes_.tolist()) != set(LABEL_ORDER):
            raise BaselineError("final classifier classes differ")
        model.gamma_baseline_version_ = BASELINE_VERSION
        model.gamma_preprocessing_version_ = PREPROCESSING_VERSION
        model.gamma_selected_identifier_ = FROZEN_SELECTED_IDENTIFIER
        model.gamma_selected_C_ = FROZEN_SELECTED_C
        model.gamma_fitting_image_ids_ = tuple(fitting_ids)
        model.gamma_fit_scope_ = (
            "exactly 48 train + validation rows; test rows excluded"
        )

        X_test = np.stack(
            [
                load_preprocessed_image(validation["image_paths"][row["image_id"]])
                for row in test_rows
            ]
        ).astype(np.float32, copy=False)
        y_test = np.asarray([row["label"] for row in test_rows], dtype=np.str_)
        if X_test.shape != (12, FEATURE_DIMENSION):
            raise BaselineError("final test feature array shape differs")
        predictions = model.predict(X_test)
        metrics = calculate_metrics(y_test, predictions, test_ids)

        model_path = staging / "models/final_train_validation_model.joblib"
        results_path = staging / "metrics/final_test_results.json"
        audit_output_path = staging / "metrics/final_test_audit.json"
        plot_path = staging / "plots/final_test_confusion_matrix.png"
        joblib.dump(model, model_path, compress=3)
        _plot_confusion_matrix(
            plot_path,
            metrics["confusion_matrix"],
            title="Frozen Logistic Regression — Final Test Set",
        )
        limitations = [
            "The final test set contains only 12 images and two examples per class.",
            "One test error changes accuracy by 8.33 percentage points.",
            "One error within a class changes that class's recall by 50 percentage points.",
            "All data comes from one capture session and one fixed layout and configuration.",
            "The split is source-group-aware, not capture-session-held-out.",
            "Results are feasibility evidence, not deployment-level generalization.",
        ]
        results_document = {
            "schema_version": FINAL_RESULTS_SCHEMA_VERSION,
            "baseline_version": BASELINE_VERSION,
            "evaluation": "frozen one-time final test",
            "frozen_configuration": frozen_final_configuration(),
            "label_order": list(LABEL_ORDER),
            "fitting_image_ids": fitting_ids,
            "test_image_ids": test_ids,
            "train_validation_count": 48,
            "per_class_fitting_counts": {
                label: int(fitting_counts[label]) for label in LABEL_ORDER
            },
            "test_count": 12,
            "per_class_test_counts": {
                label: int(test_counts[label]) for label in LABEL_ORDER
            },
            "test_metrics": metrics,
            "per_class_metrics": metrics["per_class"],
            "confusion_matrix": metrics["confusion_matrix"],
            "predictions_by_opaque_image_id": metrics["predictions"],
            "unknown_other_analysis": metrics["unknown_other_analysis"],
            "warnings": warning_records,
            "no_model_choice_changed_after_test_evaluation": True,
            "no_post_test_fitting_or_tuning": True,
            "limitations": limitations,
        }
        _write_json(results_path, results_document)

        manifest_path = Path(manifest)
        final_paths = {
            "final_model": output / "models/final_train_validation_model.joblib",
            "results_json": output / "metrics/final_test_results.json",
            "confusion_matrix": output / "plots/final_test_confusion_matrix.png",
        }
        staged_paths = {
            "final_model": model_path,
            "results_json": results_path,
            "confusion_matrix": plot_path,
        }

        def display_final(path: Path) -> str:
            if repo_root is not None:
                return _relative_display_path(path, repo_root)
            return f"{output.name}/{path.relative_to(output).as_posix()}"

        audit_document = {
            "schema_version": FINAL_AUDIT_SCHEMA_VERSION,
            "baseline_version": BASELINE_VERSION,
            "source_code": {
                "identifier": f"{BASELINE_VERSION}:final-test-v1",
                "path": _relative_display_path(Path(__file__), repo_root),
                "sha256": sha256_file(Path(__file__)),
            },
            "input_split_manifest": {
                "path": _relative_display_path(manifest_path, repo_root),
                "sha256": validation["manifest_sha256"],
            },
            "input_split_audit": {
                "path": _relative_display_path(validation["audit"]["path"], repo_root),
                "sha256": validation["audit"]["sha256"],
            },
            "input_selection_artifacts": {
                relative: {
                    "path": _relative_display_path(path, repo_root),
                    "sha256": selection["hashes"][relative],
                }
                for relative, path in selection["paths"].items()
            },
            "library_versions": _library_versions(),
            "random_seed": seed,
            "frozen_preprocessing": frozen_final_configuration()["preprocessing"],
            "frozen_model_hyperparameters": frozen_final_configuration()[
                "classifier"
            ],
            "feature_array_hashes": {
                "X_fitting": {
                    "sha256_uncompressed_c_order_bytes": array_sha256(X_fitting),
                    "dtype": str(X_fitting.dtype),
                    "shape": list(X_fitting.shape),
                },
                "y_fitting": {
                    "sha256_uncompressed_c_order_bytes": array_sha256(y_fitting),
                    "dtype": str(y_fitting.dtype),
                    "shape": list(y_fitting.shape),
                },
                "fitting_image_ids": {
                    "sha256_uncompressed_c_order_bytes": array_sha256(
                        np.asarray(fitting_ids, dtype=np.str_)
                    ),
                    "dtype": str(np.asarray(fitting_ids, dtype=np.str_).dtype),
                    "shape": [48],
                },
                "X_test": {
                    "sha256_uncompressed_c_order_bytes": array_sha256(X_test),
                    "dtype": str(X_test.dtype),
                    "shape": list(X_test.shape),
                },
                "y_test": {
                    "sha256_uncompressed_c_order_bytes": array_sha256(y_test),
                    "dtype": str(y_test.dtype),
                    "shape": list(y_test.shape),
                },
                "test_image_ids": {
                    "sha256_uncompressed_c_order_bytes": array_sha256(
                        np.asarray(test_ids, dtype=np.str_)
                    ),
                    "dtype": str(np.asarray(test_ids, dtype=np.str_).dtype),
                    "shape": [12],
                },
            },
            "counts": {
                "fitting": 48,
                "test": 12,
                "per_class_fitting": {
                    label: int(fitting_counts[label]) for label in LABEL_ORDER
                },
                "per_class_test": {
                    label: int(test_counts[label]) for label in LABEL_ORDER
                },
            },
            "execution_ordering_evidence": {
                "events": [
                    "metadata preflight completed before image decoding",
                    "48 fitting images decoded",
                    "one frozen pipeline fitted",
                    "12 test images decoded after fitting",
                    "one prediction call made by that pipeline",
                    "no fitting or tuning occurred after prediction",
                ],
                "fitting_images_opened_before_fit": 48,
                "test_images_opened_during_fitting": 0,
                "test_images_opened_after_fitting": 12,
                "fit_calls_before_test_prediction": 1,
                "models_producing_test_predictions": 1,
                "test_prediction_calls": 1,
                "post_test_fit_calls": 0,
            },
            "output_artifacts": {
                key: {
                    "path": display_final(path),
                    "sha256": sha256_file(staged_paths[key]),
                }
                for key, path in final_paths.items()
            },
            "zero_source_group_leakage": True,
            "final_evaluation_performed_once": True,
            "no_post_test_fitting_or_tuning": True,
            "no_model_choice_changed_after_test_evaluation": True,
        }
        _write_json(audit_output_path, audit_document)
        _validate_final_staging(staging)
        if output.exists() or output.is_symlink():
            raise BaselineError("final-test output directory appeared during staging")
        os.replace(staging, output)
        staging = None
    except BaselineError:
        raise
    except Exception as exc:
        raise BaselineError(
            f"final test failed before atomic installation: {exc}"
        ) from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    return {
        "baseline_version": BASELINE_VERSION,
        "selected_model": FROZEN_SELECTED_IDENTIFIER,
        "selected_C": FROZEN_SELECTED_C,
        "fitting_count": 48,
        "test_count": 12,
        "test_prediction_calls": 1,
        "post_test_fit_calls": 0,
        "output_dir": _relative_display_path(output, repo_root),
        "final_test_evaluation": "complete",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CS 4710 train/validation-only baseline selection"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="validate manifest metadata without decoding images"
    )
    validate_parser.add_argument("--manifest", required=True, type=Path)
    validate_parser.add_argument("--audit", type=Path)

    select_parser = subparsers.add_parser(
        "select", help="fit on train and select on validation only"
    )
    select_parser.add_argument("--manifest", required=True, type=Path)
    select_parser.add_argument("--output-dir", required=True, type=Path)
    select_parser.add_argument("--audit", type=Path)
    select_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    select_parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="required proof of intent to exclude the test split",
    )

    for command, help_text in (
        (
            "final-test-preflight",
            "validate frozen final-test inputs without decoding, fitting, or writing",
        ),
        (
            "final-test",
            "refit the frozen pipeline and evaluate the untouched test exactly once",
        ),
    ):
        final_parser = subparsers.add_parser(command, help=help_text)
        final_parser.add_argument("--manifest", required=True, type=Path)
        final_parser.add_argument(
            "--selection-artifacts", required=True, type=Path
        )
        final_parser.add_argument("--output-dir", required=True, type=Path)
        final_parser.add_argument("--audit", type=Path)
        final_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
        final_parser.add_argument(
            "--confirm-frozen-selection",
            action="store_true",
            required=True,
            help="required acknowledgement that model selection is frozen",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            validation = validate_manifest(args.manifest, audit_path=args.audit)
            result = {
                "baseline_version": BASELINE_VERSION,
                "manifest_sha256": validation["manifest_sha256"],
                "row_count": len(validation["rows"]),
                "class_counts": validation["class_counts"],
                "split_counts": validation["split_counts"],
                "source_group_count": validation["source_group_count"],
                "split_audit_reconciled": validation["audit"] is not None,
                "images_decoded": 0,
                "artifacts_written": 0,
            }
        elif args.command == "select":
            result = run_selection(
                args.manifest,
                args.output_dir,
                seed=args.seed,
                exclude_test=args.exclude_test,
                audit_path=args.audit,
            )
        elif args.command == "final-test-preflight":
            result = preflight_final_test(
                args.manifest,
                args.selection_artifacts,
                args.output_dir,
                seed=args.seed,
                confirm_frozen_selection=args.confirm_frozen_selection,
                audit_path=args.audit,
            )
            result = {
                key: value
                for key, value in result.items()
                if not key.startswith("_")
            }
        else:
            result = run_final_test(
                args.manifest,
                args.selection_artifacts,
                args.output_dir,
                seed=args.seed,
                confirm_frozen_selection=args.confirm_frozen_selection,
                audit_path=args.audit,
            )
    except BaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
