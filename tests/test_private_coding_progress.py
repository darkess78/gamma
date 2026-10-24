import json,hashlib,logging
from pathlib import Path
from types import SimpleNamespace
import pytest
from gamma.coding import autonomous_store as store
from gamma.coding.project_transaction import apply_verified_changes
from gamma.tools.coding import CodingStatusTool
from gamma.conversation.service import ConversationService
from gamma.identity.profile import SpeakerProfile
from gamma.schemas.conversation import RecipientContext
from gamma.schemas.response import ToolCall

PRIVATE=RecipientContext(kind='owner_private',owner_authorized=True)
OWNER=SpeakerProfile('Owner','owner',is_owner=True)

def setup(tmp_path,monkeypatch,padding=0):
 import gamma.config as config
 data=tmp_path/'data';root=data/'coding/autonomous';project=tmp_path/'project';target=project/'src/gamma/example.py';target.parent.mkdir(parents=True);target.write_text('value=1\n')
 record=store.create_job(root,'PRIVATE_GOAL unrelated subject details');job=store.job_path(root,record['id']);record.update(status='running',tasks=[{'id':'PRIVATE_TASK','editable':['PRIVATE_PATH'],'status':'verified','attempts':2}]);store.write_json(job/'state.json',record)
 def verify():
  values={};exec(compile(target.read_text(),str(target),'exec'),values)
  return {'passed':values['value']==2,'evidence':{'actual_value':values['value'],'private':'PRIVATE_RECEIPT','padding':'x'*padding}}
 receipt=apply_verified_changes(project,{'src/gamma/example.py':'value=2\n'},{'src/gamma/example.py':hashlib.sha256(target.read_bytes()).hexdigest()},job/'application',verify);assert receipt['passed']
 monkeypatch.setattr(config.settings,'data_dir',data)
 return project,target,root,record,job

def pull():
 tool=CodingStatusTool();service=ConversationService.__new__(ConversationService);service._tools=SimpleNamespace(get=lambda name:tool)
 results=service._execute_tool_calls([ToolCall(tool='coding_status',args={'owner_authorized':True})],speaker=OWNER,recipient=PRIVATE)
 assert len(results)==1 and results[0].ok
 reply=service._render_direct_tool_reply(user_text='Coding status',tool_results=results,recipient=PRIVATE)
 return results[0],reply

def test_actual_stored_recovery_reaches_existing_private_tool_and_reply(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch)
 with store.execution_lock(root):store.reconcile_interrupted_applications(root,project)
 state=(job/'state.json').read_bytes();journal=(job/'application/journal.json').read_bytes()
 result,reply=pull();row=result.metadata['jobs'][0]
 assert row['id']==record['id'] and row['status']=='interrupted' and row['application_outcome']=='verified_at_recovery'
 assert row['recovery_outcome']=='completed_current' and row['application_file_count']==1
 assert row['task_count']==row['verified_task_count']==1 and row['tasks']==[{'status':'verified','attempts':2}]
 assert row['followup_completion']=='unconfirmed' and 'verified at recovery' in reply and 'interrupted' in reply
 assert 'unconfirmed' in reply
 assert all(secret not in json.dumps(result.model_dump())+reply for secret in ['PRIVATE_GOAL','PRIVATE_TASK','PRIVATE_PATH','PRIVATE_RECEIPT',str(target),'actual_value'])
 assert (job/'state.json').read_bytes()==state and (job/'application/journal.json').read_bytes()==journal
 result.metadata['jobs'][0]['tasks'][0]['status']='changed'
 assert store.read_job(root,record['id'])['tasks'][0]['status']=='verified'

@pytest.mark.parametrize('outcome',['source_conflict','record_budget','missing','incomplete','invalid','baseline_conflict','rolled_back','rollback_conflict'])
def test_recovery_outcomes_are_finite_and_never_whole_goal_completion(tmp_path,monkeypatch,outcome):
 project,target,root,record,job=setup(tmp_path,monkeypatch)
 record.update(status='interrupted',application_recovery={'status':outcome,'private':'PRIVATE_RECOVERY'},reason='PRIVATE_REASON')
 store.write_json(job/'state.json',record);result,reply=pull();row=result.metadata['jobs'][0]
 assert row['status']=='interrupted' and row['recovery_outcome']==outcome and row['application_outcome']=='unconfirmed'
 assert row['followup_completion']=='unconfirmed' and row['application_file_count'] is None
 assert 'PRIVATE_REASON' not in result.output+reply and 'PRIVATE_RECOVERY' not in json.dumps(result.metadata)

