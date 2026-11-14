"""Chat window: queue Twitch chat, let one picker loop answer the best recent message.

Before this, every chat message was one blocking HTTP call that ran a whole
turn, so chat backed up and replies came late. Now:

* ``ChatWindow.submit`` takes a chat event and returns at once. Messages that
  would not make Shana speak (ambient chat, filtered input) are traced right
  away through the cheap no-model path; messages that would speak join the
  window.
* One picker thread (``ChatWindow.step`` in a loop) waits until Shana is free
  to speak (pacer gap, current line finished, speech budget, no synchronous
  owner turn running), scores everything in the window, drops stale messages
  and runs the best one through ``StreamBrain.handle_event``.
* Turns the pacer deferred are pulled back into the window and compete like
  chat, so deferred messages are played later instead of never.
* When chat is busy, a short summary of the other recent messages is passed to
  the turn as stream context. Any quote in it must match a real message
  exactly (``quotes_are_verbatim``); a summarizer whose quotes fail the check is
  replaced by the deterministic one.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol
from uuid import uuid4

from .event_rules import _decision_would_speak, _input_safety
from .models import StreamInputEvent


logger = logging.getLogger(__name__)


DEFAULT_MAX_AGE_SECONDS = 40.0
DEFAULT_MAX_ITEMS = 50
DEFAULT_BUSY_THRESHOLD = 5
MAX_DEFERRED_REPLAYS = 2
MAX_QUOTE_CHARS = 120

SUPPORT_EVENT_KINDS = {"follow", "raid", "donation", "bits", "subscription", "redeem"}

TRUST_SCORES = {
    "owner": 500.0,
    "trusted": 30.0,
    "regular": 15.0,
    "normal": 0.0,
    "new_viewer": -5.0,
    "suspicious": -40.0,
}

_QUESTION_WORDS = {
    "what", "why", "how", "who", "when", "where", "which", "can", "could", "do", "does", "did",
    "is", "are", "will", "would", "should", "have", "has",
}
_LOW_EFFORT_TOKENS = {
    "lol", "lmao", "lul", "kekw", "w", "l", "gg", "pog", "poggers", "xd", "haha", "hahaha", "ok", "f", "o7",
    "omegalul", "pogchamp", "kappa", "lmfao", "rofl", "hi", "hey", "yo",
}
_STOPWORDS = {
    "shana", "gamma", "this", "that", "with", "have", "what", "your", "just", "like", "from", "about",
    "they", "them", "there", "their", "would", "could", "should", "will", "been", "were", "when", "then",
    "than", "into", "does", "doing", "dont", "it's", "i'm", "you're", "really", "think", "know", "here",
    "some", "more", "much", "very", "also", "only", "even", "still", "well", "yeah", "okay", "chat",
    "going", "want", "make", "good", "time", "today",
}
_WORD_RE = re.compile(r"[a-z0-9']+")
_QUOTE_RE = re.compile(r'"([^"]+)"')


@dataclass
class WindowItem:
    event: StreamInputEvent
    synthesize_speech: bool
    fast_mode: bool
    received_at: float
    origin: str = "chat"  # "chat" or "deferred"
    ingress_verified: bool = False  # the event's platform identity came from Gamma's own worker
    queue_id: str = field(default_factory=lambda: uuid4().hex)
    replays: int = 0


@dataclass(frozen=True)
class ChatSummary:
    text: str
    quotes: tuple[str, ...] = ()
    message_count: int = 0
    viewer_count: int = 0


class ChatSummarizer(Protocol):
    """Plug point for a small local model later; must return quotes verbatim."""

    def summarize(self, messages: list[StreamInputEvent], *, window_seconds: float) -> ChatSummary:
        ...


# --- scoring ---------------------------------------------------------------


def actor_key(event: StreamInputEvent) -> str:
    platform_id = event.actor.platform_id or event.actor.display_name or event.event_id
    return f"{event.actor.source.strip().lower()}:{platform_id}"


def is_owner_event(event: StreamInputEvent) -> bool:
    return (
        "owner" in event.actor.roles
        or bool(event.metadata.get("is_owner"))
        or str(event.metadata.get("trust_level") or "") == "owner"
    )


def is_direct_mention(text: str) -> bool:
    lowered = text.lower()
    return "shana" in lowered or "gamma" in lowered


def is_question(text: str) -> bool:
    stripped = text.strip()
    if "?" in stripped:
        return True
    words = _WORD_RE.findall(stripped.lower())
    # "shana what ..." -> look past a leading name
    while words and words[0] in {"shana", "gamma", "hey", "yo", "so", "ok", "okay"}:
        words = words[1:]
    return bool(words) and words[0] in _QUESTION_WORDS


def is_low_effort(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 3 or not any(char.isalpha() for char in stripped):
        return True
    words = _WORD_RE.findall(stripped.lower())
    return bool(words) and all(word in _LOW_EFFORT_TOKENS for word in words)


def score_item(item: WindowItem, *, now: float, duplicate_texts: Counter | None = None) -> float:
    """Higher is better. Owner > support events > mentions > questions > trust > freshness; spam last."""
    event = item.event
    text = (event.text or "").strip()
    score = 0.0
    if is_owner_event(event):
        score += TRUST_SCORES["owner"]
    else:
        score += TRUST_SCORES.get(str(event.metadata.get("trust_level") or "new_viewer"), 0.0)
    if event.kind in SUPPORT_EVENT_KINDS:
        score += 150.0
    if is_direct_mention(text):
        score += 100.0
    if is_question(text):
        score += 40.0
    score += 2.0 * max(0, min(event.priority, 50))
    category = str(_input_safety(event).get("category") or "")
    if category == "spam_or_scam":
        score -= 120.0
    elif category == "prompt_injection":
        score -= 150.0
    elif category and category != "normal":
        score -= 30.0
    if is_low_effort(text):
        score -= 40.0
    if duplicate_texts is not None and duplicate_texts[_normalized(text)] > 1:
        score -= 15.0
    score -= max(0.0, now - item.received_at)  # one point per second of age
    return round(score, 3)


def _normalized(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


# --- summary ---------------------------------------------------------------


def _display_name(event: StreamInputEvent) -> str:
    name = str(event.metadata.get("safe_display_name") or "").strip()
    return name or "a viewer"


def is_quotable(event: StreamInputEvent) -> bool:
    """Only quote text that is exactly what the viewer typed and passed input safety."""
    text = event.text or ""
    if not text.strip() or len(text) > MAX_QUOTE_CHARS or '"' in text:
        return False
    raw = event.metadata.get("raw_text")
    if raw is not None and str(raw) != text:
        return False  # sanitizer changed it: not verbatim any more
    category = str(_input_safety(event).get("category") or "normal")
    return category == "normal"


def quotes_are_verbatim(summary_text: str, messages: list[StreamInputEvent]) -> bool:
    """Every "double-quoted" span in the summary must equal a real message's text."""
    real = {event.text or "" for event in messages if is_quotable(event)}
    return all(quote in real for quote in _QUOTE_RE.findall(summary_text))


