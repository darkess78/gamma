"""Transcribe a media file (audio or video) and print the full transcript.

Usage:
    python -m gamma.run_transcribe <file> [options]

Examples:
    python -m gamma.run_transcribe episode.mkv
    python -m gamma.run_transcribe episode.mkv --language ja --timestamps
    python -m gamma.run_transcribe clip.wav --model large-v3 --out transcript.txt
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .config import settings
from .run_prepare_tts_dataset import (
    MEDIA_EXTENSIONS,
    choose_audio_stream,
    ensure_ffmpeg,
    extract_working_audio,
)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a media file and print the full transcript.",
    )
    parser.add_argument("input_path", help="Audio or video file to transcribe.")
    parser.add_argument(
        "--language",
        default="ja",
        help="Whisper language hint, e.g. 'ja'. Use 'auto' to let Whisper detect it.",
    )
    parser.add_argument(
        "--model",
        default=settings.stt_model,
        help="faster-whisper model name, e.g. 'base', 'medium', 'large-v3'.",
    )
    parser.add_argument(
        "--device",
        default=settings.stt_device,
        help="Inference device: 'cpu' or 'cuda'.",
    )
    parser.add_argument(
        "--compute-type",
        default=settings.stt_compute_type,
        help="faster-whisper compute type, e.g. 'int8' or 'float16'.",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=5,
        help="Beam size passed to faster-whisper (higher = slower but more accurate).",
    )
    parser.add_argument(
        "--audio-stream-index",
        type=int,
        default=None,
        help="Force a specific audio stream index (useful for dual-audio releases).",
    )
    parser.add_argument(
        "--audio-stream-lang",
        default=None,
        help="Preferred audio stream language tag, e.g. 'jpn' or 'ja'.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="ffmpeg executable.",
    )
    parser.add_argument(
        "--ffprobe-bin",
        default="ffprobe",
        help="ffprobe executable.",
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Prefix each line with [start --> end] timestamps.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write transcript to this file instead of (or in addition to) stdout.",
    )
    return parser.parse_args(argv)


def _is_pure_audio(path: Path) -> bool:
    return path.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".mka"}


def _needs_ffmpeg(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS and not _is_pure_audio(path)


def transcribe_file(args: argparse.Namespace) -> list[dict]:
    """Return list of segment dicts with keys: start, end, text."""
    from faster_whisper import WhisperModel

    input_path = Path(args.input_path).expanduser().resolve()
    language = None if args.language == "auto" else args.language

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    # Pure audio files can be fed directly to whisper
    if _is_pure_audio(input_path):
        transcription_source = str(input_path)
        segments_iter, _info = model.transcribe(
            transcription_source,
            language=language,
            vad_filter=True,
            beam_size=args.beam_size,
            condition_on_previous_text=False,
        )
        return [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments_iter
            if s.text.strip()
        ]

    # Video / container files need audio extraction first
    ensure_ffmpeg(args.ffmpeg_bin, args.ffprobe_bin)

    with tempfile.TemporaryDirectory(prefix="gamma-transcribe-") as tmp:
        tmp_path = Path(tmp)
        audio_path = tmp_path / "audio.wav"

        stream_index = choose_audio_stream(args.ffprobe_bin, input_path, args)
        print(f"Using audio stream index {stream_index} from {input_path.name}", file=sys.stderr)
        extract_working_audio(args.ffmpeg_bin, input_path, audio_path, stream_index)

        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            beam_size=args.beam_size,
            condition_on_previous_text=False,
        )
        detected = info.language or args.language
        print(f"Detected language: {detected}", file=sys.stderr)
        return [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments_iter
            if s.text.strip()
        ]


def format_transcript(segments: list[dict], timestamps: bool) -> str:
    lines: list[str] = []
    for seg in segments:
        text = seg["text"]
        if timestamps:
            start = seg["start"]
            end = seg["end"]
            lines.append(f"[{start:.2f} --> {end:.2f}]  {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    input_path = Path(args.input_path).expanduser().resolve()

    if not input_path.exists():
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 1

    if input_path.suffix.lower() not in MEDIA_EXTENSIONS:
        print(
            f"warning: '{input_path.suffix}' is not a known media extension; attempting anyway.",
            file=sys.stderr,
        )

    segments = transcribe_file(args)
    transcript = format_transcript(segments, timestamps=args.timestamps)

    print(transcript)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.write_text(transcript + "\n", encoding="utf-8")
        print(f"\nTranscript written to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
