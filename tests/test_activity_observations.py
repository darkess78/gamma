"""Actual admitted files/results reach bounded context with erasure provenance."""
import hashlib
import json

import pytest

from gamma.coordination.activity_observations import bounded_file_previews, project_activity_observations
from gamma.coordination.personal import identifier
from test_coordination_work import make_core


NOTE='PRIVATE_BIRD_NOTE: chirp trill chirp.'
ANALYSIS=json.dumps({'source_path':'note.txt','quote':NOTE,
    'observation':'This selected symbolic string contains a repeated syllable. '+ 'x'*280,
    'limitation':'A symbolic string does not establish an actual bird species.',
    'next_question':'Would a second independently supplied string have the same repeated unit?'})


def specification():
    return {'mode':'build','goal':'Inspect selected symbolic bird data',
        'files':{'analysis.json':'{}','note.txt':NOTE,'verify_analysis.py':'assert True\n'},
        'editable':['analysis.json'],
        'checks':[{'name':'analysis_matches_note','module':'verify_analysis','kind':'feature'}]}


def core_with_task(root):
    core=make_core(root,propose=lambda *_:{'plan':['Inspect admitted note','Save a bounded candidate analysis'],
                                         'edits':{'analysis.json':ANALYSIS}})
    def check(workspace,spec):
        passed=(workspace/'analysis.json').read_text()==ANALYSIS
        return {'name':spec.name,'kind':spec.kind,'passed':passed,'exit_code':0 if passed else 1,
                'output_tail':'candidate bytes checked','infrastructure_error':False}
    core.work_runner.check=check
    activity=core.work_control('prepare','prepare',spec=specification(),authorize_start=True)['activity']
    return core,activity


def complete_own(core,activity):
    with core.store.transaction(core.token) as db:
        current=core.store.get(db,'activity',activity['id'])
        current.update(origin='self_exploration',owner_commitment=False)
        core.store.put(db,'activity',activity['id'],current)
    core.work_control('start','start',task_id=activity['id'])
    core.tick();core.future.result(timeout=20)
    core.status()
    return core.store.read('activity',activity['id'])


def test_prepared_data_is_observed_without_start_or_extra_filesystem_access(tmp_path):
    core,activity=core_with_task(tmp_path)
    try:
        preview=project_activity_observations(core.store,activity)
        assert preview['observation_kind']=='admitted_source_snapshot'
        assert next(f for f in preview['files'] if f['path']=='note.txt')['excerpt']==NOTE
        assert preview['result_ref'] is None
        assert core.store.read('run',activity['id']) is None
        with core.store.transaction(core.token) as db:
            bound=core.growth.activity_observations(db,activity)
        sid=identifier('src','activity',activity['id'],-1)
        assert bound['source_ids']==[sid]
        source=core.store.read('personal_source',sid)
        assert source['source_kind']=='verified_worker_observation'
        assert source['origin_kind']=='admitted_work_source'
        assert 'execution is not implied' in source['text']
        assert source['artifact_ref']==activity['initial_checkpoint_ref']
    finally:core.close()


def test_actual_completed_generated_analysis_reaches_context_with_tail_and_hash(tmp_path):
    core,activity=core_with_task(tmp_path)
    try:
        finished=complete_own(core,activity)
        with core.store.transaction(core.token) as db:
            context=json.loads(core._context(db,'private','What did your project show?'))
        experience=next(e for e in context['growth']['experiences'] if e.get('source_observations'))
        preview=experience['source_observations']
        analysis=next(f for f in preview['files'] if f['path']=='analysis.json')
        assert analysis['excerpt']==ANALYSIS
        assert 'next_question' in analysis['excerpt']
        assert analysis['sha256']==hashlib.sha256(ANALYSIS.encode()).hexdigest()
        assert analysis['excerpt_is_partial'] is False
        assert analysis['role']=='generated_work_product'
        original=next(f for f in preview['files'] if f['path']=='note.txt')
        assert original['role']=='provided_input' and original['excerpt']==NOTE
        assert original['sha256']==hashlib.sha256(NOTE.encode()).hexdigest() and original['excerpt_is_partial'] is False
        assert next(f for f in preview['files'] if f['path']=='verify_analysis.py')['role']=='protected_check'
        assert preview['result_ref']==finished['result_ref']
        assert preview['source_sha256']==finished['source_sha256']
        worker_sid=identifier('src','activity',activity['id'],finished['observed_revision'])
        assert experience['source_ids']==[worker_sid]
        assert identifier('src','activity',activity['id'],-1) in core.store.read('personal_source',worker_sid)['dependencies']['sources']
        assert 'unverified meaning' in preview['interpretation']
        assert sum(len(f['excerpt']) for f in preview['files'])<=960
    finally:core.close()


