from __future__ import annotations

import argparse
import json
from pathlib import Path

from .conversation.service import ConversationService
from .voice.stt import STTService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a file-based voice roundtrip through STT -> LLM -> TTS.")
    parser.add_argument("audio_file", help="Path to an input audio file.")
    parser.add_argument(
        "--skip-tts",
        action="store_true",
        help="Transcribe and generate a reply without synthesizing audio.",
    )
    parser.add_argument(
        "--save-json",
        help="Optional path for a JSON artifact. Defaults to a sidecar next to generated audio when TTS runs.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    source = Path(args.audio_file).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"audio file not found: {source}")

    stt = STTService()
    conversation = ConversationService()

    transcript = stt.transcribe_audio(str(source)).strip()
    if not transcript:
        raise SystemExit("transcription came back empty")

    response = conversation.respond(
        transcript,
        synthesize_speech=not args.skip_tts,
    )

    payload = {
        "input_audio": str(source),
        "transcript": transcript,
        "reply": response.spoken_text,
        "audio_path": response.audio_path,
        "audio_content_type": response.audio_content_type,
    }

    artifact_path: Path | None = None
    if args.save_json:
        artifact_path = Path(args.save_json).expanduser().resolve()
    elif response.audio_path:
        artifact_path = Path(response.audio_path).with_suffix(".json")

    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload["json_artifact"] = str(artifact_path)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
