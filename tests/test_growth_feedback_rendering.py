"""Independent current-result chronology and production-rendering regressions."""
import ast
import hashlib
import json
from pathlib import Path
import types

import pytest

from gamma.coordination.personal_models import PersonalStateUpdates
from gamma.schemas.response import AssistantResponse
from test_coordination_work import make_core
from gamma.coordination.personal import identifier


def sealed_persona_builder(project,name='feedback-persona-before.py',
    expected='bb509f521fe156f9a864c4fec3cb6064686402c39c93595eaf20a19ef3c1e7ed'):
    path=project/'data/coordination/p4/review'/name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    scope={'__package__':'gamma.persona'}
    exec(compile(path.read_text(),str(path),'exec'),scope)
    return scope['build_coordination_prompt']


def captured_production_system(context,user_text,budget,persona_builder=None):
    """Run the actual app draft, replacing only its conversation transport."""
    from gamma.coordination import app
    import inspect
    from gamma.persona.coordination import build_coordination_prompt, is_owner_text_input
    from gamma.schemas.conversation import RecipientContext
    tree=ast.parse(Path(app.__file__).read_text())
    function=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='draft')
    builder=persona_builder or build_coordination_prompt
    class InjectBudget(ast.NodeTransformer):
        def visit_ImportFrom(self,node):
            return None if node.module in {'context_budget','persona.coordination'} else node
    function=InjectBudget().visit(function)
    captured=[]
    def transport(text,**kwargs):
        captured.append((text,kwargs))
        return kwargs
    scope={**vars(app),'RecipientContext':RecipientContext,
        'runtime_label':inspect.signature(app.configured_app).parameters['runtime_label'].default,
        'compact_coordination_context':budget,'build_coordination_prompt':builder,
        'is_owner_text_input':is_owner_text_input,
        'conversation':types.SimpleNamespace(respond=transport)}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<actual-private-app-draft>','exec'),scope)
    result=scope['draft'](user_text,json.dumps(context,ensure_ascii=False))
    assert len(captured)==1 and captured[0][0]==user_text
    assert result['coordination_mode'] and not result['synthesize_speech']
    return builder(result['coordination_persona'],result['background_context'])


