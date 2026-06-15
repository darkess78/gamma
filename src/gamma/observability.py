from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from fastapi import FastAPI, Request

from .config import settings


_CONTEXT_FIELDS = ("request_id", "trace_id", "turn_id", "event_id", "message_id", "session_id")
_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("gamma_log_context", default={})
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|oauth)\s+[A-Za-z0-9._~+/\-=]{8,}"),
    re.compile(r"(?i)([?&](?:access_token|token|secret|password)=)[^&\s]+"),
)
_REDACTED = "[REDACTED]"


def configure_logging(
    service: str,
    *,
    log_path: Path | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    stderr: bool | None = None,
) -> logging.Logger:
    """Configure a process/service logger with bounded structured JSON output."""
    logger = logging.getLogger(f"gamma.runtime.{service}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if getattr(logger, "_gamma_configured", False):
        return logger

    path = log_path or settings.data_dir / "runtime" / "logs" / f"{service}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes or int(getattr(settings, "log_max_bytes", 5 * 1024 * 1024)),
        backupCount=backup_count if backup_count is not None else int(getattr(settings, "log_backup_count", 5)),
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLogFormatter(service))
    logger.addHandler(file_handler)

    use_stderr = bool(getattr(settings, "log_stderr_enabled", True)) if stderr is None else stderr
    if use_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(HumanLogFormatter(service))
        logger.addHandler(stderr_handler)

    setattr(logger, "_gamma_configured", True)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    event_name: str,
    message: str,
    *,
    exc_info: bool | BaseException | tuple[Any, Any, Any] | None = None,
    **fields: Any,
) -> None:
    logger.log(
        level,
        message,
        extra={"event_name": event_name, "event_fields": fields},
        exc_info=exc_info,
    )


def bind_context(**fields: Any) -> contextvars.Token[dict[str, str]]:
    context = dict(_context.get())
    for key in _CONTEXT_FIELDS:
        value = fields.get(key)
        if value is not None and str(value).strip():
            context[key] = str(value)
    return _context.set(context)


def reset_context(token: contextvars.Token[dict[str, str]]) -> None:
    _context.reset(token)


def current_context() -> dict[str, str]:
    return dict(_context.get())


def current_request_id() -> str | None:
    return _context.get().get("request_id")


def install_request_logging(app: FastAPI, *, service: str, logger: logging.Logger | None = None) -> None:
    request_logger = logger or configure_logging(service)

    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        request_id = _request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        token = bind_context(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            log_event(
                request_logger,
                logging.ERROR,
                "http.request.exception",
                "Unhandled HTTP request failure.",
                exc_info=True,
                method=request.method,
                path=_normalized_route(request),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                error_class=type(exc).__name__,
                detail=str(exc),
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            level = logging.WARNING if response.status_code >= 400 else logging.INFO
            log_event(
                request_logger,
                level,
                "http.request.completed",
                "HTTP request completed.",
                method=request.method,
                path=_normalized_route(request),
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            return response
        finally:
            reset_context(token)


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "event": getattr(record, "event_name", record.name),
            "message": record.getMessage(),
            **current_context(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, Mapping):
            payload.update(fields)
        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            payload.setdefault("error_class", exc_type.__name__ if exc_type else "Exception")
            payload.setdefault("detail", str(exc_value or ""))
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=True, separators=(",", ":"))


class HumanLogFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        event_name = getattr(record, "event_name", record.name)
        fields = redact(getattr(record, "event_fields", {}))
        suffix = f" {json.dumps(fields, ensure_ascii=True, sort_keys=True)}" if fields else ""
        message = f"{record.levelname} {self.service} {event_name}: {redact(record.getMessage())}{suffix}"
        if record.exc_info:
            message = f"{message}\n{redact(self.formatException(record.exc_info))}"
        return message


def redact(value: Any, *, key: str | None = None) -> Any:
    if key and _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return _REDACTED
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        result = value
        for pattern in _SECRET_PATTERNS:
            replacement = r"\1 [REDACTED]" if pattern.groups else _REDACTED
            result = pattern.sub(replacement, result)
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in {"authorization", "cookie", "password", "secret", "token"}:
        return True
    if normalized.endswith(("_token", "_password", "_secret", "_cookie")):
        return True
    return any(part in normalized for part in ("raw_audio", "audio_bytes", "audio_content"))


def _request_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    if normalized and len(normalized) <= 128 and all(character.isalnum() or character in "._:-" for character in normalized):
        return normalized
    return uuid4().hex


def _normalized_route(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return str(route_path or request.url.path)
