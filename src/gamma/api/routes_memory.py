"""Memory and known-person routes."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import APIRouter, HTTPException

from . import deps
from ..errors import ConfigurationError, ExternalServiceError, GammaError
from ..schemas.memory import KnownPersonPayload, MemoryClearRequest, MemoryItemCreate, MemoryItemUpdate


router = APIRouter()

@router.get("/v1/memory/stats")
def memory_stats() -> dict[str, str | int]:
    try:
        return deps.get_conversation_service().memory_stats()
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GammaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/memory")
def memory_snapshot(limit: int = 100) -> dict:
    service = deps.get_memory_service()
    return {
        "stats": service.stats(),
        "known_people": service.get_known_people(limit=max(1, min(limit, 1000))),
        "recent_items": service.recent_items(limit=max(1, min(limit, 1000))),
    }


@router.post("/v1/memory/items")
def memory_item_create(payload: MemoryItemCreate) -> dict:
    try:
        item = deps.get_memory_service().create_item(payload.model_dump())
        return {"ok": True, "detail": "Memory created.", "item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/v1/memory/items/{kind}/{item_id}")
def memory_item_update(kind: str, item_id: int, payload: MemoryItemUpdate) -> dict:
    try:
        item = deps.get_memory_service().update_item(kind, item_id, payload.model_dump())
        return {"ok": True, "detail": "Memory updated.", "item": item}
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/v1/memory/clear")
def memory_clear(payload: MemoryClearRequest) -> dict:
    service = deps.get_memory_service()
    if payload.scope == "all":
        result = service.clear_all()
    elif payload.scope == "recent":
        result = service.clear_recent(minutes=payload.minutes)
    else:
        result = service.clear_selected([item.model_dump() for item in payload.selections])
    return {"ok": True, "detail": "Memory cleared.", **result}


@router.put("/v1/memory/people")
def memory_person_save(payload: KnownPersonPayload) -> dict:
    try:
        person = deps.get_memory_service().save_known_person(payload.model_dump())
        return {"ok": True, "detail": "Known person saved.", "person": person}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/v1/memory/people/{person_id}")
def memory_person_delete(person_id: int) -> dict:
    if not deps.get_memory_service().delete_known_person(person_id):
        raise HTTPException(status_code=404, detail="known person not found")
    return {"ok": True, "detail": "Known person deleted.", "id": person_id}
