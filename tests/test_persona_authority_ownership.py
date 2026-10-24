import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlmodel import Session

from gamma.memory.state_authority import documents
from gamma.persona.assistant_state import AssistantStateStore
from gamma.persona.emotion_service import EmotionMemoryService
from gamma.tools.builtin import SaveCoreMemoryTool, SaveMemoryTool


def test_explicit_persona_paths_preserve_legacy_and_reopen_authority(tmp_path):
    feeling_path = tmp_path / "feeling.json"
    emotion_path = tmp_path / "emotion.json"
    feeling_source = json.dumps({"current_emotion": "happy", "recent_emotions": ["happy"]})
    emotion_source = json.dumps({"state": {"current_emotion": "happy"}, "episodes": [], "patterns": []})
    feeling_path.write_text(feeling_source)
    emotion_path.write_text(emotion_source)
    feeling = AssistantStateStore(feeling_path)
    emotion = EmotionMemoryService(emotion_path)
    assert feeling.load().current_emotion == "happy"
    feeling.update(emotion="concerned", user_text="fixture", reply_text="fixture")
    emotion.update_from_turn(emotion="concerned", user_text="fixture", reply_text="fixture", session_id="fixture")
    assert feeling_path.read_text() == feeling_source
    assert emotion_path.read_text() == emotion_source
    assert AssistantStateStore(feeling_path).load().current_emotion == "concerned"
    assert EmotionMemoryService(emotion_path).load_bundle()["state"].current_emotion == "concerned"
    assert AssistantStateStore(tmp_path / "unrelated.json").load().current_emotion == "neutral"
    with Session(feeling._documents.engine) as session:
        row = session.execute(select(documents)).mappings().one()
        assert json.loads(row["legacy_payload"])["current_emotion"] == "happy"


def test_explicit_persona_concurrent_updates_retain_each_change(tmp_path):
    path = tmp_path / "feeling.json"
    stores = [AssistantStateStore(path), AssistantStateStore(path)]
    def update(index):
        stores[index].update(emotion="happy", user_text=f"fixture {index}", reply_text="fixture")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update, range(2)))
    state = AssistantStateStore(path).load()
    assert len(state.recent_emotions) == 2
    assert any("fixture 0" in note for note in state.notes)
    assert any("fixture 1" in note for note in state.notes)
    assert not path.exists()


@pytest.mark.parametrize("tool,args", [
    (SaveMemoryTool, {"type": "profile", "text": "Fictional fact", "session_id": "forged"}),
    (SaveCoreMemoryTool, {"fact": "Fictional core fact"}),
])
def test_model_memory_tool_direct_execution_cannot_write(tool, args):
    # Avoid constructor dependencies; run must reject before consulting storage.
    result = tool.__new__(tool).run(**args)
    assert not result.ok
    assert result.metadata == {"saved": 0, "reason": "decision_ownership_required"}


def test_incremental_compatibility_never_invokes_unowned_generator(monkeypatch):
    import gamma.run_live_voice_worker as worker
    captured = {}
    def committed(**kwargs):
        captured.update(kwargs)
        return {"reply_text": "committed"}
    monkeypatch.setattr(worker, "_run_simple_chunked", committed)
    result = worker._run_incremental_experimental(
        started_at=0, args=object(), transcript="fixture", synthesize_speech=False,
        conversation=object(), stream_brain=object(), sentence_generator=object(),
        output_path=None, status_path=None, status_payload={},
        response_mode="incremental_experimental", planner_state={}, turn_state=object())
    assert result == {"reply_text": "committed"}
    assert captured["response_mode"] == "simple_chunked"
    assert "sentence_generator" not in captured
