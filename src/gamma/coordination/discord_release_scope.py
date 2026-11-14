"""Exact permanent-release Discord transport binding, without provider imports."""
import re

_ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000",
    "http://[::1]:8000",
}

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def release_origin(value):
    if not isinstance(value, str):
        raise ValueError("release_origin must be a string")
    candidate = value
    if candidate.endswith("/"):
        candidate = candidate[:-1]
    if candidate in _ALLOWED_ORIGINS:
        return candidate
    raise ValueError("release_origin must be exactly http://127.0.0.1:8000 or http://[::1]:8000")


def validate_release_binding(binding):
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict")
    required_keys = {"release_id", "identity_id", "manifest_sha256", "scope", "purpose"}
    if set(binding.keys()) != required_keys:
        raise ValueError("binding must contain exactly the required keys")
    release_id = binding["release_id"]
    identity_id = binding["identity_id"]
    manifest_sha256 = binding["manifest_sha256"]
    scope = binding["scope"]
    purpose = binding["purpose"]
    if not isinstance(release_id, str) or not _HEX32_RE.fullmatch(release_id):
        raise ValueError("release_id must be lowercase ASCII hex32")
    if not isinstance(identity_id, str) or not _HEX32_RE.fullmatch(identity_id):
        raise ValueError("identity_id must be lowercase ASCII hex32")
    if not isinstance(manifest_sha256, str) or not _HEX64_RE.fullmatch(manifest_sha256):
        raise ValueError("manifest_sha256 must be lowercase ASCII hex64")
    if scope != "private_release_owner":
        raise ValueError("scope must be 'private_release_owner'")
    if purpose != "permanent":
        raise ValueError("purpose must be 'permanent'")
    return dict(binding)


def release_owner_matches(observed, expected):
    try:
        validate_release_binding(expected)
    except ValueError:
        return False
    if not isinstance(observed, dict):
        return False
    required_keys = {"release_id", "identity_id", "manifest_sha256", "scope", "purpose"}
    for key in required_keys:
        if key not in observed:
            return False
        if type(observed[key]) is not type(expected[key]) or observed[key] != expected[key]:
            return False
    return True