class DeterministicChatSummarizer:
    """Counts, repeated topics and who is asking what. No model."""

    def __init__(self, *, max_topics: int = 3, max_questions: int = 3) -> None:
        self._max_topics = max_topics
        self._max_questions = max_questions

    def summarize(self, messages: list[StreamInputEvent], *, window_seconds: float) -> ChatSummary:
        viewers = {actor_key(event) for event in messages}
        parts = [
            f"Busy chat: {len(messages)} other recent messages from {len(viewers)} "
            f"viewer{'s' if len(viewers) != 1 else ''} in the last {int(round(window_seconds))} s."
        ]
        topic_counts: Counter = Counter()
        for event in messages:
            if not is_quotable(event):
                continue
            words = {word for word in _WORD_RE.findall((event.text or "").lower()) if len(word) >= 4 and word not in _STOPWORDS}
            topic_counts.update(words)
        topics = [(word, count) for word, count in topic_counts.most_common() if count >= 2][: self._max_topics]
        if topics:
            parts.append("Repeated topics: " + ", ".join(f"{word} ({count})" for word, count in topics) + ".")
        quotes: list[str] = []
        asks: list[str] = []
        seen_askers: set[str] = set()
        for event in sorted(messages, key=lambda e: e.occurred_at, reverse=True):
            text = event.text or ""
            key = actor_key(event)
            if key in seen_askers or not is_question(text) or not is_quotable(event):
                continue
            seen_askers.add(key)
            quotes.append(text)
            asks.append(f'{_display_name(event)} asked "{text}"')
            if len(asks) >= self._max_questions:
                break
        if asks:
            parts.append("Questions: " + "; ".join(asks) + ".")
        return ChatSummary(text=" ".join(parts), quotes=tuple(quotes), message_count=len(messages), viewer_count=len(viewers))


