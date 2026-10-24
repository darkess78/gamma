"""Connection-owned playback receipts, separate from generation and synthesis."""
from __future__ import annotations

import os
import time
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import select, func

from ..config import settings
from ..memory.state_authority import playback_leases as leases, playback_receipts as receipts, playback_offers as offers
from .authority import DeliveryAuthority, DeliveryDenied
from .digest import fingerprint
from .protocol import next_status, validate_ack


class PlaybackLedger(DeliveryAuthority):
    def open(self, *, receiver, target):
        if os.getpid() != self.pid or target not in {"dashboard_monitor", "stream_public", "discord_call"}:
            raise DeliveryDenied("invalid_playback_receiver")
        lease_id = uuid4().hex
        with self._session() as session:
            session.execute(leases.insert().values(id=lease_id, generation=self.generation,
                receiver=receiver, target=target, expires=time.time() + 60, status="active"))
            session.commit()
        return lease_id

    def _lease(self, session, lease_id, receiver):
        row = session.execute(select(leases).where(leases.c.id == lease_id)).mappings().first()
        if (os.getpid() != self.pid or row is None or row["generation"] != self.generation
                or row["receiver"] != receiver or row["status"] != "active" or row["expires"] <= time.time()):
            raise DeliveryDenied("playback_lease_obsolete")
        return row

    def renew(self, lease_id, receiver):
        with self._session() as session:
            self._lease(session, lease_id, receiver)
            session.execute(leases.update().where(leases.c.id == lease_id).values(expires=time.time() + 60))
            session.commit()

    def offer(self, lease_id, receiver, event, *, target_epoch=0, presence=""):
        """Record an actual connection delivery, never a history replay."""
        grant = event["payload"].get("delivery_grant")
        if not isinstance(grant, str) or not grant:
            raise DeliveryDenied("delivery_grant_required")
        expected_url = f"{settings.shana_base_url}/v1/delivery/{grant}/audio"
        if event["payload"].get("audio_url") != expected_url or any(
                event["payload"].get(key) for key in ("audio_path", "audio_base64")):
            raise DeliveryDenied("audio_reference_not_bound_to_grant")
        _, _, audio_digest = self.read_audio(grant, target=event["target_policy"])
        with self._session() as session:
            lease = self._lease(session, lease_id, receiver)
            target = event["target_policy"]
            if (target != lease["target"] and not (lease["target"] == "dashboard_monitor" and target == "stream_public")):
                raise DeliveryDenied("playback_target_mismatch")
            if event.get("replay"):
                raise DeliveryDenied("playback_replay_forbidden")
            if session.execute(select(offers.c.event_id).where(
                    offers.c.lease_id == lease_id, offers.c.grant_id == grant)).first():
                raise DeliveryDenied("playback_grant_already_offered")
            pending = session.execute(select(func.count()).select_from(receipts).where(
                receipts.c.lease_id == lease_id, receipts.c.status.in_(("offered", "started")))).scalar_one()
            if pending >= 128:
                raise DeliveryDenied("playback_receiver_queue_full")
            event_id = event["event_id"]
            prior = session.execute(select(receipts.c.status).where(
                receipts.c.lease_id == lease_id, receipts.c.event_id == event_id)).scalar()
            if prior is not None:
                raise DeliveryDenied("playback_already_offered")
            expiry = time.time() + 30
            digest = fingerprint(event["payload"])
            session.execute(receipts.insert().values(lease_id=lease_id, event_id=event_id,
                digest=digest, expires=expiry, status="offered", updated_at=time.time()))
            session.execute(offers.insert().values(lease_id=lease_id, event_id=event_id, grant_id=grant,
                target=target, target_epoch=target_epoch, presence=presence, permit_until=0))
            session.commit()
        return dict(schema_version=1, lease_id=lease_id, event_id=event_id, expires_at=expiry,
            digest=digest, audio_digest=audio_digest, grant_id=grant)

    def prepare(self, lease_id, receiver, message, *, eligible, target_epoch, presence):
        if not isinstance(message, dict) or set(message) != {"type", "schema_version", "lease_id", "event_id"} or message["type"] != "playback_prepare":
            raise ValueError("invalid playback prepare envelope")
        supplied, event_id, _ = validate_ack({**message, "type": "playback_ack", "status": "started"})
        if supplied != lease_id:
            raise DeliveryDenied("playback_receiver_mismatch")
        with self._session() as session:
            self._lease(session, lease_id, receiver)
            row = session.execute(select(receipts).where(receipts.c.lease_id == lease_id,
                receipts.c.event_id == event_id)).mappings().first()
            offer = session.execute(select(offers).where(offers.c.lease_id == lease_id,
                offers.c.event_id == event_id)).mappings().first()
            if row is None or offer is None or row["status"] != "offered" or row["expires"] <= time.time():
                raise DeliveryDenied("playback_offer_inactive")
            if (not eligible(offer["target"]) or target_epoch(offer["target"]) != offer["target_epoch"]
                    or presence != offer["presence"]):
                raise DeliveryDenied("playback_context_changed")
            if not offer["grant_id"]:
                raise DeliveryDenied("delivery_grant_required")
            self._audio_record(session, offer["grant_id"], offer["target"])
            if offer["permit_until"]:
                raise DeliveryDenied("playback_already_permitted")
            expires = min(time.time() + 5, row["expires"])
            session.execute(offers.update().where(offers.c.lease_id == lease_id,
                offers.c.event_id == event_id).values(permit_until=expires))
            session.commit()
        return dict(type="playback_permit", lease_id=lease_id, event_id=event_id, expires_at=expires)

    def acknowledge(self, lease_id, receiver, message):
        supplied_lease, event_id, requested = validate_ack(message)
        if supplied_lease != lease_id:
            raise DeliveryDenied("playback_receiver_mismatch")
        with self._session() as session:
            self._lease(session, lease_id, receiver)
            row = session.execute(select(receipts).where(receipts.c.lease_id == lease_id,
                receipts.c.event_id == event_id)).mappings().first()
            if row is None or row["expires"] <= time.time():
                raise DeliveryDenied("playback_offer_expired_or_unknown")
            if requested in {"started", "completed"}:
                offer = session.execute(select(offers).where(offers.c.lease_id == lease_id,
                    offers.c.event_id == event_id)).mappings().first()
                if offer is None or (requested == "started" and row["status"] == "offered" and offer["permit_until"] <= time.time()):
                    raise DeliveryDenied("playback_start_not_permitted")
                if not offer["grant_id"]:
                    raise DeliveryDenied("delivery_grant_required")
                self._audio_record(session, offer["grant_id"], offer["target"])
            status = next_status(row["status"], requested)
            # Once playback starts, completion has a bounded five-minute window.
            expiry = time.time() + 300 if row["status"] == "offered" and status == "started" else row["expires"]
            session.execute(receipts.update().where(receipts.c.lease_id == lease_id,
                receipts.c.event_id == event_id).values(status=status, expires=expiry, updated_at=time.time()))
            session.commit()
        return dict(event_id=event_id, status=status)

    def revoke_obsolete(self, lease_id, receiver, *, eligible, target_epoch, presence):
        """Revoke queued and active receipts; work is bounded by offer admission."""
        revoked = []
        with self._session() as session:
            self._lease(session, lease_id, receiver)
            rows = session.execute(select(receipts.c.event_id, receipts.c.expires,
                offers.c.grant_id, offers.c.target, offers.c.target_epoch, offers.c.presence)
                .select_from(receipts.join(offers, (receipts.c.lease_id == offers.c.lease_id)
                    & (receipts.c.event_id == offers.c.event_id)))
                .where(receipts.c.lease_id == lease_id, receipts.c.status.in_(("offered", "started")))
                .limit(128)).mappings().all()
            for row in rows:
                invalid = (not row["grant_id"] or row["expires"] <= time.time() or not eligible(row["target"])
                    or target_epoch(row["target"]) != row["target_epoch"] or presence != row["presence"])
                if not invalid and row["grant_id"]:
                    try:
                        self._audio_record(session, row["grant_id"], row["target"])
                    except DeliveryDenied:
                        invalid = True
                if invalid:
                    session.execute(receipts.update().where(receipts.c.lease_id == lease_id,
                        receipts.c.event_id == row["event_id"]).values(status="interrupted", updated_at=time.time()))
                    revoked.append(row["event_id"])
            session.commit()
        return revoked

    def close(self, lease_id, receiver):
        with self._session() as session:
            changed = session.execute(leases.update().where(leases.c.id == lease_id,
                leases.c.receiver == receiver, leases.c.generation == self.generation).values(status="closed", expires=0))
            if changed.rowcount:
                session.execute(receipts.update().where(receipts.c.lease_id == lease_id,
                    receipts.c.status.in_(("offered", "started"))).values(status="interrupted", updated_at=time.time()))
            session.commit()


@lru_cache(maxsize=4)
def _ledger(database_url):
    return PlaybackLedger(database_url=database_url)


def get_playback_ledger():
    return _ledger(settings.database_url)
