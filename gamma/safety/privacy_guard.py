from __future__ import annotations

import re
from dataclasses import dataclass


PRIVACY_REFUSAL = "I'm not going to share private info like that."

_QUESTION_MARKERS = re.compile(
    r"\b(what|whats|what's|where|where's|wheres|which|give|tell|show|say|reveal|drop|post|leak|find|lookup|look up)\b",
    re.IGNORECASE,
)
_SUBJECT_MARKERS = re.compile(
    r"\b(your|you|u|shana|gamma|me|my|mine|owner|user|creator|developer|dev|streamer|neety|they|them|their|he|him|his|she|her)\b",
    re.IGNORECASE,
)
_DIRECT_LOCATION_QUESTIONS = [
    re.compile(r"\bwhere\s+(do|does|did|is|are|was|were)\s+[^?.!,]{0,80}\b(live|stay|sleep|reside|located|based)\b", re.IGNORECASE),
    re.compile(r"\bwhere\s+[^?.!,]{0,80}\b(home|house|apartment|apt|place)\b", re.IGNORECASE),
]
_SENSITIVE_REQUEST_PATTERNS = [
    re.compile(r"\b(ip|ipv4|ipv6)\s*(address|addr)?\b", re.IGNORECASE),
    re.compile(r"\b(home|physical|street|mailing|billing|shipping|residential)\s+address\b", re.IGNORECASE),
    re.compile(r"\b(address|addy)\b", re.IGNORECASE),
    re.compile(r"\b(phone|cell|mobile)\s*(number|#)?\b", re.IGNORECASE),
    re.compile(r"\b(email|e-mail)\s*(address)?\b", re.IGNORECASE),
    re.compile(r"\b(real|legal|full)\s+name\b", re.IGNORECASE),
    re.compile(r"\b(last name|surname)\b", re.IGNORECASE),
    re.compile(r"\b(ssn|social security|social)\s*(number)?\b", re.IGNORECASE),
    re.compile(r"\b(date of birth|dob|birthday)\b", re.IGNORECASE),
    re.compile(r"\b(workplace|work address|school|license plate)\b", re.IGNORECASE),
    re.compile(r"\b(latitude|longitude|gps|coordinates|coords)\b", re.IGNORECASE),
]
_EDUCATIONAL_PATTERNS = [
    re.compile(r"\bwhat\s+is\s+an?\s+ip\s+address\b", re.IGNORECASE),
    re.compile(r"\bhow\s+do\s+ip\s+addresses\s+work\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+is\s+an?\s+address\b", re.IGNORECASE),
]

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
_STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'-]{1,60}\s+"
    r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|cir|circle|way|place|pl|terrace|ter)\b",
    re.IGNORECASE,
)
_COORDINATE_RE = re.compile(r"\b-?\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\b")


@dataclass(slots=True)
class PrivacyDecision:
    blocked: bool
    matched_rules: list[str]
    replacement_text: str = PRIVACY_REFUSAL


def review_private_info_request(text: str) -> PrivacyDecision:
    normalized = _normalize(text)
    if not normalized:
        return PrivacyDecision(blocked=False, matched_rules=[])
    if any(pattern.search(normalized) for pattern in _EDUCATIONAL_PATTERNS):
        return PrivacyDecision(blocked=False, matched_rules=[])

    matched: list[str] = []
    for pattern in _DIRECT_LOCATION_QUESTIONS:
        if pattern.search(normalized):
            matched.append(pattern.pattern)

    asks_for_info = bool(_QUESTION_MARKERS.search(normalized))
    has_subject = bool(_SUBJECT_MARKERS.search(normalized))
    if asks_for_info and has_subject:
        matched.extend(pattern.pattern for pattern in _SENSITIVE_REQUEST_PATTERNS if pattern.search(normalized))

    return PrivacyDecision(blocked=bool(matched), matched_rules=matched)


def review_private_info_output(text: str) -> PrivacyDecision:
    normalized = _normalize(text)
    if not normalized:
        return PrivacyDecision(blocked=False, matched_rules=[])

    matched: list[str] = []
    for name, pattern in [
        ("ipv4_address", _IPV4_RE),
        ("ipv6_address", _IPV6_RE),
        ("email_address", _EMAIL_RE),
        ("phone_number", _PHONE_RE),
        ("street_address", _STREET_ADDRESS_RE),
        ("coordinates", _COORDINATE_RE),
    ]:
        if pattern.search(normalized):
            matched.append(name)
    return PrivacyDecision(blocked=bool(matched), matched_rules=matched)


def _normalize(text: str) -> str:
    return " ".join((text or "").replace("_", " ").split())
