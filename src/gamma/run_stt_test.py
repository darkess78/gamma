from __future__ import annotations

import sys

from .voice.stt import STTService


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m gamma.run_stt_test <audio-file>")
    source = sys.argv[1]
    service = STTService()
    text = service.transcribe_audio(source)
    print(text)


if __name__ == "__main__":
    main()