# --- the window ------------------------------------------------------------


class ChatWindow:
    """Recent-chat queue plus the single picker loop. Thread-safe."""

    def __init__(
        self,
        brain,
        *,
        now=time.monotonic,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        max_items: int = DEFAULT_MAX_ITEMS,
        busy_threshold: int = DEFAULT_BUSY_THRESHOLD,
        summarizer: ChatSummarizer | None = None,
        result_limit: int = 200,
        idle_wait_seconds: float = 1.0,
    ) -> None:
        self._brain = brain
        self._now = now
        self.max_age_seconds = max_age_seconds
        self.max_items = max_items
        self.busy_threshold = busy_threshold
        self._summarizer = summarizer
        self._fallback_summarizer = DeterministicChatSummarizer()
        self._result_limit = result_limit
        self._idle_wait_seconds = idle_wait_seconds
        self._lock = threading.RLock()
        self._wakeup = threading.Condition(self._lock)
        self._items: list[WindowItem] = []
        self._recent: deque[tuple[float, StreamInputEvent]] = deque(maxlen=200)
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._in_flight: WindowItem | None = None
        self._foreground = 0
        self._last_answered: str | None = None
        self._counters: Counter = Counter()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- intake --

    def submit(self, event: StreamInputEvent, *, synthesize_speech: bool = False, fast_mode: bool = True,
               ingress_verified: bool = False) -> dict[str, Any]:
        """Add a chat event; returns at once with a queue id (status "queued" or "handled")."""
        now = self._now()
        with self._lock:
            self._recent.append((now, event))
        decision = self._brain.decide(event)
        if not _decision_would_speak(decision):
            # Nothing to say: trace it through the cheap no-model path now.
            result = self._brain.handle_event(event, synthesize_speech=synthesize_speech, fast_mode=fast_mode,
                                              ingress_verified=ingress_verified)
            ticket = {
                "status": "handled",
                "queue_id": event.event_id,
                "event_id": event.event_id,
                "decision": result.decision.decision,
                "reason": result.decision.reason,
            }
            self._store_result(event.event_id, {**ticket, "trace_id": result.trace_id})
            with self._lock:
                self._counters["handled_inline"] += 1
            return ticket
        item = WindowItem(
            event=event, synthesize_speech=synthesize_speech, fast_mode=fast_mode,
            received_at=now, queue_id=event.event_id, ingress_verified=ingress_verified,
        )
        overflow: list[WindowItem] = []
        with self._lock:
            self._items.append(item)
            if len(self._items) > self.max_items:
                overflow = self._evict_lowest(now, len(self._items) - self.max_items)
            self._counters["queued"] += 1
            size = len(self._items)
            self._results[item.queue_id] = {"status": "queued", "queue_id": item.queue_id, "event_id": event.event_id}
            self._trim_results()
            self._wakeup.notify_all()
        for dropped in overflow:
            self._skip(dropped, "chat_window_overflow")
        return {"status": "queued", "queue_id": item.queue_id, "event_id": event.event_id, "window_size": size}

    @contextmanager
    def foreground(self) -> Iterator[None]:
        """Hold the picker while a synchronous (owner/Monitor) turn runs: owner first."""
        with self._lock:
            self._foreground += 1
        try:
            yield
        finally:
            with self._lock:
                self._foreground -= 1
                self._wakeup.notify_all()

    # -- the picker --

    def step(self) -> dict[str, Any]:
        """One picker iteration. Returns {"action": "idle"|"wait"|"handled", ...}."""
        stale: list[WindowItem] = []
        with self._lock:
            now = self._now()
            self._pull_deferred(now)
            for item in list(self._items):
                if now - item.received_at > self.max_age_seconds:
                    self._items.remove(item)
                    stale.append(item)
            foreground = self._foreground > 0
            pick = None if foreground else self._choose(now)
        for item in stale:
            self._skip(item, "chat_window_stale")
        if foreground:
            return {"action": "wait", "wait_seconds": 0.2, "reason": "foreground_turn", "dropped": len(stale)}
        if pick is None:
            return {"action": "idle", "dropped": len(stale)}
        wait = float(self._brain.speech_ready_in(pick.event))
        if wait > 0:
            return {"action": "wait", "wait_seconds": wait, "reason": "pacing", "dropped": len(stale)}
        with self._lock:
            if pick not in self._items:  # cleared meanwhile
                return {"action": "idle", "dropped": len(stale)}
            self._items.remove(pick)
            self._in_flight = pick
            summary = self._summary_for(pick, now)
        return self._run(pick, summary, dropped=len(stale))

    def _choose(self, now: float) -> WindowItem | None:
        if not self._items:
            return None
        duplicates = Counter(_normalized(item.event.text or "") for item in self._items)
        candidates = self._items
        if self._last_answered is not None:
            others = [item for item in self._items if actor_key(item.event) != self._last_answered]
            if others:
                candidates = others
        return max(
            candidates,
            key=lambda item: (score_item(item, now=now, duplicate_texts=duplicates), -item.received_at),
        )

    def _run(self, item: WindowItem, summary: ChatSummary | None, *, dropped: int) -> dict[str, Any]:
        event = item.event
        if item.origin == "deferred":
            event.metadata["chat_window_replay"] = item.replays
        try:
            result = self._brain.handle_event(
                event,
                synthesize_speech=item.synthesize_speech,
                fast_mode=item.fast_mode,
                chat_summary=summary.text if summary else None,
                ingress_verified=item.ingress_verified,
            )
        except Exception as exc:
            logger.exception("chat window turn failed")
            self._store_result(item.queue_id, {"status": "error", "queue_id": item.queue_id, "event_id": event.event_id, "error": str(exc)})
            with self._lock:
                self._in_flight = None
                self._counters["errors"] += 1
            return {"action": "handled", "queue_id": item.queue_id, "error": str(exc), "dropped": dropped}
        spoke = _decision_would_speak(result.decision)
        with self._lock:
            self._in_flight = None
            if spoke:
                self._last_answered = actor_key(event)
            self._counters["answered" if spoke else "processed"] += 1
        record = {
            "status": "done",
            "queue_id": item.queue_id,
            "event_id": event.event_id,
            "origin": item.origin,
            "decision": result.decision.decision,
            "reason": result.decision.reason,
            "trace_id": result.trace_id,
            "spoken_text": result.assistant_response.speech_text if result.assistant_response else None,
            "summary": summary.text if summary else None,
        }
        self._store_result(item.queue_id, record)
        return {"action": "handled", "dropped": dropped, **record}

    def _pull_deferred(self, now: float) -> None:
        for entry in self._brain.take_deferred():
            event = entry["event"]
            replays = int(event.metadata.get("chat_window_replay") or 0) + 1
            queued_at = float(entry.get("queued_at") or now)
            if replays > MAX_DEFERRED_REPLAYS or now - queued_at > self.max_age_seconds:
                self._counters["deferred_dropped"] += 1
                continue
            if any(existing.event.event_id == event.event_id for existing in self._items):
                continue
            options = entry.get("options") or {}
            controls = event.metadata.get("twitch_controls") if isinstance(event.metadata.get("twitch_controls"), dict) else {}
            self._items.append(
                WindowItem(
                    event=event,
                    synthesize_speech=bool(options.get("synthesize_speech", controls.get("voice_enabled", False))),
                    fast_mode=bool(options.get("fast_mode", True)),
                    received_at=queued_at,
                    origin="deferred",
                    queue_id=event.event_id,
                    replays=replays,
                    ingress_verified=bool(options.get("ingress_verified", False)),
                )
            )
            self._counters["deferred_pulled"] += 1

    def _summary_for(self, pick: WindowItem, now: float) -> ChatSummary | None:
        others = [
            event for received_at, event in self._recent
            if now - received_at <= self.max_age_seconds and event.event_id != pick.event.event_id
        ]
        if len(others) < self.busy_threshold:
            return None
        if self._summarizer is not None:
            try:
                summary = self._summarizer.summarize(others, window_seconds=self.max_age_seconds)
                if quotes_are_verbatim(summary.text, others):
                    return summary
                logger.warning("chat summarizer quoted text no viewer sent; using the deterministic summary")
                self._counters["summary_quote_rejected"] += 1
            except Exception:
                logger.exception("chat summarizer failed; using the deterministic summary")
        return self._fallback_summarizer.summarize(others, window_seconds=self.max_age_seconds)

    def _evict_lowest(self, now: float, count: int) -> list[WindowItem]:
        duplicates = Counter(_normalized(item.event.text or "") for item in self._items)
        ranked = sorted(self._items, key=lambda item: (score_item(item, now=now, duplicate_texts=duplicates), item.received_at))
        evicted = ranked[:count]
        for item in evicted:
            self._items.remove(item)
        return evicted

    def _skip(self, item: WindowItem, reason: str) -> None:
        with self._lock:
            self._counters[reason] += 1
        try:
            result = self._brain.record_skipped(item.event, reason=reason, metadata={"origin": item.origin})
            trace_id = result.trace_id
        except Exception:
            trace_id = None
        self._store_result(item.queue_id, {"status": "dropped", "queue_id": item.queue_id, "event_id": item.event.event_id,
                                           "reason": reason, "trace_id": trace_id})

    def _store_result(self, queue_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._results[queue_id] = record
            self._results.move_to_end(queue_id)
            self._trim_results()

    def _trim_results(self) -> None:
        while len(self._results) > self._result_limit:
            self._results.popitem(last=False)

    # -- inspection / control --

    def status(self, queue_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._results.get(queue_id)
            return dict(record) if record else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._now()
            duplicates = Counter(_normalized(item.event.text or "") for item in self._items)
            items = [
                {
                    "queue_id": item.queue_id,
                    "kind": item.event.kind,
                    "origin": item.origin,
                    "display_name": _display_name(item.event),
                    "age_seconds": round(now - item.received_at, 1),
                    "score": score_item(item, now=now, duplicate_texts=duplicates),
                }
                for item in self._items
            ]
            items.sort(key=lambda entry: entry["score"], reverse=True)
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "size": len(self._items),
                "items": items,
                "in_flight": self._in_flight.queue_id if self._in_flight else None,
                "foreground_turns": self._foreground,
                "max_age_seconds": self.max_age_seconds,
                "counters": dict(self._counters),
            }

    def clear(self) -> int:
        """Drop everything waiting (Stop Shana). The turn in flight finishes."""
        with self._lock:
            items, self._items = self._items, []
        for item in items:
            self._skip(item, "chat_window_cleared")
        return len(items)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="shana-chat-picker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop.set()
        with self._lock:
            self._wakeup.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                outcome = self.step()
            except Exception:
                logger.exception("chat picker step failed")
                outcome = {"action": "wait", "wait_seconds": 1.0}
            if outcome["action"] == "handled":
                continue
            wait = self._idle_wait_seconds if outcome["action"] == "idle" else min(max(outcome.get("wait_seconds", 0.2), 0.05), 1.0)
            with self._lock:
                if self._stop.is_set():
                    break
                self._wakeup.wait(timeout=wait)
