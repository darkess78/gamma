from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from gamma.memory.continuity import ContinuityService


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def test_turn_journal_and_working_state_survive_service_reconstruction() -> None:
    with TemporaryDirectory() as temp_dir:
        url = _database_url(Path(temp_dir) / "continuity.db")
        first = ContinuityService(database_url=url)
        exchange_id = first.begin_exchange(
            session_id="session-a",
            text="Continue the deployment checklist?",
            speaker_name="Owner",
            input_source="local",
            privacy_scope="local_private",
        )
        first.complete_exchange(
            exchange_id=exchange_id,
            session_id="session-a",
            assistant_text="Yes, the next step is validation.",
            trace_id="trace-a",
            output_target="dashboard_monitor",
            privacy_scope="local_private",
            internal_summary="Validate the deployment checklist.",
        )

        reconstructed = ContinuityService(database_url=url)
        snapshot = reconstructed.snapshot("session-a")
        turns = reconstructed.recent_turns(
            "session-a",
            privacy_scopes={"local_private"},
            token_budget=1000,
        )

    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert snapshot["working_state"]["active_topic"] == "Continue the deployment checklist?"
    assert snapshot["working_state"]["next_intended_action"]
    assert snapshot["summary"]["completed_exchanges"] == 1


def test_unknown_scope_cannot_read_private_turns() -> None:
    with TemporaryDirectory() as temp_dir:
        service = ContinuityService(database_url=_database_url(Path(temp_dir) / "privacy.db"))
        exchange_id = service.begin_exchange(
            session_id="private-session",
            text="Owner private fact",
            speaker_name="Owner",
            input_source="local",
            privacy_scope="local_private",
        )
        service.complete_exchange(
            exchange_id=exchange_id,
            session_id="private-session",
            assistant_text="Private reply",
            trace_id=None,
            output_target="dashboard_monitor",
            privacy_scope="local_private",
        )

        turns = service.recent_turns(
            "private-session",
            privacy_scopes={"local_generic", "public"},
            token_budget=1000,
        )
        context = service.prompt_context(
            "private-session",
            privacy_scopes={"local_generic", "public"},
            token_budget=1000,
        )

    assert turns == []
    assert "Owner private fact" not in context
    assert "Private reply" not in context


def test_pending_turn_is_marked_interrupted_after_reconstruction() -> None:
    with TemporaryDirectory() as temp_dir:
        url = _database_url(Path(temp_dir) / "interrupted.db")
        first = ContinuityService(database_url=url)
        first.begin_exchange(
            session_id="session-b",
            text="This generation will be interrupted",
            speaker_name="Owner",
            input_source="local",
            privacy_scope="local_private",
        )

        reconstructed = ContinuityService(database_url=url)
        completed = reconstructed.recent_turns(
            "session-b",
            privacy_scopes={"local_private"},
            token_budget=1000,
        )
        deleted = reconstructed.delete_session("session-b")

    assert completed == []
    assert deleted == 1


def test_durable_output_restores_text_without_replay_instruction() -> None:
    with TemporaryDirectory() as temp_dir:
        url = _database_url(Path(temp_dir) / "output.db")
        first = ContinuityService(database_url=url)
        first.record_output(
            target_policy="dashboard_monitor",
            turn_id="wake-1",
            text="Good morning.",
            status="completed",
            spoken=True,
        )

        output = ContinuityService(database_url=url).last_output("dashboard_monitor")

    assert output["text"] == "Good morning."
    assert output["status"] == "completed"
    assert "audio" not in output
