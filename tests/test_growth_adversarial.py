"""Independent integrity, authority and reversal checks for scoped development."""
from copy import deepcopy
import hashlib

import pytest

from gamma.coding.practice_artifacts import ArtifactStore
from gamma.coordination.growth_models import frozen_cases, validate_comparison, validate_scope
from gamma.coordination.growth import GrowthState
from gamma.coordination.personal import PersonalState, digest, identifier
from gamma.coordination.store import Store
from gamma.coordination.transfer import export_snapshot, restore_snapshot


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def measurement_fixture(tmp_path, *, artifacts=None, source_ids=None):
    """Synthetic unit data. These flags are not evidence of model improvement."""
    artifacts=artifacts or ArtifactStore(tmp_path/'artifacts')
    source_ids=source_ids or ['src-'+'b'*24]
    frozen={'kind':'growth_frozen_cases','format':1,'component':'dialogue_guidance',
        'model':'fictional-evaluation-model','source_sha256':fingerprint('fixed candidate source'),
        'checker_sha256':fingerprint('fixed independent checker'),'frozen_at':900.,
        'scope_target':'dialogue','scope_marker':'work-status','source_ids':source_ids,
        'development_families':['development-owner-correction'],
        'development_input_sha256':[fingerprint('historical development question')],
        'evaluation_families':['new-input-question','new-outcome-question'],
        'cases':[{'id':name,'family':family,'input_sha256':fingerprint('new question '+name),
            'expected_sha256':fingerprint('actual checked outcome '+name)} for name,family in (
                ('case-a','new-input-question'),('case-b','new-outcome-question'))]}
    frozen_ref=artifacts.put(frozen)
    trial={'id':'trial-'+'a'*24,'version':1,'guidance_sha256':fingerprint('A narrowly scoped trial.'),
        'created_at':1000.,'scope_target':'dialogue','scope_marker':'work-status','frozen_ref':frozen_ref}
    comparison={'kind':'growth_comparison','format':1,'trial_id':trial['id'],'trial_version':1,
        'guidance_sha256':trial['guidance_sha256'],'frozen_ref':frozen_ref,'model':frozen['model'],
        'source_sha256':frozen['source_sha256'],'checker_sha256':frozen['checker_sha256'],
        'scope_target':'dialogue','scope_marker':'work-status','measured':True,'evaluator':'objective_code',
        'started_at':1001.,'finished_at':1005.,'cases':[],'regressions':[]}
    for definition in frozen['cases']:
        case=dict(definition,baseline_passed=False,candidate_passed=True)
        for phase in ('baseline','candidate'):
            response={'kind':'growth_case_response','format':1,'case_id':definition['id'],'phase':phase,
                'model':frozen['model'],'source_sha256':frozen['source_sha256'],
                'input_sha256':definition['input_sha256'],'response':{'reason_code':'unit-fixture-only'},
                'source_ids':source_ids,
                'checker':{'implementation_sha256':frozen['checker_sha256'],
                    'expected_sha256':definition['expected_sha256'],'passed':phase=='candidate'},
                'trial_id':None if phase=='baseline' else trial['id'],
                'trial_version':0 if phase=='baseline' else trial['version'],
                'guidance_sha256':None if phase=='baseline' else trial['guidance_sha256']}
            case[phase+'_ref']=artifacts.put(response)
        comparison['cases'].append(case)
    for name in ('identity','privacy','authority'):
        ref=artifacts.put({'kind':'growth_regression_evidence','format':1,'name':name,'passed':True,
            'checker_sha256':frozen['checker_sha256'],'source_sha256':frozen['source_sha256'],
            'model':frozen['model'],'source_ids':source_ids,'details':{'fixture':'synthetic unit evidence'}})
        comparison['regressions'].append({'name':name,'passed':True,'evidence_ref':ref})
    return artifacts,frozen,trial,comparison


def check_fixture(values):
    artifacts,frozen,trial,comparison=values
    return validate_comparison(comparison,frozen,trial,artifacts)


