
import hashlib,json,os,subprocess,sys
from pathlib import Path
import pytest
from gamma.coding import autonomous_store as store
from gamma.coding import autonomous

CHILD=r"""
import json,hashlib,os,sys
from pathlib import Path
from gamma.coding import autonomous_store as store, project_transaction as transaction
project=Path(sys.argv[1]);job=Path(sys.argv[2]);stop=sys.argv[3]
target=project/'src/gamma/synthetic_value.py'
original=transaction.write_json
def publish(path,value):
    original(path,value)
    if Path(path)==job/'application/journal.json' and value.get('status')==stop:
        os._exit(77)
transaction.write_json=publish
def verify():
    values={}
    exec(compile(target.read_text(),str(target),'exec'),values)
    observed=values['value']()
    (job/'verification-count.json').write_text(json.dumps({'calls':1,'observed':observed}))
    return {'passed':observed==2,'evidence':{'synthetic_actual_value':observed}}
with store.execution_lock(job.parent):
    transaction.apply_verified_changes(project,{'src/gamma/synthetic_value.py':'def value():\n    return 2\n'},
        {'src/gamma/synthetic_value.py':hashlib.sha256(target.read_bytes()).hexdigest()},job/'application',verify)
raise AssertionError('Synthetic crash boundary was not reached')
"""

def crash(tmp_path,stop):
    project=tmp_path/'project';target=project/'src/gamma/synthetic_value.py';target.parent.mkdir(parents=True)
    target.write_text('def value():\n    return 1\n')
    data=tmp_path/'data';root=data/'coding/autonomous'
    record=store.create_job(root,'Return two from the synthetic fixture function')
    record.update(status='running',stage='applying')
    job=store.job_path(root,record['id']);store.write_json(job/'state.json',record)
    result=subprocess.run([sys.executable,'-B','-c',CHILD,str(project),str(job),stop],text=True,capture_output=True,timeout=10)
    assert result.returncode==77,(result.returncode,result.stdout,result.stderr)
    assert store.read_job(root,record['id'])['status']=='running'
    return project,target,data,root,record['id'],job

def recover(project,data,root,identifier,monkeypatch):
    import gamma.config as config
    import gamma.coding.project_activation as activation
    callbacks=[]
    def forbidden(*args,**kwargs):
        callbacks.append(True);raise AssertionError('Recovery must not dispatch/replay/activate')
    monkeypatch.setattr(config,'PROJECT_ROOT',project)
    monkeypatch.setattr(config.settings,'data_dir',data)
    monkeypatch.setattr(activation,'activate_source',forbidden)
    monkeypatch.setattr(autonomous.RepositoryAgent,'execute',forbidden)
    monkeypatch.setattr(sys,'argv',['synthetic-coding-recovery','--once'])
    autonomous.main()
    first=store.read_job(root,identifier)
    autonomous.main()
    second=store.read_job(root,identifier)
    assert first==second and not callbacks and second['status']=='interrupted'
    return second

def test_completed_durable_application_is_visible_after_interrupted_recovery(tmp_path,monkeypatch):
    project,target,data,root,identifier,job=crash(tmp_path,'completed')
    receipt=json.loads((job/'application/journal.json').read_text())
    assert receipt['status']=='completed' and receipt['passed'] is True
    assert receipt['evidence']['synthetic_actual_value']==2
    initial_journal=(job/'application/journal.json').read_bytes()
    state=recover(project,data,root,identifier,monkeypatch)
    assert target.read_text()=='def value():\n    return 2\n'
    assert (job/'application/journal.json').read_bytes()==initial_journal
    assert state.get('application',{}).get('status')=='completed'
    assert state['application']['passed'] is True

def test_unverified_application_remains_interrupted_with_no_replay(tmp_path,monkeypatch):
    project,target,data,root,identifier,job=crash(tmp_path,'verifying')
    assert not (job/'verification-count.json').exists()
    state=recover(project,data,root,identifier,monkeypatch)
    assert target.read_text()=='def value():\n    return 2\n'
    assert json.loads((job/'application/journal.json').read_text())['passed'] is False
    assert state['status']=='interrupted'