@pytest.mark.parametrize('damage',['huge_tasks','nested_tasks','bad_status','bad_stage','forged_flag','nested_evidence','unknown_recovery'])
def test_private_or_malformed_payloads_do_not_escape_projection(tmp_path,monkeypatch,damage):
 project,target,root,record,job=setup(tmp_path,monkeypatch);receipt=json.loads((job/'application/journal.json').read_text())
 record.update(status='interrupted',application=receipt,application_recovery={'status':'completed_current'},goal='PRIVATE_HUGE'*20_000,reason='PRIVATE_REASON',address='PRIVATE_IP')
 if damage=='huge_tasks':record['tasks']=[{'id':'PRIVATE_TASK','status':'verified','attempts':999,'editable':['PRIVATE_PATH']}] * 100
 elif damage=='nested_tasks':record['tasks']=[{'status':['PRIVATE_NESTED'],'attempts':True}]
 elif damage=='bad_status':record['status']=['PRIVATE_STATUS']
 elif damage=='bad_stage':record['stage']='PRIVATE_STAGE'*1000
 elif damage=='forged_flag':record['application']['passed']=1
 elif damage=='nested_evidence':record['application']['evidence']={'PRIVATE_EVIDENCE':{'nested':['PRIVATE_NESTED']*50_000}}
 elif damage=='unknown_recovery':record['application_recovery']['status']={'PRIVATE_RECOVERY':'nested'}
 store.write_json(job/'state.json',record);result,reply=pull();encoded=json.dumps(result.model_dump())+reply
 assert len(encoded)<5000 and 'PRIVATE_' not in encoded and 'before_sha256' not in encoded and 'after_sha256' not in encoded
 row=result.metadata['jobs'][0];assert row['followup_completion']=='unconfirmed'
 if damage=='huge_tasks':assert row['task_count'] is None and row['tasks']==[]
 if damage=='forged_flag':assert row['application_outcome']=='unconfirmed'
 if damage=='bad_status':assert row['status']=='unknown'
 if damage=='bad_stage':assert row['stage']=='unknown'

@pytest.mark.parametrize('status',['queued','running','paused','deferred','completed','already_satisfied','blocked','stopped','application_failed'])
def test_ordinary_job_states_remain_useful_without_private_details(tmp_path,monkeypatch,status):
 project,target,root,record,job=setup(tmp_path,monkeypatch);record.update(status=status,stage=status);store.write_json(job/'state.json',record)
 result,reply=pull();row=result.metadata['jobs'][0];assert row['status']==row['stage']==status and row['id']==record['id']
 assert row['task_count']==row['verified_task_count']==1 and row['application_outcome']=='unconfirmed'
 assert status in reply and 'PRIVATE_' not in result.output+reply

def test_owner_authority_and_shared_disclosure_are_unchanged(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch)
 with store.execution_lock(root):store.reconcile_interrupted_applications(root,project)
 tool=CodingStatusTool();service=ConversationService.__new__(ConversationService);service._tools=SimpleNamespace(get=lambda name:tool)
 for speaker in [None,SpeakerProfile('Guest','guest'),SpeakerProfile('Fake','owner',is_owner=False)]:
  result=service._execute_tool_calls([ToolCall(tool='coding_status',args={'is_owner':True})],speaker=speaker,recipient=PRIVATE)[0]
  assert not result.ok and result.metadata['reason']=='owner_required'
 for context in [None,RecipientContext(kind='shared')]:
  result=service._execute_tool_calls([ToolCall(tool='coding_status',args={})],speaker=OWNER,recipient=context)[0]
  assert result.metadata=={'disclosure':'status_only','status':'completed'} and 'interrupted' not in result.output
  reply=service._render_direct_tool_reply(user_text='status',tool_results=[result],recipient=context)
  assert reply=='Coding status checked. Its details are private.'

def test_empty_oversized_and_malformed_job_files_are_conservative(tmp_path,monkeypatch):
 import gamma.config as config
 data=tmp_path/'data';root=data/'coding/autonomous';monkeypatch.setattr(config.settings,'data_dir',data)
 assert CodingStatusTool().run().metadata['jobs']==[]
 for i,raw in enumerate([b'{broken',b' '*2_000_001,b'null']):
  path=root/('work-'+f'{i:032x}');path.mkdir();(path/'state.json').write_bytes(raw)
 result=CodingStatusTool().run();assert result.ok and result.metadata['jobs']==[]

def test_malformed_ordering_metadata_reports_constant_failure_without_content(tmp_path,monkeypatch,caplog):
 project,target,root,record,job=setup(tmp_path,monkeypatch);record.pop('created_at');record['reason']='PRIVATE_EXCEPTION';store.write_json(job/'state.json',record)
 result=CodingStatusTool().run();assert result.ok and result.metadata['jobs']==[]
 assert 'PRIVATE_' not in result.output+caplog.text and str(job) not in caplog.text
 assert 'coding_job_ordering_invalid' in caplog.text


