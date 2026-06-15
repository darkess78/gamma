from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import torch
from gamma.config import settings
from gamma.observability import configure_logging
from gamma.schemas.voice import AudioEvent, SpeakerAffect, VoiceInputContext
from gamma.voice.affect import VoiceAffectResult
from gamma.voice.audio_input import NormalizedAudio
from gamma.voice.audio_understanding import (
    AudioEventBackend,
    AudioUnderstandingService,
    HuggingFaceAudioEventBackend,
    HuggingFaceSpeakerEmotionBackend,
    SpeakerEmotionBackend,
    build_audio_prompt_context,
)


class _AffectAnalyzer:
    def analyze_audio(self, audio: NormalizedAudio, *, transcript: str = "") -> VoiceAffectResult:
        return VoiceAffectResult(
            ok=True,
            features={"duration_ms": 1000.0, "word_count": len(transcript.split())},
            labels={
                "energy": "high",
                "pace": "fast",
                "delivery": "energetic_fast",
                "confidence": 0.35,
                "source": "signal_features",
            },
        )


class _Decoder:
    def __init__(self) -> None:
        self.calls = 0

    def decode_path(self, path: Path | str) -> NormalizedAudio:
        self.calls += 1
        return NormalizedAudio(
            samples=(0.1,) * 16_000,
            sample_rate=16_000,
            source_path=Path(path),
            decoder="test",
        )


class _EmotionBackend(SpeakerEmotionBackend):
    provider_name = "test"

    def __init__(self, confidence: float = 0.9) -> None:
        self.confidence = confidence

    def analyze(
        self,
        audio: NormalizedAudio,
        *,
        transcript: str,
        prosody: VoiceAffectResult,
    ) -> SpeakerAffect:
        return SpeakerAffect(
            emotion="happy",
            confidence=self.confidence,
            energy="high",
            pace="fast",
            delivery="energetic_fast",
            source=self.provider_name,
        )


class _EventBackend(AudioEventBackend):
    provider_name = "test"

    def detect(self, audio: NormalizedAudio, *, transcript: str) -> list[AudioEvent]:
        return [
            AudioEvent(label="laughter", confidence=0.91, start_ms=100.0, end_ms=500.0, source=self.provider_name),
            AudioEvent(label="alarm", confidence=0.99, start_ms=600.0, end_ms=900.0, source=self.provider_name),
            AudioEvent(label="cough", confidence=0.2, start_ms=950.0, end_ms=1000.0, source=self.provider_name),
        ]


class _FailingEventBackend(AudioEventBackend):
    provider_name = "failing"

    def detect(self, audio: NormalizedAudio, *, transcript: str) -> list[AudioEvent]:
        raise RuntimeError("model unavailable")


class _FakeAudioModel:
    def __init__(self, logits: list[float], labels: dict[int, str]) -> None:
        self._logits = torch.tensor([logits])
        self.config = SimpleNamespace(id2label=labels)

    def __call__(self, **_kwargs):
        return SimpleNamespace(logits=self._logits)

    def to(self, _device: str):
        return self

    def eval(self):
        return self


class AudioUnderstandingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._settings = {
            "audio_understanding_enabled": settings.audio_understanding_enabled,
            "audio_understanding_prompt_enabled": settings.audio_understanding_prompt_enabled,
            "speaker_emotion_min_confidence": settings.speaker_emotion_min_confidence,
            "speaker_emotion_requires_transcript": settings.speaker_emotion_requires_transcript,
            "audio_event_min_confidence": settings.audio_event_min_confidence,
            "audio_event_labels": settings.audio_event_labels,
            "audio_understanding_endpoint": settings.audio_understanding_endpoint,
        }
        settings.audio_understanding_enabled = True
        settings.audio_understanding_prompt_enabled = False
        settings.speaker_emotion_min_confidence = 0.65
        settings.speaker_emotion_requires_transcript = True
        settings.audio_event_min_confidence = 0.70
        settings.audio_event_labels = ("laughter", "cough")
        settings.audio_understanding_endpoint = None

    def tearDown(self) -> None:
        for key, value in self._settings.items():
            setattr(settings, key, value)

    def test_combines_affect_and_filtered_audio_events(self) -> None:
        decoder = _Decoder()
        service = AudioUnderstandingService(
            decoder=decoder,  # type: ignore[arg-type]
            affect_analyzer=_AffectAnalyzer(),  # type: ignore[arg-type]
            speaker_emotion_backend=_EmotionBackend(),
            audio_event_backend=_EventBackend(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = service.analyze_path(Path(temp_dir) / "voice.wav", transcript="that was funny")

        self.assertTrue(result.ok)
        self.assertEqual(result.speaker_affect.emotion, "happy")  # type: ignore[union-attr]
        self.assertEqual([event.label for event in result.events], ["laughter"])
        self.assertIn("audio_events_ms", result.timing_ms)
        self.assertEqual(decoder.calls, 1)

    def test_low_confidence_emotion_becomes_uncertain(self) -> None:
        service = AudioUnderstandingService(
            decoder=_Decoder(),  # type: ignore[arg-type]
            affect_analyzer=_AffectAnalyzer(),  # type: ignore[arg-type]
            speaker_emotion_backend=_EmotionBackend(confidence=0.4),
            audio_event_backend=_EventBackend(),
        )

        result = service.analyze_path("/tmp/unused.wav", transcript="hello")

        self.assertEqual(result.speaker_affect.emotion, "uncertain")  # type: ignore[union-attr]

    def test_emotion_model_is_skipped_without_transcript(self) -> None:
        backend = Mock(spec=SpeakerEmotionBackend)
        backend.analyze.return_value = SpeakerAffect(
            emotion="happy",
            confidence=0.9,
            source="test",
        )
        service = AudioUnderstandingService(
            decoder=_Decoder(),  # type: ignore[arg-type]
            affect_analyzer=_AffectAnalyzer(),  # type: ignore[arg-type]
            speaker_emotion_backend=backend,
            audio_event_backend=_EventBackend(),
        )

        result = service.analyze_path("/tmp/unused.wav", transcript="")

        backend.analyze.assert_not_called()
        self.assertEqual(result.speaker_affect.emotion, "unknown")  # type: ignore[union-attr]
        self.assertEqual([event.label for event in result.events], ["laughter"])

    def test_audio_event_failure_does_not_fail_affect_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "audio-understanding.jsonl"
            logger = configure_logging(
                f"audio-event-failure-{id(log_path)}",
                log_path=log_path,
                stderr=False,
            )
            service = AudioUnderstandingService(
                decoder=_Decoder(),  # type: ignore[arg-type]
                affect_analyzer=_AffectAnalyzer(),  # type: ignore[arg-type]
                speaker_emotion_backend=_EmotionBackend(),
                audio_event_backend=_FailingEventBackend(),
                logger=logger,
            )

            result = service.analyze_path("/tmp/unused.wav", transcript="hello")
            for handler in logger.handlers:
                handler.flush()
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertTrue(result.ok)
        self.assertEqual(result.events, [])
        self.assertIn("audio events unavailable", result.detail or "")
        failure = next(record for record in records if record["event"] == "audio_understanding.audio_events.failed")
        self.assertEqual(failure["provider"], "failing")
        self.assertEqual(failure["error_class"], "RuntimeError")
        self.assertIn("Traceback", failure["traceback"])

    def test_prompt_context_is_opt_in_and_confidence_gated(self) -> None:
        context = VoiceInputContext(
            ok=True,
            speaker_affect=SpeakerAffect(
                emotion="happy",
                confidence=0.9,
                energy="high",
                pace="fast",
                delivery="energetic_fast",
                source="test",
            ),
            events=[
                AudioEvent(label="laughter", confidence=0.91, start_ms=0.0, end_ms=400.0, source="test")
            ],
        )

        self.assertIsNone(build_audio_prompt_context(context))
        prompt = build_audio_prompt_context(context, enabled=True)

        self.assertIn("probable speaker emotion: happy", prompt or "")
        self.assertIn("probable audio event: laughter", prompt or "")
        self.assertIn("not user-stated facts", prompt or "")

    def test_huggingface_speaker_emotion_maps_superb_labels(self) -> None:
        backend = HuggingFaceSpeakerEmotionBackend(model_name="test/emotion")
        backend._feature_extractor = lambda *_args, **_kwargs: {"input_values": torch.tensor([[0.1]])}  # type: ignore[attr-defined]
        backend._model = _FakeAudioModel([0.1, 2.0], {0: "neu", 1: "hap"})  # type: ignore[attr-defined]
        audio = _Decoder().decode_path("/tmp/unused.wav")
        prosody = _AffectAnalyzer().analyze_audio(audio, transcript="hello")

        result = backend.analyze(audio, transcript="hello", prosody=prosody)

        self.assertEqual(result.emotion, "happy")  # type: ignore[union-attr]
        self.assertGreater(result.confidence, 0.8)  # type: ignore[union-attr]
        self.assertEqual(result.source, "huggingface:test/emotion")  # type: ignore[union-attr]

    def test_huggingface_audio_events_are_timestamped_by_window(self) -> None:
        backend = HuggingFaceAudioEventBackend(model_name="test/events", window_seconds=1.0)
        backend._feature_extractor = lambda *_args, **_kwargs: {"input_values": torch.tensor([[0.1]])}  # type: ignore[attr-defined]
        backend._model = _FakeAudioModel([2.2, -2.4], {0: "Laughter", 1: "Speech"})  # type: ignore[attr-defined]
        audio = NormalizedAudio(
            samples=(0.1,) * 32_000,
            sample_rate=16_000,
            source_path=Path("/tmp/unused.wav"),
            decoder="test",
        )

        events = backend.detect(audio, transcript="hello")

        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].start_ms, 0.0)
        self.assertEqual(events[0].end_ms, 1_000.0)
        self.assertEqual(events[2].start_ms, 1_000.0)
        self.assertEqual(events[2].end_ms, 2_000.0)

    def test_remote_sidecar_response_is_used_when_configured(self) -> None:
        settings.audio_understanding_endpoint = "http://127.0.0.1:9883/analyze"
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "speaker_affect": {
                "emotion": "happy",
                "confidence": 0.9,
                "energy": "high",
                "pace": "fast",
                "delivery": "energetic_fast",
                "source": "sidecar",
            },
            "events": [],
            "features": {},
            "analyzer_version": "audio-understanding-v1",
            "timing_ms": {"sidecar_total_ms": 100.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "voice.wav"
            path.write_bytes(b"audio")
            with patch("gamma.voice.audio_understanding.httpx.post", return_value=response) as post:
                result = AudioUnderstandingService(
                    decoder=_Decoder(),  # type: ignore[arg-type]
                    affect_analyzer=_AffectAnalyzer(),  # type: ignore[arg-type]
                ).analyze_path(path, transcript="hello")

        self.assertEqual(result.speaker_affect.emotion, "happy")  # type: ignore[union-attr]
        post.assert_called_once()

    def test_remote_failure_falls_back_without_loading_model_backends(self) -> None:
        settings.audio_understanding_endpoint = "http://127.0.0.1:9883/analyze"
        with patch("gamma.voice.audio_understanding.httpx.post", side_effect=RuntimeError("offline")):
            result = AudioUnderstandingService(
                decoder=_Decoder(),  # type: ignore[arg-type]
                affect_analyzer=_AffectAnalyzer(),  # type: ignore[arg-type]
            ).analyze_path("/tmp/unused.wav", transcript="hello")

        self.assertTrue(result.ok)
        self.assertEqual(result.speaker_affect.emotion, "unknown")  # type: ignore[union-attr]
        self.assertEqual(result.events, [])


if __name__ == "__main__":
    unittest.main()
