"""Three safety tiers for everything Shana says.

1. **Safety floor** (always on; ``speech_filter_hard_block_enabled`` exists only in the config
   file): the privacy guard, the floor word list and patterns, the heuristic block patterns
   (doxxing, "you deserve to die"), and the reviewer's severe categories (hate, sexual, minors,
   self-harm, threats, dangerous help, privacy). A floor stop silences the whole reply. The line
   is kept out of everything Shana remembers and goes to the admin log instead.
2. **Word list** (dashboard switch): bleeps the words in ``safety_banned_words.txt``.
3. **Extra safety** (dashboard switch): softens replies the reviewer calls rude (and, at the
   strict level, mild insults), and caps listed curse words when the word list is off.

``speech_filter_level = "none"`` switches off tiers 2 and 3; the floor stays.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..config import app_config_path, app_local_config_path, load_app_file_config, settings
from .floor_log import record_floor_event
from .hard_blocklist import BLEEP, bleep_words, floor_matches, word_list_matches
from .heuristic_filter import review as heuristic_review
from .llm_reviewer import SpeechLLMReviewer
from .privacy_guard import review_private_info_output
from .rewrite_guard import rewrite_text


@dataclass(slots=True)
class SafetyPolicyResult:
    """Outcome of the tiers.

    ``action`` is allow, bleep, soften, privacy_refusal or floor_block. ``floor_blocked`` means
    the reply must not be spoken, shown or remembered.
    """
    spoken_text: str
    blocked: bool
    matched_rules: list[str]
    action: str
    layers: list[str]
    floor_blocked: bool = False
    tiers: list[str] = field(default_factory=list)


class SpeechSafetyPolicy:
    def __init__(self, level: str) -> None:
        self._level = (level or "strict").strip().lower()
        self._reviewer = SpeechLLMReviewer()

    def apply(self, text: str, *, include_llm: bool = True, source: str = "",
              session_id: str | None = None) -> SafetyPolicyResult:
        normalized = " ".join((text or "").split())
        if not normalized:
            return SafetyPolicyResult(spoken_text="", blocked=False, matched_rules=[], action="allow", layers=[])
        level, word_list_switch, extra_switch = live_switches(self._level)
        optional_tiers = level != "none"
        word_list_on = optional_tiers and word_list_switch
        extra_on = optional_tiers and extra_switch

        privacy = review_private_info_output(normalized)
        if privacy.blocked:
            return SafetyPolicyResult(
                spoken_text=privacy.replacement_text,
                blocked=True,
                matched_rules=privacy.matched_rules,
                action="privacy_refusal",
                layers=["privacy_guard"],
                tiers=["floor"],
            )

        floor_on = settings.speech_filter_hard_block_enabled
        floor_rules: list[str] = []
        floor_layers: list[str] = []
        if floor_on:
            hits = floor_matches(normalized)
            if hits:
                floor_rules.extend(hits)
                floor_layers.append("floor_list")
        heuristic = heuristic_review(text=normalized, level=level) if settings.speech_filter_heuristic_enabled else None
        if floor_on and not floor_rules and heuristic is not None and heuristic.severe:
            floor_rules.extend(heuristic.matched_rules)
            floor_layers.append("heuristic")

        decision = None
        if not floor_rules and include_llm and settings.speech_filter_llm_enabled:
            decision = self._reviewer.review(normalized)
            if floor_on and decision.action == "block":
                floor_rules.append(decision.reason or "llm_block")
                floor_layers.append("llm")

        if floor_rules:
            record_floor_event(text=normalized, rules=floor_rules, layers=floor_layers, source=source,
                               session_id=session_id)
            return SafetyPolicyResult(
                spoken_text="",
                blocked=True,
                matched_rules=floor_rules,
                action="floor_block",
                layers=floor_layers,
                floor_blocked=True,
                tiers=["floor"],
            )

        matched: list[str] = []
        layers: list[str] = []
        tiers: list[str] = []
        action = "allow"
        spoken = normalized

        if word_list_on:
            spoken, hit = bleep_words(spoken)
            if hit:
                matched.extend(f"banned_phrase:{phrase}" for phrase in hit)
                layers.append("word_list")
                tiers.append("word_list")
                action = "bleep"

        if extra_on:
            soften_rules: list[str] = []
            if heuristic is not None and heuristic.severe and not floor_on:
                soften_rules.extend(heuristic.matched_rules)
            elif heuristic is not None and heuristic.matched_rules and level == "strict":
                soften_rules.extend(heuristic.matched_rules)
            if decision is not None and decision.action != "allow":
                # A "profanity" verdict is already handled when the word list bleeped the words.
                if not (decision.reason == "profanity" and action == "bleep"):
                    soften_rules.append(decision.reason or "llm_soften")
            if soften_rules:
                matched.extend(soften_rules)
                layers.append("extra")
                tiers.append("extra")
                action = "soften"
                spoken = rewrite_text(text=spoken, action="soften") if settings.speech_filter_auto_rewrite else spoken
            elif not word_list_on:
                spoken, capped = _cap_curses(spoken, settings.speech_filter_curse_limit)
                if capped:
                    matched.extend(f"curse_cap:{phrase}" for phrase in capped)
                    layers.append("curse_cap")
                    tiers.append("extra")
                    action = "bleep"

        return SafetyPolicyResult(
            spoken_text=spoken,
            blocked=False,
            matched_rules=matched,
            action=action,
            layers=layers,
            tiers=tiers,
        )


_switch_lock = threading.Lock()
_switch_cache: dict[str, object] = {}


def _config_mtimes() -> tuple[float, float]:
    out = []
    for path in (app_config_path(), app_local_config_path()):
        try:
            out.append(path.stat().st_mtime)
        except OSError:
            out.append(0.0)
    return out[0], out[1]


_STARTUP_MTIMES = _config_mtimes()


def live_switches(level: str) -> tuple[str, bool, bool]:
    """(level, word list on, extra safety on), following dashboard saves without a restart.

    Until a config file changes after startup this is just ``settings``; after a dashboard save
    it reads the saved values. The floor is not here: it has no live switch.
    """
    mtimes = _config_mtimes()
    if mtimes == _STARTUP_MTIMES:
        return level, settings.speech_filter_word_list_enabled, settings.speech_filter_extra_enabled
    with _switch_lock:
        if _switch_cache.get("mtimes") != mtimes:
            try:
                config = load_app_file_config()
            except Exception:
                config = {}
            _switch_cache["mtimes"] = mtimes
            _switch_cache["values"] = (
                str(config.get("speech_filter_level", level) or level).strip().lower(),
                bool(config.get("speech_filter_word_list_enabled", settings.speech_filter_word_list_enabled)),
                bool(config.get("speech_filter_extra_enabled", settings.speech_filter_extra_enabled)),
            )
        return _switch_cache["values"]  # type: ignore[return-value]


def _cap_curses(text: str, limit: int) -> tuple[str, list[str]]:
    """Keep at most ``limit`` listed curse words in one reply; bleep the rest."""
    if not word_list_matches(text):
        return text, []
    _, every = bleep_words(text)
    if not every:
        return text, []
    kept = 0
    out: list[str] = []
    capped: list[str] = []
    for token in text.split(" "):
        replaced, hit = bleep_words(token)
        if hit:
            if kept < max(0, limit):
                kept += 1
                out.append(token)
                continue
            capped.extend(hit)
            out.append(replaced if replaced != token else BLEEP)
            continue
        out.append(token)
    return " ".join(out), list(dict.fromkeys(capped))
