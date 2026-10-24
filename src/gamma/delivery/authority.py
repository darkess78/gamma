"""Durable, expiring permissions for exact delivery content and context."""
from __future__ import annotations

import time
import json
import os
import weakref
from contextlib import contextmanager
from uuid import uuid4
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

from ..config import settings
from ..errors import ConversationError
from ..memory.state_authority import StateAuthority, StateConflict, delivery_grants as grants, delivery_targets as targets, exchanges, delivery_artifacts as artifacts
from ..memory.state_authority import dispatch_attempts, delivery_payloads
from ..schemas.response import AssistantResponse
from .digest import fingerprint
from .artifacts import snapshot, verified_bytes
from ..supervisor.lifecycle_lock import lifecycle_lock

PROCESS_GENERATION = uuid4().hex
TERMINAL = ("played", "cancelled", "failed", "expired")


class DeliveryDenied(ConversationError):
    pass


def content_digest(response):
    return fingerprint({"display_text": response.display_text, "speech_text": response.speech_text,
                        "emotion": response.emotion, "styles": list(response.voice_styles),
                        "motions": list(response.motions), "delivery_mode": response.delivery_mode})


def policy_revision(controls=None):
    return fingerprint({"level": settings.speech_filter_level, "hard": settings.speech_filter_hard_block_enabled,
        "heuristic": settings.speech_filter_heuristic_enabled, "llm": settings.speech_filter_llm_enabled,
        "model": settings.speech_filter_llm_model, "rewrite": settings.speech_filter_auto_rewrite,
        "tagging_model": settings.local_llm_tagging_model, "light_model": settings.local_llm_light_model,
        "endpoint": settings.local_llm_endpoint, "temperature": settings.speech_filter_llm_temperature,
        "controls": controls or {}})


def presence_snapshot():
    from ..presence import load_presence_state
    state = load_presence_state(downgrade_stale_live=True)
    return {key: state.get(key) for key in ("control_revision", "mode", "audience", "outputs")}


def _bound_policy(policy):
    return json.dumps({"review": policy, "base": policy_revision(), "presence": fingerprint(presence_snapshot())}, sort_keys=True)


def _presence_matches(policy):
    try:
        bound = json.loads(policy)
        return bound["presence"] == fingerprint(presence_snapshot()) and bound["base"] == policy_revision()
    except (ValueError, TypeError, KeyError):
        return False


