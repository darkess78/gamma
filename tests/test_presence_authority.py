import json

import pytest

from gamma.config import settings
from gamma.memory.continuity import ContinuityService
from gamma.memory.state_authority import StateConflict
from gamma.performer.bus import PerformerEventBus
from gamma.presence import load_presence_state, save_presence_state, presence_state_for_mode


def begin(owner):
    return owner.begin_exchange(session_id="fixture", text="fixture", speaker_name="fixture",
                                input_source="test", privacy_scope="local_private")


def complete(owner, exchange):
    owner.complete_exchange(exchange_id=exchange, session_id="fixture", assistant_text="fixture",
                            trace_id=exchange, output_target="internal", privacy_scope="local_private")


def test_presence_telemetry_preserves_decision_operator_controls_invalidate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    save_presence_state(presence_state_for_mode("wake"))
    owner = ContinuityService(database_url=settings.database_url)
    pending = begin(owner)
    state = load_presence_state()
    state["autonomy"]["scheduler"]["reason"] = "fixture telemetry"
    save_presence_state(state)
    complete(owner, pending)
    pending = begin(owner)
    state = load_presence_state()
    state["outputs"]["voice"] = False
    save_presence_state(state)
    with pytest.raises(StateConflict):
        complete(owner, pending)


def test_presence_legacy_json_is_preserved(tmp_path):
    path = tmp_path / "state.json"
    source = json.dumps(presence_state_for_mode("wake"))
    path.write_text(source)
    state = load_presence_state(path=path)
    state["activity"]["current_turn_id"] = "fixture"
    save_presence_state(state, path=path)
    assert path.read_text() == source
    assert load_presence_state(path=path)["activity"]["current_turn_id"] == "fixture"


def test_stale_bus_merges_only_its_requested_target(tmp_path):
    path = tmp_path / "performer.json"
    source = json.dumps({"muted_targets": []})
    path.write_text(source)
    first = PerformerEventBus(state_path=path)
    second = PerformerEventBus(state_path=path)
    first.set_target_muted("stream_public", True)
    second.set_target_muted("dashboard_monitor", True)
    assert PerformerEventBus(state_path=path).stats()["muted_targets"] == ["dashboard_monitor", "stream_public"]
    first.set_target_muted("stream_public", False)
    assert PerformerEventBus(state_path=path).stats()["muted_targets"] == ["dashboard_monitor"]
    assert path.read_text() == source


def test_persistent_mute_invalidates_owned_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    owner = ContinuityService(database_url=settings.database_url)
    pending = begin(owner)
    bus = PerformerEventBus(state_path=tmp_path / "runtime" / "performer" / "state.json")
    bus.set_target_muted("stream_public", True)
    with pytest.raises(StateConflict):
        complete(owner, pending)


def test_late_interaction_cannot_overwrite_newer_presence(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    owner = ContinuityService()
    old = begin(owner)
    complete(owner, old)
    newer = begin(owner)
    complete(owner, newer)
    current = load_presence_state()
    current["activity"]["session_id"] = "newer"
    save_presence_state(current, decision_id=newer, generation_id=owner.generation_id)
    late = load_presence_state()
    late["activity"]["session_id"] = "older"
    with pytest.raises(StateConflict):
        save_presence_state(late, decision_id=old, generation_id=owner.generation_id)
    assert load_presence_state()["activity"]["session_id"] == "newer"
