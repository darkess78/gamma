"""Independent scope, recovery and receipt attacks on private practical work.

These orchestration checks use fictional sources and a deterministic check
adapter. Actual tool execution and model quality have separate connected proof.
"""
from copy import deepcopy

import pytest

from gamma.coding.durable_practice import PracticeRunner, PracticeYield
from gamma.coding.practice import _digest
from gamma.coordination.store import Store
from gamma.coordination.core import Core
from gamma.coordination.personal_models import PersonalStateUpdates
from gamma.coordination.work import WorkRunner
from gamma.coordination.work_models import validate_spec, safe_relative
from gamma.schemas.response import AssistantResponse


def spec():
    return {'mode':'build','goal':'Change the local answer to two and preserve the checked interface.',
        'files':{'app.py':'VALUE = 1\n','checks.py':'import app\nassert app.VALUE == 2\n'},
        'editable':['app.py'],'checks':[{'name':'answer flow','module':'checks','kind':'feature'}]}


def checked(workspace, check):
    passed=(workspace/'app.py').read_text()=='VALUE = 2\n'
    return {'name':check.name,'kind':check.kind,'passed':passed,'exit_code':0 if passed else 1,
            'infrastructure_error':False,'output_tail':'answer checked' if passed else 'expected answer two'}


def prepared(tmp_path, *, propose=None, check=checked, clock=lambda:100):
    store=Store(tmp_path/'state')
    runner=WorkRunner(store,propose=propose or (lambda *_:{'plan':['Change the answer.'],'edits':{'app.py':'VALUE = 2\n'}}),
                      check=check,clock=clock)
    ref=runner.prepare(spec())
    run={'id':'work-'+'a'*32,'kind':'practical','checkpoint_ref':ref,'initial_checkpoint_ref':ref,
         'status':'queued','desired_state':'running','revision':0,'created_at':100,
         'expires_at':400,'budget_seconds':300,'maximum_attempts':2}
    with store.transaction() as db:store.put(db,'run',run['id'],run)
    return store,runner,run['id']


def replace_checkpoint(store,runner,identifier,checkpoint):
    with store.transaction() as db:
        run=store.get(db,'run',identifier)
        run['checkpoint_ref']=runner.artifacts.put(checkpoint)
        store.put(db,'run',identifier,run)
    return store.read('run',identifier)


@pytest.mark.parametrize('name', [
    '../outside.py','/outside.py','C:/outside.py','C:\\outside.py','\\\\server\\share\\x.py',
    'sub/../app.py','./app.py','sub//app.py','app.py\n','app.py\x00',
    '.env','config/app.local.toml','config/voices.local.toml','sitecustomize.py',
    'usercustomize.py','unittest.py','json.py','sub/con.py','aux.txt',
    'csv.py','tempfile.py','types.py','hashlib.py',
])
def test_nonportable_sensitive_and_import_shadowing_paths_rejected(name):
    with pytest.raises(ValueError):safe_relative(name)


def test_task_normalization_is_defensive_and_research_has_no_edit_authority():
    raw=spec();normal=validate_spec(raw)
    normal['files']['app.py']='changed outside the task'
    normal['editable'].append('checks.py')
    assert raw==spec()
    raw['mode']='research'
    with pytest.raises(ValueError):validate_spec(raw)


def test_protected_test_and_missing_checks_cannot_be_admitted():
    raw=spec();raw['editable'].append('checks.py')
    with pytest.raises(ValueError):validate_spec(raw)
    raw=spec();raw['checks']=[]
    with pytest.raises(ValueError):validate_spec(raw)
    raw=spec();raw['activate']=True
    with pytest.raises(ValueError):validate_spec(raw)


@pytest.mark.parametrize('extra', ['APP.py', 'app.py/helper.py'])
def test_path_aliases_and_file_directory_collisions_fail_before_dispatch(extra):
    raw=spec();raw['files'][extra]='conflicting source'
    with pytest.raises(ValueError):validate_spec(raw)


def test_protected_check_package_initializer_cannot_be_editable():
    raw=spec();raw['files']['checks_package/__init__.py']=''
    raw['files']['checks_package/feature.py']=raw['files'].pop('checks.py')
    raw['checks'][0]['module']='checks_package.feature'
    raw['editable'].append('checks_package/__init__.py')
    with pytest.raises(ValueError):validate_spec(raw)