def test_owner_changed_source_is_preserved_with_no_replay_or_completion_claim(tmp_path,monkeypatch):
    project,target,data,root,identifier,job=crash(tmp_path,'completed')
    target.write_text('def value():\n    return 3\n')
    state=recover(project,data,root,identifier,monkeypatch)
    assert target.read_text()=='def value():\n    return 3\n'
    assert state['status']=='interrupted'


def assert_classification(state, expected):
    assert state['status']=='interrupted'
    assert state['application_recovery']['status']==expected
    if expected!='completed_current':assert 'application' not in state

@pytest.mark.parametrize('boundary',['prepared','applying','verifying'])
def test_incomplete_real_transaction_retains_original_bytes_and_no_callbacks(tmp_path,monkeypatch,boundary):
    project,target,data,root,identifier,job=crash(tmp_path,boundary)
    initial=(job/'application/journal.json').read_bytes();before=target.read_bytes()
    state=recover(project,data,root,identifier,monkeypatch)
    assert_classification(state,'incomplete')
    assert target.read_bytes()==before and (job/'application/journal.json').read_bytes()==initial

@pytest.mark.parametrize('damage',['missing','json','flags','before_hash','after_hash','evidence','empty_files','too_many_files','extra_root','extra_entry','protected','traversal','source_symlink','parent_symlink','receipt_symlink','directory_symlink','oversized','fifo','null','duplicate','nonfinite'])
def test_untrusted_receipts_never_report_current_installation(tmp_path,monkeypatch,damage):
    project,target,data,root,identifier,job=crash(tmp_path,'completed');path=job/'application/journal.json'
    value=json.loads(path.read_text());expected='invalid'
    if damage=='missing':path.unlink();expected='missing'
    elif damage=='json':path.write_text('{broken')
    elif damage=='flags':value['passed']=1;path.write_text(json.dumps(value))
    elif damage=='before_hash':value['files']['src/gamma/synthetic_value.py']['before_sha256']='Z'*64;path.write_text(json.dumps(value))
    elif damage=='after_hash':value['files']['src/gamma/synthetic_value.py']['after_sha256']='a'*64;path.write_text(json.dumps(value));expected='source_conflict'
    elif damage=='evidence':value['evidence']=[];path.write_text(json.dumps(value))
    elif damage=='empty_files':value['files']={};path.write_text(json.dumps(value))
    elif damage=='too_many_files':value['files']={f'src/gamma/f{i}.py':{'before_sha256':None,'after_sha256':'a'*64} for i in range(13)};path.write_text(json.dumps(value))
    elif damage=='extra_root':value['project_root']=str(tmp_path/'external');path.write_text(json.dumps(value))
    elif damage=='extra_entry':value['files']['src/gamma/synthetic_value.py']['path']='external';path.write_text(json.dumps(value))
    elif damage=='protected':value['files']['src/gamma/config.py']=value['files'].pop('src/gamma/synthetic_value.py');path.write_text(json.dumps(value))
    elif damage=='traversal':value['files']['../external.py']=value['files'].pop('src/gamma/synthetic_value.py');path.write_text(json.dumps(value))
    elif damage=='source_symlink':outside=tmp_path/'outside.py';outside.write_bytes(target.read_bytes());target.unlink();target.symlink_to(outside)
    elif damage=='parent_symlink':parent=project/'src/gamma';outside=tmp_path/'outside-dir';parent.rename(outside);parent.symlink_to(outside,target_is_directory=True)
    elif damage=='receipt_symlink':outside=tmp_path/'outside.json';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
    elif damage=='directory_symlink':parent=job/'application';outside=tmp_path/'outside-receipts';parent.rename(outside);parent.symlink_to(outside,target_is_directory=True)
    elif damage=='oversized':path.write_bytes(b' '*2_000_001)
    elif damage=='fifo':path.unlink();os.mkfifo(path)
    elif damage=='null':path.write_text('null')
    elif damage=='duplicate':path.write_text(path.read_text().replace('"passed": true','"passed": true, "passed": true',1))
    elif damage=='nonfinite':value['evidence']['bad']=float('nan');path.write_text(json.dumps(value))
    before_target=target.read_bytes();journal=path.read_bytes() if path.is_file() else None
    state=recover(project,data,root,identifier,monkeypatch);assert_classification(state,expected)
    assert target.read_bytes()==before_target
    if journal is not None:assert path.read_bytes()==journal