def test_comparison_keeps_integrity_distinct_from_measurement_truth(tmp_path):
    result=check_fixture(measurement_fixture(tmp_path))
    assert result['improved_cases']==['case-a','case-b']
    assert result['evidence_provenance']=='trusted_owner_measurement'
    assert result['truth_not_certified_by_hash'] is True


@pytest.mark.parametrize('extra', [{'confidence':1.0},{'score':100},{'agreement_count':3},{'minimum_successes':1}])
def test_vote_score_or_arbitrary_count_cannot_replace_bound_evidence(tmp_path,extra):
    values=measurement_fixture(tmp_path)
    values[3].update(extra)
    with pytest.raises(ValueError):check_fixture(values)


@pytest.mark.parametrize('key,value', [('measured',1),('evaluator','llm_judge'),('format',True),
    ('trial_version',True),('trial_version',2),('guidance_sha256','b'*64),
    ('model','different-model'),('source_sha256','c'*64),('checker_sha256','d'*64),
    ('scope_target','work'),('scope_marker','all conversations')])
def test_evaluation_rejects_changed_component_trial_or_nonobjective_claim(tmp_path,key,value):
    values=measurement_fixture(tmp_path);values[3][key]=value
    with pytest.raises(ValueError):check_fixture(values)


def test_comparison_frozen_reference_is_the_actual_pretrial_manifest(tmp_path):
    values=measurement_fixture(tmp_path)
    values[3]['frozen_ref']=values[0].put({'kind':'unrelated_artifact'})
    with pytest.raises(ValueError):check_fixture(values)


def test_frozen_cases_cannot_be_created_after_trial(tmp_path):
    values=measurement_fixture(tmp_path);values[1]['frozen_at']=1002.
    with pytest.raises(ValueError):check_fixture(values)


@pytest.mark.parametrize('mutation',['duplicate','missing','family','input','expected','candidate_flag'])
def test_case_selection_and_boolean_claims_cannot_drift_after_freezing(tmp_path,mutation):
    values=measurement_fixture(tmp_path);comparison=values[3]
    if mutation=='duplicate':comparison['cases'][1]=deepcopy(comparison['cases'][0])
    elif mutation=='missing':comparison['cases'].pop()
    elif mutation=='family':comparison['cases'][0]['family']='development-owner-correction'
    elif mutation=='input':comparison['cases'][0]['input_sha256']='e'*64
    elif mutation=='expected':comparison['cases'][0]['expected_sha256']='f'*64
    else:comparison['cases'][0]['candidate_passed']=1
    with pytest.raises(ValueError):check_fixture(values)


@pytest.mark.parametrize('phase,field,value', [('baseline','trial_id','trial-'+'a'*24),
    ('baseline','trial_version',1),('baseline','guidance_sha256','b'*64),
    ('candidate','trial_id','trial-'+'b'*24),('candidate','trial_version',2),
    ('candidate','guidance_sha256','c'*64),('candidate','input_sha256','d'*64),
    ('candidate','model','different-model'),('candidate','source_sha256','e'*64)])
def test_response_artifacts_are_bound_to_the_correct_phase_and_trial(tmp_path,phase,field,value):
    artifacts,frozen,trial,comparison=measurement_fixture(tmp_path)
    response=artifacts.get(comparison['cases'][0][phase+'_ref']);response[field]=value
    comparison['cases'][0][phase+'_ref']=artifacts.put(response)
    with pytest.raises(ValueError):validate_comparison(comparison,frozen,trial,artifacts)


def test_claimed_success_must_match_the_saved_checker_receipt(tmp_path):
    artifacts,frozen,trial,comparison=measurement_fixture(tmp_path)
    response=artifacts.get(comparison['cases'][0]['candidate_ref']);response['checker']['passed']=False
    comparison['cases'][0]['candidate_ref']=artifacts.put(response)
    with pytest.raises(ValueError):validate_comparison(comparison,frozen,trial,artifacts)