def test_forgetting_prepared_source_suppresses_feedback_and_new_revision_without_deleting_work(tmp_path):
    core,activity=core_with_task(tmp_path)
    try:
        with core.store.transaction(core.token) as db:
            source_id=core.growth.activity_observations(db,activity)['source_ids'][0]
        core.personal_control('forget-prepared','forget',{'target_id':source_id})
        finished=complete_own(core,activity)
        with core.store.transaction(core.token) as db:
            assert core.growth.activity_observations(db,finished) is None
            raw=json.dumps(core.growth.context(db))
        assert NOTE not in raw
        assert ANALYSIS not in raw
        worker_sid=identifier('src','activity',activity['id'],finished['observed_revision'])
        assert core.store.read('personal_source',worker_sid)['status']=='forgotten'
        assert core.work_state(activity['id'])['files']['analysis.json']==ANALYSIS
        assert core.work_runner.artifacts.get(finished['result_ref'])['source_sha256']==finished['source_sha256']
    finally:core.close()


def test_completed_source_and_choice_depend_on_all_exposed_alternatives(tmp_path):
    core,activity=core_with_task(tmp_path)
    try:
        other=core.work_control('other','prepare',spec={**specification(),'goal':'A distinct admitted alternative'})['activity']
        with core.store.transaction(core.token) as db:
            first=core.growth.activity_observations(db,activity)['source_ids'][0]
            alternative=core.growth.activity_observations(db,other)['source_ids'][0]
            core.store.put(db,'growth_choice','choice-exposed',{'id':'choice-exposed','accepted':True,
                'task_id':activity['id'],'source_ids':[first,alternative],'task_goal':NOTE,
                'reason_code':'The alternative observation shaped this choice.'})
        finished=complete_own(core,activity)
        worker_sid=identifier('src','activity',activity['id'],finished['observed_revision'])
        assert alternative in core.store.read('personal_source',worker_sid)['dependencies']['sources']
        core.personal_control('forget-alternative','forget',{'target_id':alternative})
        with core.store.transaction(core.token) as db:
            assert core.growth.activity_observations(db,finished) is None
            choices=core.growth.context(db)['choices']
        choice=core.store.read('growth_choice','choice-exposed')
        assert choice['task_goal']==choice['reason_code']==''
        assert choice['context_source_removed'] is True
        projected=next(c for c in choices if c['id']=='choice-exposed')
        assert projected['source_ids']==[]
        assert projected['context_source_removed'] is True
        assert core.work_state(activity['id'])['files']['analysis.json']==ANALYSIS
    finally:core.close()


def test_preview_rejects_invalid_completion_and_protected_source_mutation(tmp_path):
    core,activity=core_with_task(tmp_path)
    try:
        with pytest.raises(ValueError):
            project_activity_observations(core.store,{**activity,'checkpoint_ref':'/etc/passwd'})
        finished=complete_own(core,activity)
        with pytest.raises(ValueError,match='completion_mismatch'):
            project_activity_observations(core.store,{**finished,'source_sha256':'0'*64})
        checkpoint=core.work_runner.artifacts.get(finished['checkpoint_ref'])
        source=core.work_runner.artifacts.get(checkpoint['source_ref'])
        source['files']['note.txt']='UNADMITTED_REPLACEMENT'
        checkpoint['source_ref']=core.work_runner.artifacts.put(source)
        forged={**finished,'checkpoint_ref':core.work_runner.artifacts.put(checkpoint)}
        with pytest.raises(ValueError):project_activity_observations(core.store,forged)
    finally:core.close()


def test_bounded_helper_does_not_execute_data_and_preserves_complete_small_json():
    rows=bounded_file_previews({'analysis.json':ANALYSIS,'note.txt':NOTE,'verify.py':'raise AssertionError'},['analysis.json'])
    assert rows[0]['excerpt']==ANALYSIS
    assert len(rows)<=3
    assert sum(len(row['excerpt']) for row in rows)<=960
    for value,editable in (([],()),({'x':1},()),({'x':'data'},['outside'])):
        with pytest.raises(ValueError):bounded_file_previews(value,editable)


def test_primary_input_survives_multiple_work_products_with_truthful_roles_and_partial_flags():
    original='A'*128
    files={'a-output.txt':'x'*700,'b-output.txt':'y'*700,'c-output.txt':'z'*700,
           'provided.dat':original,'guard.py':'assert False'}
    rows=bounded_file_previews(files,['a-output.txt','b-output.txt','c-output.txt'],['guard.py'])
    assert [r['role'] for r in rows]==['generated_work_product','provided_input','generated_work_product']
    assert rows[0]['excerpt']==files['a-output.txt'] and not rows[0]['excerpt_is_partial']
    assert rows[1]['excerpt']==original and not rows[1]['excerpt_is_partial']
    assert rows[2]['excerpt_is_partial'] is True
    assert sum(len(r['excerpt']) for r in rows)==960
    for row in rows:assert row['sha256']==hashlib.sha256(files[row['path']].encode()).hexdigest()
    for checks in ('guard.py',['outside'],['a-output.txt']):
        with pytest.raises(ValueError):bounded_file_previews(files,['a-output.txt'],checks)