@pytest.mark.parametrize('status',['rolled_back','rollback_conflict'])
def test_noncompleted_receipt_is_not_completion_evidence(tmp_path,monkeypatch,status):
    project,target,data,root,identifier,job=crash(tmp_path,'completed')
    path=job/'application/journal.json';value=json.loads(path.read_text());value.update(passed=False,status=status,error='RuntimeError',rollback_conflicts=[])
    path.write_text(json.dumps(value));original=path.read_bytes()
    state=recover(project,data,root,identifier,monkeypatch);assert_classification(state,status)
    assert path.read_bytes()==original and target.read_text()=='def value():\n    return 2\n'

def test_later_owner_edit_revokes_current_evidence_without_touching_journal(tmp_path,monkeypatch):
    project,target,data,root,identifier,job=crash(tmp_path,'completed')
    initial=recover(project,data,root,identifier,monkeypatch);assert_classification(initial,'completed_current')
    target.write_text('def value():\n    return 3\n');journal=(job/'application/journal.json').read_bytes()
    updated=recover(project,data,root,identifier,monkeypatch);assert_classification(updated,'source_conflict')
    assert target.read_text()=='def value():\n    return 3\n' and (job/'application/journal.json').read_bytes()==journal

def test_terminal_records_and_unrelated_progress_are_preserved(tmp_path,monkeypatch):
    project,target,data,root,identifier,job=crash(tmp_path,'completed')
    state=store.read_job(root,identifier);state.update(status='completed',owner_note='preserve completed work');store.write_json(job/'state.json',state)
    second=store.create_job(root,'Preserve independently completed unrelated work');other=store.job_path(root,second['id']);second.update(status='failed',application={'passed':False,'status':'rolled_back'},unrelated={'scope':'public','notes':['keep']});store.write_json(other/'state.json',second)
    before={p:p.read_bytes() for p in [job/'state.json',other/'state.json']}
    state.update(status='running',unrelated={'scope':'private','notes':['keep']});store.write_json(job/'state.json',state)
    result=recover(project,data,root,identifier,monkeypatch)
    assert result['unrelated']==state['unrelated'] and (other/'state.json').read_bytes()==before[other/'state.json']
    terminal={**result,'status':'completed'};store.write_json(job/'state.json',terminal)
    # Startup must preserve existing terminal records verbatim.
    import gamma.config as config
    monkeypatch.setattr(config,'PROJECT_ROOT',project);monkeypatch.setattr(config.settings,'data_dir',data)
    current=(job/'state.json').read_bytes();autonomous.main();assert (job/'state.json').read_bytes()==current


FULL_CHILD=r"""
import os,sys,json,hashlib
from pathlib import Path
from gamma.coding import autonomous, autonomous_store as store, learner, project_transaction as transaction
project=Path(sys.argv[1]);root=Path(sys.argv[2]);identifier=sys.argv[3];job=store.job_path(root,identifier)
class Idle:
 def __init__(self,**kwargs):pass
 def require_idle(self):pass
learner.LocalPracticeLearner=Idle;autonomous.LocalPracticeLearner=Idle
original=transaction.write_json
def publish(path,value):
 original(path,value)
 if Path(path)==job/'application/journal.json' and value.get('status')=='completed':os._exit(77)
transaction.write_json=publish
class FixtureAgent(autonomous.RepositoryAgent):
 def plan(self,workspace):return [{'id':'value','editable':['src/gamma/synthetic_value.py'],'depends_on':[],'instructions':'Return two from the fixture function'}]
 def acceptance(self,task,sources):return 'def test_value():\n    from gamma.synthetic_value import value\n    assert value() == 2\n'
 def propose(self,task,sources,feedback):return {'src/gamma/synthetic_value.py':'def value():\n    return 2\n'}
 def ask(self,*args,**kwargs):return {'approved':True,'reason':'Actual installed synthetic value checked'}
def checks(workspace,**options):
 values={};target=workspace/'src/gamma/synthetic_value.py'
 exec(compile(target.read_text(),str(target),'exec'),values);observed=values['value']()
 return {'passed': observed==2 if options.get('acceptance') else observed in {1,2},'count':1,'actual_value':observed}
with store.execution_lock(root):
 FixtureAgent(project=project,root=root,endpoint='unused',checks=checks,activate=None).execute(identifier)
raise AssertionError('Full caller did not reach completed journal')
"""