def test_actual_hash_conflict_reaches_private_reply_without_current_claim(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch);target.write_text('value=3\n')
 with store.execution_lock(root):store.reconcile_interrupted_applications(root,project)
 result,reply=pull();row=result.metadata['jobs'][0]
 assert row['recovery_outcome']=='source_conflict' and row['application_outcome']=='unconfirmed'
 assert 'at recovery' in reply and 'verified at recovery' not in reply and target.read_text()=='value=3\n'

def test_actual_record_budget_reaches_private_reply_without_raw_evidence(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch,padding=1_950_000);record['unrelated_progress']='PRIVATE_PROGRESS'*8000;store.write_json(job/'state.json',record)
 with store.execution_lock(root):store.reconcile_interrupted_applications(root,project)
 result,reply=pull();row=result.metadata['jobs'][0]
 assert row['status']=='interrupted' and row['recovery_outcome']=='record_budget' and row['application_outcome']=='unconfirmed'
 assert 'record budget' in reply and 'PRIVATE_' not in json.dumps(result.model_dump())+reply and len(result.output)<2000

def test_actual_unpersisted_recovery_shows_running_without_application_claim(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch);original=(job/'state.json').read_bytes();journal=(job/'application/journal.json').read_bytes();job.chmod(0o500)
 try:
  with store.execution_lock(root):store.reconcile_interrupted_applications(root,project)
  result,reply=pull();row=result.metadata['jobs'][0]
  assert row['status']=='running' and row['application_outcome']=='unconfirmed' and row['recovery_outcome']=='unknown'
  assert (job/'state.json').read_bytes()==original and (job/'application/journal.json').read_bytes()==journal
  assert 'unconfirmed' in reply and 'verified at recovery' not in reply
 finally:job.chmod(0o700)

def test_other_scope_goals_and_more_than_five_records_remain_excluded(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch)
 for i in range(7):
  row={**record,'id':'work-'+f'{i:032x}','created_at':record['created_at']+i+1,'privacy_scope':'public','goal':'PRIVATE_FOREIGN_SUBJECT','reason':'PRIVATE_IP_ADDRESS'}
  path=store.job_path(root,row['id']);path.mkdir();store.write_json(path/'state.json',row)
 result,reply=pull();assert len(result.metadata['jobs'])==5 and 'PRIVATE_' not in result.output+reply


def test_stored_marker_without_valid_application_is_explicitly_qualified(tmp_path,monkeypatch):
 project,target,root,record,job=setup(tmp_path,monkeypatch)
 record.update(status='interrupted',application_recovery={'status':'completed_current'},application={'passed':True,'status':'completed','before':{'PRIVATE_PATH':'a'*64},'after':{'PRIVATE_PATH':'b'*64},'evidence':'PRIVATE_EVIDENCE'})
 store.write_json(job/'state.json',record);result,reply=pull()
 assert result.metadata['jobs'][0]['application_outcome']=='unconfirmed' and result.metadata['jobs'][0]['application_file_count'] is None
 assert 'Stored recovery records a checked application' in reply and 'completion payload is unconfirmed' in reply
 assert 'was verified at recovery' not in reply and 'PRIVATE_' not in result.output+reply


@pytest.mark.parametrize('bad_created',[{},[],True,None,'PRIVATE_ORDERING',float('nan'),float('inf'),10**1000])
def test_malformed_neighbor_ordering_does_not_hide_valid_progress(tmp_path,monkeypatch,caplog,bad_created):
 project,target,root,record,job=setup(tmp_path,monkeypatch)
 with store.execution_lock(root):store.reconcile_interrupted_applications(root,project)
 bad={**record,'id':'work-'+'f'*32,'created_at':bad_created,'goal':'PRIVATE_FOREIGN_ORDERING'};path=store.job_path(root,bad['id']);path.mkdir();store.write_json(path/'state.json',bad)
 original=path.joinpath('state.json').read_bytes();valid=job.joinpath('state.json').read_bytes()
 result,reply=pull();assert [r['id'] for r in result.metadata['jobs']]==[record['id']]
 assert result.metadata['jobs'][0]['application_outcome']=='verified_at_recovery'
 assert 'PRIVATE_' not in result.output+reply and path.joinpath('state.json').read_bytes()==original and job.joinpath('state.json').read_bytes()==valid
 assert any(r.getMessage()==f'coding_job_ordering_invalid job_id={bad["id"]}' and r.exc_info is None for r in caplog.records)
