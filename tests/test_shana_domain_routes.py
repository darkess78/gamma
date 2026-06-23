from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from gamma.api import routes
from gamma.schemas.memory import KnownPersonPayload, MemoryClearRequest, MemoryItemCreate, MemoryItemUpdate, ViewerTrustPayload


def test_memory_snapshot_and_mutations_delegate_to_shana_stores() -> None:
    memory = Mock()
    memory.stats.return_value = {"profile_count": 1}
    memory.get_known_people.return_value = [{"id": 2}]
    memory.recent_items.return_value = [{"kind": "profile_fact", "id": 3}]
    memory.create_item.return_value = {"id": 4}
    memory.update_item.return_value = {"id": 4, "summary": "updated"}
    memory.clear_recent.return_value = {"cleared_total": 2, "minutes": 15}
    memory.save_known_person.return_value = {"id": 5, "name": "Friend"}
    memory.delete_known_person.return_value = True

    with patch.object(routes, "get_memory_service", return_value=memory):
        snapshot = routes.memory_snapshot(limit=25)
        created = routes.memory_item_create(MemoryItemCreate(kind="profile_fact", summary="fact"))
        updated = routes.memory_item_update("profile_fact", 4, MemoryItemUpdate(summary="updated"))
        cleared = routes.memory_clear(MemoryClearRequest(scope="recent", minutes=15))
        person = routes.memory_person_save(KnownPersonPayload(name="Friend"))
        deleted = routes.memory_person_delete(5)

    assert snapshot["stats"]["profile_count"] == 1
    assert created["item"]["id"] == 4
    assert updated["item"]["summary"] == "updated"
    assert cleared["cleared_total"] == 2
    assert person["person"]["name"] == "Friend"
    assert deleted["id"] == 5


def test_missing_memory_person_maps_to_404() -> None:
    memory = Mock()
    memory.delete_known_person.return_value = False
    with patch.object(routes, "get_memory_service", return_value=memory):
        with pytest.raises(HTTPException) as caught:
            routes.memory_person_delete(999)
    assert caught.value.status_code == 404


def test_viewer_trust_routes_are_shana_owned() -> None:
    store = Mock()
    record = SimpleNamespace(
        platform="twitch",
        platform_user_id="u1",
        display_name="Viewer",
        trust_level="trusted",
        notes=None,
        pronunciation_alias=None,
        created_at="now",
        updated_at="now",
    )
    store.list_records.return_value = [record]
    store.upsert.return_value = record
    with patch.object(routes, "get_viewer_trust_store", return_value=store):
        listed = routes.stream_viewer_trust()
        saved = routes.stream_viewer_trust_save(
            ViewerTrustPayload(platform_user_id="u1", display_name="Viewer", trust_level="trusted")
        )
    assert listed["items"][0]["platform_user_id"] == "u1"
    assert saved["record"]["trust_level"] == "trusted"
