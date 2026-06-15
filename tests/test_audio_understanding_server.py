from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma import audio_understanding_server
from gamma.observability import configure_logging
from gamma.schemas.voice import VoiceInputContext


class _Service:
    def analyze_path(self, path, *, transcript: str = "") -> VoiceInputContext:
        return VoiceInputContext(
            ok=True,
            features={"transcript": transcript, "bytes": path.stat().st_size},
        )


class _Upload:
    filename = "voice.wav"

    async def read(self) -> bytes:
        return b"RIFFaudio"


class AudioUnderstandingServerTest(unittest.TestCase):
    def test_health_exposes_independent_devices(self) -> None:
        with patch.object(audio_understanding_server, "_service", _Service()):
            response = audio_understanding_server.health()

        self.assertIn("speaker_emotion_device", response)
        self.assertIn("audio_event_device", response)
        self.assertIn("torch", response)

    def test_analyze_accepts_uploaded_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "audio-understanding.jsonl"
            logger = configure_logging(
                f"audio-understanding-server-{id(log_path)}",
                log_path=log_path,
                stderr=False,
            )
            with (
                patch.object(audio_understanding_server, "_service", _Service()),
                patch.object(audio_understanding_server, "_logger", logger),
                patch.object(audio_understanding_server.settings, "data_dir", Path(temp_dir)),
            ):
                response = asyncio.run(
                    audio_understanding_server.analyze(
                        audio_file=_Upload(),  # type: ignore[arg-type]
                        transcript="hello",
                    )
                )
            for handler in logger.handlers:
                handler.flush()
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(response.features["transcript"], "hello")
        self.assertEqual(response.features["bytes"], 9)
        completed = next(record for record in records if record["event"] == "audio_understanding.request.completed")
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["event_count"], 0)
        self.assertNotIn("RIFFaudio", json.dumps(records))
        self.assertNotIn("hello", json.dumps(records))

    def test_preload_failure_logs_traceback(self) -> None:
        class _FailingService:
            def __init__(self, **_kwargs) -> None:
                return None

            def preload(self) -> None:
                raise RuntimeError("model preload failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "audio-understanding.jsonl"
            logger = configure_logging(
                f"audio-understanding-preload-{id(log_path)}",
                log_path=log_path,
                stderr=False,
            )
            with (
                patch.object(audio_understanding_server, "_logger", logger),
                patch.object(audio_understanding_server, "AudioUnderstandingService", _FailingService),
            ):
                with self.assertRaisesRegex(RuntimeError, "model preload failed"):
                    audio_understanding_server.preload_models()
            for handler in logger.handlers:
                handler.flush()
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        failure = next(record for record in records if record["event"] == "audio_understanding.preload.failed")
        self.assertEqual(failure["error_class"], "RuntimeError")
        self.assertIn("Traceback", failure["traceback"])

    def test_lifespan_logs_start_and_stop(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "audio-understanding.jsonl"
                logger = configure_logging(
                    f"audio-understanding-lifespan-{id(log_path)}",
                    log_path=log_path,
                    stderr=False,
                )
                with (
                    patch.object(audio_understanding_server, "_logger", logger),
                    patch.object(audio_understanding_server, "preload_models"),
                ):
                    async with audio_understanding_server.lifespan(audio_understanding_server.app):
                        pass
                for handler in logger.handlers:
                    handler.flush()
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

            events = [record["event"] for record in records]
            self.assertEqual(
                events,
                ["audio_understanding.service.start", "audio_understanding.service.stop"],
            )

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
