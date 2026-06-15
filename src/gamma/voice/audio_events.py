from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..schemas.voice import AudioEvent


_LABEL_ALIASES = {
    "alarm": "alarm",
    "applause": "clapping",
    "clap": "clapping",
    "clapping": "clapping",
    "cough": "cough",
    "coughing": "cough",
    "coughing fit": "cough",
    "crying": "crying",
    "crying sobbing": "crying",
    "door knock": "door_knock",
    "door_knock": "door_knock",
    "gasp": "gasp",
    "gasping": "gasp",
    "laughter": "laughter",
    "laughing": "laughter",
    "giggle": "laughter",
    "snicker": "laughter",
    "music": "music",
    "sigh": "sigh",
    "sighing": "sigh",
    "sneeze": "sneeze",
    "sneezing": "sneeze",
    "sobbing": "crying",
    "throat clearing": "throat_clearing",
    "throat_clearing": "throat_clearing",
}


@dataclass(frozen=True, slots=True)
class AudioEventPolicy:
    allowed_labels: frozenset[str]
    min_confidence: float
    min_duration_ms: float = 100.0
    merge_gap_ms: float = 250.0
    cooldown_ms: float = 500.0
    max_events: int = 20


def normalize_audio_event_label(label: str) -> str | None:
    normalized = " ".join(label.strip().lower().replace("-", " ").replace("_", " ").split())
    direct = _LABEL_ALIASES.get(normalized)
    if direct:
        return direct
    for alias, canonical in _LABEL_ALIASES.items():
        if alias in normalized:
            return canonical
    return None


def postprocess_audio_events(
    events: Iterable[AudioEvent],
    *,
    policy: AudioEventPolicy,
) -> list[AudioEvent]:
    candidates: list[AudioEvent] = []
    for event in events:
        label = normalize_audio_event_label(event.label)
        if label is None or label not in policy.allowed_labels or event.confidence < policy.min_confidence:
            continue
        start_ms = max(0.0, float(event.start_ms or 0.0))
        end_ms = max(start_ms, float(event.end_ms if event.end_ms is not None else start_ms))
        if end_ms - start_ms < policy.min_duration_ms:
            continue
        candidates.append(
            event.model_copy(
                update={
                    "label": label,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            )
        )

    candidates.sort(key=lambda event: (float(event.start_ms or 0.0), event.label))
    merged: list[AudioEvent] = []
    for event in candidates:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.label == event.label
            and float(event.start_ms or 0.0) - float(previous.end_ms or 0.0) <= policy.merge_gap_ms
        ):
            merged[-1] = previous.model_copy(
                update={
                    "confidence": max(previous.confidence, event.confidence),
                    "end_ms": max(float(previous.end_ms or 0.0), float(event.end_ms or 0.0)),
                }
            )
            continue
        merged.append(event)

    output: list[AudioEvent] = []
    last_by_label: dict[str, float] = {}
    for event in merged:
        start_ms = float(event.start_ms or 0.0)
        last_end = last_by_label.get(event.label)
        if last_end is not None and start_ms - last_end < policy.cooldown_ms:
            continue
        output.append(event)
        last_by_label[event.label] = float(event.end_ms or start_ms)
        if len(output) >= policy.max_events:
            break
    return output
