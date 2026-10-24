"""Release only leases with positive evidence that all inference has exited."""
import math
import re
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx
import psutil

from .tts_receipts import load_receipt


def worker_exited(record):
    if record.get("host") != socket.gethostname():
        return False
    pid, birth = record.get("pid"), record.get("birth")
    if type(pid) is not int or pid <= 0 or type(birth) not in {int, float} or not math.isfinite(birth) or birth <= 0:
        return False
    try:
        return psutil.Process(pid).create_time() != birth
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def remote_completed(record, job_id):
    endpoint = record.get("endpoint")
    if not isinstance(endpoint, str):
        return False
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.path.rstrip("/").endswith("/tts"):
        return False
    path = parts.path.rstrip("/")[:-4] + "/jobs/" + job_id
    url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                return False
            data = bytearray()
            for chunk in response.iter_bytes(8193):
                data.extend(chunk)
                if len(data) > 8192:
                    return False
    import json
    result = json.loads(data)
    return (isinstance(result, dict) and type(result.get("schema_version")) is int
            and result["schema_version"] == 1 and result.get("job_id") == job_id
            and isinstance(result.get("generation"), str) and re.fullmatch(r"[a-f0-9]{32}", result["generation"]) is not None
            and result.get("state") in {"completed", "failed"})


def reconcile(admission, receipt_root):
    lease = admission.status("tts")
    if lease is None:
        return True
    token = lease["token"]
    try:
        record = load_receipt(receipt_root, token)
        if type(record.get("schema_version")) is not int or record["schema_version"] != 1 or not worker_exited(record):
            return False
        complete = record.get("phase") == "completed"
        if not complete and record.get("phase") == "started":
            complete = (record.get("rvc") is False and record.get("provider") in {"qwen", "qwen-tts", "qwen_tts", "qwentts"}
                        and remote_completed(record, token))
        if not complete:
            return False
        admission.release_many(admission.resources_for(token), token)
        return True
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        return False
