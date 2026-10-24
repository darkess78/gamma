import os
import subprocess
import sys
from uuid import uuid4

import pytest

from gamma.brain.image_maintenance import ImageMaintenance
from gamma.resources.admission import Admission


def old_snapshot(root):
    root.mkdir(exist_ok=True)
    path = root / (uuid4().hex + ".bin")
    path.write_bytes(b"fixture")
    os.utime(path, (1, 1))
    return path


def test_only_expired_owned_regular_files_are_removed(tmp_path):
    root = tmp_path / "images"
    old = old_snapshot(root)
    recent = old_snapshot(root)
    os.utime(recent, (10000, 10000))
    outside = tmp_path / "outside"
    outside.write_bytes(b"protected")
    link = root / (uuid4().hex + ".bin")
    link.symlink_to(outside)
    unknown = root / "user-image.png"
    unknown.write_bytes(b"protected")
    directory = root / (uuid4().hex + ".bin")
    directory.mkdir()
    nested = old_snapshot(directory)
    maintenance = ImageMaintenance(root, Admission(tmp_path / "leases.db"))
    try:
        counts = maintenance.sweep(now=10000, retention_seconds=3600)
        assert counts["artifacts"] == 1 and counts["bytes"] == 7
        assert not old.exists()
        assert recent.exists() and link.is_symlink() and nested.exists()
        assert unknown.read_bytes() == outside.read_bytes() == b"protected"
    finally:
        maintenance.close()


@pytest.mark.parametrize("state", ["active", "uncertain"])
def test_occupied_inference_protects_every_snapshot_regardless_of_age(tmp_path, state):
    root = tmp_path / "images"
    old = old_snapshot(root)
    admission = Admission(tmp_path / "leases.db")
    token = admission.acquire("brain-inference")
    if state == "uncertain":
        admission.uncertain("brain-inference", token)
    maintenance = ImageMaintenance(root, admission)
    try:
        assert maintenance.sweep(now=100000)["deferred"] == 1
        assert old.exists()
        assert admission.status("brain-inference")["token"] == token
        admission.release("brain-inference", token)
        assert maintenance.sweep(now=100000)["artifacts"] == 1
    finally:
        maintenance.close()


def test_incremental_scan_progresses_past_retained_entries(tmp_path):
    root = tmp_path / "images"
    retained = [old_snapshot(root) for _ in range(2)]
    for path in retained:
        os.utime(path, (100000, 100000))
    files = [old_snapshot(root) for _ in range(7)]
    maintenance = ImageMaintenance(root, Admission(tmp_path / "leases.db"))
    try:
        removed = 0
        for _ in range(10):
            counts = maintenance.sweep(now=100000, batch_size=2)
            assert counts["scanned"] <= 2
            removed += counts["artifacts"]
        assert removed == 7 and all(not p.exists() for p in files)
        assert all(p.exists() for p in retained)
    finally:
        maintenance.close()


def test_guard_excludes_cross_process_admission_without_durable_lease(tmp_path):
    admission = Admission(tmp_path / "leases.db")
    code = "from gamma.resources.admission import Admission; import sys; a=Admission(sys.argv[1]); print('ready', flush=True); input(); print(a.acquire('brain-inference'), flush=True)"
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "leases.db")],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with admission.idle_guard("brain-inference") as idle:
            assert idle
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(input="go\n", timeout=0.2)
            assert admission.status("brain-inference") is None
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert admission.status("brain-inference")["token"] == stdout.strip()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_maintenance_process_crash_cannot_leave_a_lease(tmp_path):
    admission = Admission(tmp_path / "leases.db")
    code = "from gamma.resources.admission import Admission; import sys,os\na=Admission(sys.argv[1])\nwith a.idle_guard('brain-inference') as idle:\n assert idle\n os._exit(0)\n"
    subprocess.run([sys.executable, "-c", code, str(tmp_path / "leases.db")], check=True, timeout=5)
    assert admission.status("brain-inference") is None
    token = admission.acquire("brain-inference")
    admission.release("brain-inference", token)


def test_core_startup_runs_image_retention_and_shutdown_closes_scan(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from gamma.config import settings
    from gamma.memory.lifecycle import StateLifecycle
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = tmp_path / "runtime/model-images"
    root.mkdir(parents=True)
    old = old_snapshot(root)
    lifecycle = StateLifecycle(SimpleNamespace(reconcile_expired_generations=lambda: 0))
    lifecycle._delivery = SimpleNamespace(sweep=lambda: dict(receipts=0, grants=0, leases=0, artifacts=0, errors=0), close=lambda: None)
    async def run():
        await lifecycle.start()
        try:
            assert not old.exists()
        finally:
            await lifecycle.stop()
        assert lifecycle._images.scan._iterator is None
    asyncio.run(run())
