import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from gamma.coding import autonomous_store as store
from gamma.coding.autonomous import RepositoryAgent
from gamma.coding.project_policy import allowed_source, inventory, read_sources, validate_edits
from gamma.coding.project_transaction import apply_verified_changes
from gamma.coding.project_verification import acceptance_source, snapshot


def test_source_scope_and_secret_exclusion(tmp_path):
    source=tmp_path/'src/gamma/memory/example.py';source.parent.mkdir(parents=True);source.write_text('x=1\n')
    (tmp_path/'.env').write_text('private')
    for path in ['../secret','src/gamma/config.py','src/gamma/dashboard/auth.py','src/gamma/coding/autonomous.py','src/gamma/safety/filters.py']:
        assert not allowed_source(path)
    assert allowed_source('src/gamma/coding/lessons.py')
    assert [r['path'] for r in inventory(tmp_path)]==['src/gamma/memory/example.py']
    snap=tmp_path/'snapshot'; snapshot(tmp_path,snap)
    assert not (snap/'.env').exists()
    assert read_sources(tmp_path,['src/gamma/memory/example.py'])=={'src/gamma/memory/example.py':'x=1\n'}


def test_scope_does_not_follow_symlink_parent(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();(outside/'bad.py').write_text('x=1')
    parent=tmp_path/'src/gamma';parent.mkdir(parents=True);(parent/'memory').symlink_to(outside,target_is_directory=True)
    assert inventory(tmp_path)==[]
    with pytest.raises(ValueError):read_sources(tmp_path,['src/gamma/memory/bad.py'])


@pytest.mark.parametrize('source',['import pytest\npytest.main=lambda:0','import os\nos._exit(0)','import builtins\nbuiltins.__import__=lambda:None','eval("1")'])
def test_candidate_cannot_bypass_verification(source):
    with pytest.raises(ValueError):validate_edits({'src/gamma/memory/example.py':source},['src/gamma/memory/example.py'])


def test_acceptance_requires_real_assertions():
    for source in ['x=1','def test_nothing():\n pass','import pytest\n@pytest.mark.skip\ndef test_x():\n assert False']:
        with pytest.raises(ValueError):acceptance_source(source)
    acceptance_source('def test_behavior():\n assert 1==1')


def test_new_file_transaction_and_no_replay(tmp_path):
    root=tmp_path/'project';(root/'src/gamma/memory').mkdir(parents=True)
    relative='src/gamma/memory/example.py';receipt=tmp_path/'receipt'
    result=apply_verified_changes(root,{relative:'x=1\n'},{relative:None},receipt,
                                 lambda:{'passed':True,'evidence':{'tests':1}})
    assert result['passed'] and (root/relative).read_text()=='x=1\n'
    with pytest.raises(ValueError):apply_verified_changes(root,{relative:'x=1\n'},{relative:None},receipt,lambda:True)


def test_multi_file_rollback_preserves_concurrent_edit(tmp_path):
    root=tmp_path/'project';parent=root/'src/gamma/memory';parent.mkdir(parents=True)
    a,b='src/gamma/memory/a.py','src/gamma/memory/b.py'
    for relative in [a,b]:(root/relative).write_text('old\n')
    digest=hashlib.sha256(b'old\n').hexdigest()
    def verify():
        (root/b).write_text('user edit\n');return {'passed':False,'evidence':{'failure':True}}
    result=apply_verified_changes(root,{a:'new\n',b:'new\n'},{a:digest,b:digest},tmp_path/'receipt',verify)
    assert not result['passed'] and result['status']=='rollback_conflict'
    assert (root/a).read_text()=='old\n' and (root/b).read_text()=='user edit\n'


def test_store_preserves_controls_and_bounds(tmp_path):
    record=store.create_job(tmp_path,'Improve repository behavior with tests')
    with pytest.raises(ValueError):store.create_job(tmp_path,'Another competing owner goal')
    store.control_job(tmp_path,record['id'],'pause')
    assert json.loads((store.job_path(tmp_path,record['id'])/'control.json').read_text())['action']=='pause'
    assert store.read_job(tmp_path,record['id'])['status']=='queued'
    with pytest.raises(ValueError):store.job_path(tmp_path,'../private')


def test_real_pipeline_requires_frozen_checks_and_installed_verification(tmp_path):
    project=tmp_path/'project';path=project/'src/gamma/memory/example.py';path.parent.mkdir(parents=True)
    path.write_text('def value():\n return 0\n')
    root=tmp_path/'jobs';record=store.create_job(root,'Make value return one while preserving behavior')
    calls=[]
    def checks(workspace,**options):
        calls.append(options)
        if options.get('full_suite'):return {'passed':True,'count':3,'evidence':'protected'}
        source=(Path(workspace)/'src/gamma/memory/example.py').read_text()
        return {'passed':'return 1' in source,'count':1,'output_tail':'expected one','infrastructure_error':False}
    agent=RepositoryAgent(project=project,root=root,endpoint='http://127.0.0.1:11434',checks=checks)
    responses=iter([
        {'action':'read','paths':['src/gamma/memory/example.py']},
        {'action':'plan','tasks':[{'id':'one','editable':['src/gamma/memory/example.py'],'depends_on':[],
                                 'instructions':'Make value() return integer one'}]},
        {'approved':True,'reason':'matches goal','test_source':'from gamma.memory.example import value\ndef test_value():\n assert value()==1\n'},
        {'patches':[{'path':'src/gamma/memory/example.py','old':'return 0','new':'return 1'}]},
        {'approved':True,'reason':'Requested behavior checked and regression passed'},
    ])
    agent.ask=lambda *args,**kwargs:next(responses)
    with patch('gamma.coding.autonomous.LocalPracticeLearner.require_idle',lambda self:None):result=agent.execute(record['id'])
    assert result['status']=='completed' and 'return 1' in path.read_text(), result
    assert sum(bool(c.get('full_suite')) for c in calls)==4
    assert (store.job_path(root,record['id'])/'verified-example.json').exists()


def test_baseline_failure_never_calls_model_or_applies(tmp_path):
    project=tmp_path/'project';path=project/'src/gamma/memory/example.py';path.parent.mkdir(parents=True);path.write_text('x=1')
    root=tmp_path/'jobs';record=store.create_job(root,'Improve module without bypassing regression failures')
    agent=RepositoryAgent(project=project,root=root,endpoint='http://127.0.0.1:11434',checks=lambda *a,**kw:{'passed':False,'count':2})
    agent.ask=lambda *a,**kw:pytest.fail('Model must not run after failed baseline')
    with patch('gamma.coding.autonomous.LocalPracticeLearner.require_idle',lambda self:None):result=agent.execute(record['id'])
    assert result['status']=='blocked' and path.read_text()=='x=1'


def test_regression_failure_feeds_repair_before_application(tmp_path):
    project=tmp_path/'project';path=project/'src/gamma/memory/example.py';path.parent.mkdir(parents=True)
    path.write_text('def value():\n return 0\ndef preserved():\n return 7\n')
    root=tmp_path/'jobs';record=store.create_job(root,'Make value return one and preserve the other API')
    feedback_seen=[]
    def checks(workspace,**options):
        source=(Path(workspace)/'src/gamma/memory/example.py').read_text()
        passed=('return 7' in source) if options.get('full_suite') else ('return 1' in source)
        return {'passed':passed,'count':3,'output_tail':'preserved() must return seven','infrastructure_error':False}
    agent=RepositoryAgent(project=project,root=root,endpoint='http://127.0.0.1:11434',checks=checks)
    responses=iter([
        {'action':'read','paths':['tests/test_private.py']},
        {'action':'read','paths':['src/gamma/memory/example.py']},
        {'action':'plan','tasks':[{'id':'one','editable':['src/gamma/memory/example.py'],'depends_on':[],
                                 'instructions':'Make value() return integer one; preserved() stays seven'}]},
        {'approved':True,'test_source':'from gamma.memory.example import value\ndef test_value():\n assert value()==1\n'},
        {'patches':[{'path':'src/gamma/memory/example.py','old':'return 0','new':'return 1'},
                    {'path':'src/gamma/memory/example.py','old':'return 7','new':'return 8'}]},
        {'patches':[{'path':'src/gamma/memory/example.py','old':'return 8','new':'return 7'}]},
        {'approved':True,'reason':'Repaired regression and independent acceptance passed'}])
    def ask(model,instructions,payload,**options):
        feedback_seen.append(payload.get('feedback'));return next(responses)
    agent.ask=ask
    with patch('gamma.coding.autonomous.LocalPracticeLearner.require_idle',lambda self:None):result=agent.execute(record['id'])
    assert result['status']=='completed' and result['tasks'][0]['attempts']==2, result
    assert any(isinstance(f,list) and any(r.get('output_tail')=='preserved() must return seven' for r in f) for f in feedback_seen)
    assert 'return 7' in path.read_text() and 'return 1' in path.read_text()


def test_planner_keeps_useful_source_when_batch_exceeds_context(tmp_path):
    project=tmp_path/'project';folder=project/'src/gamma/memory';folder.mkdir(parents=True)
    first='src/gamma/memory/one.py';second='src/gamma/memory/two.py'
    (project/first).write_text('x=1\n'+'# padding\n'*1100)
    (project/second).write_text('x=2\n'+'# padding\n'*1100)
    root=tmp_path/'jobs';record=store.create_job(root,'Improve the first source without overloading context')
    agent=RepositoryAgent(project=project,root=root,endpoint='http://127.0.0.1:11434')
    agent.record=record;agent.path=store.job_path(root,record['id'])
    payloads=[]
    def ask(*args,**kwargs):
        payloads.append(args[2])
        if len(payloads)==1:return {'action':'read','paths':[first,second]}
        assert first in args[2]['sources'] and second not in args[2]['sources']
        assert second in args[2]['feedback']
        return {'action':'plan','tasks':[{'id':'one','editable':[first],'depends_on':[],
            'instructions':'Preserve the current observable integer value'}]}
    agent.ask=ask
    assert agent.plan(project)[0]['editable']==[first]


def test_model_admission_waits_without_replaying_a_sent_request(tmp_path):
    import time
    root=tmp_path/'jobs';record=store.create_job(root,'Continue saved tasks after foreground inference ends')
    agent=RepositoryAgent(project=tmp_path,root=root,endpoint='http://127.0.0.1:11434')
    agent.record=record;agent.record['stage']='planning';agent.path=store.job_path(root,record['id'])
    agent.deadline=time.monotonic()+60
    admissions=[];requests=[]
    class Client:
        def __init__(self,**kwargs):pass
        def require_idle(self):
            admissions.append(1)
            if len(admissions)<3:raise RuntimeError('busy')
        def _json(self,*args):
            requests.append(args)
            return {'done':True,'done_reason':'stop','message':{'content':'{"action":"plan"}'}}
    with patch('gamma.coding.autonomous.LocalPracticeLearner',Client),patch('gamma.coding.autonomous.time.sleep',lambda _:None):
        assert agent.ask('qwen3.8:27b','Plan work',{})['action']=='plan'
    assert len(admissions)==3 and len(requests)==1
    assert agent.record['stage']=='planning'


def test_independent_owner_checks_feed_repair_and_survive_install(tmp_path):
    project=tmp_path/'project';path=project/'src/gamma/memory/example.py';path.parent.mkdir(parents=True)
    path.write_text('def value():\n return 0\n')
    root=tmp_path/'jobs'
    contract='from gamma.memory.example import value\ndef test_exact_int():\n assert type(value()) is int\n'
    record=store.create_job(root,'Make value return integer one, preserving its exact type',
        acceptance_tests={'test_goal_owner_exact_type.py':contract})
    def checks(workspace,**options):
        source=(Path(workspace)/'src/gamma/memory/example.py').read_text()
        if options.get('full_suite'):return {'passed':True,'count':3}
        owner='owner_' in options['acceptance']
        passed=('return True' not in source) if owner else ('return 1' in source or 'return True' in source)
        return {'passed':passed,'count':1,'infrastructure_error':False,'output_tail':'Owner requires exact integer, not boolean'}
    agent=RepositoryAgent(project=project,root=root,endpoint='http://127.0.0.1:11434',checks=checks)
    responses=iter([
        {'action':'read','paths':['src/gamma/memory/example.py']},
        {'action':'plan','tasks':[{'id':'one','editable':['src/gamma/memory/example.py'],'depends_on':[],
                                 'instructions':'Return one, preserving exact integer type'}]},
        {'approved':True,'test_source':'from gamma.memory.example import value\ndef test_one():\n assert value()==1\n'},
        {'patches':[{'path':'src/gamma/memory/example.py','old':'return 0','new':'return True'}]},
        {'patches':[{'path':'src/gamma/memory/example.py','old':'return True','new':'return 1'}]},
        {'approved':True,'reason':'Both frozen owner and checker criteria now pass'}])
    agent.ask=lambda *args,**kwargs:next(responses)
    with patch('gamma.coding.autonomous.LocalPracticeLearner.require_idle',lambda self:None):result=agent.execute(record['id'])
    assert result['status']=='completed' and result['tasks'][0]['attempts']==2,result
    installed=store.job_path(root,record['id'])/'installed/tests/test_goal_owner_exact_type.py'
    assert installed.read_text()==contract


def test_owner_contract_cannot_replace_tests_or_skip_checks(tmp_path):
    for contracts in [[], {'test_existing.py':'def test_it():\n assert True'},
                      {'test_goal_owner_bad.py':'import pytest\n@pytest.mark.skip\ndef test_it():\n assert True'}]:
        with pytest.raises(ValueError):
            store.create_job(tmp_path,'Improve behavior with frozen owner checks',acceptance_tests=contracts)


def test_owner_contract_mode_uses_frozen_tests_and_still_requires_final_checker(tmp_path):
    project=tmp_path/'project';path=project/'src/gamma/memory/example.py';path.parent.mkdir(parents=True)
    path.write_text('def value():\n return 0\n')
    root=tmp_path/'jobs'
    contract='from gamma.memory.example import value\ndef test_one():\n assert value()==1\n'
    record=store.create_job(root,'Make value return one using independent owner checks',
        acceptance_mode='owner_contract',acceptance_tests={'test_goal_owner_one.py':contract})
    def checks(workspace,**options):
        passed=True if options.get('full_suite') else 'return 1' in (Path(workspace)/'src/gamma/memory/example.py').read_text()
        return {'passed':passed,'count':2,'infrastructure_error':False,'output_tail':'Expected integer one'}
    agent=RepositoryAgent(project=project,root=root,endpoint='http://127.0.0.1:11434',checks=checks)
    responses=iter([
        {'action':'read','paths':['src/gamma/memory/example.py']},
        {'action':'plan','tasks':[{'id':'one','editable':['src/gamma/memory/example.py'],'depends_on':[],'instructions':'Make value() return integer one'}]},
        {'patches':[{'path':'src/gamma/memory/example.py','old':'return 0','new':'return 1'}]},
        {'approved':False,'reason':'Independent review still gates application'}])
    agent.ask=lambda *args,**kwargs:next(responses)
    with patch('gamma.coding.autonomous.LocalPracticeLearner.require_idle',lambda self:None):result=agent.execute(record['id'])
    assert result['status']=='review_rejected' and 'return 0' in path.read_text(),result
    assert (store.job_path(root,record['id'])/'workspace/tests/test_goal_owner_one.py').read_text()==contract
