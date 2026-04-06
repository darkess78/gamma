from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import settings
from ..llm.ollama_probe import probe_ollama_model_capabilities
from ..memory.service import MemoryService


class SystemStatusService:
    def __init__(self) -> None:
        self._memory = MemoryService()

    def build_status(self) -> dict[str, Any]:
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
                    "provider": settings.tts_provider,
                    "model": settings.tts_model,
                    "voice": settings.tts_voice,
                    "endpoint": settings.gpt_sovits_endpoint,
                    "health": self._check_gpt_sovits_health() if settings.tts_provider in {"local", "gpt-sovits", "gpt_sovits"} else {"ok": True, "detail": "not-local"},
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

    def _check_gpt_sovits_health(self) -> dict[str, Any]:
        if not settings.gpt_sovits_endpoint:
            return {"ok": False, "detail": "no-endpoint-configured"}
        base_url = settings.gpt_sovits_endpoint.rsplit("/tts", 1)[0]
        try:
            with urllib.request.urlopen(base_url + "/docs", timeout=5) as response:
                ok = 200 <= response.status < 400
            return {"ok": ok, "detail": "reachable" if ok else f"http-{response.status}"}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "detail": f"http-{exc.code}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

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
