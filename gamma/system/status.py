from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import settings
from ..llm.ollama_probe import probe_ollama_model_capabilities
from ..memory.service import MemoryService
from ..voice.rvc_support import (
    discover_rvc_project_root,
    discover_rvc_python,
    resolve_rvc_index_path,
    resolve_rvc_model_path,
)
from ..voice.voice_profiles import list_voice_profiles, resolve_tts_config


class SystemStatusService:
    def __init__(self) -> None:
        self._memory = MemoryService()

    def build_status(self) -> dict[str, Any]:
        tts_cfg = resolve_tts_config()
        return {
            "app": {
                "name": settings.app_name,
                "project_root": str(settings.project_root),
                "database_url": settings.database_url,
                "audio_output_dir": str(settings.audio_output_dir),
                "default_language": settings.default_language,
            },
            "providers": {
                "llm": {
                    "provider": settings.llm_provider,
                    "model": settings.llm_model,
                    "endpoint": settings.local_llm_endpoint if settings.llm_provider in {"local", "ollama"} else None,
                    "vision_enabled": settings.local_llm_supports_vision if settings.llm_provider in {"local", "ollama"} else False,
                    "vision_model": self._local_vision_model_name() if settings.llm_provider in {"local", "ollama"} else None,
                    "vision_capability": self._check_local_llm_vision_capability() if settings.llm_provider in {"local", "ollama"} else {"ok": False, "detail": "not-local"},
                    "health": self._check_ollama_health() if settings.llm_provider in {"local", "ollama"} else {"ok": True, "detail": "not-local"},
                },
                "stt": {
                    "provider": settings.stt_provider,
                    "model": settings.stt_model,
                    "device": settings.stt_device,
                },
                "tts": {
                    "provider": tts_cfg.provider,
                    "model": tts_cfg.tts_model,
                    "voice": tts_cfg.tts_voice,
                    "profile_id": tts_cfg.profile_id,
                    "profile_label": tts_cfg.profile_label,
                    "available_profiles": [profile.as_payload() for profile in list_voice_profiles()],
                    "piper_executable": tts_cfg.piper_executable if tts_cfg.provider == "piper" else None,
                    "piper_model_path": tts_cfg.piper_model_path if tts_cfg.provider == "piper" else None,
                    "rvc_enabled": tts_cfg.rvc_enabled,
                    "rvc_model_name": tts_cfg.rvc_model_name if tts_cfg.rvc_enabled else None,
                    "rvc_formant": tts_cfg.rvc_formant if tts_cfg.rvc_enabled else None,
                    "endpoint": tts_cfg.gpt_sovits_endpoint,
                    "health": self._check_tts_health(tts_cfg),
                },
            },
            "memory": {
                "stats": self._memory.stats(),
                "known_people": self._memory.get_known_people(),
                "primary_user_facts": [
                    {
                        "category": fact.category,
                        "fact_text": fact.fact_text,
                        "confidence": fact.confidence,
                    }
                    for fact in self._memory.get_profile_facts(limit=10, subject_type="primary_user")
                ],
            },
            "recent_artifacts": self._recent_artifacts(),
        }

    def _check_ollama_health(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(settings.local_llm_endpoint.rstrip("/") + "/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            model_names = [model.get("name") for model in payload.get("models", []) if isinstance(model, dict)]
            return {
                "ok": True,
                "models": model_names,
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    def _check_local_llm_vision_capability(self) -> dict[str, Any]:
        if not settings.local_llm_supports_vision:
            return {"ok": False, "detail": "vision-disabled-in-config"}
        model_name = self._local_vision_model_name()
        return probe_ollama_model_capabilities(
            endpoint=settings.local_llm_endpoint,
            model=model_name,
            timeout_seconds=5,
        )

    def _local_vision_model_name(self) -> str:
        configured = (settings.local_llm_vision_model or "").strip()
        if configured:
            return configured
        return settings.local_llm_model

    def _check_gpt_sovits_health(self, tts_cfg: Any) -> dict[str, Any]:
        if not tts_cfg.gpt_sovits_endpoint:
            return {"ok": False, "detail": "no-endpoint-configured"}
        base_url = tts_cfg.gpt_sovits_endpoint.rsplit("/tts", 1)[0]
        return self._check_http_docs_health(base_url)

    def _check_qwen_tts_health(self, tts_cfg: Any) -> dict[str, Any]:
        if not tts_cfg.qwen_tts_endpoint:
            return {"ok": False, "detail": "no-endpoint-configured"}
        base_url = tts_cfg.qwen_tts_endpoint.rsplit("/tts", 1)[0]
        return self._check_http_docs_health(base_url)

    def _check_http_docs_health(self, base_url: str) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(base_url + "/docs", timeout=5) as response:
                ok = 200 <= response.status < 400
            return {"ok": ok, "detail": "reachable" if ok else f"http-{response.status}"}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "detail": f"http-{exc.code}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    def _check_tts_health(self, tts_cfg: Any) -> dict[str, Any]:
        provider = tts_cfg.provider.strip().lower()
        if provider == "piper":
            piper = self._check_piper_health(tts_cfg)
            if not tts_cfg.rvc_enabled:
                return piper
            if not piper.get("ok"):
                return piper
            rvc = self._check_rvc_health(tts_cfg)
            return rvc if not rvc.get("ok") else {"ok": True, "detail": "ready"}
        if provider in {"local", "gpt-sovits", "gpt_sovits"}:
            return self._check_gpt_sovits_health(tts_cfg)
        if provider in {"qwen-tts", "qwen_tts", "qwen", "qwentts"}:
            return self._check_qwen_tts_health(tts_cfg)
        return {"ok": True, "detail": "not-local"}

    def _check_piper_health(self, tts_cfg: Any) -> dict[str, Any]:
        executable = (tts_cfg.piper_executable or "").strip()
        model_path = (tts_cfg.piper_model_path or "").strip()
        if not executable:
            return {"ok": False, "detail": "no-piper-executable-configured"}
        if not shutil.which(executable):
            return {"ok": False, "detail": f"piper-not-found: {executable}"}
        if not model_path:
            return {"ok": False, "detail": "no-piper-model-configured"}
        model = Path(model_path).expanduser()
        if not model.is_absolute():
            model = settings.project_root / model
        if not model.exists():
            return {"ok": False, "detail": f"missing-piper-model: {model}"}
        config_path = (tts_cfg.piper_config_path or "").strip()
        if config_path:
            config = Path(config_path).expanduser()
            if not config.is_absolute():
                config = settings.project_root / config
            if not config.exists():
                return {"ok": False, "detail": f"missing-piper-config: {config}"}
        return {"ok": True, "detail": "ready"}

    def _check_rvc_health(self, tts_cfg: Any) -> dict[str, Any]:
        rvc_root = discover_rvc_project_root(tts_cfg.rvc_project_root)
        if rvc_root is None:
            return {"ok": False, "detail": "missing-rvc-project-root"}
        infer_cli = rvc_root / "tools" / "infer_cli.py"
        if not infer_cli.exists():
            return {"ok": False, "detail": f"missing-rvc-infer-cli: {infer_cli}"}
        rvc_python = discover_rvc_python(tts_cfg.rvc_python, rvc_root)
        if rvc_python is None:
            return {"ok": False, "detail": "missing-rvc-python"}
        if not (tts_cfg.rvc_model_name or "").strip():
            return {"ok": False, "detail": "no-rvc-model-name-configured"}
        try:
            model_path = resolve_rvc_model_path(rvc_root, tts_cfg.rvc_model_name)
            index_path = resolve_rvc_index_path(rvc_root, tts_cfg.rvc_index_path, model_path.name)
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
        return {
            "ok": True,
            "detail": "ready",
            "python": str(rvc_python),
            "project_root": str(rvc_root),
            "model": str(model_path),
            "index": str(index_path),
        }

    def _recent_artifacts(self, limit: int = 12) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        if not settings.audio_output_dir.exists():
            return artifacts
        files = sorted(settings.audio_output_dir.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files[:limit]:
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        return artifacts
