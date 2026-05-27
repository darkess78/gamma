from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
import tomllib

from ..config import load_voices_file_config, settings, voices_local_config_path


@dataclass(slots=True)
class VoiceProfile:
    profile_id: str
    label: str
    provider: str
    description: str = ""
    values: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "label": self.label,
            "provider": self.provider,
            "description": self.description,
            "values": dict(self.values or {}),
        }


@dataclass(slots=True)
class ResolvedTTSConfig:
    profile_id: str | None
    profile_label: str | None
    provider: str
    tts_model: str
    tts_voice: str
    tts_format: str
    piper_executable: str
    piper_model_path: str | None
    piper_config_path: str | None
    piper_speaker_id: str | None
    rvc_enabled: bool
    rvc_python: str | None
    rvc_project_root: str | None
    rvc_model_name: str | None
    rvc_index_path: str | None
    rvc_pitch: int
    rvc_formant: float
    rvc_f0_method: str
    rvc_index_rate: float
    rvc_filter_radius: int
    rvc_rms_mix_rate: float
    rvc_protect: float
    rvc_resample_sr: int
    rvc_device: str | None
    gpt_sovits_endpoint: str | None
    gpt_sovits_reference_audio: str | None
    gpt_sovits_prompt_text: str | None
    gpt_sovits_prompt_lang: str
    gpt_sovits_text_lang: str
    gpt_sovits_timeout_seconds: int
    gpt_sovits_extra_json: dict[str, Any]
    qwen_tts_endpoint: str | None
    qwen_tts_reference_audio: str | None
    qwen_tts_reference_text: str | None
    qwen_tts_language: str
    qwen_tts_speaker: str | None
    qwen_tts_instruct: str | None
    qwen_tts_timeout_seconds: int
    qwen_tts_extra_json: dict[str, Any]
    denoise_enabled: bool
    denoise_strength: float
    denoise_gain: float


def list_voice_profiles() -> list[VoiceProfile]:
    raw_profiles = _load_voices_config().get("profiles", {})
    profiles: list[VoiceProfile] = []
    for profile_id, raw in raw_profiles.items():
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider", "")).strip()
        if not provider:
            continue
        profiles.append(
            VoiceProfile(
                profile_id=profile_id,
                label=str(raw.get("label", profile_id)),
                provider=provider,
                description=str(raw.get("description", "")),
                values=dict(raw),
            )
        )
    profiles.sort(key=lambda profile: (profile.label.lower(), profile.profile_id))
    return profiles


def get_voice_profile(profile_id: str | None) -> VoiceProfile | None:
    if not profile_id:
        return None
    for profile in list_voice_profiles():
        if profile.profile_id == profile_id:
            return profile
    return None


def save_voice_profile(profile_id: str, payload: dict[str, Any]) -> VoiceProfile:
    normalized_id = str(profile_id or "").strip()
    if not normalized_id:
        raise ValueError("profile id is required")
    provider = str(payload.get("provider", "")).strip().lower()
    if not provider:
        raise ValueError("provider is required")
    label = str(payload.get("label", normalized_id)).strip() or normalized_id
    description = str(payload.get("description", "")).strip()
    values = payload.get("values") if isinstance(payload.get("values"), dict) else {}

    cleaned: dict[str, Any] = {
        "label": label,
        "provider": provider,
    }
    if description:
        cleaned["description"] = description
    for key, value in values.items():
        if key in {"label", "provider", "description"}:
            continue
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        cleaned[key] = value

    config = _load_voices_config()
    profiles = config.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        config["profiles"] = profiles
    profiles[normalized_id] = cleaned
    _write_voices_config(config)
    return get_voice_profile(normalized_id) or VoiceProfile(
        profile_id=normalized_id,
        label=label,
        provider=provider,
        description=description,
        values=cleaned,
    )


