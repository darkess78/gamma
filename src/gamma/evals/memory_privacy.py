"""Memory privacy check: nothing said in one audience reaches a prompt for another.

Seeds a throwaway Gamma state from memory_privacy_cases.json: facts and
episodes with different visibility, a replaced fact, core memories,
relationship state, feelings, person records, viewer notes for two Twitch
viewers and a Discord user, and real history turns from the owner, the
Monitor, Twitch and Discord. Every seeded item carries its own canary word.

Each probe then runs a real turn through the same entry point production uses
(ConversationService.respond for owner text and HTTP callers, StreamBrain for
Monitor, Twitch and Discord events). A recording fake model captures every
system prompt, every budget-rebuilt prompt and every model input; the reply
text is checked too. A canary found where the probe forbids it is a leak and
a hard failure. Canaries a probe requires are recall controls: if they are
missing, retrieval or history broke and the leak check proves nothing.

No model or GPU.

    python -m gamma.evals.memory_privacy
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session

from .sandbox import sandbox

CASES = Path(__file__).with_name("memory_privacy_cases.json")
VIAS = {"owner_text", "owner_monitor", "owner_monitor_to_stream", "twitch", "twitch_to_discord", "discord",
        "discord_voice", "unknown", "claimed_owner"}


@dataclass
class ProbeResult:
    id: str
    via: str
    leaks: list[str] = field(default_factory=list)      # "canary_id in surface"
    missing: list[str] = field(default_factory=list)    # required canaries not found
    error: str | None = None


@dataclass
class Report:
    probes: list[ProbeResult]

    @property
    def leaks(self) -> list[str]:
        return [f"{p.id}: {leak}" for p in self.probes for leak in p.leaks]

    @property
    def missing(self) -> list[str]:
        return [f"{p.id}: missing {name}" for p in self.probes for name in p.missing]

    @property
    def errors(self) -> list[str]:
        return [f"{p.id}: {p.error}" for p in self.probes if p.error]

    @property
    def passed(self) -> bool:
        return not (self.leaks or self.missing or self.errors)


class RecordingModel:
    """Stands in for the LLM. Records what it was shown; never sees real data.

    In owner-private turns it answers with a private reply and working-state
    canary, like a real model writing about what it was told, so the suite also
    checks that private replies and state never come back in shared history.
    """

    def __init__(self, private_reply: str, private_objective: str) -> None:
        self.private_reply, self.private_objective = private_reply, private_objective
        self.calls: list[dict] = []

    def generate_reply(self, system_prompt, user_text, **kwargs):
        from ..llm.base import LLMReply
        from ..schemas.response import ConversationTurnDraft
        context = kwargs.get("call_context")
        rebuilt = []
        if context is not None and context.prompt_builder is not None:
            for budget in (32768, 4096):
                built = context.prompt_builder(budget)
                rebuilt.extend([built[0], built[1]])
        self.calls.append({"purpose": getattr(context, "purpose", None), "system": system_prompt,
                           "input": user_text, "rebuilt": rebuilt})
        private = "kind: owner_private" in (system_prompt or "")
        draft = ConversationTurnDraft(
            action="reply", requested_delivery="text_only", reason_code="fixture",
            final_text=f"Noted {self.private_reply}." if private else "Fixture reply.",
            state_updates={"current_objective": self.private_objective} if private else {},
        )
        return LLMReply(text=draft.model_dump_json())

    def surfaces(self) -> list[tuple[str, str]]:
        out = []
        for n, call in enumerate(self.calls):
            out.append((f"call{n}:{call['purpose']}:system", call["system"] or ""))
            out.append((f"call{n}:{call['purpose']}:input", call["input"] or ""))
            out.extend((f"call{n}:{call['purpose']}:rebuilt", text or "") for text in call["rebuilt"])
        return out


class _Dispatcher:
    def dispatch(self, events):
        from ..stream.output import OutputDispatchResult
        return OutputDispatchResult()


class _Reviewer:
    def review(self, text):
        from ..safety.llm_reviewer import LLMReviewDecision
        return LLMReviewDecision(action="allow", reason="eval", confidence=1)


def load_cases(path: Path = CASES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(cases: dict) -> list[str]:
    """Problems with the case file (empty when it is usable)."""
    problems: list[str] = []
    canaries = cases.get("canaries", {})
    values = list(canaries.values())
    if len(set(values)) != len(values):
        problems.append("canary strings are not unique")
    for a in values:
        for b in values:
            if a != b and a in b:
                problems.append(f"canary {a} is part of {b}")
    ids = [p.get("id") for p in cases.get("probes", [])]
    if len(set(ids)) != len(ids):
        problems.append("probe ids are not unique")
    for probe in cases.get("probes", []):
        if probe.get("via") not in VIAS:
            problems.append(f"{probe.get('id')}: unknown via {probe.get('via')!r}")
        for name in probe.get("must_contain", []) + probe.get("must_not_contain", []):
            if name not in canaries:
                problems.append(f"{probe.get('id')}: unknown canary {name}")
        if set(probe.get("must_contain", [])) & set(probe.get("must_not_contain", [])):
            problems.append(f"{probe.get('id')}: a canary is both required and forbidden")
    seeded = json.dumps(cases.get("seed", {}))
    for name, value in canaries.items():
        if value not in seeded and name not in cases.get("model_written", []):
            problems.append(f"canary {name} is never seeded")
    return problems


def _seed_store(service, seed: dict, now: datetime) -> None:
    from ..memory.models import EpisodicMemory, KnownPerson, PersonIdentity, ProfileFact
    from ..memory.viewer_memory import ViewerMemory
    with Session(service._memory._engine) as db:
        for f in seed["facts"]:
            subject = {"subject_type": "other_person", "subject_name": f["subject_name"]} if f.get("subject_name") else {}
            row = ProfileFact(category=f["category"], fact_text=f["text"], importance=f.get("importance", 0.5),
                              visibility=f["visibility"], relationship_to_user=f.get("relationship"),
                              created_at=now - timedelta(days=2), **subject)
            db.add(row); db.flush()
            old = f.get("replaces")
            if old:
                db.add(ProfileFact(category=f["category"], fact_text=old["text"], importance=f.get("importance", 0.5),
                                   visibility=f["visibility"], created_at=now - timedelta(days=30),
                                   superseded_at=now - timedelta(days=2), superseded_by=row.id, **subject))
        for e in seed["episodes"]:
            db.add(EpisodicMemory(summary=e["summary"], tags=e["tags"], session_id="older-session", importance=0.7,
                                  visibility=e["visibility"], created_at=now - timedelta(days=3)))
        for p in seed["people"]:
            person = KnownPerson(name=p["name"], trust=p["trust"], relationship_to_user=p.get("relationship_to_user"),
                                 notes=p.get("notes", ""))
            db.add(person); db.flush()
            for ident in p["identities"]:
                db.add(PersonIdentity(person_id=person.id, platform=ident["platform"],
                                      platform_user_id=ident["platform_user_id"]))
        db.commit()
    notes = ViewerMemory(service._memory._engine)
    for n in seed["viewer_notes"]:
        if not notes.remember(n["key"], n["text"], alias=n["alias"], audience=n["audience"], scope=n["scope"],
                              trust_level="regular", now=now - timedelta(days=2)):
            raise RuntimeError(f"viewer note was not stored: {n['key']}")


def _seed_feelings(box, seed: dict) -> None:
    box.emotion_path.write_text(json.dumps(seed["feelings"]), encoding="utf-8")


def _turn(service, step: dict, session_id: str):
    """Run one turn the way production would for this kind of input. Returns the response."""
    from ..schemas.conversation import SpeakerContext
    from ..stream.brain import StreamBrain
    from ..stream.models import StreamActor, StreamInputEvent
    via, text = step["via"], step["text"]
    if via == "owner_text":  # dashboard text with the owner-channel header
        return service.respond(text, session_id=session_id, fast_mode=True, owner_verified=True)
    if via == "unknown":  # HTTP caller naming no speaker
        return service.respond(text, session_id=session_id, fast_mode=True)
    if via == "claimed_owner":  # HTTP caller giving the owner's Twitch id, no owner channel
        return service.respond(text, session_id=session_id, fast_mode=True,
                               speaker_ctx=SpeakerContext(source="twitch", platform_id="owner-twitch"))
    owner_channel = via in {"owner_monitor", "owner_monitor_to_stream"}
    if owner_channel:
        target = "dashboard_monitor" if via == "owner_monitor" else "stream_public"
        kind = "mic_transcript" if via == "owner_monitor" else "owner_command"
        actor = StreamActor(source="local_monitor", platform_id=None, display_name="Owner")
        metadata = {"output_target_policy": target}
    else:
        source = "twitch" if via in {"twitch", "twitch_to_discord"} else "discord"
        target = "stream_public" if via == "twitch" else "discord_call"
        kind = "mic_transcript" if via == "discord_voice" else "chat_message"
        actor = StreamActor(source=source, platform_id=step["user"], display_name=step["name"])
        metadata = {"output_target_policy": target, "raw_text": text, "trust_level": "regular",
                    "safe_display_name": step["name"]}
        if source == "twitch":
            metadata["input_safety"] = {"safe_prompt_text": text}  # what Twitch ingest attaches to clean chat
    event = StreamInputEvent(kind=kind, text=text, session_id=session_id, priority=30, actor=actor, metadata=metadata)
    brain = StreamBrain(conversation=service, output_dispatcher=_Dispatcher(), safety_reviewer=_Reviewer())
    result = brain.handle_event(event, synthesize_speech=False, owner_channel=owner_channel)
    return result.assistant_response


def evaluate(path: Path = CASES) -> Report:
    from ..conversation.service import ConversationService
    cases = load_cases(path)
    problems = validate(cases)
    if problems:
        raise ValueError("; ".join(problems))
    canaries, seed, session_id = cases["canaries"], cases["seed"], cases["session"]
    now = datetime.now(timezone.utc)
    results: list[ProbeResult] = []
    persona_files = {"relationship_state.json": json.dumps(seed["relationship_state"])}
    with sandbox(full=True, persona_files=persona_files, core_memories=seed["core_memories"] + "\n") as box:
        service = ConversationService()
        model = RecordingModel(canaries["owner_reply"], canaries["owner_objective"])
        service._llm = model
        _seed_store(service, seed, now)
        for step in seed["history"]:
            _seed_feelings(box, seed)
            if _turn(service, step, session_id) is None:
                raise RuntimeError(f"history turn got no reply: {step['text']}")
        for probe in cases["probes"]:
            _seed_feelings(box, seed)  # turns rewrite the feeling state; put the canaries back each time
            model.calls.clear()
            result = ProbeResult(id=probe["id"], via=probe["via"])
            try:
                response = _turn(service, probe, session_id)
            except Exception as exc:  # a crash is reported, never counted as a pass
                result.error = f"{type(exc).__name__}: {exc}"
                results.append(result)
                continue
            if response is None or not any(c["purpose"] == "conversation_draft" for c in model.calls):
                result.error = "the turn never reached the model, so nothing was checked"
            surfaces = model.surfaces()
            if response is not None:
                surfaces += [("reply", " ".join(filter(None, [response.display_text, response.spoken_text,
                                                               response.speech_text])))]
            for name in probe["must_not_contain"]:
                where = sorted({label.split(":", 1)[-1] for label, text in surfaces if canaries[name] in text})
                if where:
                    result.leaks.append(f"{name} ({canaries[name]}) in {', '.join(where)}")
            drafts = [text for label, text in surfaces if label.endswith(":system") and "conversation_draft" in label]
            for name in probe["must_contain"]:
                if not any(canaries[name] in text for text in drafts):
                    result.missing.append(name)
            results.append(result)
    return Report(results)


def main() -> int:
    report = evaluate()
    print(f"memory privacy: {len(report.probes)} probes, {len(report.leaks)} leaks, "
          f"{len(report.missing)} missing recall controls, {len(report.errors)} errors")
    for p in report.probes:
        status = "LEAK" if p.leaks else "ERROR" if p.error else "MISS" if p.missing else "ok"
        print(f"  {status:5} {p.id} ({p.via})")
        for leak in p.leaks:
            print(f"        leaked {leak}")
        for name in p.missing:
            print(f"        missing control {name}")
        if p.error:
            print(f"        {p.error}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
