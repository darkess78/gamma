from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from gamma.config import settings
from gamma.supervisor.manager import ProcessManager
from gamma.voice.voice_profiles import resolve_tts_config


def _print_step(message: str) -> None:
    print(f"[shana-start] {message}", flush=True)


def _is_port_open(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, *, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_port_open(host, port, timeout=1.0):
            return True
        time.sleep(1)
    return False


def _probe_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return bind_host


def _spawn_detached(command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> None:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
        kwargs: dict[str, object] = {
            "cwd": cwd,
            "stdout": stdout_handle,
            "stderr": stderr_handle,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)


def _start_ollama_if_needed() -> dict[str, object]:
    provider = settings.llm_provider.strip().lower()
    if provider not in {"local", "ollama"}:
        return {"ok": True, "detail": f"LLM provider {provider} does not need Ollama startup."}

    endpoint = urlparse(settings.local_llm_endpoint)
    host = endpoint.hostname or "127.0.0.1"
    port = endpoint.port or 11434
    if host not in {"127.0.0.1", "localhost"}:
        return {"ok": True, "detail": f"LLM endpoint is remote: {settings.local_llm_endpoint}"}
    if _is_port_open(host, port):
        return {"ok": True, "detail": f"Ollama is already listening on {host}:{port}."}

    ollama = shutil.which("ollama")
    if not ollama:
        return {
            "ok": False,
            "detail": "Ollama is configured but the ollama command was not found on PATH.",
        }

    runtime_dir = settings.data_dir / "runtime"
    _print_step("Starting Ollama for local LLM.")
    _spawn_detached(
        [ollama, "serve"],
        cwd=settings.project_root,
        stdout_path=runtime_dir / "ollama.stdout.log",
        stderr_path=runtime_dir / "ollama.stderr.log",
    )
    if not _wait_for_port(host, port, timeout_seconds=30):
        return {"ok": False, "detail": f"Ollama did not start listening on {host}:{port} within 30 seconds."}
    return {"ok": True, "detail": f"Ollama is listening on {host}:{port}."}


def _start_tts_sidecar_if_needed(manager: ProcessManager) -> dict[str, object]:
    tts_cfg = resolve_tts_config()
    provider = tts_cfg.provider.strip().lower()
    if provider not in {"local", "gpt-sovits", "gpt_sovits", "gptsovits", "qwen-tts", "qwen_tts", "qwen", "qwentts"}:
        return {"ok": True, "detail": f"TTS provider {provider} runs without a managed sidecar."}

    endpoint = tts_cfg.qwen_tts_endpoint if provider in {"qwen-tts", "qwen_tts", "qwen", "qwentts"} else tts_cfg.gpt_sovits_endpoint
    if not endpoint:
        return {"ok": False, "detail": f"TTS provider {provider} has no endpoint configured."}

    parsed = urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        return {"ok": False, "detail": f"TTS endpoint has no port: {endpoint}"}
    if host not in {"127.0.0.1", "localhost"}:
        return {"ok": True, "detail": f"TTS endpoint is remote and will not be managed: {endpoint}"}
    if _is_port_open(host, port):
        return {"ok": True, "detail": f"TTS sidecar is already listening on {host}:{port}."}

    _print_step(f"Starting {provider} TTS sidecar.")
    script = manager._tts_script("start", provider=provider)
    try:
        completed = subprocess.run(
            script,
            cwd=settings.project_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "detail": (exc.stderr or exc.stdout or str(exc)).strip()}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "detail": f"TTS sidecar start timed out: {exc}"}

    if not _wait_for_port(host, port, timeout_seconds=15):
        return {"ok": False, "detail": f"TTS sidecar did not listen on {host}:{port} after startup."}
    return {"ok": True, "detail": completed.stdout.strip() or f"TTS sidecar is listening on {host}:{port}."}


def _probe_shana() -> dict[str, object]:
    url = f"http://{_probe_host(settings.shana_bind_host)}:{settings.shana_port}/health"
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": response.status < 500, "detail": f"API reachable: {payload}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": str(exc)}


def _wait_for_shana(timeout_seconds: int = 45) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] = {"ok": False, "detail": "not checked"}
    while time.monotonic() < deadline:
        last = _probe_shana()
        if last.get("ok"):
            return last
        time.sleep(1)
    return last


def _probe_dashboard() -> dict[str, object]:
    url = f"http://{_probe_host(settings.dashboard_bind_host)}:{settings.dashboard_port}/health"
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": response.status < 500, "detail": f"Dashboard reachable: {payload}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": str(exc)}


def _wait_for_dashboard(timeout_seconds: int = 30) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] = {"ok": False, "detail": "not checked"}
    while time.monotonic() < deadline:
        last = _probe_dashboard()
        if last.get("ok"):
            return last
        time.sleep(1)
    return last


def start_voice_stack() -> dict[str, object]:
    manager = ProcessManager()

    _print_step("Starting dashboard.")
    dashboard = manager.start("dashboard")
    dashboard_health = _wait_for_dashboard()

    _print_step("Checking local LLM dependency.")
    llm = _start_ollama_if_needed()

    _print_step("Checking TTS sidecar dependency.")
    tts = _start_tts_sidecar_if_needed(manager)

    _print_step("Starting Shana API. STT loads in-process with Shana.")
    shana = manager.start("shana")
    health = _wait_for_shana()

    return {
        "ok": (
            bool(dashboard.get("ok"))
            and bool(dashboard_health.get("ok"))
            and bool(shana.get("ok"))
            and bool(health.get("ok"))
            and bool(llm.get("ok"))
            and bool(tts.get("ok"))
        ),
        "llm": llm,
        "tts": tts,
        "stt": {
            "ok": True,
            "detail": f"STT provider {settings.stt_provider} is in-process and starts with Shana.",
        },
        "dashboard": dashboard,
        "dashboard_health": dashboard_health,
        "shana": shana,
        "health": health,
        "urls": {
            "shana": settings.shana_base_url,
            "dashboard": settings.dashboard_base_url,
        },
    }


def main() -> int:
    result = start_voice_stack()
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