def test_actual_execute_crash_recovers_receipt_without_replaying_followups(tmp_path,monkeypatch):
 project=tmp_path/'project';target=project/'src/gamma/synthetic_value.py';target.parent.mkdir(parents=True);target.write_text('def value():\n    return 1\n')
 data=tmp_path/'data';root=data/'coding/autonomous';record=store.create_job(root,'Return two from the synthetic full caller function');job=store.job_path(root,record['id'])
 result=subprocess.run([sys.executable,'-B','-c',FULL_CHILD,str(project),str(root),record['id']],capture_output=True,text=True,timeout=10)
 assert result.returncode==77,(result.stdout,result.stderr)
 before=store.read_job(root,record['id']);assert before['status']=='running' and before['tasks'][0]['status']=='verified'
 assert (job/'baseline.json').is_file() and (job/'baseline-checks.json').is_file() and (job/'candidate-checks.json').is_file() and (job/'review.json').is_file()
 assert not (job/'verified-example.json').exists() and not (root.parent/'lessons/verified-coding.jsonl').exists()
 recovered=recover(project,data,root,record['id'],monkeypatch);assert_classification(recovered,'completed_current')
 assert recovered['tasks']==before['tasks'] and 'learning' not in recovered
 assert target.read_text()=='def value():\n    return 2\n'

@pytest.mark.parametrize('owner_edit',[False,True])
def test_actual_failed_verification_preserves_rollback_and_owner_effects(tmp_path,monkeypatch,owner_edit):
 from gamma.coding.project_transaction import apply_verified_changes
 project=tmp_path/'project';target=project/'src/gamma/synthetic_value.py';target.parent.mkdir(parents=True);target.write_text('def value():\n    return 1\n')
 data=tmp_path/'data';root=data/'coding/autonomous';record=store.create_job(root,'Preserve the synthetic failed verification');job=store.job_path(root,record['id'])
 record.update(status='running');store.write_json(job/'state.json',record)
 def verify():
  if owner_edit:target.write_text('def value():\n    return 3\n')
  return {'passed':False,'evidence':{'actual_failure':True}}
 receipt=apply_verified_changes(project,{'src/gamma/synthetic_value.py':'def value():\n    return 2\n'},{'src/gamma/synthetic_value.py':hashlib.sha256(target.read_bytes()).hexdigest()},job/'application',verify)
 expected='rollback_conflict' if owner_edit else 'rolled_back';assert receipt['status']==expected
 journal=(job/'application/journal.json').read_bytes();original=target.read_bytes()
 result=recover(project,data,root,record['id'],monkeypatch);assert_classification(result,expected)
 assert target.read_bytes()==original and (job/'application/journal.json').read_bytes()==journal

def test_trailing_hash_newline_is_invalid(tmp_path,monkeypatch):
 project,target,data,root,identifier,job=crash(tmp_path,'completed');path=job/'application/journal.json';receipt=json.loads(path.read_text());receipt['files']['src/gamma/synthetic_value.py']['before_sha256']+='\n';path.write_text(json.dumps(receipt))
 assert_classification(recover(project,data,root,identifier,monkeypatch),'invalid')


def test_mismatched_record_id_cannot_import_other_job_receipt(tmp_path,monkeypatch):
 project,target,data,root,identifier,job=crash(tmp_path,'completed')
 record=store.read_job(root,identifier);record.update(status='completed');store.write_json(job/'state.json',record)
 other=store.create_job(root,'Preserve unbound foreign job state');other_path=store.job_path(root,other['id']);other.update(id=identifier,status='running');store.write_json(other_path/'state.json',other)
 before={p:p.read_bytes() for p in [job/'state.json',other_path/'state.json']}
 import gamma.config as config
 monkeypatch.setattr(config,'PROJECT_ROOT',project);monkeypatch.setattr(config.settings,'data_dir',data);monkeypatch.setattr(sys,'argv',['synthetic','--once']);autonomous.main()
 assert all(p.read_bytes()==raw for p,raw in before.items())
 with pytest.raises(ValueError):store.read_job(root,other_path.name)

def test_stale_job_payload_cannot_override_missing_receipt(tmp_path,monkeypatch):
 project,target,data,root,identifier,job=crash(tmp_path,'completed');state=store.read_job(root,identifier);state['application']=json.loads((job/'application/journal.json').read_text());store.write_json(job/'state.json',state);(job/'application/journal.json').unlink()
 assert_classification(recover(project,data,root,identifier,monkeypatch),'missing')


