"""Strict stdlib-only protocol validation module."""

MAX_PROMPT_BYTES = 32768
MAX_REPLY_BYTES = 16384


def validate_temperature(value):
    import math
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("invalid review temperature")
    return value


def validate_request(value):
    """Validate a request mapping and return the validated fields.

    Returns a dict with exactly system_prompt, user_text, model_override.
    Raises ValueError for any malformed input.
    """
    if type(value) is not dict:
        raise ValueError("Invalid input")
    if not set(value.keys()) <= {"system_prompt", "user_text", "model_override"}:
        raise ValueError("Invalid keys")
    if "system_prompt" not in value or "user_text" not in value:
        raise ValueError("Missing required field")
    sp, ut = value["system_prompt"], value["user_text"]
    if type(sp) is not str or type(ut) is not str:
        raise ValueError("Invalid field type")
    if "\x00" in sp or "\x00" in ut:
        raise ValueError("Invalid content")
    try:
        total = len(sp.encode("utf-8")) + len(ut.encode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise ValueError("Invalid encoding")
    if total > MAX_PROMPT_BYTES:
        raise ValueError("Too large")
    mo = None
    if "model_override" in value:
        mo = value["model_override"]
        if mo is not None:
            if type(mo) is not str or not mo or len(mo) > 128:
                raise ValueError("Invalid override")
            if any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in mo):
                raise ValueError("Invalid override")
    return {"system_prompt": sp, "user_text": ut, "model_override": mo}


def validate_reply(value):
    """Validate a reply string and return it unchanged.

    Raises ValueError if the value is not a valid UTF-8-safe str within limits.
    """
    if type(value) is not str:
        raise ValueError("Invalid input")
    if "\x00" in value:
        raise ValueError("Invalid content")
    try:
        n = len(value.encode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise ValueError("Invalid encoding")
    if n > MAX_REPLY_BYTES:
        raise ValueError("Too large")
    return value
