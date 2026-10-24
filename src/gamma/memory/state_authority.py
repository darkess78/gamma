"""Transactional ownership for the continuity journal during state migration.

Only explicit lifecycle recovery can retire another generation's pending work.
Inference never holds this database transaction open.
"""
from __future__ import annotations

import json
import os
import socket
import time
from contextlib import nullcontext, contextmanager
from pathlib import Path
from uuid import uuid4

import psutil
from sqlmodel import Session
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, Index, UniqueConstraint, inspect, insert, select, update

from ..supervisor.lifecycle_lock import lifecycle_lock
from ..errors import ConversationError
from .sqlite_backup import verified_backup
from .stream_schema import register_stream_tables

metadata = MetaData()
register_stream_tables(metadata)
decision_commits = Table("gamma_decision_commit", metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("exchange_id", String, nullable=False, unique=True), sqlite_autoincrement=True)
decision_effects = Table("gamma_decision_effect", metadata,
    Column("exchange_id", String, primary_key=True), Column("effect", String, primary_key=True))
publications = Table("gamma_publication", metadata,
    Column("grant_id", String, primary_key=True), Column("exchange_id", String, nullable=False),
    Column("generation_id", String, nullable=False), Column("session_id", String, nullable=False),
    Column("sequence", Integer, nullable=False), Column("target", String, nullable=False),
    Column("text", Text, nullable=False), Column("turn_id", String, nullable=False),
    Column("status", String, nullable=False), Column("spoken", Integer, nullable=False),
    Column("occurred_at", Float, nullable=False))
delivery_payloads = Table("gamma_delivery_payload", metadata,
    Column("grant_id", String, primary_key=True), Column("payload", Text, nullable=False))
dispatch_attempts = Table("gamma_delivery_dispatch", metadata,
    Column("id", String, primary_key=True), Column("grant_id", String, nullable=False),
    Column("exchange_id", String, nullable=False), Column("generation_id", String, nullable=False),
    Column("target", String, nullable=False), Column("consumer", String, nullable=False),
    Column("status", String, nullable=False), Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("exchange_id", "target", "consumer", name="uq_delivery_dispatch_consumer"))
playback_offers = Table("gamma_playback_offer", metadata,
    Column("lease_id", String, primary_key=True), Column("event_id", String, primary_key=True),
    Column("grant_id", String), Column("target", String, nullable=False),
    Column("target_epoch", Integer, nullable=False), Column("presence", String, nullable=False),
    Column("permit_until", Float, nullable=False))
delivery_artifacts = Table("gamma_delivery_artifact", metadata,
    Column("grant_id", String, primary_key=True), Column("path", Text, nullable=False),
    Column("digest", String, nullable=False), Column("size", Integer, nullable=False),
    Column("content_type", String, nullable=False), Column("policy_base", String, nullable=False))
playback_leases = Table("gamma_playback_lease", metadata,
    Column("id", String, primary_key=True), Column("generation", String, nullable=False),
    Column("receiver", String, nullable=False), Column("target", String, nullable=False),
    Column("expires", Float, nullable=False), Column("status", String, nullable=False))
playback_receipts = Table("gamma_playback_receipt", metadata,
    Column("lease_id", String, primary_key=True), Column("event_id", String, primary_key=True),
    Column("digest", String, nullable=False), Column("expires", Float, nullable=False),
    Column("status", String, nullable=False), Column("updated_at", Float, nullable=False))
delivery_targets = Table("gamma_delivery_target", metadata,
    Column("target", String, primary_key=True), Column("epoch", Integer, nullable=False))
delivery_grants = Table("gamma_delivery_grant", metadata,
    Column("id", String, primary_key=True), Column("generation", String, nullable=False),
    Column("decision_id", String), Column("digest", String, nullable=False),
    Column("target", String, nullable=False), Column("audience", String, nullable=False),
    Column("policy", String, nullable=False), Column("epoch", Integer, nullable=False),
    Column("expires", Float, nullable=False), Column("status", String, nullable=False))
documents = Table("gamma_state_document", metadata,
    Column("key", String, primary_key=True), Column("payload", Text, nullable=False),
    Column("legacy_payload", Text), Column("version", Integer, nullable=False))
generations = Table("gamma_state_generation", metadata,
    Column("id", String, primary_key=True), Column("host", String, nullable=False),
    Column("pid", Integer, nullable=False), Column("process_started", Float, nullable=False),
    Column("lease_until", Float, nullable=False), Column("status", String, nullable=False))
