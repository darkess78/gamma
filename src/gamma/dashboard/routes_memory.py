"""Memory and known-person routes, proxied to Shana."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import deps

router = APIRouter()

@router.post("/api/memory/clear")
def clear_memory() -> dict:
    """Clear all memory contents.
    
    Returns:
        dict: Confirmation response.
    """
    return deps.get_dashboard_service().clear_memory()


@router.post("/api/memory/clear-recent")
async def clear_recent_memory(request: Request) -> dict:
    """Clear recent memory items.
    
    Args:
        request: HTTP request with JSON payload containing minutes to clear.
    
    Returns:
        dict: Confirmation of how many items were cleared.
    """
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    minutes = int(payload.get("minutes", 10) or 10) if isinstance(payload, dict) else 10
    return deps.get_dashboard_service().clear_recent_memory(minutes=minutes)


@router.post("/api/memory/clear-selected")
async def clear_selected_memory(request: Request) -> dict:
    """Clear selected memory items.
    
    Args:
        request: HTTP request with JSON payload containing selections.
    
    Returns:
        dict: Confirmation of how many items were cleared.
    """
    payload = await request.json()
    selections = payload.get("items", []) if isinstance(payload, dict) else []
    return deps.get_dashboard_service().clear_selected_memory(selections if isinstance(selections, list) else [])


@router.post("/api/memory/item")
async def update_memory_item(request: Request) -> dict:
    """Update a memory item.
    
    Args:
        request: HTTP request with JSON payload containing item data.
    
    Returns:
        dict: Updated memory item data.
        Raises:
            HTTPException: 400 if payload invalid or item not found.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().update_memory_item(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/memory/item/create")
async def create_memory_item(request: Request) -> dict:
    """Create a new memory item.
    
    Args:
        request: HTTP request with JSON payload containing item data.
    
    Returns:
        dict: Created memory item data.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().create_memory_item(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/memory/people")
async def save_known_person(request: Request) -> dict:
    """Save a known person.
    
    Args:
        request: HTTP request with JSON payload containing person data.
    
    Returns:
        dict: Confirmed known person data.
        Raises:
            HTTPException: 400 if payload invalid.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="object payload is required")
    try:
        return deps.get_dashboard_service().save_known_person(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/memory/people/{person_id}")
def delete_known_person(person_id: int) -> dict:
    """Delete a known person.
    
    Args:
        person_id: ID of known person to delete.
    
    Returns:
        dict: Confirmation response.
        Raises:
            HTTPException: 404 if person not found.
    """
    try:
        return deps.get_dashboard_service().delete_known_person(person_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