def add_later_recovery(root, record):
    # Represent another inherited paused job without creating another executor.
    other={**record,'id':'work-'+'0'*32,'status':'paused','created_at':record['created_at']-1,'unrelated_progress':{'keep':'later'}}
    other.pop('application',None);other.pop('application_recovery',None)
    path=store.job_path(root,other['id']);path.mkdir();store.write_json(path/'state.json',other)
    return other,path

def test_combined_record_budget_omits_receipt_and_recovers_later_job(tmp_path,monkeypatch):
    monkeypatch.setitem(globals(),'CHILD',CHILD.replace("'synthetic_actual_value':observed","'synthetic_actual_value':observed,'padding':'x'*1_950_000"))
    project,target,data,root,identifier,job=crash(tmp_path,'completed');record=store.read_job(root,identifier)
    record['unrelated_progress']='u'*100_000;store.write_json(job/'state.json',record);other,path=add_later_recovery(root,record)
    journal=(job/'application/journal.json').read_bytes();source=target.read_bytes()
    result=recover(project,data,root,identifier,monkeypatch)
    assert_classification(result,'record_budget');assert result['unrelated_progress']==record['unrelated_progress']
    assert store.read_job(root,other['id'])['status']=='interrupted' and store.read_job(root,other['id'])['unrelated_progress']==other['unrelated_progress']
    assert (job/'state.json').stat().st_size<=2_000_000 and (job/'application/journal.json').read_bytes()==journal and target.read_bytes()==source

@pytest.mark.parametrize('failure',['state_budget','unwritable'])
def test_minimal_update_failure_preserves_record_and_later_recovery(tmp_path,monkeypatch,caplog,failure):
    project,target,data,root,identifier,job=crash(tmp_path,'completed');record=store.read_job(root,identifier)
    if failure=='state_budget':
        # Actual legal record, exactly at the existing storage cap before recovery.
        record['unrelated_progress']=''
        available=2_000_000-len(json.dumps(record,ensure_ascii=False,indent=2).encode())
        record['unrelated_progress']='u'*available
    store.write_json(job/'state.json',record);other,path=add_later_recovery(root,{**record,'unrelated_progress':'keep'})
    record['goal']='PRIVATE_DIAGNOSTIC_SENTINEL'
    if failure=='unwritable':store.write_json(job/'state.json',record)
    original=(job/'state.json').read_bytes();journal=(job/'application/journal.json').read_bytes();source=target.read_bytes()
    if failure=='unwritable':job.chmod(0o500)
    import gamma.config as config
    import gamma.coding.project_activation as activation
    callbacks=[]
    def forbidden(*args,**kwargs):callbacks.append(True);raise AssertionError('No recovery effects')
    monkeypatch.setattr(config,'PROJECT_ROOT',project);monkeypatch.setattr(config.settings,'data_dir',data)
    monkeypatch.setattr(activation,'activate_source',forbidden);monkeypatch.setattr(autonomous.RepositoryAgent,'execute',forbidden);monkeypatch.setattr(sys,'argv',['synthetic','--once'])
    try:
        autonomous.main();first=(job/'state.json').read_bytes();autonomous.main()
        assert first==original==(job/'state.json').read_bytes() and store.read_job(root,identifier)['status']=='running'
        later=store.read_job(root,other['id']);assert later['status']=='interrupted' and later['unrelated_progress']==other['unrelated_progress']
        assert not callbacks and target.read_bytes()==source and (job/'application/journal.json').read_bytes()==journal
        warnings=[r for r in caplog.records if r.name=='gamma.coding.autonomous_store']
        expected='PermissionError' if failure=='unwritable' else 'ValueError'
        assert len(warnings)==2
        assert all(r.levelname=='WARNING' and r.getMessage()==f'coding_recovery_persistence_failed job_id={identifier} exception={expected}' for r in warnings)
        assert all(r.exc_info is None and r.stack_info is None for r in warnings)
        assert 'PRIVATE_DIAGNOSTIC_SENTINEL' not in caplog.text and str(job) not in caplog.text and 'Work record too large' not in caplog.text
    finally:
        if failure=='unwritable':job.chmod(0o700)