versions = Table("gamma_state_version", metadata,
    Column("session_id", String, primary_key=True), Column("version", Integer, nullable=False))
exchanges = Table("gamma_exchange_owner", metadata,
    Column("exchange_id", String, primary_key=True), Column("generation_id", String, nullable=False, index=True),
    Column("session_id", String, nullable=False), Column("privacy_scope", String, nullable=False),
    Column("base_version", Integer, nullable=False), Column("status", String, nullable=False))
outbox = Table("gamma_decision_outbox", metadata,
    Column("id", String, primary_key=True), Column("exchange_id", String, nullable=False, unique=True),
    Column("generation_id", String, nullable=False), Column("session_id", String, nullable=False),
    Column("state_version", Integer, nullable=False), Column("privacy_scope", String, nullable=False),
    Column("payload", Text, nullable=False), Column("status", String, nullable=False),
    Column("created_at", Float, nullable=False))

Index("ix_delivery_grant_expiry", delivery_grants.c.expires)
Index("ix_delivery_artifact_path", delivery_artifacts.c.path)
Index("ix_playback_lease_expiry", playback_leases.c.expires)
Index("ix_playback_receipt_expiry", playback_receipts.c.expires)
Index("ix_playback_receipt_terminal", playback_receipts.c.status, playback_receipts.c.updated_at)
Index("ix_playback_offer_grant", playback_offers.c.grant_id)


class StateConflict(ConversationError):
    """The decision's state version or generation is no longer current."""


