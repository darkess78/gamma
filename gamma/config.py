from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
load_dotenv(PROJECT_ROOT / ".env")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merged_toml(*paths: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in paths:
        merged = _merge_dicts(merged, _read_toml(path))
    return merged


def app_config_path() -> Path:
    return CONFIG_DIR / "app.toml"


def app_local_config_path() -> Path:
    return CONFIG_DIR / "app.local.toml"


def voices_config_path() -> Path:
    return CONFIG_DIR / "voices.toml"


def voices_local_config_path() -> Path:
    return CONFIG_DIR / "voices.local.toml"


def load_app_file_config() -> dict[str, Any]:
    return _merged_toml(CONFIG_DIR / "app.example.toml", app_config_path(), app_local_config_path())


def load_voices_file_config() -> dict[str, Any]:
    return _merged_toml(CONFIG_DIR / "voices.example.toml", voices_config_path(), voices_local_config_path())


APP_CONFIG = load_app_file_config()
VOICES_CONFIG = load_voices_file_config()
MODELS_CONFIG = _read_toml(CONFIG_DIR / "models.toml")
MEMORY_CONFIG = _read_toml(CONFIG_DIR / "memory.toml")


def _config_value(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _env(name: str) -> str | None:
    return os.getenv(name)


def _setting(env_name: str, default: Any = None) -> Any:
    env_value = _env(env_name)
    if env_value is not None:
        return env_value
    return default


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _as_path(value: Any, *, default: Path) -> Path:
    if value is None or value == "":
        return default
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(slots=True)
class Settings:
    app_name: str = str(_setting("SHANA_APP_NAME", _config_value(APP_CONFIG, "app_name", default="gamma")))
    project_root: Path = PROJECT_ROOT
    database_url: str = str(
        _setting("SHANA_DATABASE_URL", _config_value(APP_CONFIG, "database_url", default="sqlite:///./gamma.db"))
    )
    memory_top_k: int = _as_int(_setting("SHANA_MEMORY_TOP_K", _config_value(APP_CONFIG, "memory_top_k", default=5)), default=5)
    data_dir: Path = PROJECT_ROOT / "data"
    audio_output_dir: Path = _as_path(
        _setting("SHANA_AUDIO_OUTPUT_DIR", _config_value(APP_CONFIG, "audio_output_dir", default="./data/audio")),
        default=PROJECT_ROOT / "data" / "audio",
    )
    image_input_dir: Path = _as_path(
        _setting("SHANA_IMAGE_INPUT_DIR", _config_value(APP_CONFIG, "image_input_dir", default="./data/images")),
        default=PROJECT_ROOT / "data" / "images",
    )
    vision_max_image_bytes: int = _as_int(
        _setting("SHANA_VISION_MAX_IMAGE_BYTES", _config_value(APP_CONFIG, "vision_max_image_bytes", default=8_000_000)),
        default=8_000_000,
    )
    default_language: str = str(
        _setting("SHANA_DEFAULT_LANGUAGE", _config_value(APP_CONFIG, "default_language", default="en"))
    )
    shana_host: str = str(_setting("SHANA_HOST", _config_value(APP_CONFIG, "shana_host", default="127.0.0.1")))
    shana_port: int = _as_int(_setting("SHANA_PORT", _config_value(APP_CONFIG, "shana_port", default=8000)), default=8000)
    dashboard_host: str = str(
        _setting("SHANA_DASHBOARD_HOST", _config_value(APP_CONFIG, "dashboard_host", default="127.0.0.1"))
    )
    dashboard_port: int = _as_int(
        _setting("SHANA_DASHBOARD_PORT", _config_value(APP_CONFIG, "dashboard_port", default=8001)),
        default=8001,
    )
    dashboard_enable_gpu: bool = _as_bool(
        _setting("SHANA_DASHBOARD_ENABLE_GPU", _config_value(APP_CONFIG, "dashboard_enable_gpu", default=True)),
        default=True,
    )
    dashboard_metrics_interval_seconds: int = _as_int(
        _setting(
            "SHANA_DASHBOARD_METRICS_INTERVAL_SECONDS",
            _config_value(APP_CONFIG, "dashboard_metrics_interval_seconds", default=10),
        ),
        default=10,
    )
    dashboard_auth_enabled: bool = _as_bool(
        _setting("SHANA_DASHBOARD_AUTH_ENABLED", _config_value(APP_CONFIG, "dashboard_auth_enabled", default=False)),
        default=False,
    )
    dashboard_auth_username: str = str(
        _setting("SHANA_DASHBOARD_AUTH_USERNAME", _config_value(APP_CONFIG, "dashboard_auth_username", default=""))
    )
    dashboard_auth_password: str = str(
        _setting("SHANA_DASHBOARD_AUTH_PASSWORD", _config_value(APP_CONFIG, "dashboard_auth_password", default=""))
    )
    dashboard_session_secret: str = str(
        _setting("SHANA_DASHBOARD_SESSION_SECRET", _config_value(APP_CONFIG, "dashboard_session_secret", default=""))
    )
    dashboard_cookie_secure: bool = _as_bool(
        _setting("SHANA_DASHBOARD_COOKIE_SECURE", _config_value(APP_CONFIG, "dashboard_cookie_secure", default=False)),
        default=False,
    )
    api_auth_enabled: bool = _as_bool(
        _setting("SHANA_API_AUTH_ENABLED", _config_value(APP_CONFIG, "api_auth_enabled", default=False)),
        default=False,
    )
    api_bearer_token: str = str(
        _setting("SHANA_API_BEARER_TOKEN", _config_value(APP_CONFIG, "api_bearer_token", default=""))
    )

    llm_provider: str = str(
        _setting(
            "SHANA_LLM_PROVIDER",
            _config_value(MODELS_CONFIG, "llm", "provider", default=_config_value(APP_CONFIG, "llm_provider", default="mock")),
        )
    )
    llm_model: str = str(
        _setting(
            "SHANA_LLM_MODEL",
            _config_value(MODELS_CONFIG, "llm", "model", default=_config_value(APP_CONFIG, "llm_model", default="gpt-4.1-mini")),
        )
    )
    openai_api_key: str | None = _setting("OPENAI_API_KEY", _config_value(APP_CONFIG, "openai_api_key"))
    local_llm_endpoint: str = str(
        _setting("SHANA_LOCAL_LLM_ENDPOINT", _config_value(APP_CONFIG, "local_llm_endpoint", default="http://127.0.0.1:11434"))
    )
    local_llm_model: str = str(
        _setting("SHANA_LOCAL_LLM_MODEL", _config_value(APP_CONFIG, "local_llm_model", default="gpt-oss:20b"))
    )
    local_llm_supports_vision: bool = _as_bool(
        _setting(
            "SHANA_LOCAL_LLM_SUPPORTS_VISION",
            _config_value(APP_CONFIG, "local_llm_supports_vision", default=False),
        ),
        default=False,
    )
    local_llm_vision_model: str | None = _setting(
        "SHANA_LOCAL_LLM_VISION_MODEL",
        _config_value(APP_CONFIG, "local_llm_vision_model", default=""),
    )
    local_llm_timeout_seconds: int = _as_int(
        _setting(
            "SHANA_LOCAL_LLM_TIMEOUT_SECONDS",
            _config_value(APP_CONFIG, "local_llm_timeout_seconds", default=120),
        ),
        default=120,
    )

    stt_provider: str = str(
        _setting(
            "SHANA_STT_PROVIDER",
            _config_value(MODELS_CONFIG, "stt", "provider", default=_config_value(APP_CONFIG, "stt_provider", default="stub")),
        )
    )
    stt_model: str = str(
        _setting(
            "SHANA_STT_MODEL",
            _config_value(MODELS_CONFIG, "stt", "model", default=_config_value(APP_CONFIG, "stt_model", default="base.en")),
        )
    )
    stt_device: str = str(_setting("SHANA_STT_DEVICE", _config_value(APP_CONFIG, "stt_device", default="cpu")))
    stt_compute_type: str = str(
        _setting("SHANA_STT_COMPUTE_TYPE", _config_value(APP_CONFIG, "stt_compute_type", default="int8"))
    )

    tts_provider: str = str(
        _setting(
            "SHANA_TTS_PROVIDER",
            _config_value(MODELS_CONFIG, "tts", "provider", default=_config_value(APP_CONFIG, "tts_provider", default="stub")),
        )
    )
    tts_profile: str | None = _setting(
        "SHANA_TTS_PROFILE",
        _config_value(APP_CONFIG, "tts_profile", default=""),
    )
    tts_model: str = str(_setting("SHANA_TTS_MODEL", _config_value(APP_CONFIG, "tts_model", default="gpt-4o-mini-tts")))
    tts_voice: str = str(
        _setting(
            "SHANA_TTS_VOICE",
            _config_value(MODELS_CONFIG, "tts", "voice", default=_config_value(APP_CONFIG, "tts_voice", default="alloy")),
        )
    )
    tts_format: str = str(_setting("SHANA_TTS_FORMAT", _config_value(APP_CONFIG, "tts_format", default="wav")))
    piper_executable: str = str(
        _setting("SHANA_PIPER_EXE", _config_value(APP_CONFIG, "piper_executable", default="piper"))
    )
    piper_model_path: str | None = _setting(
        "SHANA_PIPER_MODEL_PATH",
        _config_value(APP_CONFIG, "piper_model_path", default=""),
    )
    piper_config_path: str | None = _setting(
        "SHANA_PIPER_CONFIG_PATH",
        _config_value(APP_CONFIG, "piper_config_path", default=""),
    )
    piper_speaker_id: str | None = _setting(
        "SHANA_PIPER_SPEAKER_ID",
        _config_value(APP_CONFIG, "piper_speaker_id", default=""),
    )
    rvc_enabled: bool = _as_bool(
        _setting("SHANA_RVC_ENABLED", _config_value(APP_CONFIG, "rvc_enabled", default=False)),
        default=False,
    )
    rvc_python: str | None = _setting(
        "SHANA_RVC_PYTHON",
        _config_value(APP_CONFIG, "rvc_python", default=""),
    )
    rvc_project_root: str | None = _setting(
        "SHANA_RVC_PROJECT_ROOT",
        _config_value(APP_CONFIG, "rvc_project_root", default=""),
    )
    rvc_model_name: str | None = _setting(
        "SHANA_RVC_MODEL_NAME",
        _config_value(APP_CONFIG, "rvc_model_name", default=""),
    )
    rvc_index_path: str | None = _setting(
        "SHANA_RVC_INDEX_PATH",
        _config_value(APP_CONFIG, "rvc_index_path", default=""),
    )
    rvc_pitch: int = _as_int(
        _setting("SHANA_RVC_PITCH", _config_value(APP_CONFIG, "rvc_pitch", default=0)),
        default=0,
    )
    rvc_formant: float = float(
        _setting("SHANA_RVC_FORMANT", _config_value(APP_CONFIG, "rvc_formant", default=0.0))
    )
    rvc_f0_method: str = str(
        _setting("SHANA_RVC_F0_METHOD", _config_value(APP_CONFIG, "rvc_f0_method", default="fcpe"))
    )
    rvc_index_rate: float = float(
        _setting("SHANA_RVC_INDEX_RATE", _config_value(APP_CONFIG, "rvc_index_rate", default=0.0))
    )
    rvc_filter_radius: int = _as_int(
        _setting("SHANA_RVC_FILTER_RADIUS", _config_value(APP_CONFIG, "rvc_filter_radius", default=3)),
        default=3,
    )
    rvc_rms_mix_rate: float = float(
        _setting("SHANA_RVC_RMS_MIX_RATE", _config_value(APP_CONFIG, "rvc_rms_mix_rate", default=0.0))
    )
    rvc_protect: float = float(
        _setting("SHANA_RVC_PROTECT", _config_value(APP_CONFIG, "rvc_protect", default=0.33))
    )
    rvc_resample_sr: int = _as_int(
        _setting("SHANA_RVC_RESAMPLE_SR", _config_value(APP_CONFIG, "rvc_resample_sr", default=0)),
        default=0,
    )
    rvc_device: str | None = _setting(
        "SHANA_RVC_DEVICE",
        _config_value(APP_CONFIG, "rvc_device", default=""),
    )
    gpt_sovits_endpoint: str | None = _setting("SHANA_GPT_SOVITS_ENDPOINT")
    gpt_sovits_reference_audio: str | None = _setting("SHANA_GPT_SOVITS_REFERENCE_AUDIO")
    gpt_sovits_prompt_text: str | None = _setting("SHANA_GPT_SOVITS_PROMPT_TEXT")
    gpt_sovits_prompt_lang: str = str(_setting("SHANA_GPT_SOVITS_PROMPT_LANG", "en"))
    gpt_sovits_text_lang: str = str(_setting("SHANA_GPT_SOVITS_TEXT_LANG", default_language))
    gpt_sovits_timeout_seconds: int = _as_int(_setting("SHANA_GPT_SOVITS_TIMEOUT_SECONDS", 120), default=120)
    gpt_sovits_extra_json: dict = field(
        default_factory=lambda: json.loads(str(_setting("SHANA_GPT_SOVITS_EXTRA_JSON", "{}")) or "{}")
    )

    memory_enabled: bool = _as_bool(
        _setting("SHANA_MEMORY_ENABLED", _config_value(MEMORY_CONFIG, "enabled", default=True)),
        default=True,
    )
    memory_write_mode: str = str(
        _setting("SHANA_MEMORY_WRITE_MODE", _config_value(MEMORY_CONFIG, "write_mode", default="selective"))
    )

    @property
    def shana_base_url(self) -> str:
        return f"http://{self.shana_host}:{self.shana_port}"

    @property
    def dashboard_base_url(self) -> str:
        return f"http://{self.dashboard_host}:{self.dashboard_port}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.audio_output_dir.mkdir(parents=True, exist_ok=True)
settings.image_input_dir.mkdir(parents=True, exist_ok=True)
