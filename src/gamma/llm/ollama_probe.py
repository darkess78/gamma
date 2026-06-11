from __future__ import annotations

import json
from urllib import error, request


def fetch_ollama_model_details(*, endpoint: str, model: str, timeout_seconds: int) -> dict:
    body = json.dumps({"model": model}).encode("utf-8")
    req = request.Request(
        endpoint.rstrip("/") + "/api/show",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def probe_ollama_model_capabilities(*, endpoint: str, model: str, timeout_seconds: int) -> dict:
    try:
        payload = fetch_ollama_model_details(endpoint=endpoint, model=model, timeout_seconds=timeout_seconds)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "detail": f"http-{exc.code}: {detail}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}

    capabilities = payload.get("capabilities", [])
    if not isinstance(capabilities, list):
        capabilities = []
    normalized = [str(item).strip().lower() for item in capabilities if str(item).strip()]
    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}
    return {
        "ok": True,
        "model": model,
        "capabilities": normalized,
        "supports_vision": "vision" in normalized,
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
    }
