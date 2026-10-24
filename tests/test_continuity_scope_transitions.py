from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from gamma.memory.continuity import ContinuityService
from gamma.config import settings


DATABASE_URL = "sqlite:///{path}"


@pytest.fixture()
def service():
    with TemporaryDirectory() as temp_dir:
        url = DATABASE_URL.format(path=Path(temp_dir) / "scope.db")
        yield ContinuityService(database_url=url)


@pytest.mark.usefixtures("service")
def test_private_to_public_snapshot_and_context_scope_isolation(
    service: ContinuityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "conversation_summary_interval_exchanges",
        5,
        raising=False,
    )

    private_sentinel = "PRIVATE_SENTINEL_SECRET_OBJECTIVE"

    exchange_id = service.begin_exchange(
        session_id="session-p2p",
        text="PRIVATE_SUMMARY_SEED Remember private seed objective and relationship signals",
        speaker_name="Owner",
        input_source="local",
        privacy_scope="local_private",
    )
    service.complete_exchange(
        exchange_id=exchange_id,
        session_id="session-p2p",
        assistant_text="Private acknowledgement.",
        trace_id="trace-p2p",
        output_target="dashboard_monitor",
        privacy_scope="local_private",
        internal_summary="Private seed objective and relationship signals.",
        state_updates={
            "current_objective": private_sentinel,
            "relationship_signals": [private_sentinel],
        },
    )

    private_snapshot = service.snapshot(
        "session-p2p",
        privacy_scope="local_private",
    )

    public_exchange_id = service.begin_exchange(
        session_id="session-p2p",
        text="Public follow-up without private details",
        speaker_name="Owner",
        input_source="local",
        privacy_scope="public",
    )
    service.complete_exchange(
        exchange_id=public_exchange_id,
        session_id="session-p2p",
        assistant_text="Public follow-up acknowledged.",
        trace_id="trace-p2p-public",
        output_target="dashboard_monitor",
        privacy_scope="public",
    )

    public_context = service.prompt_context(
        "session-p2p",
        privacy_scopes={"public"},
    )
    public_snapshot = service.snapshot(
        "session-p2p",
        privacy_scope="public",
    )
    private_snapshot_after = service.snapshot(
        "session-p2p",
        privacy_scope="local_private",
    )

    assert private_sentinel not in public_context
    assert "PRIVATE_SUMMARY_SEED" not in public_context
    assert public_snapshot["summary"]["completed_exchanges"] == 1
    assert public_snapshot["working_state"]["active_topic"] == "Public follow-up without private details"
    assert public_snapshot["working_state"]["current_objective"] == ""
    assert public_snapshot["working_state"]["relevant_people"] == []

    assert private_sentinel in private_snapshot["working_state"]["current_objective"]
    assert private_snapshot["working_state"]["current_objective"] == private_sentinel
    assert private_snapshot["working_state"]["relevant_people"] == [private_sentinel]

    assert private_snapshot_after["working_state"]["current_objective"] == private_sentinel
    assert private_snapshot_after == private_snapshot
    assert private_snapshot_after["summary"]["completed_exchanges"] == 1
    assert private_snapshot_after["working_state"]["relevant_people"] == [private_sentinel]


@pytest.mark.usefixtures("service")
def test_public_private_public_scope_isolation_and_counts(
    service: ContinuityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "conversation_summary_interval_exchanges",
        5,
        raising=False,
    )

    public_sentinel = "PUBLIC_SENTINEL_OBJECTIVE"
    private_sentinel = "PRIVATE_SENTINEL_OBJECTIVE"

    public1_id = service.begin_exchange(
        session_id="session-pub-pri-pub",
        text="Public seeded objective and relation",
        speaker_name="Owner",
        input_source="local",
        privacy_scope="public",
    )
    service.complete_exchange(
        exchange_id=public1_id,
        session_id="session-pub-pri-pub",
        assistant_text="Public acknowledgement.",
        trace_id="trace-pub1",
        output_target="dashboard_monitor",
        privacy_scope="public",
        internal_summary="Public summary with objective and relation.",
        state_updates={
            "current_objective": public_sentinel,
            "relationship_signals": [public_sentinel],
        },
    )

    private_id = service.begin_exchange(
        session_id="session-pub-pri-pub",
        text="Private objective independent",
        speaker_name="Owner",
        input_source="local",
        privacy_scope="local_private",
    )
    service.complete_exchange(
        exchange_id=private_id,
        session_id="session-pub-pri-pub",
        assistant_text="Private acknowledgement.",
        trace_id="trace-pri",
        output_target="dashboard_monitor",
        privacy_scope="local_private",
        internal_summary="Private independent summary.",
        state_updates={
            "current_objective": private_sentinel,
            "relationship_signals": [private_sentinel],
        },
    )

    public2_id = service.begin_exchange(
        session_id="session-pub-pri-pub",
        text="Public follow-up omits objective and relationship",
        speaker_name="Owner",
        input_source="local",
        privacy_scope="public",
    )
    service.complete_exchange(
        exchange_id=public2_id,
        session_id="session-pub-pri-pub",
        assistant_text="Public follow-up acknowledged.",
        trace_id="trace-pub2",
        output_target="dashboard_monitor",
        privacy_scope="public",
    )

    public_context = service.prompt_context(
        "session-pub-pri-pub",
        privacy_scopes={"public"},
    )
    public_snapshot = service.snapshot(
        "session-pub-pri-pub",
        privacy_scope="public",
    )
    private_snapshot = service.snapshot(
        "session-pub-pri-pub",
        privacy_scope="local_private",
    )

    assert private_sentinel not in public_context
    assert public_sentinel in public_context

    assert public_snapshot["summary"]["completed_exchanges"] == 2
    assert private_snapshot["summary"]["completed_exchanges"] == 1

    assert public_snapshot["working_state"]["current_objective"] == public_sentinel
    assert public_snapshot["working_state"]["relevant_people"] == [public_sentinel]

    assert private_snapshot["working_state"]["current_objective"] == private_sentinel
    assert private_snapshot["working_state"]["relevant_people"] == [private_sentinel]