def profile_template(provider: str) -> dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    base = {
        "label": "",
        "provider": normalized,
        "description": "",
        "values": {},
    }
    if normalized == "piper":
        base["values"] = {
            "piper_model_path": settings.piper_model_path or "",
            "piper_config_path": settings.piper_config_path or "",
            "rvc_enabled": False,
            "rvc_model_name": "",
            "rvc_pitch": settings.rvc_pitch,
            "rvc_formant": settings.rvc_formant,
            "rvc_f0_method": settings.rvc_f0_method,
            "rvc_index_rate": settings.rvc_index_rate,
            "rvc_filter_radius": settings.rvc_filter_radius,
            "rvc_rms_mix_rate": settings.rvc_rms_mix_rate,
            "rvc_protect": settings.rvc_protect,
            "rvc_resample_sr": settings.rvc_resample_sr,
        }
    elif normalized in {"local", "gpt-sovits", "gpt_sovits"}:
        base["values"] = {
            "gpt_sovits_reference_audio": settings.gpt_sovits_reference_audio or "",
            "gpt_sovits_prompt_text": settings.gpt_sovits_prompt_text or "",
            "gpt_sovits_prompt_lang": settings.gpt_sovits_prompt_lang,
            "gpt_sovits_text_lang": settings.gpt_sovits_text_lang,
            "gpt_sovits_extra_json": dict(settings.gpt_sovits_extra_json or {}),
        }
    elif normalized in {"qwen-tts", "qwen_tts", "qwen", "qwentts"}:
        base["values"] = {
            "qwen_tts_endpoint": settings.qwen_tts_endpoint or "",
            "qwen_tts_reference_audio": settings.qwen_tts_reference_audio or "",
            "qwen_tts_reference_text": settings.qwen_tts_reference_text or "",
            "qwen_tts_language": settings.qwen_tts_language,
            "qwen_tts_speaker": settings.qwen_tts_speaker or "",
            "qwen_tts_instruct": settings.qwen_tts_instruct or "",
            "qwen_tts_timeout_seconds": settings.qwen_tts_timeout_seconds,
            "qwen_tts_extra_json": dict(settings.qwen_tts_extra_json or {}),
        }
    return base


def _coalesce(override: Any, fallback: Any) -> Any:
    if override is None:
        return fallback
    if isinstance(override, str) and override == "":
        return fallback
    return override


