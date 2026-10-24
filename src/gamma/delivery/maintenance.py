"""Bounded retention for delivery-owned records and snapshots only."""
import time

from sqlalchemy import select, or_, exists

from ..memory.state_authority import (delivery_grants as grants, delivery_artifacts as artifacts, delivery_payloads as payloads,
    playback_leases as leases, playback_receipts as receipts, playback_offers as offers)
from ..supervisor.lifecycle_lock import lifecycle_lock
from .authority import DeliveryAuthority
from .scan import ArtifactScan


class DeliveryMaintenance(DeliveryAuthority):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scan = ArtifactScan()

    def sweep(self, *, now=None, retention_seconds=86400, batch_size=256):
        if not 1 <= batch_size <= 4096 or retention_seconds < 3600:
            raise ValueError("invalid delivery retention bounds")
        now = time.time() if now is None else now
        cutoff = now - retention_seconds
        counts = dict(receipts=0, grants=0, leases=0, artifacts=0, bytes=0, scanned=0, errors=0)
        if self.artifact_root.is_symlink():
            raise ValueError("delivery artifact root must not be a symlink")
        with lifecycle_lock(self.artifact_root / ".snapshot.lock"), self._session() as session:
            expired = session.execute(select(receipts.c.lease_id, receipts.c.event_id).where(
                or_(receipts.c.expires < cutoff,
                    (receipts.c.status.in_(("completed", "failed", "interrupted"))) & (receipts.c.updated_at < cutoff)))
                .order_by(receipts.c.expires).limit(batch_size)).all()
            for lease_id, event_id in expired:
                session.execute(offers.delete().where(offers.c.lease_id == lease_id, offers.c.event_id == event_id))
                session.execute(receipts.delete().where(receipts.c.lease_id == lease_id, receipts.c.event_id == event_id))
            counts["receipts"] = len(expired)

            # Retain grants for every retained receipt, including recent terminal evidence.
            active = exists(select(1).select_from(offers.join(receipts,
                (offers.c.lease_id == receipts.c.lease_id) & (offers.c.event_id == receipts.c.event_id)))
                .where(offers.c.grant_id == grants.c.id))
            expired_grants = list(session.execute(select(grants.c.id).where(grants.c.expires < cutoff, ~active)
                .order_by(grants.c.expires).limit(batch_size)).scalars())
            if expired_grants:
                session.execute(artifacts.delete().where(artifacts.c.grant_id.in_(expired_grants)))
                session.execute(payloads.delete().where(payloads.c.grant_id.in_(expired_grants)))
                # Dispatch claims contain identifiers and status, not output text.
                # Retain them with the decision journal to prevent uncertain retry.
                session.execute(grants.delete().where(grants.c.id.in_(expired_grants)))
            counts["grants"] = len(expired_grants)

            has_receipts = exists(select(1).where(receipts.c.lease_id == leases.c.id))
            expired_leases = list(session.execute(select(leases.c.id).where(leases.c.expires < cutoff, ~has_receipts)
                .order_by(leases.c.expires).limit(batch_size)).scalars())
            if expired_leases:
                session.execute(offers.delete().where(offers.c.lease_id.in_(expired_leases)))
                session.execute(leases.delete().where(leases.c.id.in_(expired_leases)))
            counts["leases"] = len(expired_leases)

            paths, counts["scanned"] = self.scan.next_batch(self.artifact_root, limit=batch_size)
            for path in paths:
                try:
                    # Recheck the exact entry immediately before deletion. Never recurse.
                    if path.is_symlink() or path.resolve().parent != self.artifact_root.resolve():
                        continue
                    info = path.stat()
                    if info.st_mtime >= cutoff:
                        continue
                    referenced = session.execute(select(artifacts.c.grant_id).where(
                        artifacts.c.path == str(path.resolve())).limit(1)).first()
                    if referenced is not None:
                        continue
                    path.unlink()
                    counts["artifacts"] += 1
                    counts["bytes"] += info.st_size
                except OSError:
                    counts["errors"] += 1
            session.commit()
        return counts

    def close(self):
        self.scan.close()
        self.engine.dispose()
