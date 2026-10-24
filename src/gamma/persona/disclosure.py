"""Recipient views for explicitly authored public character configuration."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

# Every required field/container in the reviewed authored Shana definition.
_PUBLIC_SCHEMA = {
    "name": "str",
    "description": "str",
    "primary_purpose": "str",
    "core_personality": "str",
    "temperament": {
        "baseline": "str",
        "under_stress": "str",
        "when_embarrassed": "str",
        "when_angry": "str",
        "when_attached": "str",
        "when_vulnerable": "str"
    },
    "key_traits": {
        "positive": [
            "str"
        ],
        "flawed": [
            "str"
        ]
    },
    "likes": [
        "str"
    ],
    "dislikes": [
        "str"
    ],
    "insecurities": [
        "str"
    ],
    "speech_style": {
        "tone": "str",
        "length": "str",
        "vocabulary": "str",
        "habits": [
            "str"
        ],
        "do_not_write_as": [
            "str"
        ]
    },
    "behavior_rules": [
        "str"
    ],
    "relationship_style": {
        "with_strangers": "str",
        "with_friends": "str",
        "with_romantic_interest": "str",
        "with_someone_she_distrusts": "str"
    },
    "romance_guidelines": "str",
    "scenario_notes": {
        "casual_chat": "str",
        "emotional_scene": "str",
        "conflict": "str",
        "comforting_someone": "str"
    },
    "first_message": "str",
    "example_dialogue": [
        {
            "user": "str",
            "shana": "str"
        }
    ]
}
_RESERVED = frozenset({"contract_version", "authored_public", "private_overlay"})
_ERROR = "persona_public_contract_invalid"


def _public_value(value: Any, schema: Any) -> Any:
    if schema == "str":
        if type(value) is not str:
            raise ValueError(_ERROR)
        return value
    if type(schema) is list:
        if type(value) is not list:
            raise ValueError(_ERROR)
        return [_public_value(item, schema[0]) for item in value]
    if type(value) is not dict or any(key not in value for key in schema):
        raise ValueError(_ERROR)
    return {key: _public_value(value[key], child) for key, child in schema.items()}


def persona_view(document: Any, *, private: bool) -> dict:
    """Select declared author-public values or preserved private legacy layers.

    Declaring text in authored_public intentionally publishes it. Structural
    validation does not infer the semantic privacy of natural-language strings.
    """
    wrapper = type(document) is dict and bool(_RESERVED.intersection(document))
    if private and not wrapper:
        if not document:
            return {}
        return deepcopy(document) if type(document) is dict else {"value": deepcopy(document)}
    if not wrapper or type(document.get("contract_version")) is not int or document["contract_version"] != 1:
        raise ValueError(_ERROR)
    public = _public_value(document.get("authored_public"), _PUBLIC_SCHEMA)
    overlay = document.get("private_overlay", {})
    if type(overlay) is not dict:
        raise ValueError(_ERROR)
    if private:
        return merge_persona_layers(document["authored_public"], document, overlay)
    return public


_STYLE_KEYS = ("humor", "teasing", "affection", "verbosity", "confidence", "entertainment")


def _merge_maps(base: dict, overlay: dict) -> dict:
    result = deepcopy(base)
    for key, value in overlay.items():
        if type(result.get(key)) is dict and type(value) is dict:
            result[key] = _merge_maps(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def merge_persona_layers(public: dict, root: dict, overlay: dict) -> dict:
    """Merge private layers; control names are reserved only at their roots."""
    layers = [{key: value for key, value in layer.items() if key not in _RESERVED}
              for layer in (public, root, overlay)]
    return _merge_maps(_merge_maps(layers[0], layers[1]), layers[2])


def style_view(value: Any, *, private: bool) -> Any:
    if private:
        return deepcopy(value)
    if type(value) is not dict:
        return {}
    return {key: candidate for key in _STYLE_KEYS
            if key in value
            and type(candidate := value[key]) in (int, float)
            and 0 <= candidate <= 1 and math.isfinite(candidate)}