def test_proposal_cannot_mutate_protected_check_even_with_success_claim(tmp_path):
    calls=[]
    def proposal(*_):
        calls.append(1)
        return {'plan':['Pretend to pass.'],'edits':{'checks.py':'print("passed")'}}
    store,runner,identifier=prepared(tmp_path,propose=proposal)
    before=store.read('run',identifier)
    runner.run(identifier)
    after=store.read('run',identifier);checkpoint=runner.artifacts.get(after['checkpoint_ref'])
    assert after['status']=='needs_checker'
    assert checkpoint['source_ref']==runner.artifacts.get(before['checkpoint_ref'])['source_ref']
    assert checkpoint['attempt_count']==2 and len(calls)==2
    assert all(item['reason']=='work_proposal_scope_violation' for item in checkpoint['attempts'])


def test_completed_source_swap_does_not_reuse_previous_check_success(tmp_path):
    store,runner,identifier=prepared(tmp_path)
    runner.run(identifier)
    run=store.read('run',identifier);assert run['status']=='completed'
    checkpoint=runner.artifacts.get(run['checkpoint_ref'])
    changed={**runner.artifacts.get(checkpoint['source_ref'])['files'],'app.py':'VALUE = 99\n'}
    checkpoint['source_ref']=runner.artifacts.put({'files':changed})
    checkpoint['source_sha256']=_digest(changed)
    run=replace_checkpoint(store,runner,identifier,checkpoint)
    with pytest.raises(ValueError,match='verified_checks'):runner.verify(run)


def test_completed_candidate_cannot_silently_change_protected_files(tmp_path):
    store,runner,identifier=prepared(tmp_path);runner.run(identifier)
    run=store.read('run',identifier);checkpoint=runner.artifacts.get(run['checkpoint_ref'])
    changed={**runner.artifacts.get(checkpoint['source_ref'])['files'],'checks.py':'pass\n'}
    checkpoint['source_ref']=runner.artifacts.put({'files':changed})
    checkpoint['source_sha256']=_digest(changed)
    with pytest.raises(ValueError,match='source_or_scope'):
        runner.verify(replace_checkpoint(store,runner,identifier,checkpoint))


def test_completed_receipt_with_infrastructure_failure_is_not_verification(tmp_path):
    store,runner,identifier=prepared(tmp_path);runner.run(identifier)
    run=store.read('run',identifier);checkpoint=runner.artifacts.get(run['checkpoint_ref'])
    checkpoint['checks'][0]['infrastructure_error']=True
    with pytest.raises(ValueError,match='verified_checks'):
        runner.verify(replace_checkpoint(store,runner,identifier,checkpoint))


def test_uncertain_request_is_charged_once_and_never_automatically_replayed(tmp_path):
    calls=[]
    def unavailable(*_):
        calls.append(1)
        raise TimeoutError('possibly still running upstream')
    store,runner,identifier=prepared(tmp_path,propose=unavailable)
    runner.run(identifier)
    first=store.read('run',identifier)
    assert first['status']=='blocked' and 'no_replay' in first['reason']
    assert runner.artifacts.get(first['checkpoint_ref'])['attempt_count']==1
    replacement=WorkRunner(store,propose=lambda *_:pytest.fail('uncertain request replayed'),check=checked,clock=lambda:100)
    replacement.run(identifier)
    assert calls==[1] and store.read('run',identifier)==first


def test_pre_http_yield_does_not_spend_attempt_or_extend_absolute_scope(tmp_path):
    def yield_before_send(*_):raise PracticeYield('foreground')
    store,runner,identifier=prepared(tmp_path,propose=yield_before_send)
    before=deepcopy(store.read('run',identifier));runner.run(identifier)
    after=store.read('run',identifier);checkpoint=runner.artifacts.get(after['checkpoint_ref'])
    assert after['status']=='queued' and checkpoint['attempt_count']==0
    assert checkpoint['attempts']==[]
    assert (after['expires_at'],after['budget_seconds'],after['maximum_attempts'])==(
        before['expires_at'],before['budget_seconds'],before['maximum_attempts'])


def test_inflight_checkpoint_reopen_is_uncertain_without_new_request(tmp_path):
    store,runner,identifier=prepared(tmp_path,propose=lambda *_:pytest.fail('inflight request replayed'))
    run=store.read('run',identifier);checkpoint=runner.artifacts.get(run['checkpoint_ref'])
    checkpoint.update(stage='inference_started',attempt_count=1,
        attempts=[{'attempt':1,'source_ref':checkpoint['source_ref'],'reason':'inference_started','checks':[]}])
    replace_checkpoint(store,runner,identifier,checkpoint)
    runner.run(identifier)
    run=store.read('run',identifier)
    assert run['status']=='blocked' and run['reason']=='uncertain_inference_no_replay'
    assert runner.artifacts.get(run['checkpoint_ref'])['attempt_count']==1


