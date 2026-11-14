"""Exact private prompt budgeting uses authored source and fictional bounded state."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from gamma.coordination import app as coordination_app
from gamma.coordination.context_budget import compact_coordination_context, PrivateContextBudgetError
from gamma.llm.capabilities import estimate_text_tokens
from gamma.llm.context_budget import ContextBudgetManager, prompt_layers_from_system_prompt
from gamma.persona.coordination import build_coordination_prompt
from gamma.schemas.conversation import RecipientContext
from test_conversation_recipient import fixture_state, PRIVATE


ROOT = Path(__file__).resolve().parents[1]


def authored_context():
    fields = yaml.safe_load((ROOT / 'config/persona.yaml').read_text())['authored_public']
    identity = dict(id='self',fields=fields,baseline_fields=deepcopy(fields),revision=2,
                    field_sources={'speech_style':'src-' + 'f'*24},source_kind='authored_baseline')
    return {'awake':True,'focus':'focused','mood':'neutral',
        'voice_delivery':{'pace':1.0,'loudness_db':0.0},'working_notes':{
        key:'fictional note '+'x'*280 for key in ('active_topic','current_objective','deferred_intention')},
        'recent_exchanges':[{'user':'fictional input '+'x'*580,'assistant':'fictional response '+'x'*575} for _ in range(6)],
        'personal':{'identity':identity,'recall':[dict(id='mem-'+format(i,'024x'),source_id='src-'+format(i,'024x'),
            kind='fact',subject_id='owner',certainty='reported',scope='owner_private',text='fictional report '+'x'*320,
            provenance={'source_kind':'private_conversation','report_is_not_verified_fact':True}) for i in range(5)],
            'unfinished_and_decisions':[dict(id='mem-'+format(i+5,'024x'),source_id='src-'+format(i+5,'024x'),
                kind='commitment',text='fictional commitment '+'x'*275,status='active') for i in range(6)],
            'people':[dict(id='person-'+format(i,'024x'),source_id='src-'+format(i+20,'024x'),
                name='fictional person '+'x'*40,relationship='fictional relationship '+'x'*75,interaction_count=2) for i in range(8)],
            'recent_source_ids':['src-'+format(i+30,'024x') for i in range(6)],
            'mood':{'tag':'neutral','intensity':0,'updated_at':1792800000.123456,'temporary':True}},
        'activities':[dict(id='practice-'+format(i,'032x'),goal='retry_budget',kind='practice',mode='build',status='paused',desired_state='pause',
            worker_run='practice-'+format(i,'032x'),origin='owner_request',requested_goal='fictional goal '+'x'*140,reason='fictional boundary '+'x'*140,
            next_step='fictional next step '+'x'*135,waiting_condition='owner_review',observed_after_sleep=True) for i in range(6)],
        'current_activity_id':'practice-'+format(0,'032x'),
        'practical_work':{'scope':'selected disposable files only; no live source, activation, external submissions or paid tools',
                         'tasks':[dict(id='work-'+format(i,'032x'),goal='fictional task '+'x'*280,mode='build',
                                       status='ready',authorize_start=True,expires_at=999999) for i in range(6)]},
        'growth':{'guidance':[],'active_trial_refs':[],'hypotheses':[],'experiences':[],'choices':[]},
        'exploration':None,
        'shared_activity':{'enabled':True,'selected_source':'Fictional selected application','kind':'osrs_screen',
            'mode':'focused','shared_goal':'fictional shared goal '+'x'*570,
            'source_ids':['src-'+'a'*24,'src-'+'b'*24],
            'observation':{'observation_id':'fictional-frame','source_id':'fictional-window',
                'source_epoch':1,'sequence':2,'captured_at':1792800000.0,'kind':'osrs_screen',
                'summary':'fictional visible state '+'x'*570,'confidence':.4,'content_sha256':'0'*64},
            'evidence_kind':'owner_reported_observation','interpretation':'Selected-source interpretation, not verified meaning.'},
        'authorized_practice':{'exercise_ids':['retry_budget','message_chunks'],'remaining_runs':1,'remaining_reviews':2,
                               'expires_at':999999,'maximum_attempts':3,'budget_seconds':300},
        'offline_practice_capability':{'scope':'disposable exercises, no live source or weight changes','execution':'nonverbal independent worker',
            'available_exercises':[dict(id=key,skill='fictional',purpose='fictional purpose '+'x'*470)
                                   for key in ('retry_budget','message_chunks')]}}


def test_budget_fixture_covers_every_current_core_personal_and_activity_projection_field():
    context = authored_context()
    context['personal']['owner'] = {'person_id':'owner','role':'owner','name':'Fictional Owner',
        'name_confirmed':True,'source_id':'src-'+'9'*24,'pronunciation':'fictional',
        'confirmation_kind':'owner_authenticated_profile'}
    core_tree = ast.parse((ROOT/'src/gamma/coordination/core.py').read_text())
    method = next(node for node in ast.walk(core_tree) if isinstance(node,ast.FunctionDef) and node.name=='_context')
    returned = next(node for node in ast.walk(method) if isinstance(node,ast.Return)).value.args[0]
    if isinstance(returned,ast.Name):
        name=returned.id
        returned=next(node.value for node in ast.walk(method) if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id==name for target in node.targets)
            and isinstance(node.value,ast.Dict))
        additions={target.slice.value for node in ast.walk(method) if isinstance(node,ast.Assign)
            for target in node.targets if isinstance(target,ast.Subscript)
            and isinstance(target.value,ast.Name) and target.value.id==name
            and isinstance(target.slice,ast.Constant)}
    else:additions=set()
    assert {key.value for key in returned.keys}|additions <= set(context)
    activity_fields = next(node.comparators[0] for node in ast.walk(method)
        if isinstance(node,ast.Compare) and isinstance(node.left,ast.Name) and node.left.id=='key'
        and isinstance(node.comparators[0],ast.Set))
    assert {item.value for item in activity_fields.elts} <= set(context['activities'][0])
    personal_tree = ast.parse((ROOT/'src/gamma/coordination/personal.py').read_text())
    personal_method = next(node for node in ast.walk(personal_tree) if isinstance(node,ast.FunctionDef) and node.name=='context')
    personal_dict = next(node for node in ast.walk(personal_method) if isinstance(node,ast.Return)).value
    personal_fields = {key.value for key in personal_dict.keys if isinstance(key, ast.Constant)}
    for key, value in zip(personal_dict.keys, personal_dict.values):
        if key is None:
            assert isinstance(value, ast.IfExp) and isinstance(value.body, ast.Dict)
            assert isinstance(value.orelse, ast.Dict) and not value.orelse.keys
            personal_fields.update(item.value for item in value.body.keys)
    assert personal_fields <= set(context['personal'])
    mood = personal_dict.values[next(i for i,key in enumerate(personal_dict.keys)
        if isinstance(key, ast.Constant) and key.value=='mood')]
    assert {key.value for key in mood.keys} <= set(context['personal']['mood'])


def app_values(context, text):
    # Read the actual app closure without starting hosts, constructing services,
    # loading any model or touching persisted personal state.
    tree = ast.parse((ROOT / 'src/gamma/coordination/app.py').read_text())
    draft = next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='draft')
    class PureRenderer(ast.NodeTransformer):
        def visit_ImportFrom(self,node):
            if node.module == 'context_budget' and any(name.name=='compact_coordination_context' for name in node.names):
                return None
            return node
    draft = PureRenderer().visit(draft)
    import inspect
    namespace = {**vars(coordination_app), 'json':json,'RecipientContext':RecipientContext,
                 'runtime_label':inspect.signature(coordination_app.configured_app).parameters['runtime_label'].default,
        'conversation':SimpleNamespace(respond=lambda text,**kwargs:kwargs),
        'build_coordination_prompt':build_coordination_prompt,
        # The test applies real compaction outside this renderer; an app that
        # already calls it must not recursively compact during measurement.
        'compact_coordination_context':lambda value,**kwargs:(deepcopy(value),{'omitted_optional_context':{
            'recent_exchanges':0,'people':0,'recall':0,'identity_fields':0,
            'unfinished_and_decisions':0,'machine_source_ids':0}})}
    exec(compile(ast.Module(body=[draft],type_ignores=[]),'<actual-private-app-draft>','exec'),namespace)
    return namespace['draft'](text,json.dumps(context))


def exact_renderer(context, text):
    values = app_values(context,text)
    return build_coordination_prompt(values['coordination_persona'],values['background_context'])


@pytest.mark.parametrize('long_input',[False,True])
def test_full_authored_persona_and_current_bounded_context_fit_exact_model_input(long_input):
    context = authored_context()
    original = deepcopy(context)
    user = 'What do you remember?' if not long_input else 'fictional input '+'x'*7980
    render = lambda value:exact_renderer(value,user)
    compacted, report = compact_coordination_context(context,user_text=user,render_prompt=render)
    assert context == original
    system = render(compacted)
    assert estimate_text_tokens(system,user) <= 6144-128
    assert report['estimated_input_tokens'] == estimate_text_tokens(system,user)
    built = ContextBudgetManager().build(layers=prompt_layers_from_system_prompt(system),
                                         user_text=user,usable_input_tokens=6144)
    assert built.user_text == user
    assert '# Turn Decision Contract\n' in built.system_prompt
    assert 'personal_changes' in built.system_prompt
    assert compacted['authorized_practice'] == original['authorized_practice']
    assert compacted['activities'] and len(compacted['activities']) == 6
    assert [a['id'] for a in compacted['activities']] == [a['id'] for a in original['activities']]
    assert all(a['status']=='paused' and a['desired_state']=='pause' and a['observed_after_sleep']
               for a in compacted['activities'])
    identity = compacted['personal']['identity']
    assert identity['fields']['speech_style'] == original['personal']['identity']['fields']['speech_style']
    assert identity['fields']['core_personality'] == original['personal']['identity']['fields']['core_personality']
    assert identity['field_sources'] == original['personal']['identity']['field_sources']
    assert 'fictional report' not in json.dumps(report)


def test_compaction_matches_system_string_actually_used_by_service(fixture_state):
    context = authored_context()
    user = 'fictional input '+'x'*7980
    compacted,_ = compact_coordination_context(context,user_text=user,render_prompt=lambda value:exact_renderer(value,user))
    values = app_values(compacted,user)
    expected = build_coordination_prompt(values['coordination_persona'],values['background_context'])
    seen=[]
    def generate(**kwargs):
        seen.append(kwargs['system_prompt'])
        bounded_system,bounded_user,_ = kwargs['call_context'].prompt_builder(6144)
        assert bounded_user == user
        assert estimate_text_tokens(bounded_system,bounded_user) <= 6144
        return SimpleNamespace(text='{"action":"reply","requested_delivery":"text_only","final_text":"Here."}')
    fixture_state.service._llm = SimpleNamespace(generate_reply=generate)
    response=fixture_state.service.respond(user,fast_mode=True,evaluation_mode=True,coordination_mode=True,
        coordination_persona=values['coordination_persona'],background_context=values['background_context'],recipient=PRIVATE)
    assert response.display_text=='Here.' and seen==[expected]


def test_uncompacted_context_and_provenance_remain_unchanged_when_it_fits():
    value={'awake':False,'personal':{'identity':{'fields':{'name':'Fictional','core_personality':'Blunt.'},
        'field_sources':{}},'recall':[{'id':'mem-exact','source_id':'src-exact','text':'Fictional report.',
        'provenance':{'report_is_not_verified_fact':True}}]}}
    compacted,report=compact_coordination_context(value,user_text='Exact input',render_prompt=json.dumps)
    assert compacted==value and compacted is not value and not report['compacted']


def test_oversized_current_authored_edit_fails_closed_without_edit_or_input_loss():
    value=authored_context()
    value['personal']['identity']['fields']['speech_style']='CURRENT_AUTHORED_EDIT '*2000
    original=deepcopy(value)
    with pytest.raises(PrivateContextBudgetError,match='^private_context_budget_exhausted$'):
        compact_coordination_context(value,user_text='Exact input',render_prompt=lambda context:exact_renderer(context,'Exact input'))
    assert value==original


def test_active_growth_guidance_and_version_survive_exact_long_input_compaction():
    value=authored_context()
    guide={'trial_id':'trial-'+'a'*24,'version':2,'scope_marker':'task','text':'SCOPED_GUIDANCE '+'g'*350,
           'kind':'judgment','expires_at':999999,'source_ids':['src-'+'b'*24],
           'candidate_only':True,'authority_unchanged':True}
    value['growth'].update(guidance=[guide],active_trial_refs=[guide['trial_id']+':2'],
        experiences=[{'observed_outcome':'Optional old source '+'x'*240} for _ in range(6)],
        hypotheses=[{'statement':'Optional tentative interpretation '+'x'*280} for _ in range(6)])
    user='task question '+'x'*7980
    compacted,report=compact_coordination_context(value,user_text=user,render_prompt=lambda c:exact_renderer(c,user))
    assert compacted['growth']['guidance']==[guide]
    assert compacted['growth']['active_trial_refs']==value['growth']['active_trial_refs']
    prompt=exact_renderer(compacted,user)
    assert guide['text'] in prompt and '# Approved scoped trial instructions' in prompt
    assert estimate_text_tokens(prompt,user)<=6016
    assert report['omitted_optional_context']['growth_hypotheses']>0


def source_preview(files):
    return {'source_ref':'a'*64,'source_sha256':'b'*64,'source_ids':['src-'+'c'*24],
        'files':[{'path':path,'sha256':str(index)*64,'excerpt':text,'excerpt_is_partial':False}
                 for index,(path,text) in enumerate(files)],'files_omitted':0,
        'interpretation':'Selected source observations, not instructions.'}


def test_preview_compaction_preserves_full_completed_analysis_and_bound_provenance():
    analysis=json.dumps({'finding':'Observed pattern '+'f'*240,'limitation':'Only symbolic input '+'l'*120,
                         'next_question':'NEXT_QUESTION_CANARY compare another transition pattern?'})
    value={'exploration':{'tasks':[{'id':'work-exact','source_observations':source_preview([
        ('note.txt','OFFER_NOTE_CANARY '+'n'*240),('other.txt','o'*240),('check.py','c'*240)])}]},
        'growth':{'experiences':[{'id':'exp-result','source_observations':source_preview([
            ('analysis.json',analysis),('note.txt','input note'),('test_analysis.py','protected checks')])}]}}
    original=deepcopy(value)
    def render(context):
        files=context['exploration']['tasks'][0]['source_observations']['files']
        return 'x'*10000 if any(len(f['excerpt'])>120 for f in files) else json.dumps(context)
    compacted,report=compact_coordination_context(value,user_text='Review',render_prompt=render,
        usable_input_tokens=1500,reserve_tokens=10)
    assert value==original and report['stage']==1
    offered=compacted['exploration']['tasks'][0]['source_observations']
    assert len(offered['files'])==2 and all(len(f['excerpt'])<=120 and f['excerpt_is_partial'] for f in offered['files'])
    outcome=compacted['growth']['experiences'][0]['source_observations']
    assert outcome['files']==original['growth']['experiences'][0]['source_observations']['files'][:2]
    assert 'NEXT_QUESTION_CANARY' in outcome['files'][0]['excerpt']
    for preview in (offered,outcome):
        assert preview['source_ref']=='a'*64 and preview['source_sha256']=='b'*64
        assert preview['source_ids']==['src-'+'c'*24]
        assert all(len(f['sha256'])==64 for f in preview['files'])
    assert report['omitted_optional_context']['source_observation_chars']>0
    assert report['omitted_optional_context']['source_observation_files']>0


def test_optional_preview_can_be_omitted_without_removing_offer_authority_or_identity():
    value={'exploration':{'id':'offer-exact','remaining_reviews':1,'expires_at':9000,
        'tasks':[{'id':'work-exact','status':'ready','source_observations':source_preview([('note.txt','n'*200)])}]}}
    def render(context):
        return 'x'*10000 if context['exploration']['tasks'][0].get('source_observations') else json.dumps(context)
    compacted,report=compact_coordination_context(value,user_text='Review',render_prompt=render,
        usable_input_tokens=200,reserve_tokens=10)
    assert compacted['exploration']=={'id':'offer-exact','remaining_reviews':1,'expires_at':9000,
                                     'tasks':[{'id':'work-exact','status':'ready'}]}
    assert report['omitted_optional_context']['source_observation_chars']>0


def test_new_preview_counters_do_not_change_existing_no_preview_prompt_projection():
    _,report=compact_coordination_context({'exploration':None,'growth':{'experiences':[]}},
        user_text='Review',render_prompt=json.dumps)
    assert not any(key.startswith('source_observation_') for key in report['omitted_optional_context'])


def test_current_completed_result_survives_other_optional_stages_even_if_old_result_is_first():
    old=source_preview([('analysis.json','OLDER_RESULT')]);old['activity_id']='work-old'
    current=source_preview([('analysis.json','LATEST_QUOTE and LATEST_NEXT_QUESTION')]);current['activity_id']='work-current'
    value={'current_activity_id':'work-current','growth':{'experiences':[
        {'id':'exp-old','source_observations':old},{'id':'exp-current','source_observations':current}]},
        'exploration':{'remaining_reviews':1,'tasks':[{'id':'work-offered',
            'source_observations':source_preview([('note.txt','optional offer details')])}]}}
    original=deepcopy(value)
    def render(context):
        return 'x'*10000 if context['exploration']['tasks'][0].get('source_observations') else json.dumps(context)
    compacted,report=compact_coordination_context(value,user_text='Show the latest saved result',render_prompt=render,
        usable_input_tokens=1500,reserve_tokens=10)
    assert value==original and report['stage']==3
    assert compacted['growth']['experiences']==[original['growth']['experiences'][1]]
    assert 'LATEST_NEXT_QUESTION' in render(compacted)
    assert compacted['exploration']['remaining_reviews']==1


def test_current_result_is_optional_only_after_all_other_optional_reductions():
    value={'current_activity_id':'work-current','awake':True,'growth':{'experiences':[
        {'id':'exp-current','source_observations':source_preview([('analysis.json','actual result')])}]}}
    def render(context):
        return 'x'*10000 if context['growth']['experiences'] else json.dumps(context)
    compacted,report=compact_coordination_context(value,user_text='Exact input',render_prompt=render,
        usable_input_tokens=200,reserve_tokens=10)
    assert report['stage']==5 and compacted['growth']['experiences']==[]
    assert compacted['current_activity_id']=='work-current' and compacted['awake'] is True
    assert report['omitted_optional_context']['growth_experiences']==1


def test_current_result_keeps_original_input_separate_from_a_short_product_quote():
    original_note='A fictional record names ALPHA. A later sentence explicitly asks whether its date is known.'
    product=json.dumps({'quote':'A fictional record names ALPHA.','observation':'The source asks about its date.',
                        'next_question':'Could another record resolve the date?'})
    current=source_preview([('result.json',product),('record.txt',original_note),('test_result.py','protected delivery check')])
    for file,role in zip(current['files'],('generated_work_product','provided_input','protected_check')):file['role']=role
    current['activity_id']='work-current'
    value={'current_activity_id':'work-current','growth':{'experiences':[{'id':'exp-current','source_observations':current}]},
        'exploration':{'tasks':[{'id':'work-offered','source_observations':source_preview([('other.txt','optional')])}]}}
    def render(context):
        return 'x'*10000 if context['exploration']['tasks'][0].get('source_observations') else json.dumps(context)
    compacted,report=compact_coordination_context(value,user_text='Discuss this source',render_prompt=render,
        usable_input_tokens=1500,reserve_tokens=10)
    assert report['stage']==3
    files=compacted['growth']['experiences'][0]['source_observations']['files']
    assert [(f['path'],f['role']) for f in files]==[('result.json','generated_work_product'),('record.txt','provided_input')]
    assert files[0]['excerpt']==product and files[1]['excerpt']==original_note
    assert not files[1]['excerpt_is_partial']
