from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _find_project_root() -> Path:
    override = os.getenv("SHANA_PROJECT_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if not (root / "pyproject.toml").exists() or not (root / "config" / "app.example.toml").exists():
            raise RuntimeError(f"SHANA_PROJECT_ROOT does not look like the Gamma repo root: {root}")
        return root

    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "config" / "app.example.toml").exists():
            return candidate

    raise RuntimeError("Could not locate Gamma project root. Set SHANA_PROJECT_ROOT to the repo root.")


PROJECT_ROOT = _find_project_root()
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


def voices_presets_config_path() -> Path:
    return CONFIG_DIR / "voices.presets.toml"


def voices_local_config_path() -> Path:
    return CONFIG_DIR / "voices.local.toml"


def load_app_file_config() -> dict[str, Any]:
    return _merged_toml(CONFIG_DIR / "app.example.toml", app_config_path(), app_local_config_path())


def load_desired_app_file_config() -> dict[str, Any]:
    return load_app_file_config()


def load_desired_tts_selection() -> dict[str, str]:
    config = load_desired_app_file_config()
    return {
        "tts_provider": str(config.get("tts_provider", "")).strip(),
        "tts_profile": str(config.get("tts_profile", "")).strip(),
    }


def load_voices_file_config() -> dict[str, Any]:
    return _merged_toml(
        CONFIG_DIR / "voices.example.toml",
        voices_presets_config_path(),
        voices_config_path(),
        voices_local_config_path(),
    )


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


