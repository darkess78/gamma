from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from gamma.api import routes
from gamma.api import deps, routes_memory, routes_stream
from gamma.schemas.memory import ViewerTrustPayload


def test_missing_memory_person_maps_to_404() -> None:
    memory = Mock()
    memory.delete_known_person.return_value = False
    with patch.object(deps, "get_memory_service", return_value=memory):
        with pytest.raises(HTTPException) as caught:
            routes_memory.memory_person_delete(999)
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
    with patch.object(deps, "get_viewer_trust_store", return_value=store):
        listed = routes_stream.stream_viewer_trust()
        saved = routes_stream.stream_viewer_trust_save(
            ViewerTrustPayload(platform_user_id="u1", display_name="Viewer", trust_level="trusted")
        )
    assert listed["items"][0]["platform_user_id"] == "u1"
    assert saved["record"]["trust_level"] == "trusted"