def test_actual_full_state_result_survives_the_production_request_renderer():
    """Private full-state integration fixture is distributed in the review bundle."""
    from gamma.coordination.context_budget import compact_coordination_context
    project=Path(__file__).resolve().parents[1];review=project/'data/coordination/p4/review'
    old_path=review/'feedback-ba9-prequery-context.json'
    current_path=review/'feedback-ba9-current-context.json'
    budget_path=review/'feedback-budget-before.py'
    if not all(p.exists() for p in (old_path,current_path,budget_path,review/'feedback-persona-before.py')):
        pytest.skip('Install the private P4 full-state replay fixtures from its review bundle.')
    assert hashlib.sha256(old_path.read_bytes()).hexdigest()=='59bdc221fa221077901fa8a25d5cebcb6a801f3969b4ef7e8fbe3e7f6a39219e'
    assert hashlib.sha256(budget_path.read_bytes()).hexdigest()=='f482c2e2f4d13f3634793a5cba3d27cd33ba231bb949ccc24ae9c272b32cbe93'
    old=json.loads(old_path.read_text());current=json.loads(current_path.read_text())
    sealed={};exec(compile(budget_path.read_text(),str(budget_path),'exec'),sealed)
    before=captured_production_system(old['context'],old['user_text'],sealed['compact_coordination_context'],
        sealed_persona_builder(project))
    fingerprint=lambda text:hashlib.sha256(json.dumps(text,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    assert fingerprint(before)==old['expected_failed_http_system_sha256']
    assert not any(needle in before for needle in old['needles'])
    assert current['user_text']==old['user_text'] and current['needles']==old['needles']
    after=captured_production_system(current['context'],current['user_text'],compact_coordination_context)
    assert all(needle in after for needle in old['needles'])
    assert (len(after)+len(current['user_text'])+3)//4<=6144


def test_actual_second_result_reaches_the_production_renderer_after_canonical_source_admission():
    from gamma.coordination.context_budget import compact_coordination_context
    project=Path(__file__).resolve().parents[1];review=project/'data/coordination/p4/review'
    old_path=review/'feedback-3cf9-prequery-context.json'
    current_path=review/'feedback-3cf9-current-context.json'
    budget_path=review/'feedback-budget-before-second.py'
    if not all(p.exists() for p in (old_path,current_path,budget_path,review/'feedback-persona-before.py')):
        pytest.skip('Install the private P4 second full-state replay fixture from its review bundle.')
    assert hashlib.sha256(old_path.read_bytes()).hexdigest()=='eaac166ca2988a3807e1d0a4e25d0ea07698911c7bb07d5ed45d7e3248b11e82'
    assert hashlib.sha256(budget_path.read_bytes()).hexdigest()=='e48e2dd67a9128d6439cb9443aa9a522cf938670610cd95ed02ad8e2570d2e1c'
    old=json.loads(old_path.read_text());current=json.loads(current_path.read_text())
    sealed={};exec(compile(budget_path.read_text(),str(budget_path),'exec'),sealed)
    before=captured_production_system(old['context'],old['user_text'],sealed['compact_coordination_context'],
        sealed_persona_builder(project))
    fingerprint=lambda text:hashlib.sha256(json.dumps(text,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    assert fingerprint(before)==old['expected_failed_http_system_sha256']
    assert not any(needle in before for needle in old['needles'])
    assert current['user_text']==old['user_text'] and current['needles']==old['needles']
    assert any(e.get('source_observations',{}).get('activity_id')==current['context']['current_activity_id']
               for e in current['context']['growth']['experiences'])
    after=captured_production_system(current['context'],current['user_text'],compact_coordination_context)
    assert all(needle in after for needle in old['needles'])
    assert (len(after)+len(current['user_text'])+3)//4<=6144


def test_actual_source_span_failure_preserves_original_input_separately_from_generated_quote():
    from gamma.coordination.context_budget import compact_coordination_context
    project=Path(__file__).resolve().parents[1];review=project/'data/coordination/p4/review'
    old_path=review/'source-span-51-before-context.json';current_path=review/'source-span-51-current-final-context.json'
    budget_path=review/'source-span-budget-before.py';persona_path=review/'source-span-persona-before.py'
    if not all(p.exists() for p in (old_path,current_path,budget_path,persona_path)):
        pytest.skip('Install the private P4 source-span replay fixture from its review bundle.')
    assert hashlib.sha256(old_path.read_bytes()).hexdigest()=='2b310ce2a93286c26cc1e6aa298a9cd75c9ffc880aeed6cb55944bedeefbc9d1'
    assert hashlib.sha256(budget_path.read_bytes()).hexdigest()=='e48e2dd67a9128d6439cb9443aa9a522cf938670610cd95ed02ad8e2570d2e1c'
    old=json.loads(old_path.read_text());current=json.loads(current_path.read_text());sealed={}
    exec(compile(budget_path.read_text(),str(budget_path),'exec'),sealed)
    before=captured_production_system(old['context'],old['user_text'],sealed['compact_coordination_context'],
        sealed_persona_builder(project,'source-span-persona-before.py',
            'fb84cea98b5ec8856d9814798e2cbb0516954007e4a1f199d82ff1d5b56952ff'))
    fingerprint=lambda text:hashlib.sha256(json.dumps(text,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    assert fingerprint(before)==old['expected_failed_http_system_sha256']
    assert all(needle in before for needle in old['needles'])
    assert old['full_selected_input'] not in before
    assert current['full_selected_input']==old['full_selected_input'] and current['user_text']==old['user_text']
    previews=[e['source_observations'] for e in current['context']['growth']['experiences'] if e.get('source_observations')
        and e['source_observations'].get('activity_id')==current['context']['current_activity_id']]
    assert len(previews)==1
    original=next(f for f in previews[0]['files'] if f['path']==current['selected_input_path'] and f.get('role')=='provided_input')
    product=next(f for f in previews[0]['files'] if f.get('role')=='generated_work_product')
    assert original['excerpt']==current['full_selected_input'] and original['excerpt_is_partial'] is False
    assert hashlib.sha256(original['excerpt'].encode()).hexdigest()==original['sha256']
    assert product['excerpt_is_partial'] is False
    assert hashlib.sha256(product['excerpt'].encode()).hexdigest()==product['sha256']
    assert len(json.loads(product['excerpt'])['quote'])<len(original['excerpt'])
    assert sum(len(f['excerpt']) for f in previews[0]['files'])<=960
    after=captured_production_system(current['context'],current['user_text'],compact_coordination_context)
    assert current['full_selected_input'] in after and all(needle in after for needle in current['needles'])
    assert 'provided_input' in after and 'generated_work_product' in after
    assert (len(after)+len(current['user_text'])+3)//4<=6144


def test_refreshing_three_completed_activities_keeps_the_actual_latest_result_first(tmp_path):
    clock=[1000.];core=make_core(tmp_path,clock=lambda:clock[0])
    core.work_runner.propose=lambda *_:{'plan':['Read the selected note.'],'edits':{}}
    try:
        markers=[]
        for index in range(3):
            ids=[];marker='ACTUAL_COMPLETION_'+str(index);markers.append(marker)
            for suffix,text in [('selected',marker),('alternative','A different optional note.')]:
                spec={'mode':'research','goal':'Inspect selected note '+str(index)+' '+suffix,
                    'files':{'note.txt':text},'editable':[],'checks':[]}
                ids.append(core.work_control('prepare-'+str(index)+suffix,'prepare',spec=spec,
                    authorize_start=True,budget_seconds=600)['activity']['id'])
            core.growth_control('offer-'+str(index),'opportunities',{'task_ids':ids,'maximum_reviews':1})
            core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
                state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=ids[0]))
            core.review();core.tick();core.future.result(timeout=15)
            assert core.growth_state()['choices'][0]['accepted'] is True
            assert core.store.read('activity',ids[0])['status']=='completed'
            clock[0]+=20
        # Repeated prompt construction may refresh evidence but cannot make an
        # older completion appear to be the latest completed project.
        for _ in range(3):
            with core.store.transaction(core.token) as db:
                context=json.loads(core._context(db,'later','What is the latest actual result?'))
            own=[e for e in context['growth']['experiences'] if e.get('source_observations')]
            assert markers[-1] in str(own[0]['source_observations'])
            assert markers[-2] in str(own[1]['source_observations'])
    finally:core.close()


def test_completed_result_uses_canonical_roots_beyond_sixteen_sources_and_honors_ancestor_pruning(tmp_path):
    clock=[1000.];core=make_core(tmp_path,clock=lambda:clock[0])
    core.work_runner.propose=lambda *_:{'plan':['Read the actual selected note.'],'edits':{}}
    try:
        sources=[]
        for index in range(16):
            core.input('Historical observation '+str(index),'history-'+str(index),'source-'+str(index))
            sources.append(core.store.read('turn','source-'+str(index))['personal_receipt']['source_id'])
        clock[0]+=29*86400
        core.growth_control('history-review','experience',{'source_ids':sources,
            'situation':'Sixteen older sources were available for this review.','options':[],
            'action':'Owner reviewed actual retained reports.','expected_outcome':'No new source is invented.',
            'observed_outcome':'A bounded synthetic history set.','uncertainty':'Unit source-closure fixture, not real learning.'})
        ids=[];marker='NEW_RESULT_AFTER_LONG_HISTORY'
        for index,text in enumerate((marker,'Another current note.')):
            spec={'mode':'research','goal':'Inspect current selected note '+str(index),
                'files':{'note.txt':text},'editable':[],'checks':[]}
            ids.append(core.work_control('long-prepare-'+str(index),'prepare',spec=spec,
                authorize_start=True,budget_seconds=600)['activity']['id'])
        core.growth_control('long-offer','opportunities',{'task_ids':ids,'maximum_reviews':1})
        core.draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent',
            state_updates=PersonalStateUpdates(activity_action='start_work',work_task_id=ids[0]))
        core.review();choice=core.growth_state()['choices'][0]
        assert len(choice['source_ids'])>16 and set(sources)<=set(choice['source_ids'])
        core.tick();core.future.result(timeout=15)
        state=core.growth_state();activity=core.store.read('activity',ids[0])
        assert activity['status']=='completed'
        experience=next(e for e in state['experiences'] if e.get('activity_id')==ids[0] and e.get('source_observations'))
        assert marker in str(experience['source_observations'])
        worker_sid=identifier('src','activity',ids[0],activity['observed_revision'])
        assert experience['source_ids']==[worker_sid]
        worker=core.store.read('personal_source',worker_sid)
        assert set(choice['source_ids'])<=set(worker['dependencies']['sources'])
        original_source_ref=experience['source_observations']['source_ref']
        original_artifact=core.growth.artifacts.get(original_source_ref)
        # Actual retention prunes older ancestors while the recent worker root
        # is still active. Validating only that root would leak its derivatives.
        clock[0]+=2*86400
        with core.store.transaction(core.token) as db:
            assert core.personal.prune(db)>=16
            assert core.store.get(db,'personal_source',sources[0])['status']=='pruned'
            assert core.store.get(db,'personal_source',worker_sid)['status']=='active'
            core.growth.sync(db)
        for _ in range(2):
            state=core.growth_state()
            assert not any(e.get('activity_id')==ids[0] and e.get('source_observations') for e in state['experiences'])
            with core.store.transaction(core.token) as db:
                context=json.loads(core._context(db,'after-retention','What source observations are available?'))
            assert marker not in str(context['growth'])+str(context['exploration'])
        assert core.growth.artifacts.get(original_source_ref)==original_artifact
        core.work_runner.verify(core.store.read('run',ids[0]))
    finally:core.close()


@pytest.mark.parametrize('audience,has_policy', [('owner_private', True), ('public', False)])
def test_actual_extracted_renderer_uses_real_owner_marker_admission(audience, has_policy):
    from gamma.coordination.context_budget import compact_coordination_context
    from test_coordination_context_budget import authored_context
    context = authored_context()
    context['owner_input'] = {'audience': audience, 'kind': 'text', 'address_cue': 'direct'}
    system = captured_production_system(context, 'Who am I?', compact_coordination_context)
    assert ('# Private interaction capability\n' in system) is has_policy
    assert 'Only the current coordinator scope permits actions.' in system
