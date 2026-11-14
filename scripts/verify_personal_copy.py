"""Private copied-history migration/reversal; emit counts, hashes and booleans only.

Run on the isolated Gamma candidate. Original production stores and the copied
legacy bundle are read-only. The old-code provider is a capture adapter: no LLM
request is made and no private prompt text is written to evidence or stdout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

from gamma.coordination.core import Core
from gamma.coordination.store import Store, private_path
from gamma.coordination.personal import identifier
from gamma.coordination.personal_import import reconcile_snapshot
from gamma.coordination.transfer import export_snapshot, restore_snapshot, prepare_p1_source, project_legacy, identity_lease
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


UNITS=('gamma-shana.service','gamma-dashboard.service','gamma-coding-idle.service','gamma-coding-autonomous.service')


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2))
    path.chmod(0o600)


def pids():
    values={}
    for unit in UNITS:
        result=subprocess.run(['systemctl','--user','show',unit,'--property=MainPID','--value'],capture_output=True,text=True,check=True)
        pid=int(result.stdout.strip())
        if pid<=0:raise ValueError('production_service_not_running')
        values[unit]=pid
    return values


def bundle_files(snapshot):
    manifest_path=private_path(snapshot/'manifest.json')
    manifest=json.loads(manifest_path.read_text())
    files={manifest_path:hash_file(manifest_path)}
    database=private_path(manifest['database']['snapshot_path'])
    if not database.is_relative_to(snapshot):raise ValueError('database_outside_private_copy')
    files[database]=manifest['database']['snapshot_sha256']
    for item in manifest.get('copied_files',[]):
        path=private_path(item['snapshot_path'])
        if not path.is_relative_to(snapshot):raise ValueError('auxiliary_outside_private_copy')
        files[path]=item['sha256']
    if any(hash_file(path)!=value for path,value in files.items()):
        raise ValueError('private_snapshot_changed')
    return manifest,files


def rows(root):
    store=Store(root)
    with store.transaction() as db:
        return {(kind,key):json.loads(body) for kind,key,body in db.execute('SELECT kind,id,body FROM records')}


def clone_foundation(source,state):
    """Read-only online backup and hash-checked immutable artifacts, no source edits."""
    source=private_path(source)
    database=private_path(source/'coordination.sqlite3')
    original={database:hash_file(database)}
    state.mkdir(mode=0o700)
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as old:
        old.execute('PRAGMA query_only=ON')
        with sqlite3.connect(state/'coordination.sqlite3') as new:old.backup(new)
    (state/'coordination.sqlite3').chmod(0o600)
    artifacts=source/'artifacts'
    if artifacts.exists():
        (state/'artifacts').mkdir(mode=0o700)
        for path in artifacts.iterdir():
            private_path(path)
            if not path.is_file():raise ValueError('unexpected_foundation_artifact')
            original[path]=hash_file(path)
            if path.stem!=original[path] or path.suffix!='.json':raise ValueError('foundation_artifact_hash')
            shutil.copyfile(path,state/'artifacts'/path.name)
            (state/'artifacts'/path.name).chmod(0o600)
    return original


PRIVATE_OLD_TURN=r'''
import hashlib,json,pathlib,sys
payload=json.loads(pathlib.Path(sys.argv[1]).read_text())
import gamma.coordination.core as module
assert pathlib.Path(module.__file__).resolve()==pathlib.Path(payload['project'])/'src/gamma/coordination/core.py'
assert hashlib.sha256(pathlib.Path(module.__file__).read_bytes()).hexdigest()==payload['core_sha256']
assert 'gamma.coordination.personal' not in sys.modules
from gamma.coordination.store import Store
from gamma.coding.durable_practice import PracticeRunner
from gamma.conversation.service import ConversationService
from gamma.llm.base import LLMReply
from gamma.schemas.conversation import RecipientContext
checks=[]
class Capture:
    supports_vision=False
    def generate_reply(self,system_prompt,user_text,*args,**kwargs):
        checks.append({'prompt_sha256':hashlib.sha256(system_prompt.encode()).hexdigest(),
            'forgotten_absent':payload['forgotten'] not in system_prompt,
            'current_correction_present':payload['current'] in system_prompt,
            'authored_edit_present':payload['authored'] in system_prompt})
        return LLMReply(text='The fictional correction is current and the fictional repair commitment remains unfinished.')
service=ConversationService();service._llm=Capture()
def draft(text,context):
    return service.respond(text,synthesize_speech=False,fast_mode=True,evaluation_mode=True,coordination_mode=True,
        speech_allowed=False,speech_requested=False,delivery_context='direct_text',background_context=context,
        recipient=RecipientContext('owner_private','dashboard_monitor',True))
store=Store(payload['state'])
core=module.Core(store,draft=draft,runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))
try:
    result=core.input('What is the fictional copper workshop drink and the fictional bridge repair commitment?',
        'copied-reversal','copied-p1-turn')
    assert result['result']['assistant_response']['display_text']
    assert checks and all(item['forgotten_absent'] and item['current_correction_present'] and item['authored_edit_present'] for item in checks)
    assert 'gamma.coordination.personal' not in sys.modules
    pathlib.Path(payload['result']).write_text(json.dumps({'old_core_sha256':payload['core_sha256'],
        'actual_old_service_provider_calls':len(checks),'prompt_checks':checks,'private_prompt_saved':False,
        'p2_module_imported':False,'old_turn_completed':True}))
finally:core.close()
'''


def verify_copy(root,project,snapshot,foundation):
    root,project,snapshot,foundation=map(private_path,(root,project,snapshot,foundation))
    if root.exists() or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise ValueError('fresh_isolated_migration_root_required')
    root.mkdir(parents=True,mode=0o700)
    before_pids=pids()
    manifest,original_snapshot=bundle_files(snapshot)
    state=root/'state'
    original_foundation=clone_foundation(foundation,state)
    database=private_path(manifest['database']['snapshot_path'])
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        raw={name:[dict(r) for r in db.execute('SELECT * FROM "'+name+'"')] for name in
            ('conversationjournalentry','knownperson','personidentity','rollingsessionsummary','workingstatecheckpoint',
             'stream_self_goals','stream_temp_memory')}
    expected={'conversationjournalentry':14,'knownperson':1,'personidentity':2,'rollingsessionsummary':4,
              'workingstatecheckpoint':4,'stream_self_goals':1,'stream_temp_memory':5418}
    if any(len(raw[name])!=count for name,count in expected.items()):raise ValueError('private_snapshot_census_drift')
    if len(manifest['copied_files'])!=5:raise ValueError('private_snapshot_auxiliary_census_drift')
    draft=lambda *_:AssistantResponse(display_text='The fictional copied-state exchange was received.',delivery_mode='text_only')
    def opened():
        store=Store(state)
        return Core(store,draft=draft,runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))
    core=opened()
    try:
        first=reconcile_snapshot(core,snapshot)
        imported_rows=rows(state)
        sources=[body for (kind,_),body in imported_rows.items() if kind=='personal_source' and body.get('legacy_ref')]
        def table_sources(table):return [s for s in sources if s['legacy_ref'].get('table')==table]
        for table in ('conversationjournalentry','knownperson','personidentity','rollingsessionsummary','workingstatecheckpoint','stream_self_goals'):
            primary_key='session_id' if table in {'rollingsessionsummary','workingstatecheckpoint'} else 'id'
            if {s['legacy_ref']['id'] for s in table_sources(table)}!={r[primary_key] for r in raw[table]}:
                raise ValueError('legacy_source_primary_keys_not_preserved')
        auxiliary={s['legacy_ref']['file'] for s in sources if 'file' in s['legacy_ref']}
        if auxiliary!={f['relative_path'] for f in manifest['copied_files']}:raise ValueError('auxiliary_history_missing')
        people=[v for (kind,_),v in imported_rows.items() if kind=='person' and v.get('legacy_table')=='knownperson']
        accounts=[v for (kind,_),v in imported_rows.items() if kind=='account' and v.get('legacy_table')=='personidentity']
        mapping={p['legacy_id']:p['id'] for p in people}
        expected_links={(r['platform'],r['platform_user_id'],mapping[r['person_id']]) for r in raw['personidentity']}
        if {(a['platform'],a['platform_user_id'],a['person_id']) for a in accounts}!=expected_links:
            raise ValueError('exact_legacy_account_tuple_mismatch')
        if any(a.get('grants_authority') is not False for a in accounts):raise ValueError('unexpected_account_authority')
        if any('permissions' in p or 'is_owner' in p for p in people):raise ValueError('unexpected_person_authority')
        records=[v for (kind,_),v in imported_rows.items() if kind=='personal_record' and v.get('source_kind','').startswith('legacy_')]
        if any(v.get('certainty')!='legacy_unverified' for v in records):raise ValueError('legacy_claim_promoted_to_verified')
        if any(v.get('certainty')!='legacy_unverified' for v in sources):raise ValueError('legacy_source_promoted_to_verified')
        pending=[v for v in records if v.get('source_kind')=='legacy_checkpoint']
        if len(pending)!=5 or any(v['status']!='pending' or v.get('execution_authority') is not False for v in pending):
            raise ValueError('legacy_unfinished_goal_authority_mismatch')
        quarantine=[v for (kind,_),v in imported_rows.items() if kind=='personal_quarantine' and v.get('table')=='stream_temp_memory']
        if len(quarantine)!=1 or quarantine[0]['row_count']!=5418 or quarantine[0]['raw_evidence']!='owner_controlled_backup':
            raise ValueError('temporary_stream_aggregate_quarantine_missing')
        if table_sources('stream_temp_memory'):raise ValueError('temporary_stream_promoted_to_personal_source')
        with core.store.transaction() as db:
            if any(core.personal.recall(db,s.get('text','')[:100],audience='shared') for s in sources):
                raise ValueError('private_recall_leaked_to_shared')
        second=reconcile_snapshot(core,snapshot)
        if any(second[k] for k in ('sources_added','records_added','people_added','accounts_added')):
            raise ValueError('private_reimport_not_idempotent')
        if first['migration_id']!=second['migration_id']:raise ValueError('migration_identity_changed')
        # Fictional changes prove that rollback keeps state created after this actual copied-data import.
        core.input('A fictional copied-state experience: copper observatory workshop.','copied-post','copied-post-experience')
        memory=core.personal_control('copied-preference','memory',{'op':'remember','kind':'preference','subject_id':'owner',
            'topic':'fictional copper workshop drink','text':'The fictional copper workshop drink is lavender tea.'})['id']
        current='The fictional copper workshop drink is mint tea.'
        core.personal_control('copied-correction','memory',{'op':'correct','target_id':memory,'kind':'preference','text':current})
        forgotten='fictional-saffron-comet-8673'
        secret=core.personal_control('copied-secret','memory',{'op':'remember','kind':'fact','subject_id':'owner',
            'topic':'fictional erased marker','text':forgotten})['id']
        core.draft=lambda *_:AssistantResponse(display_text='The fictional marker was '+forgotten,delivery_mode='text_only')
        core.input('What was the fictional erased marker?','copied-post','copied-derived-secret')
        core.personal_control('copied-forget','forget',{'target_id':secret})
        core.draft=draft
        authored='Uses brief but clear fictional rehearsal replies.'
        core.personal_control('copied-authored','identity',{'fields':{'description':authored}})
        commitment=core.personal_control('copied-commitment','memory',{'op':'remember','kind':'commitment','subject_id':'owner',
            'topic':'fictional bridge repair commitment','text':'The fictional bridge repair commitment remains unfinished.'})['id']
        core.control('copied-sleep','sleep')
        core.input('A fictional queued message after the private migration.','copied-queue','copied-asleep-message')
        core.control('copied-wake','wake')
        identity=core.personal_state()['identity_id']
    finally:core.close()
    before=rows(state)
    bundle=root/'portable-current-state'
    snapshot_report=export_snapshot(state,bundle)
    restored=root/'restore-validation'
    restore_snapshot(bundle,restored)
    if rows(restored)!=before:raise ValueError('closed_current_snapshot_round_trip_changed_rows')
    old_source=root/'p1-source'
    baseline=prepare_p1_source(project,old_source,project/'data/coordination/p2/p1-source-baseline.zip',
        project/'data/coordination/p2/p1-source-manifest.json')
    projection=project_legacy(state,old_source)
    payload={'state':str(state),'project':str(old_source),'core_sha256':baseline['core_sha256'],
        'forgotten':forgotten,'current':current,'authored':authored,'result':str(root/'private-old-result.json')}
    private_json(root/'private-old-payload.json',payload)
    env=dict(os.environ,PYTHONPATH=str(old_source/'src'),SHANA_PROJECT_ROOT=str(old_source),
        SHANA_DATABASE_URL='sqlite:///'+str(old_source/'data/memory/gamma.db'),SHANA_LLM_PROVIDER='mock',
        SHANA_LLM_ROUTER_ENABLED='false',SHANA_MEMORY_ENABLED='true',TMPDIR='/tmp')
    with identity_lease(state,project/'data/coordination/identities'):
        result=subprocess.run([sys.executable,'-c',PRIVATE_OLD_TURN,str(root/'private-old-payload.json')],
            cwd=old_source,env=env,capture_output=True,text=True,timeout=90)
    # Do not save or print arbitrary exception/provider output that could include private values.
    if result.returncode:raise ValueError('private_old_code_rehearsal_failed')
    old_result=json.loads((root/'private-old-result.json').read_text())
    core=opened()
    try:
        after=rows(state)
        if not set(before)<=set(after):raise ValueError('reversal_dropped_canonical_rows')
        for key,value in before.items():
            if key[0] not in {'brain','activity','person'} and after[key]!=value:
                raise ValueError('reversal_changed_postmigration_history')
            if key[0]=='person':
                expected_person=dict(value)
                if key[1]=='owner':
                    expected_person['interaction_count']+=1  # Exactly one completed genuine old-code turn.
                if after[key]!=expected_person:raise ValueError('reversal_changed_person_state')
        if core.personal_state()['identity_id']!=identity:raise ValueError('reversal_changed_identity')
        if core.store.read('personal_record',memory)['text']!=current:raise ValueError('reversal_lost_correction')
        if core.store.read('personal_record',commitment)['status']!='active':raise ValueError('reversal_lost_unfinished_commitment')
        if core.store.read('turn','copied-asleep-message')['status']!='queued':raise ValueError('reversal_lost_sleep_queue')
        source=core.store.read('personal_source',identifier('src','turn','copied-p1-turn'))
        if not source or source['status']!='active':raise ValueError('old_completed_turn_not_backfilled')
        with core.store.transaction() as db:
            if forgotten in core._context(db,'copied-reversal','fictional erased marker'):
                raise ValueError('forgotten_marker_returned_after_reversal')
            if core.personal.recall(db,'fictional copper workshop drink',audience='shared'):
                raise ValueError('private_recall_shared_after_reversal')
    finally:core.close()
    if any(hash_file(path)!=value for path,value in original_snapshot.items()):raise ValueError('original_private_copy_modified')
    if any(hash_file(path)!=value for path,value in original_foundation.items()):raise ValueError('original_p1_evidence_modified')
    if pids()!=before_pids:raise ValueError('production_service_pids_changed')
    report={'scope':'isolated_private_copied_data_migration_and_reversal','inference_requests':0,
        'snapshot_source_sha256':manifest['database']['snapshot_sha256'],'original_snapshot_files_verified':len(original_snapshot),
        'original_foundation_files_verified':len(original_foundation),'original_sources_unchanged':True,
        'source_census':expected,'journal_primary_keys_preserved':True,'exact_account_links_preserved':True,
        'legacy_claims_verified':False,'authority_granted':False,'auxiliary_sources_preserved':5,
        'legacy_unfinished_reports_preserved':len(pending),'temporary_stream_rows_quarantined':5418,
        'reimport_idempotent':True,'shared_recall_empty':True,'migration_id':first['migration_id'],
        'current_state_snapshot_records':snapshot_report['record_count'],'current_state_snapshot_restored_exactly':True,
        'current_state_snapshot_files':len(snapshot_report['files']),'baseline':baseline,'projection':projection,
        'old_code':old_result,'all_postmigration_rows_preserved':True,'new_old_code_turn_backfilled':True,
        'queued_message_preserved':True,'correction_and_authored_edit_preserved':True,'unfinished_commitment_preserved':True,
        'production_service_pids_unchanged':True,'production_activated':False,'private_prompt_saved':False,
        'legacy_bundle_atomic_across_stores':manifest.get('bundle_atomic_across_legacy_stores',False)}
    private_json(root/'report.json',report)
    for path in root.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--foundation',type=Path,required=True)
    parser.add_argument('--root',type=Path)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    root=args.root or project/'data/coordination/p2'/('private-copy-'+uuid.uuid4().hex)
    try:
        report=verify_copy(root,project,args.snapshot,args.foundation)
    except Exception as exc:
        # Static exception class only. No payload values or private traceback reach tools.
        print(json.dumps({'scope':'private_copy_verification','passed':False,'error_type':type(exc).__name__,
            'error_code':'private_verification_failed'}))
        sys.exit(1)
    print(json.dumps({'evidence_root':str(root),'report':report},indent=2))
