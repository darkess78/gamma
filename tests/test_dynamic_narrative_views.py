from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlmodel import Session, select

from gamma.conversation.service import ConversationService
from gamma.identity.profile import SpeakerProfile
from gamma.memory.models import KnownPerson
from gamma.persona import loader
from gamma.persona.emotion_models import AssistantEmotionState
from gamma.schemas.conversation import RecipientContext, SpeakerContext
from test_conversation_recipient import Capture, PRIVATE, SHARED, fixture_state, prompt_layers


CANARIES = (
    "CORE_PRIVATE_CANARY", "EMOTION_CAUSE_CANARY", "EMOTION_TARGET_CANARY",
    "EMOTION_NOTE_CANARY", "EMOTION_DATE_CANARY", "EMOTION_EPISODE_CANARY",
    "EMOTION_PATTERN_CANARY",
)
IDENTITY_NAME = "MAPPED_PRIVATE_NAME_CANARY"
IDENTITY_NOTE = "IDENTITY_PRIVATE_CANARY"
OWNER = SpeakerContext(source="twitch", platform_id="fixture-owner")
UNKNOWN = SpeakerContext(source="twitch", platform_id="unmapped-public-input")


@pytest.fixture
def narrative_state(fixture_state):
    state = fixture_state
    (state.data / "core_memories.md").write_text("- CORE_PRIVATE_CANARY\n")
    payload = {
        "state": {"current_emotion": "concerned", "intensity": 0.65, "decay_turns_remaining": 3,
                  "recent_emotions": ["happy", "concerned"], "cause_summary": "EMOTION_CAUSE_CANARY",
                  "emotional_target": "EMOTION_TARGET_CANARY", "notes": ["EMOTION_NOTE_CANARY"],
                  "updated_at": "EMOTION_DATE_CANARY"},
        "episodes": [{"event_summary": "fixture topic EMOTION_EPISODE_CANARY", "emotion": "concerned",
                      "intensity": 0.5, "trigger_type": "concerned", "relationship_effect": "steady",
                      "importance": 0.9, "created_at": "2026-10-08T00:00:00Z"}],
        "patterns": [{"pattern_text": "EMOTION_PATTERN_CANARY", "emotion_family": "concerned",
                      "confidence": 0.9, "evidence_count": 2, "subject_scope": "user",
                      "last_reinforced_at": "2026-10-08T00:00:00Z"}],
    }
    (state.data / "assistant_emotion_memory.json").write_text(json.dumps(payload))
    (state.persona / "style.json").write_text('{"humor":0.6,"teasing":0.7}')
    authored = loader.yaml.safe_load((state.config / "persona.yaml").read_text())
    authored["authored_public"]["description"] = "SAFE_AUTHORED_SHANA_CANARY"
    (state.config / "persona.yaml").write_text(loader.yaml.safe_dump(authored))
    with Session(state.service._memory._engine) as db:
        person = db.exec(select(KnownPerson).where(KnownPerson.name == "FixtureOwner")).one()
        person.name = IDENTITY_NAME
        person.notes = IDENTITY_NOTE
        db.add(person)
        db.commit()
    return state


def assert_view(prompts, *, private, identified=True):
    assert prompts
    for prompt in prompts:
        assert all((canary in prompt) == private for canary in CANARIES)
        for canary in (IDENTITY_NAME, IDENTITY_NOTE):
            assert (canary in prompt) == (private and identified)
        assert "SAFE_CHARACTER_CANARY" in prompt
        assert "SAFE_AUTHORED_SHANA_CANARY" in prompt
        assert "Respect private context." in prompt
        assert '"humor": 0.6' in prompt and '"teasing": 0.7' in prompt
        assert "current_emotion: concerned" in prompt and "intensity: 0.65" in prompt
        assert "recent_emotions: happy, concerned" in prompt


@pytest.mark.parametrize("recipient", [None, RecipientContext(), SHARED, PRIVATE])
@pytest.mark.parametrize("speaker", [OWNER, UNKNOWN])
@pytest.mark.parametrize("path", ["initial", "repair", "failed_repair", "finalizer"])
def test_real_conversation_paths_select_dynamic_view(narrative_state, recipient, speaker, path):
    service = narrative_state.service
    adapter = Capture(malformed=path in ("repair", "failed_repair"),
                      fail_repair=path == "failed_repair", tools=path == "finalizer")
    service._llm = adapter
    service.respond("fixture topic concerned", recipient=recipient, speaker_ctx=speaker, fast_mode=True)
    assert_view(prompt_layers(adapter), private=recipient is PRIVATE, identified=speaker is OWNER)
    if path in ("repair", "failed_repair"):
        assert any(c["purpose"] == "conversation_structured_repair" for c in adapter.calls)
    # Unknown speaker lacks tool authority by design, independently of display.
    if path == "finalizer" and speaker is OWNER:
        assert any(c["purpose"] == "tool_finalizer" for c in adapter.calls)
    owner = service._identity.resolve(OWNER)
    assert owner.name == IDENTITY_NAME and owner.notes == IDENTITY_NOTE
    assert owner.tools_allowed and owner.memory_read_allowed and owner.subject_type == "primary_user"


