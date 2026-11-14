"""The finite replacement verifier is inert by default and has strict local choices."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


@pytest.fixture
def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/verify_replacement_continuity.py'
    spec = importlib.util.spec_from_file_location('replacement_verifier', path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def test_default_is_inert_without_provider_filesystem_or_host_operations(module, monkeypatch, capsys):
    def forbidden(*_args, **_kwargs): raise AssertionError('default crossed the effect boundary')
    for key in ('prepare', 'run', 'child', 'voice_sample', 'verify_prepared'): monkeypatch.setattr(module, key, forbidden)
    module.main([])
    assert json.loads(capsys.readouterr().out) == {'status':'prepared_only','model_calls':0,'voice_calls':0,'production_changes':False}


@pytest.mark.parametrize('arguments', [
    ['--model','gemini'], ['--model','unknown'], ['--endpoint','https://example.invalid'],
    ['--voice-profile','new_voice'], ['--model','openai'],
])
def test_parser_rejects_unknown_remote_or_new_components(module, arguments):
    with pytest.raises(SystemExit) as error: module.parser().parse_args(arguments)
    assert error.value.code == 2


def test_known_component_limits_are_fixed(module):
    assert module.PRIMARY == 'qwen3.8:27b' and module.REPLACEMENT == 'gemma4:e4b'
    assert module.MAX_GENERATE == 16
    assert module.PROFILES == ('qwen_shana_dataset_xvec_soft','qwen_shana_dataset_ref_soft')
    assert len(module.REFERENCE_SHA) == 64


def test_durable_comparison_includes_later_exchanges_and_unfinished_records(module, tmp_path):
    source = tmp_path / 'state'; source.mkdir()
    with sqlite3.connect(source / 'coordination.sqlite3') as db:
        db.execute('CREATE TABLE records(kind TEXT,id TEXT,body TEXT)')
        for kind, key, body in [('personal_identity','identity',{'identity_id':'authored'}),
            ('personal_record','unfinished',{'kind':'commitment','status':'active','text':'unfinished blueprint'}),
            ('turn','later',{'status':'completed','text':'observatory'}),
            ('brain','state',{'owner':'ephemeral'})]:
            db.execute('INSERT INTO records VALUES(?,?,?)',(kind,key,json.dumps(body)))
    before = module.durable(source)
    assert set(before) == {'personal_identity/identity','personal_record/unfinished','turn/later'}
    with sqlite3.connect(source / 'coordination.sqlite3') as db:
        db.execute('UPDATE records SET body=? WHERE kind=?',(json.dumps({'owner':'new'}),'brain'))
    assert module.durable(source) == before
    with sqlite3.connect(source / 'coordination.sqlite3') as db:
        db.execute('UPDATE records SET body=? WHERE id=?',(json.dumps({'status':'resolved'}),'unfinished'))
    assert module.durable(source) != before


def test_count_check_requires_exact_two_and_rejects_twelve(module):
    assert module.answer_checks('2',{'count':'2'})['count']
    assert module.answer_checks('Two.',{'count':'2'})['count']
    assert not module.answer_checks('12',{'count':'2'})['count']
    assert not module.answer_checks('There are 12 items.',{'count':'2'})['count']


def test_unfinished_check_rejects_completed_blueprint_and_requires_condition(module):
    assert module.answer_checks('The blueprint is unfinished.',{'unfinished':'blueprint'})['unfinished']
    assert module.answer_checks('The blueprint is not yet completed.',{'unfinished':'blueprint'})['unfinished']
    assert not module.answer_checks('The blueprint review is completed.',{'unfinished':'blueprint'})['unfinished']
    assert not module.answer_checks('The blueprint is finished; the map is pending.',{'unfinished':'blueprint'})['unfinished']
    assert not module.answer_checks('The blueprint.',{'unfinished':'blueprint'})['unfinished']


def test_primary_role_recognizes_actual_oneof_schema_and_rejects_safety_format(module):
    from gamma.coordination.turn_schema import private_turn_schema
    for compact in (False, True):
        assert module.primary_turn_format(private_turn_schema(compact=compact))
    assert not module.primary_turn_format('json')
    assert not module.primary_turn_format({'properties':{'safe':{},'reason':{}}})
    assert not module.primary_turn_format({'oneOf':[{}]*9})
