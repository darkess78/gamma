"""Pure eligibility and renderer pins for the optional private prefix cache."""
import hashlib

_MODEL = "qwen3.8:27b"
_ENDPOINT = "http://127.0.0.1:11434"
_DIGEST = "22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643"
_VERSION = "0.34.2"
_TEMPLATE_SHA = "b507b9c2f6ca642bffcd06665ea7c91f235fd32daeefdf875a0f938db05fb315"
_FAMILY = "qwen35"

_START_TAG = chr(60) + "|im_" + "start|" + chr(62)
_END_TAG = chr(60) + "|im_" + "end|" + chr(62)

_HEADERS = (
    "# Core Persona",
    "# Boundaries",
    "# Private interaction capability",
    "# Turn Decision Contract",
)

_SEPARATOR = "\n\n# Private Coordination Context\n"


def _utf8_len(s):
    return len(s.encode("utf-8"))


def _has_tag(s, tag):
    return tag in s


def private_prefix(system_prompt, *, model, endpoint, has_images):
    if type(system_prompt) is not str:
        return None
    if model != _MODEL:
        return None
    if endpoint != _ENDPOINT:
        return None
    if has_images is not False:
        return None

    try:
        length = _utf8_len(system_prompt)
    except UnicodeError:
        return None
    if length > 32000:
        return None

    if _has_tag(system_prompt, _START_TAG) or _has_tag(system_prompt, _END_TAG):
        return None

    if system_prompt.count(_SEPARATOR) != 1:
        return None

    idx = system_prompt.find(_SEPARATOR)
    prefix = system_prompt[:idx]

    for header in _HEADERS:
        if prefix.count(header + "\n") != 1:
            return None

    raw = _START_TAG + "system" + "\n" + prefix
    if _utf8_len(raw) > 24000:
        return None

    return raw


def renderer_verified(version, show, tags):
    if type(version) is not dict or type(show) is not dict or type(tags) is not dict:
        return False

    if version.get("version") != _VERSION:
        return False

    template = show.get("template")
    if type(template) is not str:
        return False
    try:
        template_sha = hashlib.sha256(template.encode("utf-8")).hexdigest()
    except UnicodeError:
        return False
    if template_sha != _TEMPLATE_SHA:
        return False

    details = show.get("details")
    if not isinstance(details, dict):
        return False
    if details.get("family") != _FAMILY:
        return False

    model_info = show.get("model_info")
    if not isinstance(model_info, dict):
        return False
    if model_info.get("general.architecture") != _FAMILY:
        return False

    models = tags.get("models")
    if type(models) is not list:
        return False
    matches = [entry for entry in models if type(entry) is dict
               and (entry.get("name") == _MODEL or entry.get("model") == _MODEL)]
    if len(matches) != 1:
        return False

    entry = matches[0]
    if not isinstance(entry, dict):
        return False

    name = entry.get("name")
    model = entry.get("model")
    digest = entry.get("digest")

    if name == _MODEL or model == _MODEL:
        if digest != _DIGEST:
            return False
    else:
        return False

    return True


def priming_payload(prefix):
    if type(prefix) is not str:
        raise ValueError("invalid prefix")

    wrapper = _START_TAG + "system" + "\n"
    if not prefix.startswith(wrapper):
        raise ValueError("invalid wrapper")

    body = prefix[len(wrapper):]

    try:
        length = _utf8_len(prefix)
    except UnicodeError as exc:
        raise ValueError("invalid prefix encoding") from exc
    if length > 24000:
        raise ValueError("prefix too long")

    if _has_tag(body, _START_TAG) or _has_tag(body, _END_TAG):
        raise ValueError("extra tags")

    if _SEPARATOR in body:
        raise ValueError("dynamic context in prefix")

    pre = body

    for header in _HEADERS:
        if pre.count(header + "\n") != 1:
            raise ValueError("header format")

    return {
        "model": _MODEL,
        "prompt": prefix,
        "raw": True,
        "think": False,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_ctx": 8192,
            "num_batch": 512,
            "temperature": 0.2,
            "num_predict": 1,
        },
    }
