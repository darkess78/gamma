"""3D avatar adapter: drives a VRM model over the VMC protocol (OSC over UDP).

Gamma sends blendshape values (expression, mouth, blink) to a VMC receiver
such as Warudo or VSeeFace running on the streaming PC. Idle body motion is
left to the avatar app. See specs/avatar_3d.md.

Layout mirrors vtube_studio.py: a config dataclass, a sender (transport), an
adapter that turns performer events into state, and a runner that subscribes
to the performer bus and streams frames at a fixed rate.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import random
import re
import socket
import struct
import time
import wave
from array import array
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import load_app_file_config, settings
from ..observability import bind_context, configure_logging, log_event, reset_context
from .bus import PerformerEventBus
from .models import STREAM_PUBLIC_TARGET, PerformerOutputEvent
from .osc import encode_osc_bundle, encode_osc_message

VMC_BLEND_VAL = "/VMC/Ext/Blend/Val"
VMC_BLEND_APPLY = "/VMC/Ext/Blend/Apply"

# Gamma emotion labels (conversation/shared.py ALLOWED_EMOTIONS) mapped onto
# the VRM preset expressions every VRoid export has. "Surprised" is the extra
# clip VRoid Studio exports for VRM 0.x. Custom clips can replace these via
# the [avatar3d.expressions] table in app.toml.
VRM0_EXPRESSIONS: dict[str, dict[str, float]] = {
    "neutral": {},
    "happy": {"Joy": 1.0},
    "teasing": {"Fun": 0.7, "Blink_R": 0.5},
    "concerned": {"Sorrow": 0.7},
    "excited": {"Joy": 0.7, "Surprised": 0.6},
    "embarrassed": {"Joy": 0.35, "Sorrow": 0.4},
    "annoyed": {"Angry": 0.8},
}
VRM1_EXPRESSIONS: dict[str, dict[str, float]] = {
    "neutral": {},
    "happy": {"happy": 1.0},
    "teasing": {"relaxed": 0.7, "blinkRight": 0.5},
    "concerned": {"sad": 0.7},
    "excited": {"happy": 0.7, "surprised": 0.6},
    "embarrassed": {"happy": 0.35, "sad": 0.4},
    "annoyed": {"angry": 0.8},
}
VRM0_MOUTH = {"a": "A", "i": "I", "u": "U", "e": "E", "o": "O"}
VRM1_MOUTH = {"a": "aa", "i": "ih", "u": "ou", "e": "ee", "o": "oh"}
VRM0_BLINK = "Blink"
VRM1_BLINK = "blink"


@dataclass(slots=True)
class Avatar3DAdapterConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 39539
    naming: str = "vrm0"
    fps: int = 30
    lipsync: str = "text"
    chars_per_second: float = 14.0
    lipsync_delay_ms: int = 0
    mouth_gain: float = 0.8
    auto_blink: bool = True
    expression_fade_ms: int = 150
    target_policy: str = STREAM_PUBLIC_TARGET
    bundle: bool = True
    expressions: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, app_config: dict[str, Any] | None = None) -> "Avatar3DAdapterConfig":
        app_config = app_config if app_config is not None else load_app_file_config()
        nested = app_config.get("avatar3d", {}) if isinstance(app_config.get("avatar3d"), dict) else {}
        return cls(
            enabled=settings.avatar3d_enabled,
            host=settings.avatar3d_host,
            port=settings.avatar3d_port,
            naming=settings.avatar3d_naming,
            fps=settings.avatar3d_fps,
            lipsync=settings.avatar3d_lipsync,
            chars_per_second=settings.avatar3d_lipsync_chars_per_second,
            lipsync_delay_ms=settings.avatar3d_lipsync_delay_ms,
            auto_blink=settings.avatar3d_auto_blink,
            target_policy=settings.avatar3d_target_policy,
            expressions=_expression_overrides(nested.get("expressions", {})),
        )

    def expression_map(self) -> dict[str, dict[str, float]]:
        base = VRM1_EXPRESSIONS if self.naming == "vrm1" else VRM0_EXPRESSIONS
        merged = {key: dict(value) for key, value in base.items()}
        merged.update({key: dict(value) for key, value in self.expressions.items()})
        return merged

    def mouth_names(self) -> dict[str, str]:
        return VRM1_MOUTH if self.naming == "vrm1" else VRM0_MOUTH

    def blink_name(self) -> str:
        return VRM1_BLINK if self.naming == "vrm1" else VRM0_BLINK


def vmc_blend_packets(values: dict[str, float], *, bundle: bool = True) -> list[bytes]:
    """Encode one frame: a Blend/Val per blendshape, then Blend/Apply."""
    messages = [encode_osc_message(VMC_BLEND_VAL, name, float(value)) for name, value in values.items()]
    messages.append(encode_osc_message(VMC_BLEND_APPLY))
    if bundle:
        return [encode_osc_bundle(messages)]
    return messages


class VMCSender:
    """Fire-and-forget UDP sender for VMC frames."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        bundle: bool = True,
        socket_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.bundle = bundle
        self._socket_factory = socket_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self._socket: Any | None = None
        self.packets_sent = 0
        self.last_error: str | None = None

    def send_blendshapes(self, values: dict[str, float]) -> int:
        if self._socket is None:
            self._socket = self._socket_factory()
        sent = 0
        for packet in vmc_blend_packets(values, bundle=self.bundle):
            self._socket.sendto(packet, (self.host, self.port))
            sent += 1
        self.packets_sent += sent
        self.last_error = None
        return sent

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Lip sync from text (and WAV loudness when the audio is in the event)


