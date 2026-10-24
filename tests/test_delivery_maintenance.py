import asyncio
import hashlib
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

from sqlalchemy import select, update, inspect
from sqlmodel import Session

from gamma.delivery.maintenance import DeliveryMaintenance
from gamma.delivery.scan import ArtifactScan
from gamma.memory.continuity import ContinuityService
from gamma.memory.lifecycle import StateLifecycle
from gamma.memory.state_authority import delivery_grants, delivery_artifacts
from gamma.memory.state_authority import playback_receipts
from gamma.delivery.playback import PlaybackLedger
import pytest
from state_fixtures import commit_response
from gamma.schemas.response import AssistantResponse


def grant_with_audio(authority, source):
    response = AssistantResponse(spoken_text="Fictional receipt", audio_path=str(source), audio_content_type="audio/wav")
    commit_response(authority, response)
    context = dict(target="dashboard_monitor", audience="private", policy="fixture")
    grant = authority.issue(response, **context)
    authority.dispatch(grant, response, **context)
    return grant


def test_shared_snapshot_survives_until_last_reference_expires(tmp_path):
    maintenance = DeliveryMaintenance(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    source = tmp_path / "source.wav"
    source.write_bytes(b"fictional shared audio")
    old, live = [grant_with_audio(maintenance, source) for _ in range(2)]
    now = time.time()
    with Session(maintenance.engine) as session:
        session.execute(update(delivery_grants).where(delivery_grants.c.id == old).values(expires=now - 90000))
        snapshot = Path(session.execute(select(delivery_artifacts.c.path).limit(1)).scalar_one())
        session.commit()
    os.utime(snapshot, (now - 90000, now - 90000))
    assert maintenance.sweep(now=now)["grants"] == 1
    assert snapshot.read_bytes() == b"fictional shared audio"
    assert maintenance.read_audio(live)[0] == b"fictional shared audio"
    with Session(maintenance.engine) as session:
        session.execute(update(delivery_grants).where(delivery_grants.c.id == live).values(expires=now - 90000))
        session.commit()
    counts = [maintenance.sweep(now=now) for _ in range(3)]
    assert sum(item["grants"] for item in counts) == 1
    assert sum(item["artifacts"] for item in counts) == 1
    assert not snapshot.exists() and source.exists()
    maintenance.close()


def test_scanner_and_cleanup_preserve_other_files_links_and_fresh_artifacts(tmp_path):
    maintenance = DeliveryMaintenance(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    root = maintenance.artifact_root
    root.mkdir()
    now = time.time()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"preserve outside")
    (root / "operator-note.txt").write_text("preserve note")
    link = root / ("f" * 64 + ".bin")
    link.symlink_to(outside)
    fresh = root / ("e" * 64 + ".bin")
    fresh.write_bytes(b"fresh")
    old = root / ("d" * 64 + ".bin")
    old.write_bytes(b"old")
    os.utime(old, (now - 90000, now - 90000))
    counts = [maintenance.sweep(now=now, batch_size=2) for _ in range(6)]
    assert all(item["scanned"] <= 2 for item in counts)
    assert sum(item["artifacts"] for item in counts) == 1
    assert outside.read_bytes() == b"preserve outside" and link.is_symlink()
    assert fresh.exists() and (root / "operator-note.txt").exists() and not old.exists()
    maintenance.close()


def test_retention_bounds_database_mutations(tmp_path):
    maintenance = DeliveryMaintenance(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    response = AssistantResponse(spoken_text="Fictional text")
    commit_response(maintenance, response)
    for _ in range(7):
        maintenance.issue(response, target="dashboard_monitor", audience="private", policy="fixture")
    now = time.time()
    with Session(maintenance.engine) as session:
        session.execute(update(delivery_grants).values(expires=now - 90000))
        session.commit()
    counts = [maintenance.sweep(now=now, batch_size=2) for _ in range(4)]
    assert [item["grants"] for item in counts] == [2, 2, 2, 1]
    maintenance.close()


def test_core_lifecycle_runs_retention_without_conversation_request(tmp_path):
    continuity = ContinuityService(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    authority = DeliveryMaintenance(database_url=str(continuity._engine.url))
    authority.issue(commit_response(authority, AssistantResponse(spoken_text="Old fixture")), target="dashboard_monitor", audience="private", policy="fixture")
    with Session(authority.engine) as session:
        session.execute(update(delivery_grants).values(expires=time.time() - 90000))
        session.commit()
    async def exercise():
        lifecycle = StateLifecycle(continuity)
        await lifecycle.start()
        await lifecycle.stop()
    asyncio.run(exercise())
    with Session(authority.engine) as session:
        assert session.execute(select(delivery_grants.c.id)).first() is None
    authority.close()


def test_active_receipt_preserves_expired_grant_evidence(tmp_path):
    ledger = PlaybackLedger(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    maintenance = DeliveryMaintenance(database_url=str(ledger.engine.url))
    source = tmp_path / "source.wav"
    source.write_bytes(b"fictional active audio")
    grant = grant_with_audio(ledger, source)
    lease = ledger.open(receiver="active", target="dashboard_monitor")
    from gamma.config import settings
    ledger.offer(lease, "active", dict(event_id="active", target_policy="dashboard_monitor",
        payload={"delivery_grant": grant, "audio_url": f"{settings.shana_base_url}/v1/delivery/{grant}/audio"}))
    now = time.time()
    with Session(ledger.engine) as session:
        session.execute(update(delivery_grants).where(delivery_grants.c.id == grant).values(expires=now - 90000))
        session.commit()
    assert maintenance.sweep(now=now)["grants"] == 0
    with Session(ledger.engine) as session:
        assert session.execute(select(delivery_artifacts.c.grant_id)).scalar_one() == grant
    ledger.close(lease, "active")
    assert maintenance.sweep(now=now)["grants"] == 0
    with Session(ledger.engine) as session:
        session.execute(update(playback_receipts).values(updated_at=now - 90000))
        session.commit()
    assert maintenance.sweep(now=now)["grants"] == 1
    maintenance.close()


def test_cleanup_cannot_remove_snapshot_before_dispatch_binding(tmp_path, monkeypatch):
    from gamma.delivery import authority as module
    authority = DeliveryMaintenance(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    maintenance = DeliveryMaintenance(database_url=str(authority.engine.url))
    source = tmp_path / "source.wav"
    source.write_bytes(b"snapshot being rebound")
    saved, release, sweeping = threading.Event(), threading.Event(), threading.Event()
    original = module.snapshot
    now = time.time()
    def held_snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        os.utime(result[0], (now - 90000, now - 90000))
        saved.set()
        assert release.wait(3)
        return result
    def sweep():
        sweeping.set()
        return maintenance.sweep(now=now)
    monkeypatch.setattr(module, "snapshot", held_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch = pool.submit(grant_with_audio, authority, source)
        try:
            assert saved.wait(2)
            cleanup = pool.submit(sweep)
            assert sweeping.wait(2)
            with pytest.raises(TimeoutError):
                cleanup.result(timeout=0.05)
        finally:
            release.set()
        grant = dispatch.result(timeout=3)
        assert cleanup.result(timeout=3)["artifacts"] == 0
    assert authority.read_audio(grant)[0] == b"snapshot being rebound"
    maintenance.close()
    authority.close()


def test_cleanup_reports_file_errors_and_preserves_failed_target(tmp_path, monkeypatch):
    maintenance = DeliveryMaintenance(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    maintenance.artifact_root.mkdir()
    path = maintenance.artifact_root / ("d" * 64 + ".bin")
    path.write_bytes(b"fixture")
    now = time.time()
    os.utime(path, (now - 90000, now - 90000))
    original = Path.unlink
    def blocked(self, *args, **kwargs):
        if self == path:
            raise PermissionError("fixture")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", blocked)
    counts = maintenance.sweep(now=now)
    assert counts["errors"] == 1 and counts["artifacts"] == 0
    assert path.read_bytes() == b"fixture"
    maintenance.close()


def test_missing_retention_index_is_migrated_with_verified_backup(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'state.db'}"
    maintenance = DeliveryMaintenance(database_url=database_url)
    with maintenance.engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_delivery_grant_expiry")
    prior = set((tmp_path / "backups").glob("*.db"))
    migrated = DeliveryMaintenance(database_url=database_url)
    assert "ix_delivery_grant_expiry" in {item["name"] for item in inspect(migrated.engine).get_indexes("gamma_delivery_grant")}
    backups = set((tmp_path / "backups").glob("*.db")) - prior
    assert len(backups) == 1
    import sqlite3
    with sqlite3.connect(next(iter(backups))) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("select name from sqlite_master where name='ix_delivery_grant_expiry'").fetchone() is None
    migrated.close()
    maintenance.close()
