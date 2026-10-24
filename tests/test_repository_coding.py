import ast
import json
from pathlib import Path

import pytest
from gamma.coding.repository import create_job, job_path, list_jobs, _pure_source, status_summary, submission_lock


def test_jobs_are_editable_and_bounded(tmp_path):
    record=create_job(tmp_path,'Make Discord bot status accurate','qwen3.8:27b')
    assert list_jobs(tmp_path)[0]['id']==record['id']
    assert job_path(tmp_path,record['id'])/'state.json'
    assert record['model_weights_changed'] is False
    for identifier in ['../secret','project-abc','/root']:
        with pytest.raises(ValueError): job_path(tmp_path,identifier)


@pytest.mark.parametrize('source', ['import os', 'open("secret")', 'def f():\n return eval("1")',
                                  'def f(x):\n return x.__class__', 'global dangerous'])
def test_pure_helper_cannot_gain_tools(source):
    with pytest.raises(ValueError): _pure_source(source)


def test_symlink_storage_refused(tmp_path):
    (tmp_path/'outside').mkdir()
    (tmp_path/'link').symlink_to(tmp_path/'outside',target_is_directory=True)
    with pytest.raises(ValueError): create_job(tmp_path/'link','Make Discord status accurate now','qwen3.8:27b')


def test_unrelated_goal_not_claimed_completed(tmp_path):
    with pytest.raises(ValueError): create_job(tmp_path,'Build me a shopping website','qwen3.8:27b')


def test_queue_submissions_are_serialized(tmp_path):
    with submission_lock(tmp_path):
        with pytest.raises(ValueError):
            with submission_lock(tmp_path): pass
    with submission_lock(tmp_path): pass


def test_installed_helper_used_without_import_cache(tmp_path):
    target=tmp_path/'src/gamma/integrations/discord/bot_status.py'
    target.parent.mkdir(parents=True)
    for label in ['first installed source','second installed source']:
        target.write_text('def summarize(process,state,now):\n return '+repr({'connected':False,'voice':'unknown','label':label}))
        result,digest=status_summary({}, {}, 100, tmp_path)
        assert result['label']==label
        assert len(digest)==64


def test_unsafe_installed_helper_is_not_executed(tmp_path):
    target=tmp_path/'src/gamma/integrations/discord/bot_status.py'
    target.parent.mkdir(parents=True)
    target.write_text('open("somewhere", "w")')
    result,digest=status_summary({}, {}, 100, tmp_path)
    assert result['connected'] is False and digest is None
