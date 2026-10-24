"""Bounded versioned JSON frames for the private brain transport."""
import json
import re
import math

def _invalid_constant(value):
    raise ValueError("nonfinite number")

def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite number")
    return result

VERSION = 1
MAX_FRAME = 1048576
_ALLOWED_TYPES = {'ready', 'execute', 'callback', 'callback_result', 'result', 'error'}
_HEX32_RE = re.compile(r'^[0-9a-f]{32}$')

def _no_duplicates(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"duplicate key: {k}")
        d[k] = v
    return d

def _validate_envelope(message):
    if not isinstance(message, dict):
        raise ValueError("envelope must be a dict")
    if 'schema_version' not in message:
        raise ValueError("missing schema_version")
    sv = message['schema_version']
    if isinstance(sv, bool) or not isinstance(sv, int) or sv != VERSION:
        raise ValueError("invalid schema_version")
    gen = message.get('generation')
    if not isinstance(gen, str) or not _HEX32_RE.fullmatch(gen):
        raise ValueError("invalid generation")
    req_id = message.get('request_id')
    typ = message.get('type')
    if type(typ) is not str or typ not in _ALLOWED_TYPES:
        raise ValueError("invalid type")
    if typ == 'ready':
        if req_id is not None and (not isinstance(req_id, str) or not _HEX32_RE.fullmatch(req_id)):
            raise ValueError("invalid request_id for ready")
    else:
        if not isinstance(req_id, str) or not _HEX32_RE.fullmatch(req_id):
            raise ValueError("invalid request_id")
    # extra fields allowed
    return None

def encode(message):
    _validate_envelope(message)
    try:
        json_str = json.dumps(message, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"invalid JSON: {e}") from e
    data = json_str.encode('utf-8') + b'\n'
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large")
    return data

def decode(raw):
    if not isinstance(raw, bytes):
        raise ValueError("raw must be bytes")
    if len(raw) > MAX_FRAME:
        raise ValueError("frame too large")
    if not raw.endswith(b'\n'):
        raise ValueError("unterminated frame")
    raw = raw[:-1]
    try:
        msg = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_invalid_constant, parse_float=_finite_float)
    except json.JSONDecodeError as e:
        raise ValueError("malformed JSON") from e
    except ValueError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    _validate_envelope(msg)
    return msg
