from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(slots=True)
class Settings:
    app_name: str = "gamma"
    project_root: Path = PROJECT_ROOT
    database_url: str = os.getenv("RIKO_DATABASE_URL", "sqlite:///./gamma.db")
    memory_top_k: int = int(os.getenv("RIKO_MEMORY_TOP_K", "5"))
    data_dir: Path = PROJECT_ROOT / "data"
    audio_output_dir: Path = PROJECT_ROOT / "data" / "audio"
    default_language: str = os.getenv("RIKO_DEFAULT_LANGUAGE", "en")

    llm_provider: str = os.getenv("RIKO_LLM_PROVIDER", "mock")
    llm_model: str = os.getenv("RIKO_LLM_MODEL", "gpt-4.1-mini")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    local_llm_endpoint: str = os.getenv("RIKO_LOCAL_LLM_ENDPOINT", "http://127.0.0.1:11434")
    local_llm_model: str = os.getenv("RIKO_LOCAL_LLM_MODEL", "gpt-oss:20b")
    local_llm_timeout_seconds: int = int(os.getenv("RIKO_LOCAL_LLM_TIMEOUT_SECONDS", "120"))

    stt_provider: str = os.getenv("RIKO_STT_PROVIDER", "stub")
    stt_model: str = os.getenv("RIKO_STT_MODEL", "base.en")
    stt_device: str = os.getenv("RIKO_STT_DEVICE", "cpu")
    stt_compute_type: str = os.getenv("RIKO_STT_COMPUTE_TYPE", "int8")

    tts_provider: str = os.getenv("RIKO_TTS_PROVIDER", "stub")
    tts_model: str = os.getenv("RIKO_TTS_MODEL", "gpt-4o-mini-tts")
    tts_voice: str = os.getenv("RIKO_TTS_VOICE", "alloy")
    tts_format: str = os.getenv("RIKO_TTS_FORMAT", "wav")
    gpt_sovits_endpoint: str | None = os.getenv("RIKO_GPT_SOVITS_ENDPOINT")
    gpt_sovits_reference_audio: str | None = os.getenv("RIKO_GPT_SOVITS_REFERENCE_AUDIO")
    gpt_sovits_prompt_text: str | None = os.getenv("RIKO_GPT_SOVITS_PROMPT_TEXT")
    gpt_sovits_prompt_lang: str = os.getenv("RIKO_GPT_SOVITS_PROMPT_LANG", "en")
    gpt_sovits_text_lang: str = os.getenv("RIKO_GPT_SOVITS_TEXT_LANG", os.getenv("RIKO_DEFAULT_LANGUAGE", "en"))
    gpt_sovits_timeout_seconds: int = int(os.getenv("RIKO_GPT_SOVITS_TIMEOUT_SECONDS", "120"))
    gpt_sovits_extra_json: dict = field(default_factory=lambda: json.loads(os.getenv("RIKO_GPT_SOVITS_EXTRA_JSON", "{}")))

    memory_enabled: bool = os.getenv("RIKO_MEMORY_ENABLED", "true").lower() == "true"
    memory_write_mode: str = os.getenv("RIKO_MEMORY_WRITE_MODE", "selective")


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.audio_output_dir.mkdir(parents=True, exist_ok=True)
