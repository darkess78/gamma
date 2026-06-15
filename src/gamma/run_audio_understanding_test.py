from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .voice.audio_understanding import AudioUnderstandingService
from .voice.stt import STTService


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze speaker affect and non-speech audio events.")
    parser.add_argument("audio_file")
    parser.add_argument("--transcript", default="")
    parser.add_argument("--transcribe", action="store_true")
    args = parser.parse_args()

    audio_path = Path(args.audio_file)
    transcript = args.transcript.strip()
    if args.transcribe:
        transcript = STTService().transcribe_audio(str(audio_path)).strip()

    started_at = time.perf_counter()
    result = AudioUnderstandingService().analyze_path(audio_path, transcript=transcript)
    payload = result.model_dump()
    payload["transcript"] = transcript
    payload.setdefault("timing_ms", {})
    payload["timing_ms"]["cli_total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