@pytest.mark.parametrize('mutation',['missing_privacy','duplicate_authority','different_source','mismatched_pass'])
def test_regression_guards_are_complete_and_source_bound(tmp_path,mutation):
    artifacts,frozen,trial,comparison=measurement_fixture(tmp_path)
    if mutation=='missing_privacy':comparison['regressions'].pop(1)
    elif mutation=='duplicate_authority':comparison['regressions'][1]=deepcopy(comparison['regressions'][2])
    else:
        evidence=artifacts.get(comparison['regressions'][0]['evidence_ref'])
        evidence['source_sha256' if mutation=='different_source' else 'passed']='f'*64 if mutation=='different_source' else False
        comparison['regressions'][0]['evidence_ref']=artifacts.put(evidence)
    with pytest.raises(ValueError):validate_comparison(comparison,frozen,trial,artifacts)


def test_families_cannot_overlap_or_omit_an_evaluation_family(tmp_path):
    values=measurement_fixture(tmp_path)
    frozen=deepcopy(values[1]);frozen['development_families'].append('new-input-question')
    with pytest.raises(ValueError):frozen_cases(frozen)
    frozen=deepcopy(values[1]);frozen['evaluation_families'].append('unrepresented-family')
    with pytest.raises(ValueError):frozen_cases(frozen)


def test_exact_development_input_cannot_be_relabelled_as_new_evaluation_family(tmp_path):
    values=measurement_fixture(tmp_path);frozen=deepcopy(values[1])
    frozen['development_input_sha256'].append(frozen['cases'][0]['input_sha256'])
    with pytest.raises(ValueError):frozen_cases(frozen)


@pytest.mark.parametrize('key,value',[('target','work'),('expires_seconds',True),('expires_seconds',0),
    ('expires_seconds',86401),('marker',''),('marker','\n'),('guidance',''),('review_rationale','')])
def test_unsupported_or_unbounded_trial_scope_is_rejected(key,value):
    scope={'target':'dialogue','marker':'work-status','guidance':'Inspect the bound task receipt.',
        'expires_seconds':300,'review_condition':'Review contradictory results.','review_rationale':'One observed mistake needs a narrow trial.'}
    scope[key]=value
    with pytest.raises(ValueError):validate_scope(scope)


@pytest.fixture
def state_case(tmp_path):
    store=Store(tmp_path/'state');token=store.claim(0);clock=[1000.]
    personal=PersonalState(store,token,clock=lambda:clock[0])
    growth=GrowthState(store,token,personal,clock=lambda:clock[0])
    source_id=identifier('src','adversarial-source')
    with store.transaction(token) as db:
        store.put(db,'personal_source',source_id,{'id':source_id,'source_kind':'private_conversation',
            'origin_kind':'owner_input','scope':'owner_private','status':'active',
            'text':'A fictional source for scoped development.','assistant_text':'',
            'text_sha256':digest('A fictional source for scoped development.'),
            'occurred_at':990.,'ingested_at':990.,'dependencies':{'sources':[],'memories':[]}})
    def control(action,values):
        with store.transaction(token) as db:return growth.control(db,action,values)
    experience=control('experience',{'source_ids':[source_id],'situation':'A work-status question.',
        'options':['Read actual receipt','Guess from an earlier report'],'action':'Answered before inspecting the receipt.',
        'expected_outcome':'Report the actual blocker.','observed_outcome':'The response omitted the checked blocker.',
        'uncertainty':'A synthetic unit fixture, not evidence of a real model error.'})['experience']
    hypothesis=control('hypothesis',{'experience_ids':[experience['id']],'statement':'A receipt check may improve scoped status answers.',
        'kind':'judgment','scope_target':'dialogue','scope_marker':'work-status',
        'alternatives':['The wording might have been unclear.'],'expected_outcome':'Source-bound checked status answers.'})['hypothesis']
    artifacts,frozen,placeholder,comparison=measurement_fixture(tmp_path/'measurement',artifacts=growth.artifacts,source_ids=[source_id])
    trial=control('trial',{'hypothesis_id':hypothesis['id'],'guidance':'Inspect the bound worker receipt before answering work-status questions.',
        'scope_target':'dialogue','scope_marker':'work-status','expires_seconds':300,
        'review_condition':'Stop and review contradictory outcomes.','review_rationale':'Try a bounded correction to one observed error.',
        'frozen_ref':placeholder['frozen_ref']})['trial']
    comparison.update(trial_id=trial['id'],trial_version=trial['version'],guidance_sha256=trial['guidance_sha256'])
    for case in comparison['cases']:
        response=artifacts.get(case['candidate_ref'])
        response.update(trial_id=trial['id'],trial_version=trial['version'],guidance_sha256=trial['guidance_sha256'])
        case['candidate_ref']=artifacts.put(response)
    case={'store':store,'token':token,'clock':clock,'personal':personal,'growth':growth,'control':control,
        'source_id':source_id,'experience':experience,'hypothesis':hypothesis,'trial':trial,'comparison':comparison}
    try:yield case
    finally:
        if not case.get('released'):store.release(token)


