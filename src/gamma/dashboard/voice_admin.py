"""TTS/STT provider controls, voice profiles, provider tests and remote live-voice jobs."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from ..config import app_local_config_path, load_desired_tts_selection, settings
from ..schemas.voice import VoiceRoundtripResponse
from ..system.cuda_env import prepend_cuda_library_path
from ..voice.voice_profiles import get_voice_profile, profile_template, save_voice_profile


class VoiceAdminMixin:
    """TTS/STT provider controls, voice profiles, provider tests and remote live-voice jobs."""

    def start_tts(self) -> dict[str, Any]:
        provider = self.selected_tts_provider()
        if not self._is_qwen_provider(provider):
            return {"ok": False, "detail": f"TTS start control is only available for Qwen3-TTS, not {provider}."}
        return self._run_provider_action(
            "tts_start",
            self._tts_script_command("start", provider),
            success_detail="Qwen3-TTS start requested.",
        )

    def stop_tts(self) -> dict[str, Any]:
        provider = self.selected_tts_provider()
        if not self._is_qwen_provider(provider):
            return {"ok": False, "detail": f"TTS stop control is only available for Qwen3-TTS, not {provider}."}
        return self._run_provider_action(
            "tts_stop",
            self._tts_script_command("stop", provider),
            success_detail="Qwen3-TTS stop requested.",
        )

    @staticmethod
    def _is_qwen_provider(provider: str) -> bool:
        return provider.strip().lower() in {"qwen-tts", "qwen_tts", "qwen", "qwentts"}

    @classmethod
    def _canonical_tts_provider(cls, provider: str) -> str:
        normalized = provider.strip().lower()
        return "qwen-tts" if cls._is_qwen_provider(normalized) else normalized

    def selected_tts_provider(self) -> str:
        provider = self._canonical_tts_provider(load_desired_tts_selection().get("tts_provider", ""))
        if provider in {"qwen-tts", "piper", "openai"}:
            return provider
        runtime_provider = self._canonical_tts_provider(settings.tts_provider)
        return runtime_provider if runtime_provider in {"qwen-tts", "piper", "openai"} else provider

    def selected_tts_profile(self) -> str | None:
        value = load_desired_tts_selection().get("tts_profile", "")
        if value:
            return value
        return settings.tts_profile or None

    def set_tts_provider(self, provider: str) -> dict[str, Any]:
        normalized = provider.strip().lower()
        allowed = {"piper", "qwen-tts", "openai"}
        if normalized not in allowed:
            raise ValueError(f"unsupported tts provider: {provider}")
        app_toml = app_local_config_path()
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        updated = self._upsert_toml_string(existing, "tts_provider", normalized)
        updated = self._upsert_toml_string(updated, "tts_profile", "")
        app_toml.parent.mkdir(parents=True, exist_ok=True)
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "tts_provider_select",
            "status": "ok",
            "detail": f"TTS provider set to {normalized}. Saved voice profile cleared. Restart Shana to use it for conversation responses.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": normalized,
        }
        return {
            "ok": True,
            "provider": normalized,
            "detail": "TTS provider saved. Saved voice profile cleared. Restart Shana to use it for normal conversations.",
        }

    def set_tts_profile(self, profile_id: str) -> dict[str, Any]:
        if not profile_id:
            app_toml = app_local_config_path()
            existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
            updated = self._upsert_toml_string(existing, "tts_profile", "")
            app_toml.parent.mkdir(parents=True, exist_ok=True)
            app_toml.write_text(updated, encoding="utf-8")
            self._latest_provider_action = {
                "action": "tts_profile_select",
                "status": "ok",
                "detail": "TTS profile cleared. Restart Shana to use base provider settings for conversation responses.",
                "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "provider": self.selected_tts_provider(),
                "profile": "",
            }
            return {
                "ok": True,
                "provider": self.selected_tts_provider(),
                "profile": "",
                "detail": "TTS profile cleared. Restart Shana to use base provider settings for normal conversations.",
            }
        profile = get_voice_profile(profile_id)
        if profile is None:
            raise ValueError(f"unsupported tts profile: {profile_id}")
        app_toml = app_local_config_path()
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        updated = self._upsert_toml_string(existing, "tts_profile", profile.profile_id)
        updated = self._upsert_toml_string(updated, "tts_provider", profile.provider)
        app_toml.parent.mkdir(parents=True, exist_ok=True)
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "tts_profile_select",
            "status": "ok",
            "detail": f"TTS profile set to {profile.label}. Restart Shana to use it for conversation responses.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": profile.provider,
            "profile": profile.profile_id,
        }
        return {
            "ok": True,
            "provider": profile.provider,
            "profile": profile.profile_id,
            "detail": "TTS profile saved. Restart Shana to use it for normal conversations.",
        }

    def save_tts_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(payload.get("id", "")).strip()
        profile = save_voice_profile(
            profile_id,
            {
                "label": payload.get("label", ""),
                "provider": payload.get("provider", ""),
                "description": payload.get("description", ""),
                "values": payload.get("values", {}),
            },
        )
        self._latest_provider_action = {
            "action": "tts_profile_save",
            "status": "ok",
            "detail": f"TTS profile saved: {profile.label}.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": profile.provider,
            "profile": profile.profile_id,
        }
        return {
            "ok": True,
            "profile": profile.as_payload(),
            "detail": "TTS profile saved.",
        }

    def tts_profile_editor_state(self, profile_id: str | None, provider: str | None) -> dict[str, Any]:
        profile = get_voice_profile(profile_id)
        if profile is not None:
            return profile.as_payload()
        template = profile_template(provider or self.selected_tts_provider())
        return {
            "id": "",
            "label": template.get("label", ""),
            "provider": template.get("provider", provider or self.selected_tts_provider()),
            "description": template.get("description", ""),
            "values": template.get("values", {}),
        }

    def test_stt(self) -> dict[str, Any]:
        sample = self._sample_audio_path()
        return self._run_provider_action(
            "stt_test",
            self._python_module_command("gamma.run_stt_test", str(sample)),
            success_detail="STT smoke test completed.",
        )

    def test_tts(self) -> dict[str, Any]:
        selected_provider = self.selected_tts_provider()
        selected_profile = self.selected_tts_profile()
        env = {"SHANA_TTS_PROVIDER": selected_provider}
        if selected_profile:
            env["SHANA_TTS_PROFILE"] = selected_profile
        if self._is_qwen_provider(selected_provider):
            ready = self._wait_for_qwen_tts_ready(selected_profile, timeout_seconds=90)
            if not ready.get("ok"):
                self._latest_provider_action = {
                    "action": "tts_test",
                    "status": "error",
                    "detail": "tts_test failed",
                    "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "stdout": "",
                    "stderr": ready.get("detail", "Qwen3-TTS is not ready."),
                    "duration_ms": 0.0,
                }
                return self._latest_provider_action
        return self._run_provider_action(
            "tts_test",
            self._python_module_command("gamma.run_tts_test", "Dashboard TTS smoke test."),
            env_overrides=env,
            success_detail="TTS smoke test completed.",
        )

    def synthesize_text(self, text: str) -> dict[str, Any]:
        """Synthesize *text* (multi-chunk if needed) via subprocess; return audio filename."""
        import tempfile
        selected_provider = self.selected_tts_provider()
        selected_profile = self.selected_tts_profile()
        env = {"SHANA_TTS_PROVIDER": selected_provider}
        if selected_profile:
            env["SHANA_TTS_PROFILE"] = selected_profile
        tmppath: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", encoding="utf-8", delete=False
            ) as f:
                f.write(text)
                tmppath = f.name
            completed = self._run_command(
                self._python_module_command("gamma.run_tts_test", "--file", tmppath, "--json"),
                timeout=300,
                env_overrides=env,
            )
            output = (completed.stdout or "").strip()
            payload = json.loads(output)
            audio_path = payload.get("audio_path", "")
            filename = Path(audio_path).name if audio_path else ""
            return {
                "ok": True,
                "filename": filename,
                "provider": payload.get("provider"),
                "timings_ms": payload.get("timings_ms") or {},
            }
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return {"ok": False, "detail": stderr or "synthesis failed"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": "synthesis timed out"}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"ok": False, "detail": "synthesis returned unexpected output"}
        finally:
            if tmppath:
                try:
                    Path(tmppath).unlink()
                except Exception:
                    pass

    def test_llm(self) -> dict[str, Any]:
        return self._run_provider_action(
            "llm_test",
            self._python_module_command("gamma.run_llm_test", "Dashboard LLM smoke test."),
            success_detail="LLM smoke test completed.",
        )

    def test_voice_roundtrip(self) -> dict[str, Any]:
        sample = self._sample_audio_path()
        selected_provider = self.selected_tts_provider()
        if self._is_qwen_provider(selected_provider):
            ready = self._wait_for_qwen_tts_ready(self.selected_tts_profile(), timeout_seconds=90)
            if not ready.get("ok"):
                self._latest_provider_action = {
                    "action": "voice_roundtrip_test",
                    "status": "error",
                    "detail": "voice_roundtrip_test failed",
                    "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "stdout": "",
                    "stderr": ready.get("detail", "Qwen3-TTS is not ready."),
                    "duration_ms": 0.0,
                }
                return self._latest_provider_action
        return self._run_provider_action(
            "voice_roundtrip_test",
            self._python_module_command("gamma.run_voice_roundtrip", str(sample)),
            timeout=180,
            success_detail="Full voice loop completed.",
        )

    def run_remote_voice_roundtrip(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        session_id: str | None,
        synthesize_speech: bool,
    ) -> VoiceRoundtripResponse:
        data: dict[str, Any] = {
            "synthesize_speech": "true" if synthesize_speech else "false",
        }
        if session_id:
            data["session_id"] = session_id
        payload = self._shana.post_multipart(
            "/v1/voice/roundtrip",
            data=data,
            field_name="audio_file",
            filename=filename,
            content=audio_bytes,
            content_type=content_type,
        )
        return VoiceRoundtripResponse.model_validate(payload)

    def start_remote_live_job(
        self,
        *,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str,
        turn_id: str | None,
    ) -> dict[str, Any]:
        return self._post_live_audio(
            path="/v1/voice/live/start",
            pcm_bytes=pcm_bytes,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            turn_id=turn_id,
        )

    def transcribe_remote_live_audio(self, *, pcm_bytes: bytes) -> dict[str, Any]:
        return self._post_live_audio(
            path="/v1/voice/transcribe",
            pcm_bytes=pcm_bytes,
            session_id=None,
            synthesize_speech=None,
            response_mode=None,
            turn_id=None,
        )

    def get_remote_live_job(self, turn_id: str) -> dict[str, Any]:
        return self._shana.get(f"/v1/voice/live/{turn_id}")

    def cancel_remote_live_job(self, turn_id: str, *, reason: str = "interrupted") -> dict[str, Any]:
        return self._shana.request_json(
            "POST",
            f"/v1/voice/live/{turn_id}/cancel",
            data={"reason": reason},
            timeout=30,
        )

    def remote_live_history(self, *, limit: int = 20) -> dict[str, Any]:
        return self._shana.get("/v1/voice/live/history", params={"limit": max(1, min(limit, 100))})

    def _post_live_audio(
        self,
        *,
        path: str,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool | None,
        response_mode: str | None,
        turn_id: str | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if session_id:
            data["session_id"] = session_id
        if synthesize_speech is not None:
            data["synthesize_speech"] = "true" if synthesize_speech else "false"
        if response_mode:
            data["response_mode"] = response_mode
        if turn_id:
            data["turn_id"] = turn_id
        return self._shana.post_multipart(
            path,
            data=data,
            field_name="audio_file",
            filename="live-browser.wav",
            content=self._pcm_to_wav_bytes(pcm_bytes),
            content_type="audio/wav",
        )

    def _pcm_to_wav_bytes(self, pcm_bytes: bytes) -> bytes:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm_bytes)
        return buffer.getvalue()

    def _run_provider_action(
        self,
        action_name: str,
        command: list[str],
        *,
        timeout: int = 60,
        env_overrides: dict[str, str] | None = None,
        success_detail: str,
    ) -> dict[str, Any]:
        payload = {
            "action": action_name,
            "command": command,
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        started_at = time.perf_counter()
        try:
            completed = self._run_command(command, timeout=timeout, env_overrides=env_overrides)
            payload.update(
                {
                    "status": "ok",
                    "detail": success_detail,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                }
            )
        except subprocess.CalledProcessError as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": f"{action_name} failed",
                    "returncode": exc.returncode,
                    "stdout": (exc.stdout or "").strip(),
                    "stderr": (exc.stderr or "").strip(),
                }
            )
        except subprocess.TimeoutExpired as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": f"{action_name} timed out",
                    "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                }
            )
        except Exception as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": str(exc),
                    "stdout": "",
                    "stderr": "",
                }
            )
        self._latest_provider_action = payload
        payload["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload

    def _run_command(
        self,
        command: list[str],
        *,
        timeout: int,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = prepend_cuda_library_path(os.environ.copy())
        if env_overrides:
            env.update(env_overrides)
        run_kwargs: dict[str, Any] = {
            "cwd": settings.project_root,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": True,
            "env": env,
        }
        return subprocess.run(command, **run_kwargs)

    def _wait_for_qwen_tts_ready(self, profile_id: str | None, *, timeout_seconds: int) -> dict[str, Any]:
        profile = get_voice_profile(profile_id)
        values = profile.values if profile and isinstance(profile.values, dict) else {}
        endpoint = str(values.get("qwen_tts_endpoint", "")).strip()
        if not endpoint:
            return {"ok": False, "detail": "No Qwen TTS endpoint is configured for the selected profile."}
        base_url = endpoint.rsplit("/tts", 1)[0]
        deadline = time.time() + max(timeout_seconds, 1)
        last_error = "Qwen3-TTS readiness check did not reach the server."
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base_url + "/health", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if 200 <= response.status < 300 and payload.get("status") == "ok":
                    return {"ok": True, "detail": "ready"}
                last_error = f"Unexpected Qwen3-TTS health response: HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                last_error = f"Qwen3-TTS health check failed: HTTP {exc.code}"
            except Exception as exc:
                last_error = f"Qwen3-TTS health check failed: {exc}"
            time.sleep(1)
        return {"ok": False, "detail": last_error}

    def _sample_audio_path(self) -> Path:
        sample = settings.project_root / "test_audio" / "jfk.flac"
        if not sample.exists():
            raise FileNotFoundError(f"sample audio not found: {sample}")
        return sample

    def _python_module_command(self, module: str, *args: str) -> list[str]:
        return [self._process_manager.resolve_foreground_python(), "-m", module, *args]

    def _stop_all_tts_servers(self) -> dict[str, Any]:
        return {
            "qwen_tts": self._run_stop_tts_command("qwen-tts", "Qwen3-TTS"),
        }

    def _run_stop_tts_command(self, provider: str, label: str) -> dict[str, Any]:
        command = self._tts_script_command("stop", provider)
        started_at = time.perf_counter()
        payload: dict[str, Any] = {
            "provider": provider,
            "label": label,
            "command": command,
        }
        try:
            completed = self._run_command(command, timeout=60)
            payload.update(
                {
                    "ok": True,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                }
            )
        except subprocess.CalledProcessError as exc:
            payload.update(
                {
                    "ok": False,
                    "returncode": exc.returncode,
                    "stdout": (exc.stdout or "").strip(),
                    "stderr": (exc.stderr or "").strip(),
                    "detail": f"{label} stop failed",
                }
            )
        except subprocess.TimeoutExpired as exc:
            payload.update(
                {
                    "ok": False,
                    "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                    "detail": f"{label} stop timed out",
                }
            )
        except Exception as exc:
            payload.update(
                {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "detail": str(exc),
                }
            )
        payload["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload

    def _tts_script_command(self, action: str, provider: str | None = None) -> list[str]:
        scripts_dir = settings.project_root / "scripts"
        verb = "start" if action == "start" else "stop"
        if self._is_qwen_provider(provider or ""):
            script = scripts_dir / f"{verb}_qwen_tts_server.py"
            return [self._process_manager.resolve_foreground_python(), str(script)]
        raise ValueError(f"no managed TTS sidecar for provider: {provider}")
