import hashlib
from pathlib import Path
import pytest
from gamma.coding.acceptance_retention import retain_checks, rollback_checks


def fixture(tmp_path):
    project=tmp_path/'project';(project/'tests').mkdir(parents=True)
    workspace=tmp_path/'workspace';(workspace/'tests').mkdir(parents=True)
    target='tests/test_goal_owner_feature.py'
    (workspace/target).write_text('def test_one():\n assert True\n')
    return project,workspace,target,'work-'+'a'*32


def test_retention_survives_future_snapshot_and_never_overwrites(tmp_path):
    project,workspace,target,identifier=fixture(tmp_path)
    records=retain_checks(project,workspace,[target],identifier)
    retained=Path(records[0]['path']);assert retained.read_bytes()==(workspace/target).read_bytes()
    assert retained.name.startswith('test_verified_')
    again=retain_checks(project,workspace,[target],identifier)
    assert again[0]['created'] is False
    retained.write_text('owner changes\n')
    with pytest.raises(ValueError):retain_checks(project,workspace,[target],identifier)
    assert retained.read_text()=='owner changes\n'


def test_rollback_removes_only_unchanged_created_checks(tmp_path):
    project,workspace,target,identifier=fixture(tmp_path)
    records=retain_checks(project,workspace,[target],identifier)
    path=Path(records[0]['path']);path.write_text('owner changes\n')
    assert rollback_checks(records)==[str(path)]
    assert path.exists()
    path.write_bytes((workspace/target).read_bytes())
    assert rollback_checks(records)==[] and not path.exists()


def test_partial_failure_rolls_back_own_new_checks(tmp_path):
    project,workspace,target,identifier=fixture(tmp_path)
    with pytest.raises(ValueError):retain_checks(project,workspace,[target,'../private.py'],identifier)
    assert list((project/'tests').iterdir())==[]
