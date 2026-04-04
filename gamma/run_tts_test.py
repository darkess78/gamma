from __future__ import annotations

import sys

from .voice.tts import TTSService


def main() -> None:
    text = " ".join(sys.argv[1:]).strip() or "Hello from Gamma. This is a local TTS pipeline smoke test."
    result = TTSService().synthesize(text)
    print({
        "provider": result.provider,
        "audio_path": result.audio_path,
        "content_type": result.content_type,
        "text": result.text,
    })


if __name__ == "__main__":
    main()