class StateAuthority:
    def __init__(self, engine, *, generation_id: str | None = None):
        self.engine = engine
        self.generation_id = generation_id or uuid4().hex
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self.process_started = psutil.Process().create_time()
        self.backup_path = None
        database = engine.url.database
        path = Path(database).resolve() if engine.dialect.name == "sqlite" and database and database != ":memory:" else None
        with lifecycle_lock(path.with_suffix(path.suffix + ".schema.lock")) if path else nullcontext():
            inspector = inspect(engine)
            existing = set(inspector.get_table_names())
            missing_indexes = []
            for table in metadata.tables.values():
                if table.name in existing and table.indexes:
                    names = {item["name"] for item in inspector.get_indexes(table.name)}
                    missing_indexes.extend(index for index in table.indexes if index.name not in names)
            if not set(metadata.tables).issubset(existing) or missing_indexes:
                if path:
                    self.backup_path = verified_backup(path)
                metadata.create_all(engine)
                for index in missing_indexes:
                    index.create(engine, checkfirst=True)

    @contextmanager
    def transaction(self, *, operator=False):
        """The only write transaction entry point for persistent assistant state."""
        with Session(self.engine) as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            session.info["state_authority"] = self
            if operator:
                self.invalidate(session)
            try:
                yield session
            finally:
                session.info.pop("state_authority", None)

    @staticmethod
    def require_transaction(session):
        if not isinstance(session.info.get("state_authority"), StateAuthority):
            raise StateConflict("state_authority_transaction_required")

    @staticmethod
    def invalidate(session, session_id=None):
        StateAuthority.require_transaction(session)
        affected = select(outbox.c.exchange_id)
        revision = update(versions)
        cancelled = update(outbox)
        if session_id is not None:
            affected = affected.where(outbox.c.session_id == session_id)
            revision = revision.where(versions.c.session_id == session_id)
            cancelled = cancelled.where(outbox.c.session_id == session_id)
        session.execute(update(delivery_grants).where(delivery_grants.c.decision_id.in_(affected),
            delivery_grants.c.status.not_in(("played", "cancelled", "failed", "expired"))).values(status="cancelled"))
        session.execute(cancelled.where(outbox.c.status.in_(("awaiting_authorization", "dispatched", "internal_only")))
                        .values(status="cancelled_operator"))
        session.execute(revision.values(version=versions.c.version + 1))

    def committed_decision(self, session, exchange_id, *, owned=False, current=True):
        self.require_transaction(session)
        row = session.execute(select(outbox).where(outbox.c.exchange_id == exchange_id)).mappings().first()
        owner = self.owner(session, exchange_id)
        if row is None or owner is None or owner["status"] != "completed" or row["status"] not in (
                "awaiting_authorization", "internal_only", "dispatched"):
            raise StateConflict("decision_not_current_or_committed")
        if owned:
            self._generation(session)
            if row["generation_id"] != self.generation_id:
                raise StateConflict("decision_not_owned")
        generation = session.execute(select(generations).where(generations.c.id == row["generation_id"])).mappings().first()
        if generation is None or generation["status"] != "active" or generation["host"] != self.host:
            raise StateConflict("decision_generation_inactive")
        try:
            process = psutil.Process(generation["pid"])
            alive = process.create_time() == generation["process_started"] and process.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            alive = False
        if not alive:
            raise StateConflict("decision_generation_not_live")
        if current and self.version(session, row["session_id"]) != row["state_version"]:
            raise StateConflict("stale_state_version")
        return row

    @contextmanager
    def decision_transaction(self, exchange_id, *, effect, session_id=None):
        with self.transaction() as session:
            row = self.committed_decision(session, exchange_id, owned=True)
            if session_id is not None and row["session_id"] != session_id:
                raise StateConflict("decision_scope_mismatch")
            if session.execute(select(decision_effects).where(decision_effects.c.exchange_id == exchange_id,
                    decision_effects.c.effect == effect)).first():
                raise StateConflict("decision_effect_already_committed")
            session.execute(insert(decision_effects).values(exchange_id=exchange_id, effect=effect))
            session.execute(update(versions).values(version=versions.c.version + 1))
            session.execute(update(outbox).where(outbox.c.exchange_id == exchange_id)
                            .values(state_version=row["state_version"] + 1))
            yield session

    def publish(self, session, exchange_id, grant_id, target, text, *, audio=False):
        row = self.committed_decision(session, exchange_id, current=False)
        if row["status"] == "internal_only":
            raise StateConflict("internal_decision_cannot_publish")
        sequence = session.execute(select(decision_commits.c.sequence).where(
            decision_commits.c.exchange_id == exchange_id)).scalar_one_or_none()
        if sequence is None:
            raise StateConflict("legacy_decision_cannot_publish")
        newest = session.execute(select(publications.c.sequence).where(publications.c.target == target)
                                 .order_by(publications.c.sequence.desc()).limit(1)).scalar()
        if newest is not None and newest > sequence:
            raise StateConflict("publication_superseded")
        if session.execute(select(publications).where(publications.c.exchange_id == exchange_id,
                publications.c.target == target)).first():
            raise StateConflict("decision_already_published")
        session.execute(insert(publications).values(grant_id=grant_id, exchange_id=exchange_id,
            generation_id=row["generation_id"], session_id=row["session_id"], sequence=sequence,
            target=target, text=text[:16000], turn_id=exchange_id,
            status="ready", spoken=0, occurred_at=time.time()))
        session.execute(update(outbox).where(outbox.c.exchange_id == exchange_id).values(status="dispatched"))

    def validate_publication(self, session, exchange_id, grant_id, target, text):
        self.committed_decision(session, exchange_id, owned=True, current=False)
        record = session.execute(select(publications).where(publications.c.grant_id == grant_id,
            publications.c.exchange_id == exchange_id, publications.c.target == target)).mappings().first()
        grant = session.execute(select(delivery_grants).where(delivery_grants.c.id == grant_id)).mappings().first()
        newest = session.execute(select(publications.c.sequence).where(publications.c.target == target)
                                 .order_by(publications.c.sequence.desc()).limit(1)).scalar()
        if (record is None or grant is None or grant["status"] != "dispatched" or
                record["text"] != text[:16000] or record["sequence"] != newest):
            raise StateConflict("publication_missing_or_superseded")

    def _generation(self, session):
        if os.getpid() != self.pid:
            raise StateConflict("generation_inherited_across_process")
        row = session.execute(select(generations).where(generations.c.id == self.generation_id)).mappings().first()
        if row is None:
            session.execute(insert(generations).values(id=self.generation_id, host=self.host, pid=self.pid,
                            process_started=self.process_started, lease_until=time.time() + 60, status="active"))
        elif (row["status"] != "active" or row["pid"] != self.pid or row["host"] != self.host
              or row["process_started"] != self.process_started):
            raise StateConflict("generation_retired_or_not_owned")
        else:
            session.execute(update(generations).where(generations.c.id == self.generation_id)
                            .values(lease_until=time.time() + 60))

    def version(self, session, session_id: str) -> int:
        return session.execute(select(versions.c.version).where(versions.c.session_id == session_id)).scalar() or 0

    def begin(self, session, exchange_id: str, session_id: str, privacy_scope: str):
        self.require_transaction(session)
        self._generation(session)
        current = session.execute(select(versions.c.version).where(versions.c.session_id == session_id)).scalar()
        if current is None:
            current = 0
            session.execute(insert(versions).values(session_id=session_id, version=0))
        session.execute(insert(exchanges).values(exchange_id=exchange_id, generation_id=self.generation_id,
                        session_id=session_id, privacy_scope=privacy_scope, base_version=current, status="pending"))

    def owner(self, session, exchange_id: str):
        return session.execute(select(exchanges).where(exchanges.c.exchange_id == exchange_id)).mappings().first()

    def complete(self, session, exchange_id: str, session_id: str, privacy_scope: str, payload: dict) -> int:
        self.require_transaction(session)
        self._generation(session)
        row = self.owner(session, exchange_id)
        if row is None or row["generation_id"] != self.generation_id or row["status"] != "pending":
            raise StateConflict("exchange_not_pending_or_not_owned")
        if row["session_id"] != session_id or row["privacy_scope"] != privacy_scope:
            raise StateConflict("exchange_scope_mismatch")
        changed = session.execute(update(versions).where(versions.c.session_id == session_id,
                                  versions.c.version == row["base_version"])
                                  .values(version=row["base_version"] + 1))
        if changed.rowcount != 1:
            raise StateConflict("stale_state_version")
        new_version = row["base_version"] + 1
        session.execute(update(exchanges).where(exchanges.c.exchange_id == exchange_id).values(status="completed"))
        session.execute(insert(decision_commits).values(exchange_id=exchange_id))
        session.execute(insert(outbox).values(id=uuid4().hex, exchange_id=exchange_id,
                        generation_id=self.generation_id, session_id=session_id, state_version=new_version,
                        privacy_scope=privacy_scope, payload=json.dumps(payload, ensure_ascii=False),
                        status="internal_only" if payload.get("output_target") == "internal" else "awaiting_authorization",
                        created_at=time.time()))
        return new_version

    def fail(self, session, exchange_id: str, status: str) -> bool:
        self.require_transaction(session)
        row = self.owner(session, exchange_id)
        if row is None or row["generation_id"] != self.generation_id or row["status"] != "pending":
            return False
        self._generation(session)
        session.execute(update(exchanges).where(exchanges.c.exchange_id == exchange_id).values(status=status))
        return True

    def retire(self, session):
        self.require_transaction(session)
        self._generation(session)
        session.execute(update(generations).where(generations.c.id == self.generation_id)
                        .values(status="retired", lease_until=0))

    def recoverable(self, session, generation_id: str) -> bool:
        row = session.execute(select(generations).where(generations.c.id == generation_id)).mappings().first()
        if row is None or row["status"] == "recovered":
            return False
        if row["status"] == "retired":
            return True
        # A lease expiring during uncancellable inference is not evidence of death.
        if row["lease_until"] >= time.time() or row["host"] != self.host:
            return False
        try:
            process = psutil.Process(row["pid"])
            if process.create_time() == row["process_started"] and process.status() != psutil.STATUS_ZOMBIE:
                session.execute(update(generations).where(generations.c.id == generation_id)
                                .values(lease_until=time.time() + 60))
                return False
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            session.execute(update(generations).where(generations.c.id == generation_id)
                            .values(lease_until=time.time() + 60))
            return False  # Unverifiable ownership is not permission to recover it.
        return True

    def recover(self, session, generation_id: str) -> list[str]:
        self.require_transaction(session)
        if not self.recoverable(session, generation_id):
            return []
        pending = list(session.execute(select(exchanges.c.exchange_id).where(
            exchanges.c.generation_id == generation_id, exchanges.c.status == "pending")).scalars())
        session.execute(update(exchanges).where(exchanges.c.generation_id == generation_id,
                        exchanges.c.status == "pending").values(status="interrupted"))
        session.execute(update(outbox).where(outbox.c.generation_id == generation_id,
                        outbox.c.status.in_(("awaiting_authorization", "dispatched"))).values(status="cancelled_generation"))
        session.execute(update(delivery_grants).where(delivery_grants.c.decision_id.in_(
            select(outbox.c.exchange_id).where(outbox.c.generation_id == generation_id)),
            delivery_grants.c.status.not_in(("played", "cancelled", "failed", "expired"))).values(status="cancelled"))
        # A crash after the claim may have reached an adapter. Preserve the claim
        # forever as uncertain; recovery must never invoke or requeue it.
        session.execute(update(dispatch_attempts).where(dispatch_attempts.c.generation_id == generation_id,
                        dispatch_attempts.c.status == "started").values(status="uncertain", updated_at=time.time()))
        session.execute(update(publications).where(publications.c.generation_id == generation_id,
                        publications.c.status.in_(("ready", "completed"))).values(status="cancelled_generation"))
        session.execute(update(generations).where(generations.c.id == generation_id).values(status="recovered"))
        return pending
