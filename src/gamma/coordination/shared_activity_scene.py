"""Reviewed worker helper: stable visual meaning, excluding fresh-frame transport metadata."""
import hashlib
import json

_REQUIRED_KEYS = (
    "source_epoch",
    "scene_revision",
    "source_id",
    "kind",
    "content_sha256",
    "interpretation_source_id",
    "goal_source_id",
    "goal",
    "mode",
    "label",
)


def scene_fingerprint(scene):
    if not isinstance(scene, dict):
        raise ValueError("scene must be a dict")

    missing = [key for key in _REQUIRED_KEYS if key not in scene]
    if missing:
        raise ValueError("missing required keys: %s" % ", ".join(missing))

    canonical = {key: scene[key] for key in _REQUIRED_KEYS}
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
