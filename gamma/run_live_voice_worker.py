from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .conversation.service import ConversationService
from .voice.stt import STTService


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_status(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--audio-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--synthesize-speech", choices={"true", "false"}, default="true")
    args = parser.parse_args()

    audio_path = Path(args.audio_path)
    output_path = Path(args.output_path)
    status_path = Path(args.status_path)
    synthesize_speech = args.synthesize_speech == "true"

    stt = STTService()
    conversation = ConversationService()
    started_at = time.perf_counter()
    status_payload = {
        "turn_id": args.turn_id,
        "status": "running",
        "session_id": args.session_id,
        "synthesize_speech": synthesize_speech,
        "started_at": _utc_now(),
    }
    _write_status(status_path, status_payload)

    try:
        stt_started = time.perf_counter()
        transcript = stt.transcribe_audio(str(audio_path)).strip()
        stt_ms = round((time.perf_counter() - stt_started) * 1000, 1)
        if not transcript:
            raise ValueError("transcription came back empty")

        status_payload["status"] = "running"
        status_payload["transcript"] = transcript
        _write_status(status_path, status_payload)

        conversation_started = time.perf_counter()
        response = conversation.respond(
            transcript,
            session_id=args.session_id,
            synthesize_speech=synthesize_speech,
        )
        conversation_ms = round((time.perf_counter() - conversation_started) * 1000, 1)

        if response.audio_path:
            status_payload["status"] = "speaking"
            _write_status(status_path, status_payload)

        payload = {
            "turn_id": args.turn_id,
            "status": "completed",
            "transcript": transcript,
            "reply_text": response.spoken_text,
            "audio_content_type": response.audio_content_type,
            "audio_base64": None,
            "timing_ms": {
                "stt_ms": stt_ms,
                "conversation_ms": conversation_ms,
                **response.timing_ms,
                "total_ms": round((time.perf_counter() - started_at) * 1000, 1),
            },
        }
        if response.audio_path:
            payload["audio_base64"] = __import__("base64").b64encode(Path(response.audio_path).read_bytes()).decode("ascii")
        output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        status_payload.update(
            {
                "status": "completed",
                "completed_at": _utc_now(),
            }
        )
        _write_status(status_path, status_payload)
        return 0
    except KeyboardInterrupt:
        status_payload.update({"status": "cancelled", "cancelled_at": _utc_now(), "cancel_reason": "worker-interrupted"})
        _write_status(status_path, status_payload)
        return 130
    except Exception as exc:
        status_payload.update({"status": "failed", "completed_at": _utc_now(), "error": str(exc)})
        _write_status(status_path, status_payload)
        output_path.write_text(
            json.dumps({"turn_id": args.turn_id, "status": "failed", "error": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
