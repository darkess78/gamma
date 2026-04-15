"""
Qwen3-TTS local HTTP server.

Loads Qwen3-TTS (voice-cloning Base model) and exposes a minimal HTTP API
compatible with the gamma TTS pipeline.

Endpoints:
  POST /tts          – synthesise; returns raw WAV bytes
  GET  /health       – liveness check

POST /tts JSON body:
  {
    "text":           "text to speak",
    "ref_audio_path": "/absolute/path/to/reference.wav",   # optional
    "ref_text":       "transcript of reference clip",       # optional
    "language":       "English",                            # default
    "speaker":        "Ryan",                               # CustomVoice models only
    "instruct":       "calm tone",                          # style hint (optional)
    "speed":          1.0                                   # optional
  }

Environment variables:
  QWEN_TTS_MODEL   – HuggingFace model ID (default Qwen/Qwen3-TTS-12Hz-1.7B-Base)
  QWEN_TTS_DTYPE   – "float32" | "bfloat16" (default float32; bfloat16 needs Ampere+)
  QWEN_TTS_DEVICE  – "cuda" | "cpu" | "auto" (default auto)
  QWEN_TTS_PORT    – listen port (default 9882)
  QWEN_TTS_HOST    – listen host (default 127.0.0.1)
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("qwen_tts_server")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_ID = os.getenv("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
DTYPE_STR = os.getenv("QWEN_TTS_DTYPE", "float32")
DEVICE_STR = os.getenv("QWEN_TTS_DEVICE", "auto")
PORT = int(os.getenv("QWEN_TTS_PORT", "9882"))
HOST = os.getenv("QWEN_TTS_HOST", "127.0.0.1")

def _model_type() -> str:
    """Infer model capability from MODEL_ID: 'base', 'customvoice', or 'voicedesign'."""
    m = MODEL_ID.lower()
    if "customvoice" in m or "custom_voice" in m or "custom-voice" in m:
        return "customvoice"
    if "voicedesign" in m or "voice_design" in m or "voice-design" in m:
        return "voicedesign"
    return "base"  # Base model: supports voice clone + voice design

# ---------------------------------------------------------------------------
# Model loading (lazy singleton)
# ---------------------------------------------------------------------------
_model = None
_model_sr: int = 24000


def _load_model():
    global _model, _model_sr
    if _model is not None:
        return _model

    log.info("Loading Qwen3-TTS model: %s  dtype=%s  device=%s", MODEL_ID, DTYPE_STR, DEVICE_STR)

    import torch
    from qwen_tts import Qwen3TTSModel  # type: ignore[import]

    dtype = torch.float32
    if DTYPE_STR == "bfloat16":
        dtype = torch.bfloat16
    elif DTYPE_STR == "float16":
        dtype = torch.float16

    if DEVICE_STR == "auto":
        device_map = "cuda:0" if torch.cuda.is_available() else "cpu"
    else:
        device_map = DEVICE_STR

    model = Qwen3TTSModel.from_pretrained(
        MODEL_ID,
        device_map=device_map,
        dtype=dtype,
        # FlashAttention2 not used — incompatible with Windows native CUDA builds
    )
    _model = model
    log.info("Model loaded on %s", device_map)
    return _model


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------
def _numpy_to_wav_bytes(samples, sr: int) -> bytes:
    """Convert float32 numpy array to 16-bit PCM WAV bytes."""
    import numpy as np  # imported here so the server can start before torch/numpy are installed
    pcm = (samples * 32767).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def synthesize(body: dict[str, Any]) -> bytes:
    import numpy as np

    model = _load_model()
    text = str(body.get("text", "")).strip()
    if not text:
        raise ValueError("'text' field is required and must not be empty.")

    language = str(body.get("language", "English"))
    ref_audio = body.get("ref_audio_path") or body.get("ref_audio")
    ref_text = body.get("ref_text") or body.get("prompt_text")
    speaker = body.get("speaker")
    instruct = body.get("instruct")

    # Extra generation params forwarded to the model (temperature, top_k, etc.)
    extra: dict[str, Any] = {}
    if isinstance(body.get("extra_params"), dict):
        extra.update(body["extra_params"])
    # min_new_tokens prevents the model from stopping too early (cutoff fix).
    # At 12Hz codec rate, 100 tokens ≈ 8.3s minimum — ensures the model can't
    # fire EOS while speech is still in progress, even for longer utterances.
    extra.setdefault("min_new_tokens", 100)

    mtype = _model_type()

    # Determine synthesis mode based on inputs AND what this model variant supports.
    # Base        → generate_voice_clone (needs ref_audio)
    # CustomVoice → generate_custom_voice (speaker name)
    # VoiceDesign → generate_voice_design (instruct text)
    if ref_audio:
        ref_path = Path(str(ref_audio))
        if not ref_path.exists():
            raise FileNotFoundError(f"Reference audio not found: {ref_path}")
        log.info("Voice clone: ref=%s  len(text)=%d  extra=%s", ref_path.name, len(text), extra)
        kwargs: dict[str, Any] = {
            "text": text,
            "language": language,
            "ref_audio": str(ref_path),
            **extra,
        }
        if ref_text:
            kwargs["ref_text"] = ref_text
        wavs, sr = model.generate_voice_clone(**kwargs)
    elif speaker and mtype == "customvoice":
        log.info("Custom voice: speaker=%s  len(text)=%d  extra=%s", speaker, len(text), extra)
        kwargs = {
            "text": text,
            "language": language,
            "speaker": speaker,
            **extra,
        }
        if instruct:
            kwargs["instruct"] = instruct
        wavs, sr = model.generate_custom_voice(**kwargs)
    elif mtype == "base":
        raise ValueError(
            "The loaded model is a Base model and only supports voice cloning. "
            "Set qwen_tts_reference_audio in the profile, or switch to a "
            "CustomVoice model (QWEN_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) "
            "for built-in speakers."
        )
    else:
        log.info("Voice design: len(text)=%d  extra=%s", len(text), extra)
        kwargs = {
            "text": text,
            "language": language,
            **extra,
        }
        if instruct:
            kwargs["instruct"] = instruct
        wavs, sr = model.generate_voice_design(**kwargs)

    audio = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    if hasattr(audio, "cpu"):
        audio = audio.cpu().float().numpy()
    audio = np.asarray(audio, dtype=np.float32)

    # Fade-out: apply an 80ms linear ramp to the end of the synthesised audio
    # before adding the silent pad.  This converts any hard codec edge at EOS
    # into a smooth decay and prevents the "chopped last syllable" perception
    # even when the codec stops slightly early.
    fade_out_samples = int(sr * 0.080)
    if len(audio) > fade_out_samples:
        fade_out = np.linspace(1.0, 0.0, fade_out_samples, dtype=np.float32)
        audio[-fade_out_samples:] *= fade_out

    # Pad with silence: 60ms at start, 400ms at end — extra room ensures the
    # decoded tail has space to fully decay before playback stops.
    pad_start = int(sr * 0.060)
    pad_end = int(sr * 0.400)
    audio = np.concatenate([np.zeros(pad_start, dtype=np.float32), audio, np.zeros(pad_end, dtype=np.float32)])

    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.95

    return _numpy_to_wav_bytes(audio, int(sr))


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # silence access log spam
        pass

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/health", "/health/"}:
            self._send(200, b'{"status":"ok"}')
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self) -> None:
        if self.path not in {"/tts", "/tts/"}:
            self._send(404, b'{"error":"not found"}')
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception as exc:
            self._send(400, json.dumps({"error": f"bad JSON: {exc}"}).encode())
            return
        try:
            wav_bytes = synthesize(body)
        except (ValueError, FileNotFoundError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode())
            return
        except Exception as exc:
            log.exception("Synthesis error")
            self._send(500, json.dumps({"error": str(exc)}).encode())
            return
        self._send(200, wav_bytes, "audio/wav")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # Warm up the model before accepting connections
    _load_model()
    server = HTTPServer((HOST, PORT), Handler)
    log.info("Qwen3-TTS server listening on http://%s:%d/tts", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
