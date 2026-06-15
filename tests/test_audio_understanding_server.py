from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from gamma import audio_understanding_server
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
        with patch.object(audio_understanding_server, "_service", _Service()):
            response = asyncio.run(
                audio_understanding_server.analyze(
                    audio_file=_Upload(),  # type: ignore[arg-type]
                    transcript="hello",
                )
            )

        self.assertEqual(response.features["transcript"], "hello")
        self.assertEqual(response.features["bytes"], 9)


if __name__ == "__main__":
    unittest.main()