@dataclass(slots=True)
class _Syllable:
    start: float
    end: float
    vowel: str  # one of a/i/u/e/o
    weight: float = 1.0


@dataclass(slots=True)
class _SpeechSegment:
    start: float
    end: float
    syllables: list[_Syllable]
    envelope: list[float] | None = None  # loudness 0..1 per envelope step
    envelope_step: float = 0.05


_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?|[,;:]|[.!?]+|\S", re.UNICODE)
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")
_VOWEL_OF = {"a": "a", "i": "i", "y": "i", "u": "u", "e": "e", "o": "o"}


def text_to_syllables(text: str, chars_per_second: float) -> tuple[list[_Syllable], float]:
    """Rough syllable timeline for spoken text, offsets from 0.

    Each word takes len(word) / chars_per_second, split evenly across its
    vowel groups; spaces take one character, commas 0.25 s, sentence ends
    0.4 s. Words with no Latin vowels (numbers, other scripts) open on "a".
    """
    cps = max(1.0, float(chars_per_second))
    char_time = 1.0 / cps
    cursor = 0.0
    syllables: list[_Syllable] = []
    for token in _WORD_RE.findall(text or ""):
        if token in {",", ";", ":"}:
            cursor += 0.25
            continue
        if token[0] in ".!?":
            cursor += 0.4
            continue
        if not token[0].isalnum():
            continue
        word_time = len(token) * char_time
        groups = _VOWEL_GROUP_RE.findall(token.lower())
        if not groups:
            count = len(token) if token.isdigit() else max(1, len(token) // 3)
            groups = ["a"] * count
            weight = 0.7
        else:
            weight = 1.0
        step = word_time / len(groups)
        for index, group in enumerate(groups):
            syllables.append(
                _Syllable(
                    start=cursor + index * step,
                    end=cursor + (index + 1) * step,
                    vowel=_VOWEL_OF.get(group[0], "a"),
                    weight=weight,
                )
            )
        cursor += word_time + char_time
    return syllables, cursor


def wav_loudness(audio: bytes, *, step: float = 0.05) -> tuple[float, list[float]] | None:
    """Duration and normalized RMS envelope of a 16-bit PCM WAV, or None."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError, struct.error):
        return None
    if rate <= 0 or channels <= 0:
        return None
    duration = len(frames) / float(width * channels * rate) if width else 0.0
    if width != 2 or duration <= 0:
        return (duration, []) if duration > 0 else None
    samples = array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if struct.pack("=h", 1) != struct.pack("<h", 1):  # big-endian host
        samples.byteswap()
    per_step = max(1, int(rate * step) * channels)
    rms: list[float] = []
    for offset in range(0, len(samples), per_step):
        window = samples[offset : offset + per_step]
        if not window:
            break
        rms.append(math.sqrt(sum(s * s for s in window) / len(window)))
    peak = max(rms) if rms else 0.0
    if peak <= 0:
        return duration, [0.0] * len(rms)
    return duration, [min(1.0, value / peak * 1.5) for value in rms]


# ---------------------------------------------------------------------------
# Adapter


@dataclass(slots=True)
class Avatar3DAction:
    ok: bool
    action_type: str
    detail: str
    event_type: str | None = None
    event_id: str | None = None


class Avatar3DAdapter:
    """Turns performer events into a blendshape state; ``frame()`` samples it."""

    _TURN_MEMORY = 64

    def __init__(
        self,
        config: Avatar3DAdapterConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config or Avatar3DAdapterConfig.from_settings()
        self.logger = logger or configure_logging("avatar3d")
        self._clock = clock
        self._rng = rng or random.Random()
        self._expressions = self.config.expression_map()
        self._mouth_names = self.config.mouth_names()
        self._blink_name = self.config.blink_name()
        # All blendshape names any expression uses, so we can zero them.
        self._expression_keys = sorted({name for values in self._expressions.values() for name in values})
        self._expression = "neutral"
        self._expr_from: dict[str, float] = {}
        self._expr_to: dict[str, float] = {}
        self._expr_started = 0.0
        self._segments: list[_SpeechSegment] = []
        self._voiced_turns: OrderedDict[str, str | None] = OrderedDict()
        self._mouth_level = 0.0
        self._mouth_level_until = 0.0
        self._next_blink = self._clock() + self._blink_interval()
        self._last_event_type: str | None = None
        self._last_action: dict[str, Any] | None = None
        self._last_error: str | None = None

    # -- status -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "host": self.config.host,
            "port": self.config.port,
            "naming": self.config.naming,
            "lipsync": self.config.lipsync,
            "expression": self._expression,
            "speaking": self._speaking(self._clock()),
            "queued_speech_segments": len(self._segments),
            "last_event_type": self._last_event_type,
            "last_action": self._last_action,
            "last_error": self._last_error,
        }

    # -- events -------------------------------------------------------------

    def handle_event(self, event: PerformerOutputEvent) -> Avatar3DAction:
        self._last_event_type = event.type
        try:
            action = self._apply_event(event)
        except Exception as exc:
            self._last_error = str(exc)
            log_event(
                self.logger,
                logging.ERROR,
                "avatar3d.event.translation_failed",
                "Failed to apply a performer event to the 3D avatar state.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
                event_type=event.type,
                event_id=event.event_id,
            )
            action = Avatar3DAction(ok=False, action_type="error", detail=str(exc))
        action.event_type = event.type
        action.event_id = event.event_id
        self._last_action = {"ok": action.ok, "action_type": action.action_type, "detail": action.detail, "event_type": event.type}
        return action

    def _apply_event(self, event: PerformerOutputEvent) -> Avatar3DAction:
        if not self.config.enabled:
            return Avatar3DAction(ok=True, action_type="disabled", detail="adapter disabled")
        now = self._clock()
        payload = event.payload or {}
        if event.type == "expression_set":
            expression = str(payload.get("expression") or "neutral").strip().lower()
            if expression not in self._expressions:
                return Avatar3DAction(ok=True, action_type="no_mapping", detail=f"no expression mapping for {expression}")
            self.set_expression(expression, now=now)
            return Avatar3DAction(ok=True, action_type="expression", detail=expression)
        if event.type == "speech_started":
            self._remember_turn(event.turn_id, None)
            return Avatar3DAction(ok=True, action_type="speaking_state", detail="speech started")
        if event.type == "subtitle_update":
            text = str(payload.get("text") or "")
            if self.config.lipsync != "text" or not text.strip():
                return Avatar3DAction(ok=True, action_type="ignored", detail="no lip sync for this subtitle")
            if event.turn_id not in self._voiced_turns:
                return Avatar3DAction(ok=True, action_type="ignored", detail="subtitle without speech")
            if self._voiced_turns[event.turn_id] == "chunk":
                return Avatar3DAction(ok=True, action_type="ignored", detail="turn lip sync comes from speech chunks")
            self._remember_turn(event.turn_id, "subtitle")
            self.schedule_speech(text, now=now)
            return Avatar3DAction(ok=True, action_type="lipsync", detail="scheduled from subtitle")
        if event.type == "speech_chunk_ready":
            text = str(payload.get("text") or "")
            if self.config.lipsync != "text" or not text.strip():
                return Avatar3DAction(ok=True, action_type="ignored", detail="no lip sync for this chunk")
            if self._voiced_turns.get(event.turn_id) == "subtitle":
                return Avatar3DAction(ok=True, action_type="ignored", detail="turn lip sync comes from the subtitle")
            self._remember_turn(event.turn_id, "chunk")
            audio = _decode_wav(payload)
            self.schedule_speech(text, now=now, audio=audio)
            return Avatar3DAction(ok=True, action_type="lipsync", detail="scheduled from speech chunk")
        if event.type == "output_cleared":
            self._segments.clear()
            self._mouth_level_until = 0.0
            return Avatar3DAction(ok=True, action_type="speaking_state", detail="speech cleared")
        if event.type == "speech_ended":
            # Events arrive when audio is generated, not when it finishes
            # playing, so let the scheduled mouth timeline run out.
            return Avatar3DAction(ok=True, action_type="speaking_state", detail="speech ended")
        if event.type == "mouth_level":
            try:
                level = float(payload.get("level", payload.get("value", 0.0)) or 0.0)
            except (TypeError, ValueError):
                level = 0.0
            self._mouth_level = max(0.0, min(1.0, level))
            self._mouth_level_until = now + 0.15
            return Avatar3DAction(ok=True, action_type="mouth_level", detail=f"{self._mouth_level:.2f}")
        if event.type == "motion_trigger":
            return Avatar3DAction(ok=True, action_type="unsupported", detail="motions are handled in the avatar app")
        return Avatar3DAction(ok=True, action_type="ignored", detail="event has no 3D avatar mapping")

    def set_expression(self, expression: str, *, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        self._expr_from = self._expression_values(now)
        self._expr_to = dict(self._expressions.get(expression, {}))
        self._expr_started = now
        self._expression = expression

    def schedule_speech(self, text: str, *, now: float | None = None, audio: bytes | None = None) -> None:
        now = self._clock() if now is None else now
        syllables, duration = text_to_syllables(text, self.config.chars_per_second)
        if not syllables or duration <= 0:
            return
        envelope: list[float] | None = None
        if audio:
            loudness = wav_loudness(audio)
            if loudness is not None:
                audio_duration, envelope = loudness
                if audio_duration > 0:
                    scale = audio_duration / duration
                    for syllable in syllables:
                        syllable.start *= scale
                        syllable.end *= scale
                    duration = audio_duration
                envelope = envelope or None
        self._segments = [segment for segment in self._segments if segment.end > now]
        start = max(now + self.config.lipsync_delay_ms / 1000.0, self._segments[-1].end if self._segments else 0.0)
        for syllable in syllables:
            syllable.start += start
            syllable.end += start
        self._segments.append(_SpeechSegment(start=start, end=start + duration, syllables=syllables, envelope=envelope))

    # -- frames -------------------------------------------------------------

    def frame(self, now: float | None = None) -> dict[str, float]:
        """Full blendshape state at ``now``: every managed name, zeros included."""
        now = self._clock() if now is None else now
        values: dict[str, float] = {name: 0.0 for name in self._expression_keys}
        values.update(self._expression_values(now))
        mouth = self._mouth_values(now)
        for vowel, name in self._mouth_names.items():
            values[name] = round(mouth.get(vowel, 0.0), 4)
        values[self._blink_name] = round(max(values.get(self._blink_name, 0.0), self._blink_value(now)), 4)
        return {name: round(value, 4) for name, value in values.items()}

    def neutral_frame(self) -> dict[str, float]:
        names = set(self._expression_keys) | set(self._mouth_names.values()) | {self._blink_name}
        return {name: 0.0 for name in sorted(names)}

    def _expression_values(self, now: float) -> dict[str, float]:
        fade = max(0.0, self.config.expression_fade_ms / 1000.0)
        progress = 1.0 if fade <= 0 else max(0.0, min(1.0, (now - self._expr_started) / fade))
        keys = set(self._expr_from) | set(self._expr_to)
        return {
            key: self._expr_from.get(key, 0.0) + (self._expr_to.get(key, 0.0) - self._expr_from.get(key, 0.0)) * progress
            for key in keys
        }

    def _mouth_values(self, now: float) -> dict[str, float]:
        if now < self._mouth_level_until:
            return {"a": self._mouth_level * self.config.mouth_gain}
        self._segments = [segment for segment in self._segments if segment.end > now]
        for segment in self._segments:
            if not (segment.start <= now < segment.end):
                continue
            for syllable in segment.syllables:
                if syllable.start <= now < syllable.end:
                    span = max(1e-6, syllable.end - syllable.start)
                    openness = math.sin(math.pi * (now - syllable.start) / span)
                    level = openness * syllable.weight * self.config.mouth_gain
                    if segment.envelope:
                        index = int((now - segment.start) / segment.envelope_step)
                        level *= segment.envelope[min(index, len(segment.envelope) - 1)]
                    return {syllable.vowel: max(0.0, min(1.0, level))}
            return {}
        return {}

    def _speaking(self, now: float) -> bool:
        return any(segment.start <= now < segment.end for segment in self._segments)

    def _blink_value(self, now: float) -> float:
        if not self.config.auto_blink:
            return 0.0
        blink_length = 0.15
        if now >= self._next_blink + blink_length:
            self._next_blink = now + self._blink_interval()
            return 0.0
        if now < self._next_blink:
            return 0.0
        return math.sin(math.pi * (now - self._next_blink) / blink_length)

    def _blink_interval(self) -> float:
        return self._rng.uniform(2.5, 6.0)

    def _remember_turn(self, turn_id: str, source: str | None) -> None:
        if turn_id in self._voiced_turns and source is None:
            return
        self._voiced_turns[turn_id] = source
        self._voiced_turns.move_to_end(turn_id)
        while len(self._voiced_turns) > self._TURN_MEMORY:
            self._voiced_turns.popitem(last=False)


def _decode_wav(payload: dict[str, Any]) -> bytes | None:
    content_type = str(payload.get("audio_content_type") or "").lower()
    encoded = payload.get("audio_base64")
    if not encoded or "wav" not in content_type:
        return None
    try:
        return base64.b64decode(str(encoded), validate=False)
    except (ValueError, TypeError):
        return None


def _expression_overrides(value: Any) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for emotion, shapes in value.items():
        if not isinstance(shapes, dict):
            continue
        cleaned: dict[str, float] = {}
        for name, weight in shapes.items():
            try:
                cleaned[str(name)] = max(0.0, min(1.0, float(weight)))
            except (TypeError, ValueError):
                continue
        result[str(emotion).strip().lower()] = cleaned
    return result


# ---------------------------------------------------------------------------
# Runner


class Avatar3DRunner:
    def __init__(
        self,
        bus: PerformerEventBus,
        adapter: Avatar3DAdapter | None = None,
        sender: VMCSender | None = None,
        *,
        target_policy: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bus = bus
        self.adapter = adapter or Avatar3DAdapter()
        config = self.adapter.config
        self.sender = sender or VMCSender(config.host, config.port, bundle=config.bundle)
        self.logger = logger or self.adapter.logger
        self.target_policy = target_policy or config.target_policy
        self._running = False
        self._subscriber_id: str | None = None
        self._handled_count = 0
        self._frames_sent = 0
        self._last_sequence: int | None = None
        self._last_error: str | None = None
        self._send_failures = 0
        self._stop_event: asyncio.Event | None = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "target_policy": self.target_policy,
            "subscriber_id": self._subscriber_id,
            "handled_count": self._handled_count,
            "frames_sent": self._frames_sent,
            "destination": f"{self.sender.host}:{self.sender.port}",
            "last_sequence": self._last_sequence,
            "last_error": self._last_error,
        }

    def send_frame(self, values: dict[str, float] | None = None) -> bool:
        try:
            self.sender.send_blendshapes(values if values is not None else self.adapter.frame())
        except OSError as exc:
            self._send_failures += 1
            self._last_error = str(exc)
            if self._send_failures == 1 or self._send_failures % 300 == 0:
                log_event(
                    self.logger,
                    logging.WARNING,
                    "avatar3d.send.failed",
                    "Failed to send a VMC frame.",
                    error_class=type(exc).__name__,
                    detail=str(exc),
                    destination=f"{self.sender.host}:{self.sender.port}",
                    failures=self._send_failures,
                )
            return False
        self._send_failures = 0
        self._frames_sent += 1
        return True

    async def run_until_stopped(self, *, replay_recent: int = 0) -> None:
        if self._running:
            return
        if not self.adapter.config.enabled:
            self._last_error = "avatar3d adapter disabled"
            return
        self._running = True
        self._stop_event = asyncio.Event()
        subscriber_id: str | None = None
        interval = 1.0 / max(1, min(120, int(self.adapter.config.fps)))
        log_event(
            self.logger,
            logging.INFO,
            "avatar3d.runner.start",
            "3D avatar runner starting.",
            target_policy=self.target_policy,
            destination=f"{self.sender.host}:{self.sender.port}",
        )
        try:
            subscriber_id, queue = await self.bus.subscribe(
                replay_recent=replay_recent,
                target_policy=self.target_policy,
                client_name="avatar3d_runner",
            )
            self._subscriber_id = subscriber_id
            next_frame = time.monotonic()
            while not self._stop_event.is_set():
                while True:
                    try:
                        self._handle_payload(queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if time.monotonic() >= next_frame:
                    self.send_frame()
                    next_frame = max(next_frame + interval, time.monotonic())
                timeout = max(0.001, next_frame - time.monotonic())
                try:
                    self._handle_payload(await asyncio.wait_for(queue.get(), timeout=timeout))
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            if subscriber_id is not None:
                self.bus.unsubscribe(subscriber_id)
            self._subscriber_id = None
            self.send_frame(self.adapter.neutral_frame())
            self.sender.close()
            log_event(
                self.logger,
                logging.INFO,
                "avatar3d.runner.exit",
                "3D avatar runner stopped.",
                handled_count=self._handled_count,
                frames_sent=self._frames_sent,
            )

    def _handle_payload(self, payload: dict[str, Any]) -> None:
        context_token = None
        try:
            event = PerformerOutputEvent(**payload)
            context_token = bind_context(event_id=event.event_id, turn_id=event.turn_id)
            self.adapter.handle_event(event)
            self._handled_count += 1
            self._last_sequence = event.sequence
        except Exception as exc:
            self._last_error = str(exc)
            log_event(
                self.logger,
                logging.ERROR,
                "avatar3d.runner.event_failed",
                "3D avatar runner failed to handle a performer event.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
            )
        finally:
            if context_token is not None:
                reset_context(context_token)

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()


# ---------------------------------------------------------------------------
# Manual check: python -m gamma.performer.avatar3d --host <avatar PC> [--naming vrm1]


def run_demo(
    host: str,
    port: int = 39539,
    *,
    naming: str = "vrm0",
    fps: int = 30,
    seconds_per_emotion: float = 2.5,
    sender: VMCSender | None = None,
) -> int:
    """Cycle through every emotion, saying a line in each, so the owner can
    check the model and the network path without running the whole stack.
    Returns the number of packets sent."""
    config = Avatar3DAdapterConfig(enabled=True, host=host, port=port, naming=naming, fps=fps)
    adapter = Avatar3DAdapter(config)
    sender = sender or VMCSender(host, port)
    emotions = ["neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed", "neutral"]
    try:
        for index, emotion in enumerate(emotions):
            print(emotion, flush=True)
            turn_id = f"demo-{index}"
            adapter.handle_event(PerformerOutputEvent(type="expression_set", turn_id=turn_id, payload={"expression": emotion}))
            adapter.handle_event(PerformerOutputEvent(type="speech_started", turn_id=turn_id))
            adapter.handle_event(
                PerformerOutputEvent(type="subtitle_update", turn_id=turn_id, payload={"text": f"This is {emotion} Shana."})
            )
            until = time.monotonic() + seconds_per_emotion
            while time.monotonic() < until:
                sender.send_blendshapes(adapter.frame())
                time.sleep(1.0 / max(1, fps))
        sender.send_blendshapes(adapter.neutral_frame())
    finally:
        sender.close()
    return sender.packets_sent


if __name__ == "__main__":  # pragma: no cover - manual tool
    import argparse

    parser = argparse.ArgumentParser(description="Send a VMC expression and lip-sync demo to a 3D avatar app.")
    parser.add_argument("--host", default=settings.avatar3d_host)
    parser.add_argument("--port", type=int, default=settings.avatar3d_port)
    parser.add_argument("--naming", choices=["vrm0", "vrm1"], default=settings.avatar3d_naming)
    parser.add_argument("--seconds", type=float, default=2.5, help="seconds per emotion")
    args = parser.parse_args()
    sent = run_demo(args.host, args.port, naming=args.naming, seconds_per_emotion=args.seconds)
    print(f"sent {sent} packets to {args.host}:{args.port}")