def test_overlap_is_read_only_and_private_positive_survives(narrative_state):
    state = narrative_state
    speaker = state.service._identity.resolve(OWNER)
    attributes_before = copy.deepcopy(vars(speaker))
    paths = [state.data / "core_memories.md", state.data / "assistant_emotion_memory.json"]
    storage_before = [p.read_bytes() for p in paths]
    barrier = Barrier(3)

    def build(recipient):
        barrier.wait(timeout=10)
        return loader.build_system_prompt(state.service._memory, "fixture topic concerned",
                                          speaker=speaker, recipient=recipient)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(build, recipient) for recipient in (PRIVATE, SHARED, None)]
        prompts = [f.result(timeout=15) for f in futures]
    assert_view([prompts[0]], private=True)
    assert_view(prompts[1:], private=False)
    assert vars(speaker) == attributes_before
    assert [p.read_bytes() for p in paths] == storage_before
    # Another private read still contains every learned narrative.
    assert_view([build_without_barrier(state, speaker, PRIVATE)], private=True)


def build_without_barrier(state, speaker, recipient):
    return loader.build_system_prompt(state.service._memory, "fixture topic concerned",
                                      speaker=speaker, recipient=recipient)


def test_conversation_overlap_does_not_reset_storage(narrative_state):
    state = narrative_state
    paths = [state.data / "core_memories.md", state.data / "assistant_emotion_memory.json"]
    storage_before = [p.read_bytes() for p in paths]
    private, shared = state.service, ConversationService()
    barrier = Barrier(2)
    private._llm, shared._llm = Capture(overlap=barrier), Capture(overlap=barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.respond, "fixture topic concerned", recipient=recipient,
                               speaker_ctx=OWNER, fast_mode=True, evaluation_mode=True)
                   for service, recipient in ((private, PRIVATE), (shared, SHARED))]
        for f in futures:
            f.result(timeout=20)
    assert_view(prompt_layers(private._llm), private=True)
    assert_view(prompt_layers(shared._llm), private=False)
    assert [p.read_bytes() for p in paths] == storage_before


@pytest.mark.parametrize("recipient", [None, SHARED])
@pytest.mark.parametrize("speaker", [OWNER, UNKNOWN])
@pytest.mark.parametrize("field,value", [
    ("current_emotion", "ENUM_SLOT_PRIVATE_CANARY"),
    ("current_emotion", ["ENUM_SLOT_PRIVATE_CANARY"]),
    ("intensity", "SCALAR_SLOT_PRIVATE_CANARY"),
    ("intensity", True),
    ("intensity", float("nan")),
    ("intensity", float("inf")),
    ("intensity", float("-inf")),
    ("decay_turns_remaining", "SCALAR_SLOT_PRIVATE_CANARY"),
    ("decay_turns_remaining", True),
    ("decay_turns_remaining", float("nan")),
    ("recent_emotions", ["concerned", "ENUM_SLOT_PRIVATE_CANARY", {"x": "ENUM_SLOT_PRIVATE_CANARY"}]),
    ("recent_emotions", "ENUM_SLOT_PRIVATE_CANARY"),
])
def test_bad_persisted_enum_scalar_slots_cannot_be_public_text(narrative_state, recipient, speaker, field, value):
    path = narrative_state.data / "assistant_emotion_memory.json"
    bundle = json.loads(path.read_text())
    bundle["state"][field] = value
    path.write_text(json.dumps(bundle))
    before = path.read_bytes()
    service = narrative_state.service
    service._llm = Capture(malformed=True)
    service.respond("fixture topic concerned", recipient=recipient, speaker_ctx=speaker,
                    fast_mode=True, evaluation_mode=True)
    prompts = prompt_layers(service._llm)
    assert prompts and all("SLOT_PRIVATE_CANARY" not in p for p in prompts)
    assert all(all(canary not in p for canary in CANARIES) for p in prompts)
    assert all("SAFE_CHARACTER_CANARY" in p and "SAFE_AUTHORED_SHANA_CANARY" in p for p in prompts)
    if field in ("intensity", "decay_turns_remaining"):
        assert all(("intensity: 0.00" if field == "intensity" else "decay_turns_remaining: 0") in p for p in prompts)
    if field == "current_emotion":
        assert all("current_emotion: neutral" in p for p in prompts)
    assert path.read_bytes() == before


