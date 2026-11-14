from __future__ import annotations

import re
import time
import wave
from datetime import UTC, datetime

from ..config import settings
from ..errors import ConfigurationError
from .expressive_text import strip_hidden_style_tags
from .tts_base import TTSBackend, TTSResult
from .tts_openai import OpenAITTSBackend
from .tts_piper import PiperTTSBackend
from .tts_post import TTSPostProcessingMixin
from .tts_qwen import QwenTTSBackend
from .tts_stub import StubTTSBackend
from .voice_profiles import resolve_tts_config
from uuid import uuid4


class TTSService(TTSPostProcessingMixin):
    """TTS service that selects backend by configuration.
    
    Attributes:
        _backend: Selected TTS backend instance.
        _MAX_CHUNK_CHARS: Maximum characters per chunk.

    Submethods:
        synthesize: Synthesize text to audio.
        synthesize_multipart: Synthesize in chunks for multipart output.
        _split_text: Split text into manageable chunks.
        _concat_wav_results: Concatenate WAV chunks.
        synthesize: Main synthesis entry (calls backend + post-processing).
        _maybe_apply_denoise: Apply spectral denoising.
        _maybe_apply_rvc: Apply RVC post-processing.
    """

    def __init__(self) -> None:
        """Initialize TTS service.
        
        Selects backend based on SHANA_TTS_PROVIDER configuration.
        Raises ConfigurationError for unsupported providers.
        """
        self._cfg = resolve_tts_config()
        provider = self._cfg.provider.strip().lower()
        if provider == "openai":
            self._backend: TTSBackend = OpenAITTSBackend(self._cfg)
        elif provider == "piper":
            self._backend = PiperTTSBackend(self._cfg)
        elif provider in {"qwen", "qwen-tts", "qwen_tts", "qwentts"}:
            self._backend = QwenTTSBackend(self._cfg)
        elif provider == "stub":
            self._backend = StubTTSBackend()
        elif provider == "ollama":
            raise ConfigurationError(
                "SHANA_TTS_PROVIDER=ollama is not supported. "
                "Use SHANA_TTS_PROVIDER=qwen-tts, piper, openai, or stub for tests."
            )
        else:
            raise ConfigurationError(f"Unsupported SHANA_TTS_PROVIDER: {self._cfg.provider}")

    _MAX_CHUNK_CHARS = 3800  # conservative limit; keeps all backends happy (OpenAI caps at 4096)

    def synthesize_multipart(self, text: str, emotion: str | None = None, styles: list[str] | None = None) -> TTSResult:
        """Synthesize text by splitting into chunks.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style.
            styles: Optional list of voice styles.
            
        Returns:
            TTSResult: Merged audio from all chunks.
        """
        chunks = self._split_text(text)
        if not chunks:
            raise ValueError("text is empty after parsing")
        if len(chunks) == 1:
            return self.synthesize(chunks[0], emotion=emotion, styles=styles)
        results = [self.synthesize(chunk, emotion=emotion, styles=styles) for chunk in chunks]
        return self._concat_wav_results(results)

    def _split_text(self, text: str) -> list[str]:
        """Split text into paragraph/sentence chunks under size limit.
        
        Args:
            text: Text to split.
            
        Returns:
            list[str]: Chunks under MAX_CHUNK_CHARS each.
        """
        import re
        paragraphs = re.split(r"\n\n+", text.strip())
        chunks: list[str] = []
        for para in paragraphs:
            para = para.strip().replace("\n", " ")
            if not para:
                continue
            if len(para) <= self._MAX_CHUNK_CHARS:
                chunks.append(para)
            else:
                # split long paragraphs at sentence boundaries
                sentences = re.split(r"(?<=[.!?]) +", para)
                current = ""
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    candidate = (current + " " + sentence).strip() if current else sentence
                    if current and len(candidate) > self._MAX_CHUNK_CHARS:
                        chunks.append(current)
                        current = sentence
                    else:
                        current = candidate
                if current:
                    chunks.append(current)
        return chunks

    def _concat_wav_results(self, results: list[TTSResult]) -> TTSResult:
        """Concatenate WAV results into single file.
        
        Args:
            results: List of TTSResult from chunks.
            
        Returns:
            TTSResult: Merged WAV file.
        """
        non_wav = [r for r in results if r.content_type != "audio/wav"]
        if non_wav:
            raise ConfigurationError(
                f"multi-chunk synthesis requires WAV output but got {non_wav[0].content_type}. "
                "Set SHANA_TTS_FORMAT=wav when using the OpenAI provider."
            )
        frames_list: list[bytes] = []
        params = None
        for result in results:
            with wave.open(result.audio_path, "rb") as wf:
                if params is None:
                    params = wf.getparams()
                frames_list.append(wf.readframes(wf.getnframes()))
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_path = settings.audio_output_dir / f"tts-{stamp}-{uuid4().hex[:12]}.wav"
        with wave.open(str(out_path), "wb") as wf:
            wf.setparams(params)
            for frames in frames_list:
                wf.writeframes(frames)
        total_backend_ms = round(
            sum(
                float(((r.metadata or {}).get("timings_ms") or {}).get("backend_ms") or 0)
                for r in results
            ),
            1,
        )
        return TTSResult(
            provider=results[0].provider,
            text=" ".join(r.text for r in results),
            audio_path=str(out_path),
            content_type="audio/wav",
            metadata={
                "timings_ms": {"backend_ms": total_backend_ms},
                "chunk_count": len(results),
            },
        )

    def synthesize(self, text: str, emotion: str | None = None, styles: list[str] | None = None) -> TTSResult:
        """Generate audio from text with optional post-processing.
        
        Args:
            text: Text to synthesize.
            emotion: Optional emotion style.
            styles: Optional list of voice styles.
            
        Returns:
            TTSResult: Generated audio with timing metadata.
        """
        started_at = time.perf_counter()
        expressive = strip_hidden_style_tags(text, default_emotion=emotion)
        voice_styles = list(expressive.styles)
        for style in styles or []:
            if style not in voice_styles:
                voice_styles.append(style)
        if isinstance(self._backend, QwenTTSBackend):
            result = self._backend.synthesize(text=expressive.clean_text, emotion=expressive.emotion, styles=voice_styles)
        else:
            result = self._backend.synthesize(text=expressive.clean_text, emotion=expressive.emotion)
        result = self._maybe_apply_rvc(result, emotion=expressive.emotion)
        result = self._maybe_apply_denoise(result)
        metadata = dict(result.metadata or {})
        metadata["hidden_style_tags"] = expressive.tags
        metadata["hidden_voice_styles"] = voice_styles
        metadata["emotion"] = expressive.emotion
        timings = dict(metadata.get("timings_ms", {})) if isinstance(metadata.get("timings_ms"), dict) else {}
        timings["total_tts_pipeline_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        metadata["timings_ms"] = timings
        result.metadata = metadata
        return result
