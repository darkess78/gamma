import sqlite3

import pytest

from gamma.delivery.authority import DeliveryAuthority, DeliveryDenied, policy_revision
from gamma.integrations.twitch.trust import ViewerTrustStore
from gamma.memory.continuity import ContinuityService
from gamma.memory.state_authority import StateConflict
from gamma.schemas.response import AssistantResponse
from state_fixtures import commit_response


def test_trust_change_invalidates_pending_decision_and_issued_grant(tmp_path):
    url = f"sqlite:///{tmp_path / 'state.db'}"
    continuity = ContinuityService(database_url=url)
    trust = ViewerTrustStore(database_url=url)
    delivery = DeliveryAuthority(database_url=url)
    response = commit_response(delivery, AssistantResponse(spoken_text="Previously allowed"))
    context = dict(target="stream_public", audience="public", policy=policy_revision())
    grant = delivery.issue(response, **context)
    exchange = continuity.begin_exchange(session_id="pending", text="hello", speaker_name="viewer",
        input_source="twitch", privacy_scope="local_generic")
    trust.upsert(platform="twitch", platform_user_id="viewer", trust_level="blocked")
    with pytest.raises(StateConflict):
        continuity.complete_exchange(exchange_id=exchange, session_id="pending", assistant_text="stale",
            trace_id=exchange, output_target="stream_public", privacy_scope="local_generic")
    with pytest.raises(DeliveryDenied):
        delivery.check(grant, response, **context)
    assert trust.get(platform="twitch", platform_user_id="viewer").trust_level == "blocked"


def test_legacy_trust_rows_are_backed_up_and_preserved(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE stream_viewer_trust (
            platform TEXT NOT NULL, platform_user_id TEXT NOT NULL, display_name TEXT,
            trust_level TEXT NOT NULL DEFAULT 'normal', notes TEXT, pronunciation_alias TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (platform, platform_user_id))""")
        conn.execute("INSERT INTO stream_viewer_trust VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("twitch", "known", "Known viewer", "trusted", "keep", "Alias", "original", "original"))
    trust = ViewerTrustStore(database_url=f"sqlite:///{path}")
    backup = trust._authority.backup_path
    assert backup is not None and backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT trust_level, created_at FROM stream_viewer_trust").fetchone() == ("trusted", "original")
    row = trust.get(platform="twitch", platform_user_id="known")
    assert row.notes == "keep" and row.pronunciation_alias == "Alias"
    updated = trust.upsert(platform="twitch", platform_user_id="known", trust_level="regular")
    assert updated.created_at == "original"
    with sqlite3.connect(path) as conn:
        assert "ix_stream_viewer_trust_trust_level" in {row[1] for row in conn.execute("PRAGMA index_list(stream_viewer_trust)")}


def test_failed_trust_write_rolls_back_policy_invalidation(tmp_path, monkeypatch):
    import gamma.integrations.twitch.trust as module
    url = f"sqlite:///{tmp_path / 'state.db'}"
    continuity = ContinuityService(database_url=url)
    trust = ViewerTrustStore(database_url=url)
    exchange = continuity.begin_exchange(session_id="pending", text="hello", speaker_name="viewer",
        input_source="twitch", privacy_scope="local_generic")
    monkeypatch.setattr(module, "_record_from_row", lambda row: (_ for _ in ()).throw(RuntimeError("readback failed")))
    with pytest.raises(RuntimeError):
        trust.upsert(platform="twitch", platform_user_id="viewer", trust_level="blocked")
    assert trust.get(platform="twitch", platform_user_id="viewer") is None
    continuity.complete_exchange(exchange_id=exchange, session_id="pending", assistant_text="valid",
        trace_id=exchange, output_target="internal", privacy_scope="local_generic")
