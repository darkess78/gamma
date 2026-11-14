"""Fictional P2 -> real preserved P1 code -> P2 continuity rehearsal on Gamma.

The language model in the P1 turn is a capture adapter. This proves the actual
old prompt/parser/owner path and state survival, not model quality or activation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from gamma.coordination.core import Core
from gamma.coordination.store import Store, private_path
from gamma.coordination.personal import identifier
from gamma.coordination.transfer import export_snapshot, restore_snapshot, prepare_p1_source, project_legacy, identity_lease
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


OLD_TURN = r'''
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
captured=[]
class Capture:
    supports_vision=False
    def generate_reply(self,system_prompt,user_text,*args,**kwargs):
        captured.append(system_prompt)
        return LLMReply(text='The mint tea preference is current. The blueprint commitment is still unfinished.')
service=ConversationService()
service._llm=Capture()
def draft(text,context):
    return service.respond(text,synthesize_speech=False,fast_mode=True,evaluation_mode=True,coordination_mode=True,
        speech_allowed=False,speech_requested=False,delivery_context='direct_text',background_context=context,
        recipient=RecipientContext('owner_private','dashboard_monitor',True))
store=Store(payload['state'])
runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{})
core=module.Core(store,draft=draft,runner=runner)
try:
    result=core.input('What workshop drink did we settle on, and what blueprint work remains?', 'reversal','genuine-p1-turn')
    assert result['result']['assistant_response']['display_text']
    assert captured, 'old service did not reach the provider boundary'
    prompts='\n'.join(captured)
    assert payload['forgotten'] not in prompts
    assert 'mint tea' in prompts
    assert 'brief but clear' in prompts
    assert 'jasmine tea' not in prompts
    assert 'gamma.coordination.personal' not in sys.modules
    pathlib.Path(payload['prompt']).write_text(prompts)
    pathlib.Path(payload['result']).write_text(json.dumps({'old_core_sha256':payload['core_sha256'],
        'actual_old_service_provider_calls':len(captured),'forgotten_absent':True,'current_correction_present':True,
        'authored_edit_present':True,'p2_module_imported':False,'turn_completed':True}))
finally:
    core.close()
'''


def run_reversal(root, project):
    root,project=private_path(root),private_path(project)
    if root.exists():
        raise ValueError('reversal_requires_new_evidence_directory')
    if project.name in {'gamma-main','gamma-modular-phase1'}:
        raise ValueError('reversal_requires_isolated_candidate')
    root.mkdir(parents=True,mode=0o700)
    state=root/'state'
    def draft(text,context):
        return AssistantResponse(display_text='Retained the fictional exchange.',delivery_mode='text_only')
    def opened():
        store=Store(state)
        return Core(store,draft=draft,runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))
    core=opened()
    forgotten='violet-cinder-4072'
    try:
        core.input('The fictional first experience was a trip to the lighthouse.','before','before-migration')
        memory=core.personal_control('drink','memory',{'op':'remember','kind':'preference','subject_id':'owner',
            'topic':'workshop drink','text':'The workshop drink is jasmine tea.'})['id']
        core.personal_control('correction','memory',{'op':'correct','target_id':memory,'kind':'preference',
            'text':'The workshop drink is mint tea.'})
        removed=core.personal_control('secret','memory',{'op':'remember','kind':'fact','subject_id':'owner',
            'topic':'forgotten rehearsal marker','text':forgotten})['id']
        core.draft=lambda *_:AssistantResponse(display_text='I recall '+forgotten,delivery_mode='text_only')
        core.input('What was the forgotten rehearsal marker?','after','derived-secret')
        core.personal_control('remove','forget',{'target_id':removed})
        core.draft=draft
        core.input('The fictional second experience was the observatory visit.','after','post-migration')
        core.personal_control('authored','identity',{'fields':{'description':'Uses brief but clear responses.'}})
        commitment=core.personal_control('commitment','memory',{'op':'remember','kind':'commitment','subject_id':'owner',
            'topic':'blueprint repair','text':'The blueprint repair remains unfinished.'})['id']
        core.control('permit','permit',exercise_ids=['retry_budget'],budget_seconds=300,maximum_attempts=2)
        activity=core.control('practice','practice',exercise_id='retry_budget',budget_seconds=300,maximum_attempts=2)['activity']
        core.control('sleep','sleep')
        core.input('A queued fictional communication for the next wake.','queue','asleep-queued')
        core.control('wake','wake')
        identity=core.personal_state()['identity_id']
    finally:
        core.close()
    before_rows=_rows(state)
    run_before=Store(state).read('run',activity['id'])
    bundle=root/'portable-snapshot'
    snapshot=export_snapshot(state,bundle)
    restored=root/'restored'
    restore_snapshot(bundle,restored)
    assert _rows(restored)==before_rows
    old_source=root/'p1-source'
    baseline=prepare_p1_source(project,old_source,project/'data/coordination/p2/p1-source-baseline.zip',
        project/'data/coordination/p2/p1-source-manifest.json')
    projection=project_legacy(state,old_source)
    payload={'state':str(state),'project':str(old_source),'core_sha256':baseline['core_sha256'],
        'forgotten':forgotten,'prompt':str(root/'p1-provider-prompt.txt'),'result':str(root/'p1-result.json')}
    (root/'p1-payload.json').write_text(json.dumps(payload))
    env=dict(os.environ,PYTHONPATH=str(old_source/'src'),SHANA_PROJECT_ROOT=str(old_source),
        SHANA_DATABASE_URL='sqlite:///'+str(old_source/'data/memory/gamma.db'),SHANA_LLM_PROVIDER='mock',
        SHANA_LLM_ROUTER_ENABLED='false',SHANA_MEMORY_ENABLED='true',TMPDIR='/tmp')
    with identity_lease(state,project/'data/coordination/identities'):
        result=subprocess.run([sys.executable,'-c',OLD_TURN,str(root/'p1-payload.json')],cwd=old_source,
            env=env,capture_output=True,text=True,timeout=90)
    (root/'p1-stdout.log').write_text(result.stdout)
    (root/'p1-stderr.log').write_text(result.stderr)
    if result.returncode:
        raise RuntimeError('genuine_p1_reversal_failed; inspect private p1-stderr.log')
    core=opened()
    try:
        with core.store.transaction() as db:
            old_turn=core.store.get(db,'turn','genuine-p1-turn')
            assert old_turn['status']=='completed'
            source=core.store.get(db,'personal_source',identifier('src','turn','genuine-p1-turn'))
            assert source and source['status']=='active'
            current=core.store.get(db,'personal_record',memory)
            assert current['id']==memory and current['text']=='The workshop drink is mint tea.'
            assert core.store.get(db,'personal_record',commitment)['status']=='active'
            assert core.store.get(db,'turn','asleep-queued')['status']=='queued'
            assert forgotten not in core._context(db,'reversal','forgotten rehearsal marker')
            assert core.store.get(db,'turn','post-migration')['status']=='completed'
        assert core.personal_state()['identity_id']==identity
        assert core.personal_state()['identity']['fields']['description']=='Uses brief but clear responses.'
        run_after=core.store.read('run',activity['id'])
        for key in ('id','expires_at','maximum_attempts','budget_seconds','checkpoint_ref','initial_checkpoint_ref'):
            assert run_after[key]==run_before[key]
        core.runner.artifacts.get(run_after['checkpoint_ref'])
    finally:
        core.close()
    after=_rows(state)
    # Old code may advance operational observation fields but cannot drop unknown P2 rows.
    for key,value in before_rows.items():
        if key[0] not in {'brain','activity','person'}:
            assert after[key]==value, key
    report={'scope':'isolated_fictional_reversal','model':'scripted_provider_capture',
        'snapshot':{'records':snapshot['record_count'],'files':len(snapshot['files']),'restored_exactly':True},
        'baseline':baseline,'projection':projection,'old_code':json.loads((root/'p1-result.json').read_text()),
        'all_postmigration_state_retained':True,'old_turn_backfilled_in_p2':True,'queued_communication_retained':True,
        'stable_identity_retained':True,'budgets_deadlines_and_artifacts_retained':True,'production_activated':False}
    (root/'report.json').write_text(json.dumps(report,indent=2))
    for path in root.rglob('*'):
        if path.is_file(): path.chmod(0o600)
    return report


def _rows(root):
    store=Store(root)
    with store.transaction() as db:
        return {(kind,key):json.loads(body) for kind,key,body in db.execute('SELECT kind,id,body FROM records')}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    root=args.root or project/'data/coordination/p2'/('reversal-'+uuid.uuid4().hex)
    report=run_reversal(root,project)
    print(json.dumps({'evidence_root':str(root),'report':report},indent=2))