def resolve_tts_config() -> ResolvedTTSConfig:
    profile = get_voice_profile(settings.tts_profile)
    values = profile.values if profile and isinstance(profile.values, dict) else {}
    provider = str(_coalesce(values.get("provider"), settings.tts_provider)).strip().lower()

    return ResolvedTTSConfig(
        profile_id=profile.profile_id if profile else None,
        profile_label=profile.label if profile else None,
        provider=provider,
        tts_model=str(_coalesce(values.get("tts_model"), settings.tts_model)),
        tts_voice=str(_coalesce(values.get("tts_voice"), settings.tts_voice)),
        tts_format=str(_coalesce(values.get("tts_format"), settings.tts_format)),
        piper_executable=str(_coalesce(values.get("piper_executable"), settings.piper_executable)),
        piper_model_path=_coalesce(values.get("piper_model_path"), settings.piper_model_path),
        piper_config_path=_coalesce(values.get("piper_config_path"), settings.piper_config_path),
        piper_speaker_id=_coalesce(values.get("piper_speaker_id"), settings.piper_speaker_id),
        rvc_enabled=bool(_coalesce(values.get("rvc_enabled"), settings.rvc_enabled)),
        rvc_python=_coalesce(values.get("rvc_python"), settings.rvc_python),
        rvc_project_root=_coalesce(values.get("rvc_project_root"), settings.rvc_project_root),
        rvc_model_name=_coalesce(values.get("rvc_model_name"), settings.rvc_model_name),
        rvc_index_path=_coalesce(values.get("rvc_index_path"), settings.rvc_index_path),
        rvc_pitch=int(_coalesce(values.get("rvc_pitch"), settings.rvc_pitch)),
        rvc_formant=float(_coalesce(values.get("rvc_formant"), settings.rvc_formant)),
        rvc_f0_method=str(_coalesce(values.get("rvc_f0_method"), settings.rvc_f0_method)),
        rvc_index_rate=float(_coalesce(values.get("rvc_index_rate"), settings.rvc_index_rate)),
        rvc_filter_radius=int(_coalesce(values.get("rvc_filter_radius"), settings.rvc_filter_radius)),
        rvc_rms_mix_rate=float(_coalesce(values.get("rvc_rms_mix_rate"), settings.rvc_rms_mix_rate)),
        rvc_protect=float(_coalesce(values.get("rvc_protect"), settings.rvc_protect)),
        rvc_resample_sr=int(_coalesce(values.get("rvc_resample_sr"), settings.rvc_resample_sr)),
        rvc_device=_coalesce(values.get("rvc_device"), settings.rvc_device),
        gpt_sovits_endpoint=_coalesce(values.get("gpt_sovits_endpoint"), settings.gpt_sovits_endpoint),
        gpt_sovits_reference_audio=_coalesce(values.get("gpt_sovits_reference_audio"), settings.gpt_sovits_reference_audio),
        gpt_sovits_prompt_text=_coalesce(values.get("gpt_sovits_prompt_text"), settings.gpt_sovits_prompt_text),
        gpt_sovits_prompt_lang=str(_coalesce(values.get("gpt_sovits_prompt_lang"), settings.gpt_sovits_prompt_lang)),
        gpt_sovits_text_lang=str(_coalesce(values.get("gpt_sovits_text_lang"), settings.gpt_sovits_text_lang)),
        gpt_sovits_timeout_seconds=int(
            _coalesce(values.get("gpt_sovits_timeout_seconds"), settings.gpt_sovits_timeout_seconds)
        ),
        gpt_sovits_extra_json=dict(_coalesce(values.get("gpt_sovits_extra_json"), settings.gpt_sovits_extra_json) or {}),
        qwen_tts_endpoint=_coalesce(values.get("qwen_tts_endpoint"), settings.qwen_tts_endpoint),
        qwen_tts_reference_audio=_coalesce(values.get("qwen_tts_reference_audio"), settings.qwen_tts_reference_audio),
        qwen_tts_reference_text=_coalesce(values.get("qwen_tts_reference_text"), settings.qwen_tts_reference_text),
        qwen_tts_language=str(_coalesce(values.get("qwen_tts_language"), settings.qwen_tts_language) or "English"),
        qwen_tts_speaker=_coalesce(values.get("qwen_tts_speaker"), settings.qwen_tts_speaker),
        qwen_tts_instruct=_coalesce(values.get("qwen_tts_instruct"), settings.qwen_tts_instruct),
        qwen_tts_timeout_seconds=int(_coalesce(values.get("qwen_tts_timeout_seconds"), settings.qwen_tts_timeout_seconds) or 120),
        qwen_tts_extra_json=dict(_coalesce(values.get("qwen_tts_extra_json"), settings.qwen_tts_extra_json) or {}),
        denoise_enabled=bool(_coalesce(values.get("denoise_enabled"), False)),
        denoise_strength=float(_coalesce(values.get("denoise_strength"), 1.5)),
        denoise_gain=float(_coalesce(values.get("denoise_gain"), 1.0)),
    )


def _load_voices_config() -> dict[str, Any]:
    data = load_voices_file_config()
    return data if isinstance(data, dict) else {"profiles": {}}


def _voices_config_path() -> Path:
    return voices_local_config_path()


def _write_voices_config(config: dict[str, Any]) -> None:
    path = _voices_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    lines: list[str] = []
    for profile_id in sorted(profiles.keys()):
        profile = profiles[profile_id]
        if not isinstance(profile, dict):
            continue
        lines.append(f"[profiles.{profile_id}]")
        for key, value in profile.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return json.dumps(value)
    if isinstance(value, dict):
        items = [f"{key} = {_toml_value(inner)}" for key, inner in value.items()]
        return "{ " + ", ".join(items) + " }"
    return json.dumps(str(value))