def visible_guidance(case,text='A work-status question.'):
    with case['store'].transaction(case['token']) as db:
        return case['growth'].context(db,'dialogue',text)['guidance']


def test_trial_influence_is_scoped_and_expiry_survives_new_reader(state_case):
    case=state_case
    assert visible_guidance(case)
    assert visible_guidance(case,'An unrelated personal conversation.')==[]
    case['clock'][0]=case['trial']['expires_at']
    # Reconstructing the component must not reset the trial's original expiry.
    case['growth']=GrowthState(case['store'],case['token'],case['personal'],clock=lambda:case['clock'][0])
    assert visible_guidance(case)==[]
    assert case['store'].read('growth_trial',case['trial']['id'])['status']=='expired'


def test_withdrawal_removes_guidance_without_erasing_the_experience(state_case):
    case=state_case;old=deepcopy(case['experience'])
    case['control']('withdraw',{'trial_id':case['trial']['id'],'version':case['trial']['version'],'reason':'The narrow trial is no longer useful.'})
    assert visible_guidance(case)==[]
    assert case['store'].read('growth_experience',old['id'])==old
    with pytest.raises(ValueError):
        case['control']('accept',{'trial_id':case['trial']['id'],'version':case['trial']['version'],'review_rationale':'Please accept without evidence.'})


def test_contradiction_suspends_influence_and_old_lesson_remains_reviewable(state_case):
    case=state_case;before=deepcopy(case['hypothesis'])
    case['control']('contradict',{'hypothesis_id':before['id'],'experience_id':case['experience']['id'],
        'note':'The evidence also fits a narrower explanation.'})
    assert visible_guidance(case)==[]
    hypothesis=case['store'].read('growth_hypothesis',before['id'])
    assert hypothesis['statement']==before['statement'] and hypothesis['status']=='needs_review'
    assert hypothesis['contradicting_experience_ids']==[case['experience']['id']]


def test_failed_new_case_suspends_guidance_even_when_baseline_also_failed(state_case):
    case=state_case;comparison=deepcopy(case['comparison']);artifacts=case['growth'].artifacts
    response=artifacts.get(comparison['cases'][0]['candidate_ref']);response['checker']['passed']=False
    comparison['cases'][0].update(candidate_passed=False,candidate_ref=artifacts.put(response))
    case['clock'][0]=1010.
    result=case['control']('evaluate',{'trial_id':case['trial']['id'],'evaluation_ref':artifacts.put(comparison)})
    assert result['trial']['status']=='needs_review'
    assert visible_guidance(case)==[]


def test_impossible_future_measurement_cannot_be_accepted_as_observed(state_case):
    case=state_case;ref=case['growth'].artifacts.put(case['comparison'])
    assert case['comparison']['finished_at']>case['clock'][0]
    with pytest.raises(ValueError):case['control']('evaluate',{'trial_id':case['trial']['id'],'evaluation_ref':ref})