@pytest.mark.parametrize("intensity,decay,expected", [
    (1, 3, ("intensity: 1.00", "decay_turns_remaining: 3")),
    (-4.0, -1, ("intensity: 0.00", "decay_turns_remaining: 0")),
    (8.0, 999999999999999999999999, ("intensity: 1.00", "decay_turns_remaining: 10000")),
])
def test_public_numeric_values_are_bounded_without_mutating(intensity, decay, expected):
    state = AssistantEmotionState(current_emotion="happy", intensity=intensity, decay_turns_remaining=decay)
    before = state.as_dict()
    prompt = state.to_prompt_block(include_private=False)
    assert all(s in prompt for s in expected)
    assert state.as_dict() == before


def test_shared_emotion_model_does_not_mutate_one_shared_instance():
    state = AssistantEmotionState(current_emotion="happy", intensity=0.75, notes=["MODEL_NOTE_PRIVATE_CANARY"],
                                  emotional_target="MODEL_TARGET_PRIVATE_CANARY", cause_summary="MODEL_CAUSE_PRIVATE_CANARY")
    before = copy.deepcopy(state.as_dict())
    barrier = Barrier(2)
    def render(private):
        barrier.wait(timeout=10)
        return state.to_prompt_block(include_private=private)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(render, True), pool.submit(render, False)
        private, public = a.result(timeout=15), b.result(timeout=15)
    for canary in ("MODEL_NOTE_PRIVATE_CANARY", "MODEL_TARGET_PRIVATE_CANARY", "MODEL_CAUSE_PRIVATE_CANARY"):
        assert canary in private and canary not in public
    assert "current_emotion: happy" in public and "intensity: 0.75" in public
    assert state.as_dict() == before


def test_display_does_not_change_identity_authority_or_emit_raw_labels():
    profile = SpeakerProfile(name=IDENTITY_NAME, trust="owner", notes=IDENTITY_NOTE,
                             resolved_via="PRIVATE_SOURCE_CANARY", is_owner=True)
    before = vars(profile).copy()
    public = profile.describe(include_private=False)
    assert public == "Name: Recognized owner\nTrust: owner"
    assert all(c not in public for c in (IDENTITY_NAME, IDENTITY_NOTE, "PRIVATE_SOURCE_CANARY"))
    assert IDENTITY_NAME in profile.describe(include_private=True) and IDENTITY_NOTE in profile.describe()
    assert vars(profile) == before and profile.tools_allowed and profile.subject_type == "primary_user"
    bad = SpeakerProfile(name=IDENTITY_NAME, trust="TRUST_PRIVATE_CANARY")
    assert bad.describe(include_private=False) == "Name: Unidentified speaker\nTrust: public"


def test_safe_rendering_performs_no_narrative_relevance_search(narrative_state, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("shared views must not select emotional narratives")
    monkeypatch.setattr(loader.EmotionMemoryService, "relevant_context", forbid)
    for recipient in (None, SHARED):
        prompt = loader.build_system_prompt(user_text="fixture topic concerned", recipient=recipient)
        assert all(c not in prompt for c in CANARIES)
        assert "current_emotion: concerned" in prompt


def test_actual_authored_shana_persona_remains_in_public_view(narrative_state):
    stage = Path(__file__).resolve().parents[1]
    source = stage / "src/gamma/persona"
    core = (source / "core.md").read_text().strip()
    boundaries = (source / "boundaries.md").read_text().strip()
    style = json.loads((source / "style.json").read_text())
    authored_yaml = (stage / "config/persona.yaml").read_bytes()
    for name in ("core.md", "boundaries.md", "style.json"):
        (narrative_state.persona / name).write_bytes((source / name).read_bytes())
    (narrative_state.config / "persona.yaml").write_bytes(authored_yaml)
    prompt = loader.build_system_prompt(user_text="fixture topic concerned", recipient=SHARED)
    assert core in prompt and boundaries in prompt
    assert json.dumps(style, indent=2, sort_keys=True) in prompt
    assert json.dumps(loader.yaml.safe_load(authored_yaml)["authored_public"], indent=2, sort_keys=True) in prompt
    assert "current_emotion: concerned" in prompt and "intensity: 0.65" in prompt
    assert all(canary not in prompt for canary in CANARIES)
