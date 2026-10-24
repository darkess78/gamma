import json
import math
from urllib import request
from ..resources.shared_execution import admitted_urlopen


def generate_review(system_prompt, user_text, model, temperature, endpoint, timeout):
    if isinstance(temperature, bool) or type(temperature) not in {int, float}:
        raise RuntimeError('review provider unavailable') from None
    if not math.isfinite(temperature) or not (0 <= temperature <= 1):
        raise RuntimeError('review provider unavailable') from None
    payload = json.dumps({"model": model, "system": system_prompt, "prompt": user_text, "stream": False, "options": {"temperature": temperature}}).encode("utf-8")
    try:
        req = request.Request(endpoint.rstrip("/") + "/api/generate", data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with admitted_urlopen(request.urlopen, req, timeout=min(timeout, 20), workload="safety") as resp:
            raw = resp.read(65537)
    except Exception:
        raise RuntimeError('review provider unavailable') from None
    if len(raw) > 65536:
        raise RuntimeError('review provider unavailable') from None
    try:
        data = json.loads(raw.decode("utf-8"))
        if type(data) is not dict or type(data.get("response")) is not str:
            raise RuntimeError('review provider unavailable') from None
        result = data["response"]
        if len(result.encode("utf-8")) > 16384:
            raise RuntimeError('review provider unavailable') from None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError('review provider unavailable') from None
    return result