def test_deleted_sources_remove_guidance_and_live_quoted_evidence(state_case):
    case=state_case;marker='PRIVATE_GROWTH_SOURCE_cedar_1783'
    comparison=deepcopy(case['comparison']);artifacts=case['growth'].artifacts
    response=artifacts.get(comparison['cases'][0]['candidate_ref']);response['response']['display_text']=marker
    comparison['cases'][0]['candidate_ref']=artifacts.put(response)
    case['clock'][0]=1010.
    case['control']('evaluate',{'trial_id':case['trial']['id'],'evaluation_ref':artifacts.put(comparison)})
    assert any(marker.encode() in p.read_bytes() for p in artifacts.root.glob('*.json'))
    with case['store'].transaction(case['token']) as db:
        case['personal'].forget(db,case['source_id'])
        case['growth'].sync(db)
    assert visible_guidance(case)==[]
    assert case['store'].read('growth_experience',case['experience']['id'])['status']=='source_removed'
    assert not any(marker.encode() in p.read_bytes() for p in artifacts.root.glob('*.json'))
    case['store'].release(case['token']);case['released']=True
    bundle=case['store'].root.parent/'after-forgetting-snapshot'
    export_snapshot(case['store'].root,bundle)
    assert not any(marker.encode() in p.read_bytes() for p in bundle.rglob('*') if p.is_file())


def test_unaccepted_measurement_response_cannot_retain_forgotten_source_text(state_case):
    case=state_case;marker='PRIVATE_UNACCEPTED_GROWTH_cedar_8812'
    artifacts=case['growth'].artifacts
    orphan=artifacts.get(case['comparison']['cases'][0]['candidate_ref'])
    orphan['response']['display_text']=marker
    ref=artifacts.put(orphan)
    assert marker.encode() in (artifacts.root/(ref+'.json')).read_bytes()
    # A verifier may save a response then stop before creating or accepting its
    # comparison. It is still a derivative of the identified private source.
    with case['store'].transaction(case['token']) as db:
        case['personal'].forget(db,case['source_id'])
        case['growth'].sync(db)
    assert visible_guidance(case)==[]
    assert not any(marker.encode() in p.read_bytes() for p in artifacts.root.glob('*.json'))


def test_source_removed_trial_and_interpretation_clear_source_specific_scope_labels(state_case):
    case=state_case
    with case['store'].transaction(case['token']) as db:
        case['personal'].forget(db,case['source_id']);case['growth'].sync(db)
    assert case['store'].read('growth_hypothesis',case['hypothesis']['id'])['scope_marker']==''
    assert case['store'].read('growth_trial',case['trial']['id'])['scope_marker']==''


def test_owner_control_replay_does_not_return_forgotten_trial_text(tmp_path):
    from test_coordination_work import make_core
    from test_coordination_growth import start_trial
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        trial,sid=start_trial(core)
        original_values={key:trial[key] for key in ('hypothesis_id','guidance','scope_target','scope_marker',
            'review_condition','review_rationale','frozen_ref')}
        original_values['expires_seconds']=30
        cached=core.store.read('growth_control','trial')
        assert 'SCOPED_TRIAL_TEXT' in str(cached)
        core.personal_control('forget-control-source','forget',{'target_id':sid})
        cached=core.store.read('growth_control','trial')
        assert 'SCOPED_TRIAL_TEXT' not in str(cached)
        assert trial['frozen_ref'] not in str(cached)
        replay=core.growth_control('trial','trial',original_values)
        assert 'SCOPED_TRIAL_TEXT' not in str(replay)
        assert trial['frozen_ref'] not in str(replay)
    finally:core.close()


def test_owner_control_erasure_preserves_current_reader_snapshot_closure(tmp_path):
    from test_coordination_work import make_core
    from test_coordination_growth import start_trial
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        _trial,sid=start_trial(core)
        core.personal_control('forget-control-source','forget',{'target_id':sid})
    finally:core.close()
    bundle=tmp_path.parent/(tmp_path.name+'-snapshot')
    export_snapshot(core.store.root,bundle)
    assert not any(b'SCOPED_TRIAL_TEXT' in p.read_bytes() for p in bundle.rglob('*') if p.is_file())


