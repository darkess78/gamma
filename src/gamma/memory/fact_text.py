"""Normalizing memory text and working out what a fact is about (slots, keys, signatures)."""
from __future__ import annotations

import re



def _normalize_whitespace(text: str) -> str:
    """Remove extra whitespace from text.
    
    Args:
        text: Input text.
    
    Returns:
        str: Normalized text.
    """
    return " ".join(text.strip().split())


def _canonicalize_profile_text(text: str) -> str:
    """Remove punctuation from profile text.
    
    Args:
        text: Input text.
    
    Returns:
        str: Canonicalized text.
    """
    normalized = _normalize_whitespace(text)
    normalized = re.sub(r"[.!?]+$", "", normalized)
    return normalized.strip()


def _profile_slot(category: str, fact_text: str) -> str | None:
    """Map profile facts to canonical slots.
    
    Args:
        category: Fact category (identity/preference/project).
        fact_text: Fact text.
    
    Returns:
        str | None: Canonical slot path or None.
    """
    lowered = fact_text.lower().strip()
    if category == "identity" and lowered.startswith("my name is "):
        return "identity:name"
    if category in {"preference", "user_preference", "opinion"}:
        if lowered.startswith("i do not like ") or lowered.startswith("remember that i do not like "):
            return "preference:dislike"
        if lowered.startswith("i dislike ") or lowered.startswith("remember that i dislike "):
            return "preference:dislike"
        if lowered.startswith("i hate ") or lowered.startswith("remember that i hate "):
            return "preference:dislike"
        if lowered.startswith("my favorite "):
            return "preference:favorite"
        if lowered.startswith("i prefer ") or lowered.startswith("remember that i prefer "):
            return "preference:prefer"
        if lowered.startswith("i like ") or lowered.startswith("remember that i like "):
            return "preference:like"
    if category in {"project", "project_state"}:
        if lowered.startswith("i am working on ") or lowered.startswith("i'm working on "):
            return "project:active"
        if lowered.startswith("the main project is "):
            return "project:active"
    return None


def _fact_key(category: str, fact_text: str) -> str | None:
    """What a changeable fact is about, so a newer one replaces only the matching older one.

    Your name; how you feel about one particular thing (like and dislike share a key,
    so changing your mind replaces it); your favourite of one particular kind; your
    main project. Anything else accumulates.
    """
    slot = _profile_slot(category, fact_text)
    if slot in {"identity:name", "project:active"}:
        return slot
    subject = _extract_preference_subject(slot, fact_text)
    if not subject:
        return None
    if slot in {"preference:like", "preference:dislike"}:
        return f"preference:feeling:{subject}"
    if slot == "preference:favorite":
        kind = subject.split(" is ", 1)[0].split(" are ", 1)[0].strip()
        return f"preference:favorite:{kind}" if kind and kind != subject else None
    return None


def _memory_text_signature(text: str) -> str:
    """Get signature for matching memory items.
    
    Args:
        text: Input text.
    
    Returns:
        str: Lowercase signature.
    """
    normalized = _normalize_whitespace(text).lower()
    normalized = re.sub(r"[.!?]+$", "", normalized)
    return normalized.strip()


def _episodic_signature(text: str) -> str:
    """Get signature for matching episodic memory.
    
    Args:
        text: Input text.
    
    Returns:
        str: Lowercase episodic signature.
    """
    normalized = _memory_text_signature(text)
    normalized = re.sub(r"\|\s*assistant replied:.*$", "", normalized)
    normalized = re.sub(r"^user said:\s*", "", normalized)
    normalized = re.sub(r"\b(user said|assistant replied)\b", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.strip()
    normalized = re.sub(r"[.!?]+$", "", normalized)
    return normalized.strip()


def _extract_preference_subject(slot: str | None, fact_text: str) -> str | None:
    """Extract preference subject from fact text.
    
    Args:
        slot: Canonical slot path.
        fact_text: Fact text.
    
    Returns:
        str | None: Preference subject or None.
    """
    if not slot or not slot.startswith("preference:"):
        return None
    lowered = _memory_text_signature(fact_text)
    prefixes = (
        "remember that i do not like ",
        "i do not like ",
        "remember that i dislike ",
        "i dislike ",
        "remember that i hate ",
        "i hate ",
        "my favorite ",
        "remember that i prefer ",
        "i prefer ",
        "remember that i like ",
        "i like ",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return lowered[len(prefix):].strip() or None
    return None


def _normalize_subject_name(value: str | None) -> str | None:
    """Normalize subject name to fixed length.
    
    Args:
        value: Subject name.
    
    Returns:
        str | None: Normalized name or None.
    """
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized[:120] if normalized else None


def _normalize_identity(value: object, *, limit: int = 200) -> str:
    return " ".join(str(value or "").strip().split())[:limit]
