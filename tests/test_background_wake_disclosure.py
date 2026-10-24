"""Recipient-specific context disclosure through real conversation consumers."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gamma.conversation.service import ConversationService
from gamma.errors import ConversationError
from gamma.llm.base import LLMReply
from gamma.schemas.conversation import RecipientContext, SpeakerContext
from test_conversation_recipient import PRIVATE, SHARED, EPISODE, fixture_state

BACKGROUND = "BACKGROUND_PRIVATE_CANARY"
WAKE = "WAKE_CONTEXT_PRIVATE_CANARY"
GOOD_TURN = {"action":"reply","requested_delivery":"text_only","final_text":"Safe fixture reply.",
             "reason_code":"fixture","emotion":"neutral","motions":[],"voice_styles":[],
             "tool_calls":[],"memory_candidates":[],"state_updates":{}}


class ContextCapture:
    def __init__(self, *, malformed=False, overlap=None, fail_repair=False):
        self.calls, self.malformed, self.overlap = [], malformed, overlap
        self.fail_repair = fail_repair

    def generate_reply(self, system_prompt, user_text, **kwargs):
        ctx = kwargs.get("call_context")
        purpose = getattr(ctx, "purpose", None)
        parts = {budget: ctx.prompt_builder(budget)[:2] for budget in (32768, 4096, 1024)} if ctx and ctx.prompt_builder else {}
        self.calls.append({"system":system_prompt,"user":user_text,"images":kwargs.get("image_inputs"),
                           "parts":parts,"purpose":purpose})
        if self.overlap and purpose in {"conversation_draft", "presence_wake"}:
            self.overlap.wait(timeout=10)
        invalid = (self.malformed and purpose in {"conversation_draft","presence_wake"}) or (self.fail_repair and purpose == "conversation_structured_repair")
        text = "{" if invalid else json.dumps(GOOD_TURN)
        return LLMReply(text=text)


def invoke(service, facade, recipient, *, fast=True, context=None):
    if facade == "wake":
        return service.respond_presence_wake(session_id="mixed", speaker=service._identity.resolve(None),
            wake_context=context if context is not None else WAKE, synthesize_speech=False, recipient=recipient)
    if facade == "direct_image":
        image = SimpleNamespace(filename="safe.png",stored_path=None,to_llm_input=lambda: {"fixture_image":"safe"})
        return service._respond(user_text="fixture topic", session_id="mixed", synthesize_speech=False,
            speaker=service._identity.resolve(None), recipient=recipient,
            background_context=context if context is not None else BACKGROUND, image=image,
            fast_mode=fast, persist_state=False, interaction_mode="stream")
    return service.respond("fixture topic", recipient=recipient, fast_mode=fast, evaluation_mode=True,
        speaker_ctx=SpeakerContext(source="twitch",platform_id="fixture-owner"),
        background_context=context if context is not None else BACKGROUND)


@pytest.mark.parametrize("recipient", [None, RecipientContext(), SHARED, PRIVATE])
@pytest.mark.parametrize("facade", ["respond","wake","direct_image"])
@pytest.mark.parametrize("malformed", [False,True])
@pytest.mark.parametrize("fast", [False,True])
def test_all_prompt_parts_select_context_before_building(fixture_state, recipient, facade, malformed, fast):
    service = fixture_state.service
    service._llm = ContextCapture(malformed=malformed)
    original = WAKE if facade == "wake" else BACKGROUND
    before = [p.read_bytes() for p in (fixture_state.data/"core_memories.md",fixture_state.config/"users.toml")]
    response = invoke(service, facade, recipient, fast=fast)
    assert response.display_text == "Safe fixture reply."
    assert original == (WAKE if facade == "wake" else BACKGROUND)
    assert [p.read_bytes() for p in (fixture_state.data/"core_memories.md",fixture_state.config/"users.toml")] == before
    calls = service._llm.calls
    assert calls and (any(c["purpose"] == "conversation_structured_repair" for c in calls) == malformed)
    for call in calls:
        assert (original in call["system"]) == (recipient is PRIVATE)
        assert original not in call["user"] and original not in json.dumps(call["images"])
        assert "SAFE_CHARACTER_CANARY" in call["system"]
        if call["parts"]:
            assert set(call["parts"]) == {32768,4096,1024}
            for system, user in call["parts"].values():
                assert original not in user
                if recipient is not PRIVATE:
                    assert original not in system
            assert (original in call["parts"][32768][0]) == (recipient is PRIVATE)


@pytest.mark.parametrize("facade", ["respond","wake"])
def test_private_budget_compaction_retains_original_and_public_omits(fixture_state, facade):
    service = fixture_state.service
    original = (WAKE if facade == "wake" else BACKGROUND) + " " + "private narrative " * 3000
    service._llm = ContextCapture()
    invoke(service, facade, PRIVATE, context=original)
    first = service._llm.calls[0]
    assert (WAKE if facade == "wake" else BACKGROUND) in first["system"]
    assert len(first["parts"][1024][0]) < len(first["parts"][32768][0])
    assert len(original) > 40000
    service._llm = ContextCapture()
    invoke(service, facade, SHARED, context=original)
    assert all("private narrative" not in json.dumps(c) and BACKGROUND not in json.dumps(c) and WAKE not in json.dumps(c)
               for c in service._llm.calls)


@pytest.mark.parametrize("facade", ["respond","wake","direct_image"])
def test_private_shared_overlap_does_not_mutate_context_or_identity(fixture_state, facade):
    private, public = fixture_state.service, ConversationService()
    barrier = Barrier(2)
    private._llm, public._llm = ContextCapture(overlap=barrier), ContextCapture(overlap=barrier)
    owner = private._identity.resolve(None)
    identity_before = vars(owner).copy()
    original = WAKE if facade == "wake" else BACKGROUND
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(invoke, private, facade, PRIVATE, context=original), pool.submit(invoke, public, facade, SHARED, context=original)
        assert a.result(timeout=20).display_text and b.result(timeout=20).display_text
    assert original in private._llm.calls[0]["system"]
    assert all(original not in json.dumps(c) for c in public._llm.calls)
    assert vars(owner) == identity_before and original == (WAKE if facade == "wake" else BACKGROUND)


class ReadTrap:
    def strip(self):
        raise AssertionError("unclassified context must be omitted before reading")


@pytest.mark.parametrize("facade", ["respond","wake"])
@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
def test_failed_repair_terminal_fallback_keeps_context_view(fixture_state, facade, recipient):
    service = fixture_state.service
    service._llm = ContextCapture(malformed=True,fail_repair=True)
    response = invoke(service,facade,recipient)
    original = WAKE if facade == "wake" else BACKGROUND
    assert response is not None
    assert any(c["purpose"] == "conversation_structured_repair" for c in service._llm.calls)
    assert (original in service._llm.calls[0]["system"]) == (recipient is PRIVATE)
    if recipient is not PRIVATE:
        assert all(original not in json.dumps(c) for c in service._llm.calls)
        assert original not in response.model_dump_json()


@pytest.mark.parametrize("facade", ["respond","wake","direct_image"])
def test_shared_context_is_never_read(fixture_state, facade):
    fixture_state.service._llm = ContextCapture()
    invoke(fixture_state.service, facade, SHARED, context=ReadTrap())
    assert fixture_state.service._llm.calls


@pytest.mark.parametrize("facade", ["respond","wake","direct_image"])
def test_forged_recipient_rejected_before_context_or_generation(fixture_state, facade):
    service = fixture_state.service
    service._llm = ContextCapture()
    with pytest.raises(ConversationError,match="must_be_internal"):
        invoke(service,facade,{"kind":"owner_private","owner_authorized":True},context=ReadTrap())
    assert service._llm.calls == []


def test_private_public_target_conflict_rejects_before_context(fixture_state):
    service = fixture_state.service
    service._llm = ContextCapture()
    with pytest.raises(ConversationError,match="conflicts"):
        service.respond("fixture topic", background_context=ReadTrap(), recipient=PRIVATE,
                        delivery_context="public_stream", evaluation_mode=True)
    with pytest.raises(ValueError):
        invoke(service,"wake",RecipientContext(kind="owner_private",owner_authorized=True,target_policy="stream_public"),
               context=ReadTrap())
    assert service._llm.calls == []


@pytest.mark.parametrize("mode", ["chat","stream","presence","evaluation","voice"])
def test_direct_mode_and_owner_do_not_grant_context(fixture_state, mode):
    service = fixture_state.service
    service._llm = ContextCapture()
    service._respond(user_text="fixture topic", session_id="mixed", synthesize_speech=False,
        speaker=service._identity.resolve(None),
        background_context=BACKGROUND, interaction_mode=mode, fast_mode=True,persist_state=False)
    assert all(BACKGROUND not in json.dumps(c) for c in service._llm.calls)


def test_actual_presence_producer_context_is_retained_but_default_wake_omits_it(fixture_state, monkeypatch):
    from gamma import presence
    from gamma.presence import PresenceService, presence_state_for_mode
    from gamma.schemas.presence import AudienceSelection
    state = fixture_state
    monkeypatch.setattr(presence,"presence_state_path",lambda:state.data/"presence.json")
    saved = presence_state_for_mode("wake")
    saved["wake"]["recent_openings"] = [{"opening":WAKE,"status":"text_only"}]
    presence.save_presence_state(saved)
    bus, dispatcher = Mock(), Mock()
    bus.has_eligible_listener.return_value = False
    bus.stats.return_value = {"subscriber_count":0}
    dispatcher.dispatch.return_value.model_dump.return_value = {"records":[]}
    producer = PresenceService(conversation=state.service,memory=state.service._memory,bus=bus,
                               dispatcher=dispatcher,identity=state.service._identity)
    state.service._llm = ContextCapture()
    original = state.service.respond_presence_wake
    seen = []
    def observe(**kwargs):
        seen.append(kwargs["wake_context"])
        return original(**kwargs)
    monkeypatch.setattr(state.service,"respond_presence_wake",observe)
    result = producer.wake(audience=AudienceSelection(kind="owner"),session_id="producer-wake")
    assert seen and EPISODE in seen[0] and WAKE in seen[0]
    assert result["wake"]["status"] == "text_only"
    assert all(EPISODE not in json.dumps(c) and WAKE not in json.dumps(c) for c in state.service._llm.calls)
    assert EPISODE in producer._build_wake_context(state=presence.load_presence_state(),speaker=state.service._identity.resolve(None))
    state.service._llm = ContextCapture()
    invoke(state.service,"wake",PRIVATE,context=seen[0])
    assert EPISODE in state.service._llm.calls[0]["system"] and WAKE in state.service._llm.calls[0]["system"]


@pytest.mark.parametrize("source,target", [("twitch","stream_public"),("discord","discord_call")])
def test_actual_stream_context_assembly_continues_but_shared_prompt_omits_it(fixture_state, source, target):
    from gamma.stream.brain import StreamBrain
    from gamma.stream.models import StreamActor, StreamInputEvent, StreamTurnResult, TurnDecision
    from gamma.stream.temp_memory import StreamTempMemoryStore, RecentStreamContextAssembler
    from test_conversation_recipient import Dispatcher, Reviewer
    state = fixture_state
    store = StreamTempMemoryStore(database_url="sqlite:///" + str(state.data/"stream-context.db"))
    prior = StreamInputEvent(kind="chat_message",text=BACKGROUND,
                            actor=StreamActor(source=source,platform_id="earlier",display_name="Viewer"),
                            metadata={"safe_prompt_text":BACKGROUND})
    store.record_turn(StreamTurnResult(input_event=prior,decision=TurnDecision(decision="ignore",reason="ambient")))
    assert BACKGROUND in RecentStreamContextAssembler(store).assemble()
    state.service._llm = ContextCapture()
    brain = StreamBrain(conversation=state.service,temp_memory_store=store,
                        output_dispatcher=Dispatcher(),safety_reviewer=Reviewer())
    result = brain.handle_event(StreamInputEvent(kind="chat_message",text="Shana, fixture topic",
        actor=StreamActor(source=source,platform_id="fixture-owner"),
        metadata={"output_target_policy":target,"recipient":{"kind":"owner_private","owner_authorized":True}}),
        synthesize_speech=False)
    assert result.assistant_response.display_text == "Safe fixture reply."
    assert all(BACKGROUND not in json.dumps(c) for c in state.service._llm.calls)
    assert BACKGROUND in RecentStreamContextAssembler(store).assemble()


@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
def test_tool_finalizer_receives_only_selected_background(fixture_state, recipient, monkeypatch):
    class ToolCapture(ContextCapture):
        def generate_reply(self, system_prompt, user_text, **kwargs):
            reply = super().generate_reply(system_prompt,user_text,**kwargs)
            if getattr(kwargs.get("call_context"),"purpose",None) == "conversation_draft":
                draft = dict(GOOD_TURN,action="tool_first",final_text="",tool_calls=[
                    {"tool":"memory_stats","args":{}},{"tool":"known_people","args":{}}])
                return LLMReply(text=json.dumps(draft))
            return reply
    service = fixture_state.service
    service._llm = ToolCapture()
    monkeypatch.setattr(service,"_background_memory_save",lambda *args:None)
    service.respond("fixture topic", background_context=BACKGROUND,recipient=recipient,fast_mode=True,
                    speaker_ctx=SpeakerContext(source="twitch",platform_id="fixture-owner"))
    assert any(c["purpose"] == "tool_finalizer" for c in service._llm.calls)
    for call in service._llm.calls:
        assert (BACKGROUND in call["system"]) == (recipient is PRIVATE)
        assert BACKGROUND not in call["user"]
        if recipient is not PRIVATE:
            assert BACKGROUND not in json.dumps(call)
