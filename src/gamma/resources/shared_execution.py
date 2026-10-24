"""Provider-boundary admission shared by every checkout for this OS account.

Waiting deadlines never revoke execution. An uncertain remote request is never
retried by this layer, and owner death alone cannot free a remote allocation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import socket
import tomllib
from urllib.parse import urlsplit
from uuid import uuid4

from .shared_ledger import Ledger
from ..errors import ExternalServiceError

_priority = ContextVar("model_work_priority", default="interactive")
_request_id = ContextVar("model_work_request_id", default=None)


class AdmissionUncertain(ExternalServiceError):
    """Provider completion is unknown. Do not fall back or retry this work."""


def is_uncertain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, AdmissionUncertain) or getattr(exc, "admission_uncertain", False) is True:
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def is_busy(exc):
    from .admission import AdmissionBusy
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, AdmissionBusy):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def shared_path():
    # data_dir/project_root must never select an independent production authority.
    return Path(os.environ.get("GAMMA_ADMISSION_DB", str(Path.home() / ".local/state/gamma/admission.sqlite3"))).expanduser().resolve()


def endpoint_key(endpoint):
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("invalid admission endpoint")
    host = parts.hostname.lower().rstrip(".")
    local_hosts = {"localhost", "127.0.0.1", "::1", socket.gethostname().lower()}
    try:
        import psutil
        local_hosts.update(address.address.split("%")[0] for addresses in psutil.net_if_addrs().values()
                           for address in addresses if address.family in {socket.AF_INET, socket.AF_INET6})
    except (ImportError, OSError):
        pass
    if host in local_hosts:
        host = "loopback"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # Paths identify operations, not independent provider capacity.
    return "endpoint:" + hashlib.sha256(f"{parts.scheme}://{host}:{port}".encode()).hexdigest()[:24]


def priority_for(context=None):
    if _priority.get() == "background":
        return "background"
    mode = getattr(context, "interaction_mode", "") or ""
    purpose = getattr(context, "purpose", "") or ""
    if mode in {"improvement", "evaluation", "silent", "background"} or purpose.startswith(("improvement", "silent", "adviser", "advisor", "background")):
        return "background"
    return "interactive"


@contextmanager
def work_priority(priority):
    if priority not in {"interactive", "background"}:
        raise ValueError("invalid work priority")
    token = _priority.set(priority)
    try:
        yield
    finally:
        _priority.reset(token)


@contextmanager
def work_request(identity):
    if not isinstance(identity, str) or not identity or len(identity) > 128:
        raise ValueError("invalid model work identity")
    token = _request_id.set(identity)
    try:
        yield
    finally:
        _request_id.reset(token)


def request_identity(operation, context=None, payload=b""):
    identity = _request_id.get() or getattr(context, "call_id", None)
    if not identity:
        return None
    return hashlib.sha256((str(identity) + ":" + operation).encode() + payload).hexdigest()


def policy():
    from ..config import settings
    path = Path(os.environ.get("GAMMA_ADMISSION_POLICY", str(settings.project_root / "config/admission.toml")))
    if not path.exists():
        return {"budgets": {"model-work": {"capacity": 1, "reserved": 1}}, "workloads": {}, "endpoints": {}, "wait_seconds": 2.0}
    with path.open("rb") as handle:
        result = tomllib.load(handle)
    result.setdefault("workloads", {})
    result.setdefault("endpoints", {})
    result.setdefault("wait_seconds", 2.0)
    return result


def authority(endpoint=None, workload=None):
    config = policy()
    limits = {name: (item["capacity"], item.get("reserved", 0)) for name, item in config["budgets"].items()}
    demands = {"model-work": 1}
    demands.update(config.get("workloads", {}).get(workload or "", {}).get("demands", {}))
    if demands.get("model-work") != 1:
        raise ValueError("model-work demand must be one")
    if endpoint:
        key = endpoint_key(endpoint)
        specific = config.get("endpoints", {}).get(key, {})
        limits[key] = (specific.get("capacity", 1), specific.get("reserved", 0))
        demands[key] = 1
    ledger = Ledger(shared_path(), limits)
    ledger.bind_policy(hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
    return ledger, demands, config.get("wait_seconds", 2.0)


def _evidence_path(token):
    return shared_path().parent / "admission-evidence" / (token + ".json")


def _write_evidence(token, payload):
    path = _evidence_path(token)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix("." + uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def execution(endpoint=None, *, workload="model", context=None, local=False, request_id=None):
    ledger, demands, wait = authority(endpoint, workload)
    token = ledger.acquire(demands, priority=priority_for(context), timeout=wait,
                           request_id=request_id, execution_kind="local" if local else "remote", workload=workload)
    try:
        # Persist before dispatch. A missing record is not completion evidence.
        _write_evidence(token, {"endpoint": endpoint, "provider_process": None})
    except BaseException:
        ledger.release(token)
        raise
    try:
        yield token
    except BaseException as exc:
        if local:
            # Only use local=True for synchronous, in-process compute, with no
            # detached subprocess or provider execution behind it.
            ledger.release(token)
            raise
        from urllib.error import HTTPError
        if isinstance(exc, HTTPError) and exc.code in {400, 401, 403, 404, 413, 422, 429}:
            ledger.release(token)
            raise
        ledger.uncertain(token)
        raise AdmissionUncertain("provider completion unresolved; capacity retained") from exc
    else:
        ledger.release(token)


def admitted(workload, *, endpoint=None, local=False):
    """Wrap the lowest synchronous compute boundary, including lazy model load."""
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            url = endpoint(*args, **kwargs) if callable(endpoint) else endpoint
            # Hash changed prompt content for a legitimate compacted retry;
            # persist only the digest, never prompts or audio paths.
            payload = b""
            if workload == "model":
                payload = json.dumps([list(arg for arg in args[1:] if isinstance(arg, str)),
                    {k: v for k, v in kwargs.items() if k in {"system_prompt", "user_text", "model_override"}}],
                    sort_keys=True).encode()
            with execution(url, workload=workload, context=kwargs.get("call_context"), local=local,
                           request_id=request_identity(function.__qualname__, kwargs.get("call_context"), payload)):
                return function(*args, **kwargs)
        return invoke
    return decorate


@contextmanager
def admitted_urlopen(opener, request, *, timeout, workload="model", context=None):
    with execution(request.full_url, workload=workload, context=context,
                   request_id=request_identity(workload + ":" + endpoint_key(request.full_url), context, request.data or b"")) as token:
        # Qwen's durable provider receipt uses this admission identity.
        if workload == "tts":
            job_id = request.get_header("X-shana-job-id") or token
            request.add_header("X-Shana-Job-ID", job_id)
            record = json.loads(_evidence_path(token).read_text(encoding="utf-8"))
            record["provider_job_id"] = job_id
            _write_evidence(token, record)
        with opener(request, timeout=timeout) as response:
            yield response


def _process_group_exited(record):
    # A dead direct parent is insufficient if its model descendants survive.
    # Only our own newly created POSIX session can provide group evidence.
    group = record.get("process_group")
    if os.name != "posix" or type(group) is not int or group <= 0 or record.get("host") != socket.gethostname():
        return False
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def reconcile():
    """No replay. Only exact dead-process or provider terminal-receipt evidence."""
    import httpx
    from .shared_ledger import process_identity
    ledger, _, _ = authority()
    before = ledger.snapshot()
    released = []
    for row in before["allocations"]:
        token = row["token"]
        if token in released:
            continue
        if row["execution_kind"] == "local":
            if ledger.reconcile_local_dead(token):
                released.append(token)
            continue
        # A live submitter might still dispatch. Do not clear its allocation.
        identity = process_identity(row["owner_pid"])
        if row["state"] != "uncertain" and (row["owner_identity"] == "unknown" or identity == row["owner_identity"] or identity == "unknown"):
            continue
        ledger.uncertain(token)
        try:
            record = json.loads(_evidence_path(token).read_text(encoding="utf-8"))
            provider = record.get("provider_process")
            if provider and provider["identity"] not in {None, "unknown"}:
                current = process_identity(provider["pid"])
                if current != "unknown" and current != provider["identity"] and _process_group_exited(record):
                    ledger.release(token)
                    released.append(token)
                    continue
            endpoint = record.get("endpoint") or ""
            if urlsplit(endpoint).path.rstrip("/").endswith("/tts"):
                from ..voice.tts_reconcile import remote_completed
                if remote_completed({"endpoint": endpoint}, record.get("provider_job_id", token)):
                    ledger.release(token)
                    released.append(token)
        except (OSError, ValueError, KeyError, httpx.HTTPError):
            continue
    return {"released": released, **ledger.snapshot()}


def status():
    ledger, _, _ = authority()
    return ledger.snapshot()


def admitted_subprocess_run(command, *, workload, input=None, capture_output=False,
                            check=False, timeout=None, **kwargs):
    """Track a direct model subprocess; no shell and no automatic replay."""
    import subprocess
    from .process_identity import process_identity
    failure = None
    result = None
    with execution(workload=workload) as token:
        if input is not None:
            kwargs['stdin'] = subprocess.PIPE
        if capture_output:
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
        if os.name == "posix":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            failure = exc
        else:
            record = {"endpoint": None, "host": socket.gethostname(),
                "process_group": process.pid if os.name == "posix" else None,
                "provider_process": {"pid": process.pid, "identity": process_identity(process.pid)}}
            _write_evidence(token, record)
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate()
                failure = exc
            else:
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            if os.name == "posix" and not _process_group_exited(record):
                raise RuntimeError("model subprocess descendants may still be running")
    if failure is not None:
        raise failure
    if check:
        result.check_returncode()
    return result