def test_concrete_blocker_keeps_source_and_waits_without_spending_more_attempts(tmp_path):
    calls=[]
    def missing_input(*_):
        calls.append(1)
        return {'plan':['Inspect the admitted source.','Ask for the missing measurement file.'],
                'edits':{},'blocker':{'kind':'missing_input','question':'Please supply the measurement CSV required by the task.'}}
    store,runner,identifier=prepared(tmp_path,propose=missing_input)
    before=runner.artifacts.get(store.read('run',identifier)['checkpoint_ref'])
    runner.run(identifier)
    run=store.read('run',identifier);checkpoint=runner.verify(run)
    assert run['status']=='needs_checker' and run['reason']=='needs_owner_input'
    assert checkpoint['source_ref']==before['source_ref'] and checkpoint['attempt_count']==1
    assert checkpoint['blocker']['source_sha256']==before['source_sha256']
    assert checkpoint['blocker']['question']=='Please supply the measurement CSV required by the task.'
    runner.run(identifier)
    assert calls==[1] and store.read('run',identifier)==run
    checkpoint['blocker']['source_sha256']='f'*64
    with pytest.raises(ValueError,match='blocker_evidence'):
        runner.verify(replace_checkpoint(store,runner,identifier,checkpoint))


def test_blocker_cannot_smuggle_source_changes_in_the_same_proposal(tmp_path):
    store,runner,identifier=prepared(tmp_path,propose=lambda *_:{'plan':['Ask for data.'],
        'edits':{'app.py':'VALUE = 2\n'},'blocker':{'kind':'scope_decision','question':'May I broaden the source scope?'}})
    original=runner.artifacts.get(store.read('run',identifier)['checkpoint_ref'])['source_ref']
    runner.run(identifier)
    run=store.read('run',identifier);checkpoint=runner.artifacts.get(run['checkpoint_ref'])
    assert run['status']=='needs_checker' and run['reason']=='attempt_limit'
    assert checkpoint['source_ref']==original and 'blocker' not in checkpoint
    assert all(attempt['reason']=='invalid_work_blocker' for attempt in checkpoint['attempts'])


def test_check_modifying_candidate_is_blocked_instead_of_accepted(tmp_path):
    def mutating_check(workspace,check):
        (workspace/'app.py').write_text('VALUE = 2\n')
        return {'name':check.name,'kind':check.kind,'passed':True,'exit_code':0,
                'infrastructure_error':False,'output_tail':'pretend success'}
    store,runner,identifier=prepared(tmp_path,check=mutating_check)
    runner.run(identifier)
    after=store.read('run',identifier)
    assert after['status']=='blocked' and after['reason'].startswith('work_evidence_invalid:')


def test_explicit_status_revalidates_previously_observed_candidate(tmp_path):
    store=Store(tmp_path/'state')
    work=WorkRunner(store,propose=lambda *_:{'plan':['Change answer.'],'edits':{'app.py':'VALUE = 2\n'}},
                    check=checked,clock=lambda:100)
    core=Core(store,runner=PracticeRunner(store,propose=lambda *_:{},check=checked),work_runner=work,
        draft=lambda *_:AssistantResponse(display_text='Still here.',delivery_mode='text_only'),clock=lambda:100)
    try:
        activity=core.work_control('prepare','prepare',spec=spec())['activity']
        core.work_control('start','start',task_id=activity['id'])
        core.tick();core.future.result(timeout=10)
        accepted=core.work_state(activity['id'])
        assert accepted['activity']['status']=='completed'
        source=work.artifacts.root/(accepted['checkpoint']['source_ref']+'.json')
        source.write_text('{"files":{"app.py":"corrupt source"}}')
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='report_status',activity_run_id=activity['id']))
        response=core.input('What happened with the admitted task?','private','after-corruption')['result']['assistant_response']
        assert response['reason_code']=='work_status'
        assert 'is a verified candidate only' not in response['display_text']
        assert 'not verified' in response['display_text'].lower()
    finally:core.close()


@pytest.mark.parametrize('field,value', [('maximum_attempts',True),('maximum_attempts',6),
    ('expires_at',float('nan')),('budget_seconds',float('inf')),('created_at',False)])
def test_invalid_run_limits_cannot_dispatch_or_claim_checks(tmp_path,field,value):
    store,runner,identifier=prepared(tmp_path,propose=lambda *_:pytest.fail('invalid scope dispatched'))
    run=store.read('run',identifier);run[field]=value
    with pytest.raises(ValueError,match='run_scope'):runner.verify(run)
