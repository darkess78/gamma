from __future__ import annotations

import re

from .models import TwitchSafetyClassification, TrustLevel


_URL_RE = re.compile(r"\b(?:https?://|www\.|[a-z0-9.-]+\.(?:com|net|org|gg|tv|xyz|info|biz)\b)", re.IGNORECASE)
_BOT_WORDS = {"nightbot", "streamelements", "streamlabs", "moobot"}
_SCAM_PHRASES = (
    "buy followers",
    "buy viewers",
    "buy views",
    "cheap viewers",
    "viewbot",
    "best viewers",
    "promotion at",
)
# Instruction-override attempts. Plain phrases like "act as" or "you are" are
# ordinary chat and are deliberately not matched.
_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|your\s+|the\s+|my\s+)*"
    r"(?:previous\s+|prior\s+|earlier\s+|above\s+)?(?:instructions|rules|prompts?|guidelines)\b"
    r"|\bsystem\s+prompt\b|\bdeveloper\s+(?:message|mode)\b|\breveal\s+your\s+(?:prompt|instructions)\b"
    r"|\bnew\s+instructions\s*:|\bjailbreak\b",
    re.IGNORECASE,
)
_REACTION_WORDS = {"lol", "lmao", "haha", "hahaha", "wtf", "wow", "nice", "rip"}
_STREAM_CONTEXT_WORDS = {"chat", "stream", "boss", "game", "run", "build", "clip"}
_RUDE_RE = re.compile(r"\b(?:idiot|stupid|shut up|trash)\b")
_LINK_TRUSTED = {"owner", "trusted", "regular"}


def classify_chat_text(text: str, *, display_name: str | None = None, trust_level: TrustLevel = "new_viewer") -> TwitchSafetyClassification:
    """Classify a chat message for safety and priority.
    
    Args:
        text: Chat message text to classify.
        display_name: Message sender's display name.
        trust_level: Viewer trust level.
        
    Returns:
        TwitchSafetyClassification: Safety classification with safe text, priority, and reasons.
    """
    raw = text.strip()
    lowered = raw.lower()
    reasons: list[str] = []
    priority_delta = 0

    if trust_level == "blocked":
        return TwitchSafetyClassification(
            category="blocked_viewer",
            safe_prompt_text="A blocked viewer posted a message.",
            should_drop=True,
            priority_delta=-100,
            reasons=["blocked_viewer"],
        )

    if _looks_like_known_bot(display_name):
        return TwitchSafetyClassification(
            category="known_bot",
            safe_prompt_text="A known chat bot posted an automated message.",
            should_drop=True,
            priority_delta=-50,
            reasons=["known_bot"],
        )

    has_url = bool(_URL_RE.search(raw))
    has_scam_phrase = any(phrase in lowered for phrase in _SCAM_PHRASES)
    if has_url:
        reasons.append("contains_url")
    if has_scam_phrase:
        reasons.append("spam_phrase")
    if has_url and not has_scam_phrase and trust_level in _LINK_TRUSTED:
        has_url = False  # trusted viewers may share links; the link is still not followed
    if has_url or has_scam_phrase:
        return TwitchSafetyClassification(
            category="spam_or_scam",
            safe_prompt_text="A spam or scam message was posted in chat.",
            should_drop=False,
            priority_delta=-25,
            reasons=reasons,
        )

    if _PROMPT_INJECTION_RE.search(raw):
        return TwitchSafetyClassification(
            category="prompt_injection",
            safe_prompt_text="A viewer tried to give instructions to the assistant.",
            should_drop=False,
            priority_delta=-10,
            reasons=["prompt_injection"],
        )

    if "shana" in lowered or "gamma" in lowered:
        priority_delta += 5
        reasons.append("direct_mention")
    else:
        ambient_delta, ambient_reasons = _ambient_priority(raw, lowered)
        priority_delta += ambient_delta
        reasons.extend(ambient_reasons)
    if trust_level in {"trusted", "regular"}:
        priority_delta += 1
        reasons.append(f"trust_{trust_level}")
    if trust_level == "suspicious":
        priority_delta -= 5
        reasons.append("trust_suspicious")

    return TwitchSafetyClassification(
        category="normal",
        safe_prompt_text=raw,
        should_drop=False,
        priority_delta=priority_delta,
        reasons=reasons,
    )


def _ambient_priority(raw: str, lowered: str) -> tuple[int, list[str]]:
    """Calculate ambient chat priority and reasons.
    
    Args:
        raw: Original raw text.
        lowered: Lowercased version of text.
        
    Returns:
        tuple[int, list[str]]: (Priority delta, list of ambient reasons).
    """
    words = re.findall(r"[a-z0-9']+", lowered)
    if not words:
        return 0, []
    delta = 0
    reasons: list[str] = []
    if "?" in raw and len(words) >= 4:
        delta += 3
        reasons.append("ambient_question")
    if any(word in _REACTION_WORDS for word in words):
        delta += 2
        reasons.append("ambient_reaction")
    if any(word in _STREAM_CONTEXT_WORDS for word in words):
        delta += 2
        reasons.append("ambient_stream_context")
    if 6 <= len(words) <= 24:
        delta += 1
        reasons.append("ambient_reactable_length")
    if _RUDE_RE.search(lowered):
        delta -= 4
        reasons.append("ambient_rude_tone")
    return max(-5, min(delta, 6)), reasons


def safe_username_alias(display_name: str | None, *, fallback: str = "a viewer") -> str:
    """Generate a safe display name or fallback alias.
    
    Args:
        display_name: Original display name.
        fallback: Fallback string if name is unsafe.
        
    Returns:
        str: Safe username or fallback.
    """
    name = (display_name or "").strip()
    if not name:
        return fallback
    lowered = name.lower()
    if _URL_RE.search(name) or any(phrase.replace(" ", "") in lowered for phrase in _SCAM_PHRASES):
        return fallback
    if not re.fullmatch(r"[A-Za-z0-9_]{2,25}", name):
        return fallback
    if name.count("_") >= 2:
        return fallback
    if sum(1 for char in name if char.isalpha()) < 2:
        return fallback
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    spaced = re.sub(r"[_]+", " ", spaced)
    spaced = re.sub(r"\d+$", "", spaced).strip()
    return spaced or fallback


def _looks_like_known_bot(display_name: str | None) -> bool:
    """Check if display name looks like a known chat bot.
    
    Args:
        display_name: Display name to check.
        
    Returns:
        bool: True if name matches known bot names.
    """
    if not display_name:
        return False
    return display_name.strip().lower() in _BOT_WORDS
