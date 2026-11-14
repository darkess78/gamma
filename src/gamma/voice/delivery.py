# Reply-wide voice delivery policy.

_VALID_EMOTIONS = {
    "neutral",
    "happy",
    "teasing",
    "concerned",
    "excited",
    "embarrassed",
    "annoyed",
}


def resolve_emotion(
    *,
    emotion: str | None = None,
    tag_emotion: str | None = None,
    mood: str | None = None,
) -> str:
    """Select the first valid canonical emotion value.

    Priority order: emotion, tag_emotion, mood, then neutral.
    An explicit neutral value wins over any other valid value.
    """
    for value in (emotion, tag_emotion, mood):
        if value is None:
            continue
        if isinstance(value, str) and value in _VALID_EMOTIONS:
            return value
    return "neutral"