class DeliveryAuthority:
    def __init__(self, *, database_url=None, generation=None):
        self.pid = os.getpid()
        self.generation = generation or f"{os.getpid()}:{PROCESS_GENERATION}"
        self.engine = create_engine(database_url or settings.database_url, poolclass=NullPool)
        database = self.engine.url.database
        self.artifact_root = (Path(database).parent if database and database != ":memory:" else settings.data_dir) / "delivery-artifacts"
        self._finalizer = weakref.finalize(self, self.engine.dispose)
        self._state = StateAuthority(self.engine)

    @contextmanager
    def _session(self):
        try:
            with self._state.transaction() as session:
                yield session
        except StateConflict as exc:
            raise DeliveryDenied(str(exc)) from exc

    def issue(self, response, *, target, audience, policy, ttl=30.0):
        if os.getpid() != self.pid:
            raise DeliveryDenied("delivery_authority_inherited_across_process")
        if target not in {"dashboard_monitor", "stream_public", "discord_call"} or not audience or not policy or not 0 < ttl <= 300:
            raise DeliveryDenied("invalid_delivery_context")
        grant_id = uuid4().hex
        with self._session() as session:
            if not response.route_trace_id:
                raise DeliveryDenied("decision_ownership_required")
            decision = self._state.committed_decision(session, response.route_trace_id, current=False)
            if decision["status"] != "awaiting_authorization":
                raise DeliveryDenied("decision_not_awaiting_authorization")
            epoch = session.execute(select(targets.c.epoch).where(targets.c.target == target)).scalar()
            if epoch is None:
                epoch = 0
                session.execute(targets.insert().values(target=target, epoch=epoch))
            session.execute(grants.insert().values(id=grant_id, generation=self.generation,
                decision_id=response.route_trace_id, digest=content_digest(response), target=target, audience=audience,
                policy=_bound_policy(policy), epoch=epoch, expires=time.time() + ttl, status="authorized"))
            session.commit()
        return grant_id

    def _checked(self, session, grant_id, response, *, target, audience, policy):
        if os.getpid() != self.pid:
            raise DeliveryDenied("delivery_authority_inherited_across_process")
        row = session.execute(select(grants).where(grants.c.id == grant_id)).mappings().first()
        if row is None or row["generation"] != self.generation:
            raise DeliveryDenied("unknown_or_obsolete_delivery_generation")
        if row["expires"] <= time.time() or row["status"] in TERMINAL:
            raise DeliveryDenied("delivery_expired_or_terminal")
        if row["decision_id"] != response.route_trace_id:
            raise DeliveryDenied("delivery_decision_mismatch")
        self._state.committed_decision(session, row["decision_id"], current=False)
        epoch = session.execute(select(targets.c.epoch).where(targets.c.target == target)).scalar()
        if (row["epoch"] != epoch or row["target"] != target or row["audience"] != audience
                or row["policy"] != _bound_policy(policy) or row["digest"] != content_digest(response)):
            raise DeliveryDenied("delivery_context_changed")
        return row

    def check(self, grant_id, response, **context):
        with self._session() as session:
            return dict(self._checked(session, grant_id, response, **context))

    def _advance(self, grant_id, response, expected, status, **context):
        with self._session() as session:
            row = self._checked(session, grant_id, response, **context)
            if row["status"] not in expected:
                raise DeliveryDenied("delivery_stage_mismatch")
            session.execute(grants.update().where(grants.c.id == grant_id).values(status=status))
            session.commit()

    def begin_synthesis(self, grant_id, response, **context):
        self._advance(grant_id, response, {"authorized"}, "synthesizing", **context)

    def finish_synthesis(self, grant_id, response, **context):
        self._advance(grant_id, response, {"synthesizing"}, "synthesized", **context)

    def dispatch(self, grant_id, response, **context):
        # Cleanup cannot remove an old deduplicated snapshot between reuse and binding.
        with lifecycle_lock(self.artifact_root / ".snapshot.lock"):
            return self._dispatch_locked(grant_id, response, **context)

    def _dispatch_locked(self, grant_id, response, **context):
        frozen = None
        if response.audio_path:
            try:
                frozen = snapshot(Path(response.audio_path), self.artifact_root)
            except (OSError, ValueError) as exc:
                raise DeliveryDenied("invalid_synthesized_artifact") from exc
        with self._session() as session:
            row = self._checked(session, grant_id, response, **context)
            if row["status"] not in {"authorized", "synthesized"}:
                raise DeliveryDenied("delivery_stage_mismatch")
            if frozen:
                path, digest, size = frozen
                session.execute(artifacts.insert().values(grant_id=grant_id, path=str(path.resolve()),
                    digest=digest, size=size, content_type=response.audio_content_type or "audio/wav",
                    policy_base=policy_revision()))
            # Persist only outward-facing approved material, never private plans,
            # memory candidates, tool results or arbitrary provider metadata.
            payload = response.model_dump(include={"display_text", "speech_text", "emotion",
                "voice_styles", "motions", "delivery_mode", "route_trace_id", "audio_content_type"})
            payload["tts_metadata"] = {"delivery_state": "prepared", "delivery_grant": grant_id}
            if frozen:
                payload["audio_path"] = str(frozen[0].resolve())
                payload["tts_metadata"].update({"delivery_audio_digest": frozen[1],
                    "delivery_audio_url": f"{settings.shana_base_url}/v1/delivery/{grant_id}/audio"})
            session.execute(delivery_payloads.insert().values(grant_id=grant_id, payload=json.dumps(payload)))
            session.execute(grants.update().where(grants.c.id == grant_id).values(status="dispatched"))
            self._state.publish(session, row["decision_id"], grant_id, row["target"], response.display_text or "",
                                audio=bool(frozen))
            session.commit()
        response.tts_metadata["delivery_state"] = "prepared"
        response.tts_metadata["delivery_grant"] = grant_id
        if frozen:
            response.tts_metadata["delivery_audio_url"] = f"{settings.shana_base_url}/v1/delivery/{grant_id}/audio"
            response.tts_metadata["delivery_audio_digest"] = frozen[1]

    def response_for_grant(self, grant_id):
        """Read durable approved material for validation, never automatic replay."""
        with self._session() as session:
            payload = session.execute(select(delivery_payloads.c.payload).where(
                delivery_payloads.c.grant_id == grant_id)).scalar_one_or_none()
            row = session.execute(select(grants).where(grants.c.id == grant_id)).mappings().first()
            if payload is None or row is None:
                raise DeliveryDenied("delivery_payload_missing")
            response = AssistantResponse.model_validate(json.loads(payload))
            context = {key: row[key] for key in ("target", "audience", "policy")}
            try:
                context["policy"] = json.loads(row["policy"])["review"]
            except (ValueError, TypeError, KeyError) as exc:
                raise DeliveryDenied("obsolete_delivery_policy") from exc
            self._checked(session, grant_id, response, **context)
            return response, context

    def claim_dispatch(self, grant_id, response, *, consumer, **context):
        """Commit an at-most-once semantic consumer claim before external work.

        consumer must identify the adapter and event role, never a newly generated
        request ID. A missing completion is uncertain and cannot be retried.
        """
        if not isinstance(consumer, str) or not consumer.strip() or len(consumer) > 200:
            raise DeliveryDenied("invalid_dispatch_consumer")
        with self._session() as session:
            row = self._checked(session, grant_id, response, **context)
            if row["status"] != "dispatched":
                raise DeliveryDenied("delivery_not_prepared")
            existing = session.execute(select(dispatch_attempts.c.id).where(
                dispatch_attempts.c.exchange_id == row["decision_id"],
                dispatch_attempts.c.target == row["target"], dispatch_attempts.c.consumer == consumer)).first()
            if existing:
                raise DeliveryDenied("delivery_consumer_already_claimed")
            decision = self._state.committed_decision(session, row["decision_id"], current=False)
            attempt_id = uuid4().hex
            session.execute(dispatch_attempts.insert().values(id=attempt_id, grant_id=grant_id,
                exchange_id=row["decision_id"], generation_id=decision["generation_id"],
                target=row["target"], consumer=consumer, status="started",
                created_at=time.time(), updated_at=time.time()))
            session.commit()
            return attempt_id

    def finish_dispatch(self, attempt_id, *, succeeded):
        """Record adapter return, which is not evidence of media playback."""
        if os.getpid() != self.pid or type(succeeded) is not bool:
            raise DeliveryDenied("invalid_dispatch_completion")
        with self._session() as session:
            row = session.execute(select(dispatch_attempts).where(dispatch_attempts.c.id == attempt_id)).mappings().first()
            if row is None:
                raise DeliveryDenied("dispatch_attempt_missing")
            grant = session.execute(select(grants).where(grants.c.id == row["grant_id"])).mappings().first()
            if grant is None or grant["generation"] != self.generation or row["status"] != "started":
                raise DeliveryDenied("dispatch_attempt_not_owned_or_terminal")
            session.execute(dispatch_attempts.update().where(dispatch_attempts.c.id == attempt_id).values(
                status="dispatched" if succeeded else "failed", updated_at=time.time()))
            session.commit()

    def _audio_record(self, session, grant_id, target=None):
        row = session.execute(select(grants).where(grants.c.id == grant_id)).mappings().first()
        if (os.getpid() != self.pid or row is None or row["generation"] != self.generation
                or row["expires"] <= time.time() or row["status"] != "dispatched"):
            raise DeliveryDenied("audio_grant_inactive")
        if not _presence_matches(row["policy"]):
            raise DeliveryDenied("audio_presence_changed")
        self._state.committed_decision(session, row["decision_id"], current=False)
        epoch = session.execute(select(targets.c.epoch).where(targets.c.target == row["target"])).scalar()
        if row["epoch"] != epoch or (target is not None and row["target"] != target):
            raise DeliveryDenied("audio_target_changed")
        artifact = session.execute(select(artifacts).where(artifacts.c.grant_id == grant_id)).mappings().first()
        if artifact is None or artifact["policy_base"] != policy_revision():
            raise DeliveryDenied("audio_policy_changed_or_missing")
        return artifact

    def read_audio(self, grant_id, *, target=None):
        with self._session() as session:
            artifact = self._audio_record(session, grant_id, target)
            try:
                path = Path(artifact["path"])
                if path.resolve().parent != self.artifact_root.resolve():
                    raise ValueError("artifact outside owned root")
                data = verified_bytes(path, artifact["digest"], artifact["size"])
            except (OSError, ValueError) as exc:
                raise DeliveryDenied("audio_integrity_failed") from exc
            return data, artifact["content_type"], artifact["digest"]

    def cancel(self, grant_id):
        with self._session() as session:
            result = session.execute(grants.update().where(grants.c.id == grant_id, grants.c.status.not_in(TERMINAL)).values(status="cancelled"))
            session.commit()
            return bool(result.rowcount)

    def cancel_target(self, target):
        with self._session() as session:
            row = session.execute(select(targets.c.epoch).where(targets.c.target == target)).scalar()
            if row is None:
                session.execute(targets.insert().values(target=target, epoch=1))
            else:
                session.execute(targets.update().where(targets.c.target == target).values(epoch=row + 1))
            result = session.execute(grants.update().where(grants.c.target == target, grants.c.status.not_in(TERMINAL)).values(status="cancelled"))
            session.commit()
            return result.rowcount