def _as_csv(value: Any, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None or value == "":
        return default
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    return tuple(item.strip() for item in raw_items if str(item).strip())


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
        _setting("SHANA_DATABASE_URL", _config_value(APP_CONFIG, "database_url", default="sqlite:///./data/memory/gamma.db"))
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
    shana_bind_host: str = str(
        _setting(
            "SHANA_BIND_HOST",
            _config_value(
                APP_CONFIG,
                "shana_bind_host",
                default=_setting("SHANA_HOST", _config_value(APP_CONFIG, "shana_host", default="127.0.0.1")),
            ),
        )
    )
    shana_public_host: str = str(
        _setting(
            "SHANA_PUBLIC_HOST",
            _config_value(
                APP_CONFIG,
                "shana_public_host",
                default=_setting("SHANA_HOST", _config_value(APP_CONFIG, "shana_host", default="127.0.0.1")),
            ),
        )
    )
    shana_port: int = _as_int(_setting("SHANA_PORT", _config_value(APP_CONFIG, "shana_port", default=8000)), default=8000)
    shana_public_port: int = _as_int(
        _setting("SHANA_PUBLIC_PORT", _config_value(APP_CONFIG, "shana_public_port", default="")),
        default=_as_int(_setting("SHANA_PORT", _config_value(APP_CONFIG, "shana_port", default=8000)), default=8000),
    )
    shana_public_scheme: str = str(
        _setting(
            "SHANA_PUBLIC_SCHEME",
            _config_value(APP_CONFIG, "shana_public_scheme", default="http"),
        )
    ).strip().lower() or "http"
    dashboard_bind_host: str = str(
        _setting(
            "SHANA_DASHBOARD_BIND_HOST",
            _config_value(
                APP_CONFIG,
                "dashboard_bind_host",
                default=_setting("SHANA_DASHBOARD_HOST", _config_value(APP_CONFIG, "dashboard_host", default="127.0.0.1")),
            ),
        )
    )
    dashboard_public_host: str = str(
        _setting(
            "SHANA_DASHBOARD_PUBLIC_HOST",
            _config_value(
                APP_CONFIG,
                "dashboard_public_host",
                default=_setting("SHANA_DASHBOARD_HOST", _config_value(APP_CONFIG, "dashboard_host", default="127.0.0.1")),
            ),
        )
    )
    dashboard_port: int = _as_int(
        _setting("SHANA_DASHBOARD_PORT", _config_value(APP_CONFIG, "dashboard_port", default=8001)),
        default=8001,
    )
    dashboard_public_port: int = _as_int(
        _setting("SHANA_DASHBOARD_PUBLIC_PORT", _config_value(APP_CONFIG, "dashboard_public_port", default="")),
        default=_as_int(_setting("SHANA_DASHBOARD_PORT", _config_value(APP_CONFIG, "dashboard_port", default=8001)), default=8001),
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
    dashboard_public_scheme: str = str(
        _setting(
            "SHANA_DASHBOARD_PUBLIC_SCHEME",
            _config_value(APP_CONFIG, "dashboard_public_scheme", default="https" if _as_bool(_setting("SHANA_DASHBOARD_COOKIE_SECURE", _config_value(APP_CONFIG, "dashboard_cookie_secure", default=False)), default=False) else "http"),
        )
    ).strip().lower() or "http"
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
    llm_router_enabled: bool = _as_bool(
        _setting("SHANA_LLM_ROUTER_ENABLED", _config_value(APP_CONFIG, "llm_router_enabled", default=False)),
        default=False,
    )
    llm_router_default_provider: str = str(
        _setting(
            "SHANA_LLM_ROUTER_DEFAULT_PROVIDER",
            _config_value(APP_CONFIG, "llm_router_default_provider", default=""),
        )
    ).strip().lower()
    llm_router_default_model: str = str(
        _setting(
            "SHANA_LLM_ROUTER_DEFAULT_MODEL",
            _config_value(APP_CONFIG, "llm_router_default_model", default=""),
        )
    ).strip()
    llm_router_allow_hosted_escalation: bool = _as_bool(
        _setting(
            "SHANA_LLM_ROUTER_ALLOW_HOSTED_ESCALATION",
            _config_value(APP_CONFIG, "llm_router_allow_hosted_escalation", default=False),
        ),
        default=False,
    )
    llm_router_hosted_provider: str = str(
        _setting(
            "SHANA_LLM_ROUTER_HOSTED_PROVIDER",
            _config_value(APP_CONFIG, "llm_router_hosted_provider", default="openai"),
        )
    ).strip().lower() or "openai"
    llm_router_hosted_model: str = str(
        _setting(
            "SHANA_LLM_ROUTER_HOSTED_MODEL",
            _config_value(APP_CONFIG, "llm_router_hosted_model", default=""),
        )
    ).strip()
    llm_router_profile: str = str(
        _setting(
            "SHANA_LLM_ROUTER_PROFILE",
            _config_value(APP_CONFIG, "llm_router_profile", default="balanced"),
        )
    ).strip().lower() or "balanced"
    llm_router_complex_max_input_words: int = _as_int(
        _setting(
            "SHANA_LLM_ROUTER_COMPLEX_MAX_INPUT_WORDS",
            _config_value(APP_CONFIG, "llm_router_complex_max_input_words", default=120),
        ),
        default=120,
    )
    llm_router_failure_backoff_seconds: int = _as_int(
        _setting(
            "SHANA_LLM_ROUTER_FAILURE_BACKOFF_SECONDS",
            _config_value(APP_CONFIG, "llm_router_failure_backoff_seconds", default=45),
        ),
        default=45,
    )
    llm_router_chat_light_max_input_words: int = _as_int(
        _setting(
            "SHANA_LLM_ROUTER_CHAT_LIGHT_MAX_INPUT_WORDS",
            _config_value(APP_CONFIG, "llm_router_chat_light_max_input_words", default=40),
        ),
        default=40,
    )
    llm_router_persona_hosted_fallback_enabled: bool = _as_bool(
        _setting(
            "SHANA_LLM_ROUTER_PERSONA_HOSTED_FALLBACK_ENABLED",
            _config_value(APP_CONFIG, "llm_router_persona_hosted_fallback_enabled", default=False),
        ),
        default=False,
    )
    llm_router_persona_heavy_hosted_fallback_enabled: bool = _as_bool(
        _setting(
            "SHANA_LLM_ROUTER_PERSONA_HEAVY_HOSTED_FALLBACK_ENABLED",
            _config_value(APP_CONFIG, "llm_router_persona_heavy_hosted_fallback_enabled", default=True),
        ),
        default=True,
    )
    openai_api_key: str | None = _setting("OPENAI_API_KEY", _config_value(APP_CONFIG, "openai_api_key"))
    local_llm_endpoint: str = str(
        _setting("SHANA_LOCAL_LLM_ENDPOINT", _config_value(APP_CONFIG, "local_llm_endpoint", default="http://127.0.0.1:11434"))
    )
    local_llm_model: str = str(
        _setting("SHANA_LOCAL_LLM_MODEL", _config_value(APP_CONFIG, "local_llm_model", default="gpt-oss:20b"))
    )
    local_llm_light_model: str = str(
        _setting(
            "SHANA_LOCAL_LLM_LIGHT_MODEL",
            _config_value(
                MODELS_CONFIG,
                "local_workers",
                "summary_model",
                default=_config_value(APP_CONFIG, "local_llm_light_model", default=""),
            ),
        )
    )
    local_llm_tagging_model: str = str(
        _setting(
            "SHANA_LOCAL_LLM_TAGGING_MODEL",
            _config_value(
                MODELS_CONFIG,
                "local_workers",
                "tagging_model",
                default=_config_value(APP_CONFIG, "local_llm_tagging_model", default=""),
            ),
        )
    )
    local_llm_enable_routing: bool = _as_bool(
        _setting("SHANA_LOCAL_LLM_ENABLE_ROUTING", _config_value(APP_CONFIG, "local_llm_enable_routing", default=False)),
        default=False,
    )
    local_llm_light_max_input_words: int = _as_int(
        _setting(
            "SHANA_LOCAL_LLM_LIGHT_MAX_INPUT_WORDS",
            _config_value(APP_CONFIG, "local_llm_light_max_input_words", default=40),
        ),
        default=40,
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
    live_voice_response_mode: str = str(
        _setting(
            "SHANA_LIVE_VOICE_RESPONSE_MODE",
            _config_value(APP_CONFIG, "live_voice_response_mode", default="simple_chunked"),
        )
    )
    proactive_idle_enabled: bool = _as_bool(
        _setting("SHANA_PROACTIVE_IDLE_ENABLED", _config_value(APP_CONFIG, "proactive_idle_enabled", default=False)),
        default=False,
    )
    proactive_idle_min_silence_seconds: int = _as_int(
        _setting(
            "SHANA_PROACTIVE_IDLE_MIN_SILENCE_SECONDS",
            _config_value(APP_CONFIG, "proactive_idle_min_silence_seconds", default=30),
        ),
        default=30,
    )
    proactive_idle_target_silence_seconds: int = _as_int(
        _setting(
            "SHANA_PROACTIVE_IDLE_TARGET_SILENCE_SECONDS",
            _config_value(APP_CONFIG, "proactive_idle_target_silence_seconds", default=60),
        ),
        default=60,
    )
    proactive_idle_cooldown_seconds: int = _as_int(
        _setting(
            "SHANA_PROACTIVE_IDLE_COOLDOWN_SECONDS",
            _config_value(APP_CONFIG, "proactive_idle_cooldown_seconds", default=180),
        ),
        default=180,
    )
    proactive_idle_max_attempts_per_topic: int = _as_int(
        _setting(
            "SHANA_PROACTIVE_IDLE_MAX_ATTEMPTS_PER_TOPIC",
            _config_value(APP_CONFIG, "proactive_idle_max_attempts_per_topic", default=2),
        ),
        default=2,
    )
    proactive_idle_tick_seconds: int = _as_int(
        _setting(
            "SHANA_PROACTIVE_IDLE_TICK_SECONDS",
            _config_value(APP_CONFIG, "proactive_idle_tick_seconds", default=5),
        ),
        default=5,
    )
    proactive_idle_speech_enabled: bool = _as_bool(
        _setting(
            "SHANA_PROACTIVE_IDLE_SPEECH_ENABLED",
            _config_value(APP_CONFIG, "proactive_idle_speech_enabled", default=False),
        ),
        default=False,
    )
    twitch_channel: str = str(
        _setting("SHANA_TWITCH_CHANNEL", _config_value(APP_CONFIG, "twitch_channel", default=""))
    ).strip()
    twitch_bot_username: str = str(
        _setting("SHANA_TWITCH_BOT_USERNAME", _config_value(APP_CONFIG, "twitch_bot_username", default=""))
    ).strip()
    twitch_oauth_token: str = str(
        _setting("SHANA_TWITCH_OAUTH_TOKEN", _config_value(APP_CONFIG, "twitch_oauth_token", default=""))
    ).strip()
    twitch_owner_user_id: str = str(
        _setting("SHANA_TWITCH_OWNER_USER_ID", _config_value(APP_CONFIG, "twitch_owner_user_id", default=""))
    ).strip()
    twitch_client_id: str = str(
        _setting("SHANA_TWITCH_CLIENT_ID", _config_value(APP_CONFIG, "twitch_client_id", default=""))
    ).strip()
    twitch_broadcaster_user_id: str = str(
        _setting("SHANA_TWITCH_BROADCASTER_USER_ID", _config_value(APP_CONFIG, "twitch_broadcaster_user_id", default=""))
    ).strip()
    twitch_moderator_user_id: str = str(
        _setting("SHANA_TWITCH_MODERATOR_USER_ID", _config_value(APP_CONFIG, "twitch_moderator_user_id", default=""))
    ).strip()
    twitch_eventsub_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_EVENTSUB_ENABLED", _config_value(APP_CONFIG, "twitch_eventsub_enabled", default=False)),
        default=False,
    )
    twitch_ignored_bots: tuple[str, ...] = _as_csv(
        _setting(
            "SHANA_TWITCH_IGNORED_BOTS",
            _config_value(APP_CONFIG, "twitch_ignored_bots", default="Nightbot,StreamElements,Streamlabs"),
        ),
        default=("Nightbot", "StreamElements", "Streamlabs"),
    )
    twitch_irc_host: str = str(
        _setting("SHANA_TWITCH_IRC_HOST", _config_value(APP_CONFIG, "twitch_irc_host", default="irc.chat.twitch.tv"))
    ).strip() or "irc.chat.twitch.tv"
    twitch_irc_port: int = _as_int(
        _setting("SHANA_TWITCH_IRC_PORT", _config_value(APP_CONFIG, "twitch_irc_port", default=6697)),
        default=6697,
    )
    twitch_dry_run: bool = _as_bool(
        _setting("SHANA_TWITCH_DRY_RUN", _config_value(APP_CONFIG, "twitch_dry_run", default=True)),
        default=True,
    )
    twitch_voice_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_VOICE_ENABLED", _config_value(APP_CONFIG, "twitch_voice_enabled", default=False)),
        default=False,
    )
    twitch_subtitles_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_SUBTITLES_ENABLED", _config_value(APP_CONFIG, "twitch_subtitles_enabled", default=True)),
        default=True,
    )
    twitch_ambient_chat_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_AMBIENT_CHAT_ENABLED", _config_value(APP_CONFIG, "twitch_ambient_chat_enabled", default=True)),
        default=True,
    )
    twitch_mention_replies_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_MENTION_REPLIES_ENABLED", _config_value(APP_CONFIG, "twitch_mention_replies_enabled", default=True)),
        default=True,
    )
    twitch_spam_quips_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_SPAM_QUIPS_ENABLED", _config_value(APP_CONFIG, "twitch_spam_quips_enabled", default=True)),
        default=True,
    )
    twitch_self_goal_proposals_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_SELF_GOAL_PROPOSALS_ENABLED", _config_value(APP_CONFIG, "twitch_self_goal_proposals_enabled", default=True)),
        default=True,
    )
    twitch_llm_safety_review_enabled: bool = _as_bool(
        _setting("SHANA_TWITCH_LLM_SAFETY_REVIEW_ENABLED", _config_value(APP_CONFIG, "twitch_llm_safety_review_enabled", default=True)),
        default=True,
    )
    twitch_min_speech_gap_seconds: int = _as_int(
        _setting("SHANA_TWITCH_MIN_SPEECH_GAP_SECONDS", _config_value(APP_CONFIG, "twitch_min_speech_gap_seconds", default=5)),
        default=5,
    )
    twitch_spam_quip_cooldown_seconds: int = _as_int(
        _setting("SHANA_TWITCH_SPAM_QUIP_COOLDOWN_SECONDS", _config_value(APP_CONFIG, "twitch_spam_quip_cooldown_seconds", default=60)),
        default=60,
    )
    twitch_max_speech_seconds_per_minute: int = _as_int(
        _setting(
            "SHANA_TWITCH_MAX_SPEECH_SECONDS_PER_MINUTE",
            _config_value(APP_CONFIG, "twitch_max_speech_seconds_per_minute", default=20),
        ),
        default=20,
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
    stt_device_index: int = _as_int(
        _setting("SHANA_STT_DEVICE_INDEX", _config_value(APP_CONFIG, "stt_device_index", default=0)),
        default=0,
    )
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
    qwen_tts_endpoint: str | None = _setting(
        "SHANA_QWEN_TTS_ENDPOINT",
        _config_value(APP_CONFIG, "qwen_tts_endpoint", default=""),
    )
    qwen_tts_reference_audio: str | None = _setting(
        "SHANA_QWEN_TTS_REFERENCE_AUDIO",
        _config_value(APP_CONFIG, "qwen_tts_reference_audio", default=""),
    )
    qwen_tts_reference_text: str | None = _setting(
        "SHANA_QWEN_TTS_REFERENCE_TEXT",
        _config_value(APP_CONFIG, "qwen_tts_reference_text", default=""),
    )
    qwen_tts_language: str = str(
        _setting(
            "SHANA_QWEN_TTS_LANGUAGE",
            _config_value(APP_CONFIG, "qwen_tts_language", default="English"),
        )
    )
    qwen_tts_speaker: str | None = _setting(
        "SHANA_QWEN_TTS_SPEAKER",
        _config_value(APP_CONFIG, "qwen_tts_speaker", default=""),
    )
    qwen_tts_instruct: str | None = _setting(
        "SHANA_QWEN_TTS_INSTRUCT",
        _config_value(APP_CONFIG, "qwen_tts_instruct", default=""),
    )
    qwen_tts_timeout_seconds: int = _as_int(
        _setting(
            "SHANA_QWEN_TTS_TIMEOUT_SECONDS",
            _config_value(APP_CONFIG, "qwen_tts_timeout_seconds", default=120),
        ),
        default=120,
    )
    qwen_tts_extra_json: dict = field(
        default_factory=lambda: json.loads(
            str(_setting("SHANA_QWEN_TTS_EXTRA_JSON", _config_value(APP_CONFIG, "qwen_tts_extra_json", default="{}"))) or "{}"
        )
    )
    speech_filter_level: str = str(
        _setting("SHANA_SPEECH_FILTER_LEVEL", _config_value(APP_CONFIG, "speech_filter_level", default="strict"))
    ).strip().lower() or "strict"
    speech_filter_hard_block_enabled: bool = _as_bool(
        _setting("SHANA_SPEECH_FILTER_HARD_BLOCK_ENABLED", _config_value(APP_CONFIG, "speech_filter_hard_block_enabled", default=True)),
        default=True,
    )
    speech_filter_heuristic_enabled: bool = _as_bool(
        _setting("SHANA_SPEECH_FILTER_HEURISTIC_ENABLED", _config_value(APP_CONFIG, "speech_filter_heuristic_enabled", default=True)),
        default=True,
    )
    speech_filter_llm_enabled: bool = _as_bool(
        _setting("SHANA_SPEECH_FILTER_LLM_ENABLED", _config_value(APP_CONFIG, "speech_filter_llm_enabled", default=False)),
        default=False,
    )
    speech_filter_llm_model: str = str(
        _setting("SHANA_SPEECH_FILTER_LLM_MODEL", _config_value(APP_CONFIG, "speech_filter_llm_model", default=""))
    )
    speech_filter_llm_temperature: float = float(
        _setting(
            "SHANA_SPEECH_FILTER_LLM_TEMPERATURE",
            _config_value(APP_CONFIG, "speech_filter_llm_temperature", default=0.0),
        )
    )
    speech_filter_auto_rewrite: bool = _as_bool(
        _setting("SHANA_SPEECH_FILTER_AUTO_REWRITE", _config_value(APP_CONFIG, "speech_filter_auto_rewrite", default=True)),
        default=True,
    )
    speech_filter_banned_words_path: str = str(
        _setting(
            "SHANA_SPEECH_FILTER_BANNED_WORDS_PATH",
            _config_value(APP_CONFIG, "speech_filter_banned_words_path", default="./config/safety_banned_words.txt"),
        )
    ).strip()
    stream_filtered_audio_path: str = str(
        _setting("SHANA_STREAM_FILTERED_AUDIO_PATH", _config_value(APP_CONFIG, "stream_filtered_audio_path", default="./assets/audio/system/filtered.wav"))
    ).strip()
    stream_safety_review_timeout_seconds: float = float(
        _setting(
            "SHANA_STREAM_SAFETY_REVIEW_TIMEOUT_SECONDS",
            _config_value(APP_CONFIG, "stream_safety_review_timeout_seconds", default=2.0),
        )
    )
    stream_safety_review_timeout_action: str = str(
        _setting(
            "SHANA_STREAM_SAFETY_REVIEW_TIMEOUT_ACTION",
            _config_value(APP_CONFIG, "stream_safety_review_timeout_action", default="skip"),
        )
    ).strip().lower() or "skip"
    assistant_state_enabled: bool = _as_bool(
        _setting("SHANA_ASSISTANT_STATE_ENABLED", _config_value(APP_CONFIG, "assistant_state_enabled", default=True)),
        default=True,
    )
    assistant_emotion_decay_turns: int = _as_int(
        _setting("SHANA_ASSISTANT_EMOTION_DECAY_TURNS", _config_value(APP_CONFIG, "assistant_emotion_decay_turns", default=3)),
        default=3,
    )
    assistant_emotion_episode_threshold: float = float(
        _setting("SHANA_ASSISTANT_EMOTION_EPISODE_THRESHOLD", _config_value(APP_CONFIG, "assistant_emotion_episode_threshold", default=0.65))
    )
    assistant_emotion_pattern_threshold: int = _as_int(
        _setting("SHANA_ASSISTANT_EMOTION_PATTERN_THRESHOLD", _config_value(APP_CONFIG, "assistant_emotion_pattern_threshold", default=3)),
        default=3,
    )

    memory_enabled: bool = _as_bool(
        _setting("SHANA_MEMORY_ENABLED", _config_value(MEMORY_CONFIG, "enabled", default=True)),
        default=True,
    )
    memory_write_mode: str = str(
        _setting("SHANA_MEMORY_WRITE_MODE", _config_value(MEMORY_CONFIG, "write_mode", default="selective"))
    )
    memory_personality: str = str(
        _setting("SHANA_MEMORY_PERSONALITY", _config_value(MEMORY_CONFIG, "personality", default="entertainer"))
    ).strip().lower() or "entertainer"

    @property
    def shana_base_url(self) -> str:
        default_port = 443 if self.shana_public_scheme == "https" else 80
        if self.shana_public_port == default_port:
            return f"{self.shana_public_scheme}://{self.shana_public_host}"
        return f"{self.shana_public_scheme}://{self.shana_public_host}:{self.shana_public_port}"

    @property
    def shana_internal_base_url(self) -> str:
        host = self.shana_bind_host.strip()
        if host in {"", "0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.shana_port}"

    @property
    def dashboard_base_url(self) -> str:
        default_port = 443 if self.dashboard_public_scheme == "https" else 80
        if self.dashboard_public_port == default_port:
            return f"{self.dashboard_public_scheme}://{self.dashboard_public_host}"
        return f"{self.dashboard_public_scheme}://{self.dashboard_public_host}:{self.dashboard_public_port}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.audio_output_dir.mkdir(parents=True, exist_ok=True)
settings.image_input_dir.mkdir(parents=True, exist_ok=True)
