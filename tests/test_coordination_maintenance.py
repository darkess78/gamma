"""Read actual backup/restore state and preserve later accepted experience."""
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from gamma.coordination.maintenance import Maintenance
from gamma.coordination.resource_policy import storage_admitted
from gamma.coordination.store import Store
from test_coordination_core import make_core


def test_capability_startup_failure_releases_the_claimed_owner(tmp_path):
    from fastapi.testclient import TestClient
    from gamma.coordination.app import build_app
    from gamma.coding.durable_practice import PracticeRunner
    from gamma.schemas.response import AssistantResponse
    claimed = []
    def broken(core):
        claimed.append(core)
        raise RuntimeError('controlled capability startup failure')
    app = build_app(tmp_path, bearer_token='test-owner',
        draft_factory=lambda: lambda *_: AssistantResponse(display_text='Here.'),
        runner_factory=lambda store: PracticeRunner(store, propose=lambda *_:{}, check=lambda *_:{}),
        shared_activity_factory=broken, run_background=False)
    try:
        with pytest.raises(RuntimeError, match='controlled capability'):
            with TestClient(app):
                pass
        assert Store(tmp_path).read('brain', 'state')['owner'] == ''
    finally:
        if claimed and claimed[0].store.read('brain', 'state')['owner']:
            claimed[0].close()


def test_actual_live_snapshot_restore_and_later_experience(tmp_path):
    core = make_core(tmp_path)
    try:
        core.input('Remember we are comparing OSRS activities.', 'monitor-private', 'before')
        service = Maintenance(core)
        backup = service.backup('one')
        assert backup['status'] == 'verified_snapshot'
        assert service.backup('one') == backup
        core.input('A later accepted experience must stay here.', 'monitor-private', 'after')
        restored = service.rehearse(backup['id'])
        assert restored['ok'] and not restored['activated'] and not restored['current_state_overwritten']
        copies = list((tmp_path / 'recovery').glob('restore-*'))
        assert len(copies) == 1
        copy = Store(copies[0])
        assert copy.read('turn', 'before')['text'] == 'Remember we are comparing OSRS activities.'
        assert copy.read('turn', 'after') is None
        assert core.store.read('turn', 'after')['text'] == 'A later accepted experience must stay here.'
        assert core.store.read('brain', 'state')['owner'] == core.token[1]
        assert not list((tmp_path / 'recovery').glob('*-staging'))
        assert service.check()['ok']
    finally:
        core.close()


def test_low_storage_rejects_backup_before_creating_a_copy(tmp_path):
    core = make_core(tmp_path)
    try:
        service = Maintenance(core)
        disk = type('Disk', (), {'free': 1})()
        with patch('gamma.coordination.maintenance.shutil.disk_usage', return_value=disk):
            with pytest.raises(ValueError, match='insufficient_space'):
                service.backup('no-space')
        assert not service.root.exists()
        assert core.store.read('maintenance_backup', 'no-space') is None
    finally:
        core.close()


def test_tampered_backup_cannot_be_restored(tmp_path):
    core = make_core(tmp_path)
    try:
        service = Maintenance(core)
        backup = service.backup('snapshot')
        path = service.root / backup['id'] / 'coordination.sqlite3'
        with path.open('ab') as stream:
            stream.write(b'tamper')
        with pytest.raises(ValueError, match='hash_mismatch'):
            service.rehearse(backup['id'])
        assert not list(service.root.glob('restore-*'))
    finally:
        core.close()


@pytest.mark.parametrize('value', [True, False, -1, 1.5, None, '1'])
def test_byte_accounting_rejects_non_counts(value):
    with pytest.raises(ValueError):
        storage_admitted(value, 0, 0)


def test_byte_budget_exact_boundary():
    assert storage_admitted(100, 80, 20)
    assert not storage_admitted(99, 80, 20)