def test_descriptive_growth_context_binds_old_sources_and_forgetting_cascades(tmp_path):
    import json
    from test_coordination_work import make_core
    from gamma.coordination.personal_models import PersonalStateUpdates
    from gamma.schemas.response import AssistantResponse
    marker='PRIVATE_DESCRIPTIVE_GROWTH_violet_2271'
    core=make_core(tmp_path,clock=lambda:1000.,draft=lambda *_:AssistantResponse(
        display_text='Received.',delivery_mode='text_only'))
    try:
        core.input(marker,'archived-session','old-source')
        sid=core.store.read('turn','old-source')['personal_receipt']['source_id']
        experience=core.growth_control('descriptive-exp','experience',{'source_ids':[sid],
            'situation':marker,'options':['Inspect another possibility.'],'action':'Observe only.',
            'expected_outcome':'No trial is active.','observed_outcome':marker,
            'uncertainty':'Synthetic dependency test.'})['experience']
        core.growth_control('descriptive-hyp','hypothesis',{'experience_ids':[experience['id']],
            'statement':marker,'kind':'interest','scope_target':'dialogue','scope_marker':'archived-marker',
            'alternatives':['This could be incidental.'],'expected_outcome':'A tentative interpretation only.'})
        unseen=identifier('src','not-supplied-in-model-context')
        with core.store.transaction(core.token) as db:
            core.store.put(db,'personal_source',unseen,{'id':unseen,'source_kind':'private_conversation',
                'scope':'owner_private','status':'active','text':'Unexposed zirconium.',
                'text_sha256':digest('Unexposed zirconium.'),'occurred_at':900.,'ingested_at':900.,
                'dependencies':{'sources':[],'memories':[]}})
        observed=[]
        def respond(_text,raw):
            context=json.loads(raw);observed.append(context)
            assert context['growth']['guidance']==[]
            assert sid not in context['personal']['recent_source_ids']
            assert sid not in [r['source_id'] for r in context['personal']['recall']]
            assert marker in str(context['growth']['experiences'])+str(context['growth']['hypotheses'])
            return AssistantResponse(display_text=marker,delivery_mode='text_only',
                state_updates=PersonalStateUpdates(growth_proposals=[{'statement':marker,
                    'kind':'interest','scope_target':'dialogue','scope_marker':'new-marker',
                    'alternatives':['The interpretation may be wrong.'],'expected_outcome':'Tentative only.'}]))
        core.draft=respond
        core.input('Discuss neutrino oscillations.','fresh-session','new-derived')
        assert observed
        derived_id=core.store.read('turn','new-derived')['personal_receipt']['source_id']
        dependencies=core.store.read('personal_source',derived_id)['dependencies']['sources']
        assert sid in dependencies and unseen not in dependencies
        assert core.store.read('turn','new-derived')['growth_receipt']['hypotheses'][0]['accepted'] is True
        core.personal_control('forget-descriptive-source','forget',{'target_id':sid})
        assert core.store.read('personal_source',derived_id)['status']=='forgotten'
        assert core.store.read('turn','new-derived')['redacted'] is True
        with core.store.transaction(core.token) as db:
            assert all(marker not in row[0] for row in db.execute('SELECT body FROM records'))
    finally:core.close()


def feedback_offer(core,marker):
    ids=[]
    for index,text in enumerate((marker+' '+('detail '*70),'A separate selected note.')):
        spec={'mode':'research','goal':'Inspect selected note '+str(index),
            'files':{'note.txt':text},'editable':[],'checks':[]}
        result=core.work_control('preview-prepare-'+str(index),'prepare',spec=spec,
            authorize_start=True,budget_seconds=600)
        ids.append(result['activity']['id'])
    core.growth_control('preview-offer','opportunities',{'task_ids':ids,'maximum_reviews':3})
    return ids


