from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _find_project_root() -> Path:
    """Find project root.
    
    Returns:
        Path: Project root path.
    
    Raises:
        RuntimeError: If SHANA_PROJECT_ROOT is not set or paths don't exist.
    """
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
    merged = _read_toml(CONFIG_DIR / "voices.example.toml")
    try:
        presets = _read_toml(voices_presets_config_path())
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        presets = {}
    merged = _merge_dicts(merged, presets)
    merged = _merge_dicts(merged, _read_toml(voices_config_path()))
    return _merge_dicts(merged, _read_toml(voices_local_config_path()))


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


def _as_float(value: Any, *, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


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


def _raw(*keys: str, default: Any = None, env: str | None = None, source: dict[str, Any] | None = None) -> Any:
    """Environment variable if set, else the merged config value at ``keys``, else ``default``.

    The environment variable defaults to ``SHANA_`` plus the upper-cased key.
    """
    if env is None:
        if len(keys) != 1:
            raise ValueError("nested config keys need an explicit env name")
        env = "SHANA_" + keys[0].upper()
    return _setting(env, _config_value(APP_CONFIG if source is None else source, *keys, default=default))


def _str(
    *keys: str,
    default: Any,
    env: str | None = None,
    source: dict[str, Any] | None = None,
    strip: bool = False,
    lower: bool = False,
    fallback: str | None = None,
) -> str:
    value = str(_raw(*keys, default=default, env=env, source=source))
    if strip:
        value = value.strip()
    if lower:
        value = value.lower()
    if fallback is not None:
        value = value or fallback
    return value


def _int(*keys: str, default: int, env: str | None = None, source: dict[str, Any] | None = None) -> int:
    return _as_int(_raw(*keys, default=default, env=env, source=source), default=default)


def _bool(*keys: str, default: bool, env: str | None = None, source: dict[str, Any] | None = None) -> bool:
    return _as_bool(_raw(*keys, default=default, env=env, source=source), default=default)


def _float(*keys: str, default: float, env: str | None = None, source: dict[str, Any] | None = None) -> float:
    return _as_float(_raw(*keys, default=default, env=env, source=source), default=default)


@dataclass(slots=True)
class Settings:
    app_name: str = _str("app_name", default="gamma")
    project_root: Path = PROJECT_ROOT
    database_url: str = _str("database_url", default="sqlite:///./data/memory/gamma.db")
    memory_top_k: int = _int("memory_top_k", default=5)
    conversation_journal_retention_days: int = _int("conversation_journal_retention_days", default=30)
    conversation_journal_max_exchanges_per_session: int = _int(
        "conversation_journal_max_exchanges_per_session",
        default=500,
    )
    conversation_recent_turn_tokens: int = _int("conversation_recent_turn_tokens", default=2048)
    conversation_summary_interval_exchanges: int = _int("conversation_summary_interval_exchanges", default=6)
    data_dir: Path = PROJECT_ROOT / "data"
    audio_output_dir: Path = _as_path(
        _raw("audio_output_dir", default="./data/audio"),
        default=PROJECT_ROOT / "data" / "audio",
    )
    image_input_dir: Path = _as_path(
        _raw("image_input_dir", default="./data/images"),
        default=PROJECT_ROOT / "data" / "images",
    )
    vision_max_image_bytes: int = _int("vision_max_image_bytes", default=8_000_000)
    default_language: str = _str("default_language", default="en")
    shana_bind_host: str = _str(
        "shana_bind_host",
        default=_raw("shana_host", default="127.0.0.1", env="SHANA_HOST"),
        env="SHANA_BIND_HOST",
    )
    shana_public_host: str = _str(
        "shana_public_host",
        default=_raw("shana_host", default="127.0.0.1", env="SHANA_HOST"),
        env="SHANA_PUBLIC_HOST",
    )
    shana_port: int = _int("shana_port", default=8000, env="SHANA_PORT")
    shana_public_port: int = _as_int(
        _raw("shana_public_port", default="", env="SHANA_PUBLIC_PORT"),
        default=_as_int(_raw("shana_port", default=8000, env="SHANA_PORT"), default=8000),
    )
    shana_public_scheme: str = _str(
        "shana_public_scheme",
        default="http",
        strip=True,
        lower=True,
        fallback="http",
        env="SHANA_PUBLIC_SCHEME",
    )
    dashboard_bind_host: str = _str("dashboard_bind_host", default=_raw("dashboard_host", default="127.0.0.1"))
    dashboard_public_host: str = _str("dashboard_public_host", default=_raw("dashboard_host", default="127.0.0.1"))
    dashboard_port: int = _int("dashboard_port", default=8001)
    dashboard_public_port: int = _as_int(
        _raw("dashboard_public_port", default=""),
        default=_as_int(_raw("dashboard_port", default=8001), default=8001),
    )
    dashboard_enable_gpu: bool = _bool("dashboard_enable_gpu", default=True)
    dashboard_metrics_interval_seconds: int = _int("dashboard_metrics_interval_seconds", default=10)
    dashboard_auth_enabled: bool = _bool("dashboard_auth_enabled", default=False)
    dashboard_auth_username: str = _str("dashboard_auth_username", default="")
    dashboard_auth_password: str = _str("dashboard_auth_password", default="")
    dashboard_auth_password_hash: str = _str("dashboard_auth_password_hash", default="")
    dashboard_session_secret: str = _str("dashboard_session_secret", default="")
    dashboard_cookie_secure: bool = _bool("dashboard_cookie_secure", default=False)
    dashboard_public_scheme: str = _str(
        "dashboard_public_scheme",
        default="https" if _bool("dashboard_cookie_secure", default=False) else "http",
        strip=True,
        lower=True,
        fallback="http",
    )
    api_auth_enabled: bool = _bool("api_auth_enabled", default=False)
    api_bearer_token: str = _str("api_bearer_token", default="")
    # Neuro SDK game protocol at ws://<host>:8000/v1/games/neuro?key=<games_neuro_key>
    games_neuro_enabled: bool = _bool("games_neuro_enabled", default=False)
    games_neuro_key: str = _str("games_neuro_key", default="", strip=True)
    games_neuro_chooser: str = _str("games_neuro_chooser", default="model", strip=True, lower=True)
    # 3D avatar over the VMC protocol (OSC/UDP), e.g. Warudo or VSeeFace on the
    # streaming PC. See specs/avatar_3d.md. [avatar3d] table in app.toml.
    avatar3d_enabled: bool = _bool("avatar3d", "enabled", default=False, env="SHANA_AVATAR3D_ENABLED")
    avatar3d_host: str = _str("avatar3d", "host", default="127.0.0.1", env="SHANA_AVATAR3D_HOST", strip=True, fallback="127.0.0.1")
    avatar3d_port: int = _int("avatar3d", "port", default=39539, env="SHANA_AVATAR3D_PORT")
    # "vrm0" (Joy/Angry/Sorrow/Fun/A/I/U/E/O) or "vrm1" (happy/angry/sad/relaxed/aa/ih/ou/ee/oh)
    avatar3d_naming: str = _str("avatar3d", "naming", default="vrm0", env="SHANA_AVATAR3D_NAMING", strip=True, lower=True, fallback="vrm0")
    avatar3d_fps: int = _int("avatar3d", "fps", default=30, env="SHANA_AVATAR3D_FPS")
    # "text" = Gamma moves the mouth from the spoken text; "off" = the avatar app does lip sync itself.
    avatar3d_lipsync: str = _str("avatar3d", "lipsync", default="text", env="SHANA_AVATAR3D_LIPSYNC", strip=True, lower=True, fallback="text")
    avatar3d_lipsync_chars_per_second: float = _float(
        "avatar3d", "lipsync_chars_per_second", default=14.0, env="SHANA_AVATAR3D_LIPSYNC_CHARS_PER_SECOND"
    )
    avatar3d_lipsync_delay_ms: int = _int("avatar3d", "lipsync_delay_ms", default=0, env="SHANA_AVATAR3D_LIPSYNC_DELAY_MS")
    avatar3d_auto_blink: bool = _bool("avatar3d", "auto_blink", default=True, env="SHANA_AVATAR3D_AUTO_BLINK")
    avatar3d_target_policy: str = _str(
        "avatar3d", "target_policy", default="stream_public", env="SHANA_AVATAR3D_TARGET_POLICY", strip=True, lower=True, fallback="stream_public"
    )

    llm_provider: str = _str(
        "llm",
        "provider",
        default=_config_value(APP_CONFIG, "llm_provider", default="mock"),
        source=MODELS_CONFIG,
        env="SHANA_LLM_PROVIDER",
    )
    llm_model: str = _str(
        "llm",
        "model",
        default=_config_value(APP_CONFIG, "llm_model", default="gpt-4.1-mini"),
        source=MODELS_CONFIG,
        env="SHANA_LLM_MODEL",
    )
    llm_router_enabled: bool = _bool("llm_router_enabled", default=False)
    llm_router_default_provider: str = _str("llm_router_default_provider", default="", strip=True, lower=True)
    llm_router_default_model: str = _str("llm_router_default_model", default="", strip=True)
    llm_router_allow_hosted_escalation: bool = _bool("llm_router_allow_hosted_escalation", default=False)
    llm_router_hosted_provider: str = _str(
        "llm_router_hosted_provider",
        default="openai",
        strip=True,
        lower=True,
        fallback="openai",
    )
    llm_router_hosted_model: str = _str("llm_router_hosted_model", default="", strip=True)
    llm_router_profile: str = _str(
        "llm_router_profile",
        default="balanced",
        strip=True,
        lower=True,
        fallback="balanced",
    )
    llm_router_complex_max_input_words: int = _int("llm_router_complex_max_input_words", default=120)
    llm_router_failure_backoff_seconds: int = _int("llm_router_failure_backoff_seconds", default=45)
    llm_router_chat_light_max_input_words: int = _int("llm_router_chat_light_max_input_words", default=40)
    llm_router_persona_hosted_fallback_enabled: bool = _bool(
        "llm_router_persona_hosted_fallback_enabled",
        default=False,
    )
    llm_router_persona_heavy_hosted_fallback_enabled: bool = _bool(
        "llm_router_persona_heavy_hosted_fallback_enabled",
        default=True,
    )
    openai_api_key: str | None = _raw("openai_api_key", env="OPENAI_API_KEY")
    local_llm_endpoint: str = _str("local_llm_endpoint", default="http://127.0.0.1:11434")
    local_llm_model: str = _str("local_llm_model", default="gpt-oss:20b")
    # Compatibility key: local_workers.summary_model selects the light
    # conversational/auxiliary model, not a neural rolling-summary worker.
    local_llm_light_model: str = _str(
        "local_workers",
        "summary_model",
        default=_config_value(APP_CONFIG, "local_llm_light_model", default=""),
        source=MODELS_CONFIG,
        env="SHANA_LOCAL_LLM_LIGHT_MODEL",
    )
    local_llm_tagging_model: str = _str(
        "local_workers",
        "tagging_model",
        default=_config_value(APP_CONFIG, "local_llm_tagging_model", default=""),
        source=MODELS_CONFIG,
        env="SHANA_LOCAL_LLM_TAGGING_MODEL",
    )
    local_llm_enable_routing: bool = _bool("local_llm_enable_routing", default=False)
    local_llm_light_max_input_words: int = _int("local_llm_light_max_input_words", default=40)
    local_llm_supports_vision: bool = _bool("local_llm_supports_vision", default=False)
    local_llm_vision_model: str | None = _raw("local_llm_vision_model", default="")
    local_llm_keep_alive: str = _str("local_llm_keep_alive", default="30m")
    local_llm_timeout_seconds: int = _int("local_llm_timeout_seconds", default=120)
    live_voice_response_mode: str = _str("live_voice_response_mode", default="simple_chunked")
    proactive_idle_enabled: bool = _bool("proactive_idle_enabled", default=False)
    proactive_idle_min_silence_seconds: int = _int("proactive_idle_min_silence_seconds", default=30)
    proactive_idle_target_silence_seconds: int = _int("proactive_idle_target_silence_seconds", default=60)
    proactive_idle_cooldown_seconds: int = _int("proactive_idle_cooldown_seconds", default=180)
    proactive_idle_max_attempts_per_topic: int = _int("proactive_idle_max_attempts_per_topic", default=2)
    proactive_idle_tick_seconds: int = _int("proactive_idle_tick_seconds", default=5)
    proactive_idle_speech_enabled: bool = _bool("proactive_idle_speech_enabled", default=False)
    proactive_quiet_hours_start: str = _str("proactive_quiet_hours_start", default="", strip=True)
    proactive_quiet_hours_end: str = _str("proactive_quiet_hours_end", default="", strip=True)
    twitch_channel: str = _str("twitch_channel", default="", strip=True)
    twitch_bot_username: str = _str("twitch_bot_username", default="", strip=True)
    twitch_oauth_token: str = _str("twitch_oauth_token", default="", strip=True)
    twitch_owner_user_id: str = _str("twitch_owner_user_id", default="", strip=True)
    twitch_client_id: str = _str("twitch_client_id", default="", strip=True)
    twitch_broadcaster_user_id: str = _str("twitch_broadcaster_user_id", default="", strip=True)
    twitch_moderator_user_id: str = _str("twitch_moderator_user_id", default="", strip=True)
    twitch_eventsub_enabled: bool = _bool("twitch_eventsub_enabled", default=False)
    twitch_eventsub_subscriptions: tuple[str, ...] = _as_csv(
        _raw("twitch_eventsub_subscriptions", default="channel.follow"),
        default=("channel.follow",),
    )
    log_max_bytes: int = _int("log_max_bytes", default=5 * 1024 * 1024)
    log_backup_count: int = _int("log_backup_count", default=5)
    log_stderr_enabled: bool = _bool("log_stderr_enabled", default=True)
    twitch_ignored_bots: tuple[str, ...] = _as_csv(
        _raw("twitch_ignored_bots", default="Nightbot,StreamElements,Streamlabs"),
        default=("Nightbot", "StreamElements", "Streamlabs"),
    )
    twitch_irc_host: str = _str(
        "twitch_irc_host",
        default="irc.chat.twitch.tv",
        strip=True,
        fallback="irc.chat.twitch.tv",
    )
    twitch_irc_port: int = _int("twitch_irc_port", default=6697)
    twitch_dry_run: bool = _bool("twitch_dry_run", default=True)
    twitch_voice_enabled: bool = _bool("twitch_voice_enabled", default=False)
    twitch_subtitles_enabled: bool = _bool("twitch_subtitles_enabled", default=True)
    twitch_ambient_chat_enabled: bool = _bool("twitch_ambient_chat_enabled", default=True)
    twitch_mention_replies_enabled: bool = _bool("twitch_mention_replies_enabled", default=True)
    twitch_spam_quips_enabled: bool = _bool("twitch_spam_quips_enabled", default=True)
    twitch_self_goal_proposals_enabled: bool = _bool("twitch_self_goal_proposals_enabled", default=True)
    twitch_llm_safety_review_enabled: bool = _bool("twitch_llm_safety_review_enabled", default=True)
    twitch_min_speech_gap_seconds: int = _int("twitch_min_speech_gap_seconds", default=5)
    twitch_spam_quip_cooldown_seconds: int = _int("twitch_spam_quip_cooldown_seconds", default=60)
    twitch_max_speech_seconds_per_minute: int = _int("twitch_max_speech_seconds_per_minute", default=20)

    stt_provider: str = _str(
        "stt",
        "provider",
        default=_config_value(APP_CONFIG, "stt_provider", default="stub"),
        source=MODELS_CONFIG,
        env="SHANA_STT_PROVIDER",
    )
    stt_model: str = _str(
        "stt",
        "model",
        default=_config_value(APP_CONFIG, "stt_model", default="base.en"),
        source=MODELS_CONFIG,
        env="SHANA_STT_MODEL",
    )
    stt_device: str = _str("stt_device", default="cpu")
    stt_device_index: int | None = _as_int(_raw("stt_device_index", default=""), default=None)
    stt_compute_type: str = _str("stt_compute_type", default="int8")
    audio_understanding_enabled: bool = _bool("audio_understanding_enabled", default=True)
    audio_understanding_prompt_enabled: bool = _bool("audio_understanding_prompt_enabled", default=False)
    speaker_emotion_provider: str = _str("speaker_emotion_provider", default="disabled")
    speaker_emotion_model: str = _str("speaker_emotion_model", default="superb/wav2vec2-base-superb-er")
    audio_event_provider: str = _str("audio_event_provider", default="disabled")
    audio_event_model: str = _str("audio_event_model", default="MIT/ast-finetuned-audioset-10-10-0.4593")
    audio_analysis_device: str = _str("audio_analysis_device", default="cpu")
    speaker_emotion_device: str = _str(
        "speaker_emotion_device",
        default=_config_value(APP_CONFIG, "audio_analysis_device", default="cpu"),
    )
    audio_event_device: str = _str(
        "audio_event_device",
        default=_config_value(APP_CONFIG, "audio_analysis_device", default="cpu"),
    )
    audio_understanding_endpoint: str | None = _raw("audio_understanding_endpoint", default="")
    audio_understanding_bind_host: str = _str("audio_understanding_bind_host", default="127.0.0.1")
    audio_understanding_port: int = _int("audio_understanding_port", default=9883)
    audio_understanding_request_timeout_seconds: int = _int("audio_understanding_request_timeout_seconds", default=15)
    audio_understanding_python: str | None = _raw("audio_understanding_python", default="")
    audio_model_local_files_only: bool = _bool("audio_model_local_files_only", default=False)
    speaker_emotion_min_confidence: float = _float("speaker_emotion_min_confidence", default=0.65)
    speaker_emotion_requires_transcript: bool = _bool("speaker_emotion_requires_transcript", default=True)
    audio_event_min_confidence: float = _float("audio_event_min_confidence", default=0.70)
    audio_event_labels: tuple[str, ...] = _as_csv(
        _raw("audio_event_labels", default=["laughter", "cough", "clapping"]),
        default=("laughter", "cough", "clapping"),
    )
    audio_event_min_duration_ms: int = _int("audio_event_min_duration_ms", default=100)
    audio_event_merge_gap_ms: int = _int("audio_event_merge_gap_ms", default=250)
    audio_event_cooldown_ms: int = _int("audio_event_cooldown_ms", default=500)
    audio_analysis_max_seconds: int = _int("audio_analysis_max_seconds", default=30)
    audio_analysis_timeout_ms: int = _int("audio_analysis_timeout_ms", default=500)

    tts_provider: str = _str(
        "tts",
        "provider",
        default=_config_value(APP_CONFIG, "tts_provider", default="stub"),
        source=MODELS_CONFIG,
        env="SHANA_TTS_PROVIDER",
    )
    tts_profile: str | None = _raw("tts_profile", default="")
    tts_model: str = _str("tts_model", default="gpt-4o-mini-tts")
    tts_voice: str = _str(
        "tts",
        "voice",
        default=_config_value(APP_CONFIG, "tts_voice", default="alloy"),
        source=MODELS_CONFIG,
        env="SHANA_TTS_VOICE",
    )
    tts_format: str = _str("tts_format", default="wav")
    piper_executable: str = _str("piper_executable", default="piper", env="SHANA_PIPER_EXE")
    piper_model_path: str | None = _raw("piper_model_path", default="")
    piper_config_path: str | None = _raw("piper_config_path", default="")
    piper_speaker_id: str | None = _raw("piper_speaker_id", default="")
    rvc_enabled: bool = _bool("rvc_enabled", default=False)
    rvc_python: str | None = _raw("rvc_python", default="")
    rvc_project_root: str | None = _raw("rvc_project_root", default="")
    rvc_model_name: str | None = _raw("rvc_model_name", default="")
    rvc_index_path: str | None = _raw("rvc_index_path", default="")
    rvc_pitch: int = _int("rvc_pitch", default=0)
    rvc_formant: float = float(_raw("rvc_formant", default=0.0))
    rvc_f0_method: str = _str("rvc_f0_method", default="fcpe")
    rvc_index_rate: float = float(_raw("rvc_index_rate", default=0.0))
    rvc_filter_radius: int = _int("rvc_filter_radius", default=3)
    rvc_rms_mix_rate: float = float(_raw("rvc_rms_mix_rate", default=0.0))
    rvc_protect: float = float(_raw("rvc_protect", default=0.33))
    rvc_resample_sr: int = _int("rvc_resample_sr", default=0)
    rvc_device: str | None = _raw("rvc_device", default="")
    qwen_tts_endpoint: str | None = _raw("qwen_tts_endpoint", default="")
    qwen_tts_device: str = _str("qwen_tts_device", default="")
    qwen_tts_reference_audio: str | None = _raw("qwen_tts_reference_audio", default="")
    qwen_tts_reference_text: str | None = _raw("qwen_tts_reference_text", default="")
    qwen_tts_language: str = _str("qwen_tts_language", default="English")
    qwen_tts_speaker: str | None = _raw("qwen_tts_speaker", default="")
    qwen_tts_instruct: str | None = _raw("qwen_tts_instruct", default="")
    qwen_tts_timeout_seconds: int = _int("qwen_tts_timeout_seconds", default=120)
    qwen_tts_extra_json: dict = field(
        default_factory=lambda: json.loads(
            str(_raw("qwen_tts_extra_json", default="{}")) or "{}"
        )
    )
    speech_filter_level: str = _str("speech_filter_level", default="strict", strip=True, lower=True, fallback="strict")
    speech_filter_hard_block_enabled: bool = _bool("speech_filter_hard_block_enabled", default=True)
    speech_filter_heuristic_enabled: bool = _bool("speech_filter_heuristic_enabled", default=True)
    speech_filter_llm_enabled: bool = _bool("speech_filter_llm_enabled", default=False)
    speech_filter_llm_model: str = _str("speech_filter_llm_model", default="")
    speech_filter_llm_temperature: float = float(_raw("speech_filter_llm_temperature", default=0.0) )
    speech_filter_auto_rewrite: bool = _bool("speech_filter_auto_rewrite", default=True)
    speech_filter_banned_words_path: str = _str(
        "speech_filter_banned_words_path",
        default="./config/safety_banned_words.txt",
        strip=True,
    )
    stream_filtered_audio_path: str = _str(
        "stream_filtered_audio_path",
        default="./assets/audio/system/filtered.wav",
        strip=True,
    )
    stream_safety_review_timeout_seconds: float = float(_raw("stream_safety_review_timeout_seconds", default=2.0) )
    stream_safety_review_timeout_action: str = _str(
        "stream_safety_review_timeout_action",
        default="skip",
        strip=True,
        lower=True,
        fallback="skip",
    )
    assistant_state_enabled: bool = _bool("assistant_state_enabled", default=True)
    assistant_emotion_decay_turns: int = _int("assistant_emotion_decay_turns", default=3)
    assistant_emotion_episode_threshold: float = float(_raw("assistant_emotion_episode_threshold", default=0.65) )
    assistant_emotion_pattern_threshold: int = _int("assistant_emotion_pattern_threshold", default=3)

    memory_enabled: bool = _bool("enabled", default=True, source=MEMORY_CONFIG, env="SHANA_MEMORY_ENABLED")
    memory_write_mode: str = _str(
        "write_mode",
        default="selective",
        source=MEMORY_CONFIG,
        env="SHANA_MEMORY_WRITE_MODE",
    )
    memory_personality: str = _str(
        "personality",
        default="entertainer",
        source=MEMORY_CONFIG,
        strip=True,
        lower=True,
        fallback="entertainer",
        env="SHANA_MEMORY_PERSONALITY",
    )

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