def test_canonical_root_migration_reuses_the_existing_observation_and_hypothesis_reference(tmp_path):
    core,activity=core_with_task(tmp_path)
    try:
        finished=complete_own(core,activity)
        event=next(e for e in core.growth_state()['experiences'] if e.get('source_observations'))
        hypothesis=core.growth_control('migration-hypothesis','hypothesis',{
            'statement':'The selected symbolic note may contain a repeated unit.','kind':'judgment',
            'scope_target':'dialogue','scope_marker':'symbolic note','experience_ids':[event['id']],
            'alternatives':['A larger sample could contradict this interpretation.'],
            'expected_outcome':'Keep the interpretation tentative.'})['hypothesis']
        sid=identifier('src','activity',activity['id'],finished['observed_revision'])
        with core.store.transaction(core.token) as db:
            # Reconstruct the previous flattened provenance representation.
            old=core.store.get(db,'growth_experience',event['id'])
            old['source_ids']=[sid,identifier('src','activity',activity['id'],-1)]
            old['source_observations']['source_ids']=list(old['source_ids'])
            core.store.put(db,'growth_experience',event['id'],old)
            core.growth.context(db)
            first=db.execute("SELECT rowid FROM records WHERE kind='growth_experience' AND id=?",(event['id'],)).fetchone()[0]
            for _ in range(3):core.growth.context(db)
            last=db.execute("SELECT rowid FROM records WHERE kind='growth_experience' AND id=?",(event['id'],)).fetchone()[0]
        matching=[e for e in core.growth_state()['experiences'] if e.get('activity_id')==activity['id'] and e.get('source_observations')]
        assert len(matching)==1 and matching[0]['id']==event['id']
        assert matching[0]['created_at']==event['created_at'] and matching[0]['observed_at']==event['observed_at']
        assert matching[0]['source_ids']==[sid] and first==last
        assert core.store.read('growth_hypothesis',hypothesis['id'])['experience_ids']==[event['id']]
    finally:core.close()


def test_revision_observation_does_not_depend_on_itself_or_a_later_choice(tmp_path):
    clock=[1000.];core=make_core(tmp_path,clock=lambda:clock[0])
    try:
        activity=core.work_control('causal-prepare','prepare',spec=specification(),authorize_start=True)['activity']
        other=core.work_control('causal-other','prepare',spec={**specification(),'goal':'An admitted alternative'})['activity']
        with core.store.transaction(core.token) as db:
            current=core.store.get(db,'activity',activity['id']);current['origin']='self_exploration'
            core.store.put(db,'activity',current['id'],current)
            other_sid=core.growth.activity_observations(db,other)['source_ids'][0]
        core.work_control('causal-start','start',task_id=activity['id'])
        # Drive actual admission while suppressing execution in this graph fixture.
        execute=core.work_runner.run;core.work_runner.run=lambda *_:None
        core.tick();core.future.result(timeout=10);core.work_runner.run=execute
        run=core.store.read('run',activity['id'])
        # A valid paused worker checkpoint before a later accepted quiet choice.
        core.store.update_run(run['id'],run['checkpoint_ref'],status='paused',reason='pause_acknowledged')
        core.status();paused=core.store.read('activity',activity['id'])
        sid=identifier('src','activity',paused['id'],paused['observed_revision'])
        clock[0]=1100.
        with core.store.transaction(core.token) as db:
            core.store.put(db,'growth_choice','choice-later',{'id':'choice-later','task_id':activity['id'],
                'accepted':True,'action':'resume','observed_at':1100.,'source_ids':[sid,other_sid]})
            core.growth.activity_observations(db,paused)
        prior=core.store.read('personal_source',sid)['dependencies']['sources']
        assert sid not in prior and other_sid not in prior
        clock[0]=1110.
        core.store.update_run(run['id'],run['checkpoint_ref'],status='paused',reason='pause_acknowledged')
        core.status();new=core.store.read('activity',activity['id'])
        new_sid=identifier('src','activity',new['id'],new['observed_revision'])
        deps=core.store.read('personal_source',new_sid)['dependencies']['sources']
        assert sid in deps and other_sid in deps and new_sid not in deps
    finally:core.close()