def test_prepared_preview_is_factual_bounded_and_forgetting_survives_restore(tmp_path):
    import json
    from test_coordination_work import make_core
    marker='PRIVATE_PREPARED_PREVIEW_juniper_517'
    core=make_core(tmp_path/'state',clock=lambda:1000.)
    try:
        ids=feedback_offer(core,marker)
        context=core.growth_state();task=context['exploration']['tasks'][0]
        preview=task['source_observations'];sid=identifier('src','activity',ids[0],-1)
        assert preview['source_ids']==[sid]
        assert preview['observation_kind']=='admitted_source_snapshot' and preview['result_ref'] is None
        assert preview['files'][0]['excerpt_is_partial'] is True
        assert len(preview['files'][0]['excerpt'])==240 and marker in preview['files'][0]['excerpt']
        canonical=core.growth.artifacts.get(preview['source_ref'])
        assert preview['files'][0]['sha256']==hashlib.sha256(canonical['files']['note.txt'].encode()).hexdigest()
        assert core.store.read('personal_source',sid)['origin_kind']=='admitted_work_source'
        assert not any(e.get('activity_id')==ids[0] for e in context['experiences'])
        core.personal_control('forget-ready-preview','forget',{'target_id':sid})
        for _ in range(2):
            state=core.growth_state()
            assert 'source_observations' not in state['exploration']['tasks'][0]
            with core.store.transaction(core.token) as db:
                assert marker not in core._context(db,'fresh','Discuss an unrelated subject.')
        assert core.store.read('personal_source',sid)['status']=='forgotten'
        assert core.growth.artifacts.get(preview['source_ref'])==canonical
    finally:core.close()
    bundle=tmp_path/'snapshot';restored=tmp_path/'restored'
    export_snapshot(core.store.root,bundle);restore_snapshot(bundle,restored)
    reader=make_core(restored,clock=lambda:1000.)
    try:
        assert 'source_observations' not in reader.growth_state()['exploration']['tasks'][0]
        assert reader.store.read('personal_source',sid)['status']=='forgotten'
        assert reader.growth.artifacts.get(preview['source_ref'])==canonical
        with reader.store.transaction(reader.token) as db:
            for kind,raw in db.execute('SELECT kind,body FROM records'):
                if kind.startswith(('personal_','growth_')) or kind=='turn':assert marker not in raw
    finally:reader.close()


def test_ready_file_preview_binds_a_reply_and_new_interpretation_before_any_worker_runs(tmp_path):
    import json
    from test_coordination_work import make_core
    from gamma.coordination.personal_models import PersonalStateUpdates
    from gamma.schemas.response import AssistantResponse
    marker='PRIVATE_READY_REPLY_hemlock_516'
    core=make_core(tmp_path,clock=lambda:1000.)
    try:
        ids=feedback_offer(core,marker);sid=identifier('src','activity',ids[0],-1)
        observed=[]
        def respond(_text,raw):
            observed.append(json.loads(raw));assert marker in raw
            return AssistantResponse(display_text=marker,delivery_mode='text_only',
                state_updates=PersonalStateUpdates(growth_proposals=[{'statement':marker,
                    'kind':'judgment','scope_target':'dialogue','scope_marker':'ready-preview',
                    'alternatives':['This is only selected-file evidence.'],'expected_outcome':'Tentative only.'}]))
        core.draft=respond;core.input('What is currently available?','fresh','preview-quoted')
        derived=core.store.read('turn','preview-quoted')['personal_receipt']['source_id']
        assert sid in core.store.read('personal_source',derived)['dependencies']['sources']
        assert core.store.read('run',ids[0]) is None
        core.personal_control('forget-quoted-preview','forget',{'target_id':sid})
        assert core.store.read('turn','preview-quoted')['redacted'] is True
        assert core.store.read('personal_source',derived)['status']=='forgotten'
        with core.store.transaction(core.token) as db:
            for kind,raw in db.execute('SELECT kind,body FROM records'):
                if kind.startswith(('personal_','growth_')) or kind=='turn':assert marker not in raw
    finally:core.close()


