"""Fast, authenticated stream admission and receipt lookup."""
import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ..brain.queue import BrainQueueClosed, BrainQueueFull
from ..config import settings
from ..stream.ingress import StreamIngress
from ..stream.ingress_store import IngressConflict, IngressFull
from ..stream.models import StreamInputEvent

router = APIRouter()
_runtime = None
_lock = threading.Lock()


def get_stream_ingress():
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = StreamIngress(settings.data_dir / 'runtime' / 'stream-ingress.sqlite3')
        return _runtime


@router.post('/v1/stream/jobs', status_code=202)
async def accept_stream_job(request: Request, synthesize_speech: bool = False, fast_mode: bool = True):
    from .routes import submit_stream_event
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise HTTPException(413, 'stream event exceeds ingress bounds')
        body.extend(chunk)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("unsupported ingress schema")
        event = StreamInputEvent.model_validate(payload)
        return await asyncio.to_thread(lambda: get_stream_ingress().submit(
            event, speech=synthesize_speech, fast=fast_mode,
            submit=lambda wrapper: submit_stream_event(event, synthesize_speech, fast_mode, wrapper=wrapper)))
    except IngressConflict as exc:
        raise HTTPException(409, 'event ID already used with different payload') from exc
    except (IngressFull, BrainQueueClosed, BrainQueueFull) as exc:
        raise HTTPException(503, 'stream admission unavailable', headers={'Retry-After': '1'}) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, 'invalid stream event') from exc


@router.get('/v1/stream/jobs/status')
def stream_job_status():
    runtime = get_stream_ingress()
    runtime.reconcile()
    return runtime.store.stats()


@router.get('/v1/stream/jobs/{job_id}')
def stream_job_receipt(job_id: str):
    receipt = get_stream_ingress().receipt(job_id)
    if receipt is None:
        raise HTTPException(404, 'unknown stream job')
    return receipt


@router.post('/v1/stream/jobs/{job_id}/cancel')
def cancel_stream_job(job_id: str):
    result = get_stream_ingress().cancel(job_id)
    if result['receipt'] is None:
        raise HTTPException(404, 'unknown stream job')
    return result