def test_completed_analysis_and_choice_keep_all_exposed_sources_without_resurrection(tmp_path):
    import json
    from test_coordination_work import make_core
    from gamma.coordination.personal_models import PersonalStateUpdates
    from gamma.schemas.response import AssistantResponse
    marker='PRIVATE_COMPLETED_ANALYSIS_hazel_128'
    other='PRIVATE_UNSELECTED_SOURCE_yew_611'
    answer={'quote':marker,'observation':'The group AAB repeats twice.',
        'limitations':'Symbolic source only; no real audio was measured.',
        'next_question':'Would adding a gap preserve the repetition?'}
    check=("import json,unittest\nfrom pathlib import Path\nclass Analysis(unittest.TestCase):\n"
        " def test_actual_source(self):\n  value=json.loads(Path('analysis.json').read_text())\n"
        "  self.assertEqual(value['quote'],Path('source.txt').read_text().splitlines()[0])\n"
        "  self.assertTrue(value['observation'] and value['limitations'] and value['next_question'])\n")
    core=make_core(tmp_path/'state',clock=lambda:1000.)
    from gamma.coordination.work import WorkRunner
    core.work_runner=WorkRunner(core.store,clock=lambda:1000.,propose=lambda *_:{
        'plan':['Read the selected source and save bounded analysis.'],'edits':{'analysis.json':json.dumps(answer)}})
    try:
        ids=[]
        for index,value in enumerate((marker,other)):
            spec={'mode':'build','goal':'Analyze selected symbolic pattern '+str(index),
                'files':{'source.txt':value+'\nAABAAB','analysis.json':'{}','test_analysis.py':check},
                'editable':['analysis.json'],'checks':[{'name':'analysis','module':'test_analysis','kind':'regression'}]}
            ids.append(core.work_control('analysis-prepare-'+str(index),'prepare',spec=spec,
                authorize_start=True,budget_seconds=600)['activity']['id'])
        core.growth_control('analysis-offer','opportunities',{'task_ids':ids,'maximum_reviews':3})
        prepared=[identifier('src','activity',aid,-1) for aid in ids]
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            reason_code=other,state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=ids[0]))
        core.review()
        choice=core.growth_state()['choices'][0]
        assert set(prepared)<=set(choice['source_ids'])
        core.tick();core.future.result(timeout=15)
        state=core.growth_state();activity=core.store.read('activity',ids[0])
        assert activity['status']=='completed'
        experience=next(e for e in state['experiences'] if e.get('activity_id')==ids[0] and e.get('source_observations'))
        observations=experience['source_observations'];file=observations['files'][0]
        assert file['path']=='analysis.json' and file['excerpt_is_partial'] is False
        assert json.loads(file['excerpt'])==answer
        worker_sid=identifier('src','activity',ids[0],activity['observed_revision'])
        assert experience['source_ids']==[worker_sid]
        assert set(prepared)<=set(core.store.read('personal_source',worker_sid)['dependencies']['sources'])
        canonical=core.growth.artifacts.get(observations['source_ref'])
        with core.store.transaction(core.token) as db:
            actual_context=core._context(db,'later','Consider the current situation.')
            assert answer['next_question'] in actual_context and marker in actual_context
        core.personal_control('forget-unselected-alternative','forget',{'target_id':prepared[1]})
        record=core.store.read('growth_choice',choice['id'])
        assert record['context_source_removed'] is True and record['task_goal']=='' and record['reason_code']==''
        erased=core.store.read('growth_experience',experience['id'])
        assert erased['status']=='source_removed' and not erased.get('source_observations')
        assert core.growth.artifacts.get(observations['source_ref'])==canonical
        # A new valid worker revision does not make an erased decision source live.
        run=core.store.read('run',ids[0]);core.store.update_run(ids[0],run['checkpoint_ref'])
        state=core.growth_state()
        for task in state['exploration']['tasks']:assert 'source_observations' not in task
        assert core.store.read('personal_source',prepared[1])['status']=='forgotten'
        core.work_runner.verify(core.store.read('run',ids[0]))
    finally:core.close()
    bundle=tmp_path/'snapshot';restored=tmp_path/'restored'
    export_snapshot(core.store.root,bundle);restore_snapshot(bundle,restored)
    reader=make_core(restored,clock=lambda:1000.)
    try:
        for task in reader.growth_state()['exploration']['tasks']:assert 'source_observations' not in task
        assert reader.growth.artifacts.get(observations['source_ref'])==canonical
        reader.work_runner.verify(reader.store.read('run',ids[0]))
        with reader.store.transaction(reader.token) as db:
            for kind,raw in db.execute('SELECT kind,body FROM records'):
                if kind.startswith(('personal_','growth_')) or kind=='turn':
                    assert marker not in raw and other not in raw
    finally:reader.close()
