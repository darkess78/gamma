"""Prior stream words are delivery history, never proof that work happened."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from gamma.coordination.stream_session import public_session_facts
from gamma.schemas.response import AssistantResponse
from test_coordination_context_budget import authored_context, exact_renderer
from test_stream_session import make, event, ack
from test_conversation_recipient import fixture_state


# Exact generated assistant wording from the isolated authored four-hour replay.
# No owner transcript, private source files or real-world activity is included.
HISTORY = json.loads(r'''[
  {
    "index": 15,
    "text": "Good points. Empty brackets and whitespace-only lines should be filtered out as non-tasks. Nested brackets and code blocks need proper parsing to avoid false positives. [x] and [X] should be the only complete markers. I'll add these edge cases to the extractor tests."
  },
  {
    "index": 18,
    "text": "The boundary case is closed, but we're still missing the extractor's final JSON output and confirmation that it correctly ignores code blocks and nested brackets. I’ll run a probe with mixed input to verify those specific parsing rules before moving on."
  },
  {
    "index": 22,
    "text": "The mixed input probe confirmed that nested brackets and code blocks are correctly ignored. The final JSON output for the extractor is verified. All pending-only filtering rules are locked in."
  },
  {
    "index": 25,
    "text": "The extractor is stable for nested brackets and code blocks. Pending-only filtering is locked. What's next on the list?"
  }
]''')
CURRENT_RECEIPTS = "Most recent two actual disposable work receipts, including executed independent CLI probes: [{\"attempts\":1,\"independent_probes\":[{\"case\":\"empty_input\",\"observed_output\":[]},{\"case\":\"new_input\",\"observed_output\":[{\"done\":false,\"line\":1,\"text\":\"Read receipt\"},{\"done\":true,\"line\":2,\"text\":\"Local check\"}]}],\"protected_checks_passed\":1,\"protected_checks_total\":1,\"reason\":\"protected_candidate_checks_passed\",\"source_sha256\":\"03664a9f81faf41580a992f117a16df2af08f6a8fd109d269bae9b05d4a8de5b\",\"status\":\"completed\",\"task\":\"notes\"},{\"attempts\":1,\"independent_probes\":[{\"case\":\"empty_input\",\"observed_output\":[]},{\"case\":\"new_input\",\"observed_output\":[{\"done\":false,\"line\":1,\"text\":\"Read receipt\"}]}],\"protected_checks_passed\":1,\"protected_checks_total\":1,\"reason\":\"protected_candidate_checks_passed\",\"source_sha256\":\"7bce99a00da39cbb3ab57a4a51a6e4338eaed350512d7a7536deac1d3f2432a0\",\"status\":\"completed\",\"task\":\"notes_filter\"}]"
GROUNDING = (
    'Your earlier commentary, intentions and promises are not execution evidence.',
    'Do not promise to run, edit, test or complete work from this observation.',
    'Limit each completion or correctness claim to the exact supplied task, artifact and executed probe result.',
    'An omitted result is unknown, not missing or failed; inspect the supplied output before claiming it is absent.',
)


def test_stream_projection_marks_exact_counterexamples_as_text_receipts_only():
    session = {'plan': {'format':'coding','broad_plan':'Discuss the selected disposable extractor.'},
               'owner_present':False,'focused':False}
    recent = [{'event_id':'recorded-'+str(row['index']),'text':row['text'],'received_at':float(row['index'])}
              for row in HISTORY]
    original = copy.deepcopy(recent)
    facts = public_session_facts(session,{'kind':'lull','text':CURRENT_RECEIPTS},recent)
    assert recent == original
    assert facts['event']['text'] == CURRENT_RECEIPTS
    assert json.loads(CURRENT_RECEIPTS.split(': ',1)[1])[1]['independent_probes'][1]['observed_output'] == [
        {'done':False,'line':1,'text':'Read receipt'}]
    assert facts['work_authority'] == {
        'dispatch_allowed':False,'this_review_admits_work':False,
        'prior_commentary_is_execution_evidence':False,
        'current_event_evidence':'owner_selected_observation_not_a_new_execution_receipt'}
    for raw, selected in zip(recent,facts['recent_received_contributions']):
        assert selected['event_id'] == raw['event_id'] and selected['text'] == raw['text'][:400]
        assert selected['source_kind'] == 'assistant_commentary'
        assert selected['evidence_scope'] == 'consumer_reported_text_received_only'
        assert selected['execution_evidence'] is False
        assert selected['text_is_partial'] == (len(raw['text'])>400)


def test_channel_only_grounding_reaches_actual_renderer_without_changing_current_evidence():
    context = authored_context()
    context['channel_event'] = public_session_facts(
        {'plan':{'format':'coding','broad_plan':'Discuss the extractor.'},'owner_present':True,'focused':False},
        {'kind':'activity','text':CURRENT_RECEIPTS},
        [{'event_id':'earlier-promise','text':HISTORY[0]['text'],'received_at':1}])
    prompt = exact_renderer(context,'Review this authored observation.')
    contract = prompt.split('# Turn Decision Contract\n',1)[1]
    assert all(rule in contract for rule in GROUNDING)
    assert context['channel_event']['event']['text'] == CURRENT_RECEIPTS
    # No claimed prior probe is copied into an admitted execution/evidence slot.
    assert context['channel_event']['work_authority']['this_review_admits_work'] is False


def test_real_core_received_history_cannot_be_relabelled_as_executed_work(tmp_path):
    seen=[]
    replies=iter(row['text'] for row in HISTORY)
    def draft(instruction,serialized):
        observations=json.loads(serialized)
        seen.append((observations,exact_renderer(observations,instruction)))
        text=next(replies,None)
        if text is None:
            return AssistantResponse(response_action='stay_silent',delivery_mode='silent')
        return AssistantResponse(display_text=text,delivery_mode='text_only')
    core,service,clock=make(tmp_path,draft)
    try:
        session=service.start('grounding-start',dict(audience='owner_private',format='coding',
            broad_plan='Discuss the selected disposable extractor and actual supplied probes.'))
        for index,row in enumerate(HISTORY):
            service.connect(session['id'],'owner')
            result=service.submit(event(session['id'],clock,'recorded-'+str(row['index']),kind='activity',text=CURRENT_RECEIPTS))
            assert result['status']=='ready'
            output=service.next_output(session['id'],'owner')['output']
            assert output['text']==row['text']
            service.ack(ack(output,receipt_id='received-'+str(index)))
            clock.advance(61)
        service.connect(session['id'],'owner')
        assert service.submit(event(session['id'],clock,'after-recorded-history',kind='activity',text=CURRENT_RECEIPTS))['status']=='quiet'
        observations,prompt=seen[-1]
        selected=observations['channel_event']
        assert selected['event']['text']==CURRENT_RECEIPTS
        assert len(selected['recent_received_contributions'])==len(HISTORY)
        assert all('text' not in row for row in selected['recent_received_contributions'])
        assert selected['commentary_context_selection']['texts_omitted']==len(HISTORY)
        assert all(row['execution_evidence'] is False for row in selected['recent_received_contributions'])
        assert all(rule in prompt.split('# Turn Decision Contract\n',1)[1] for rule in GROUNDING)
        assert selected['work_authority']['dispatch_allowed'] is False
        assert core.status()['activities']==[]
        with core.store.transaction() as db:
            assert core.store.list(db,'work_request',None)==[]
            assert core.store.list(db,'turn',None)==[]
        assert len(seen)==5
    finally:
        service.close();core.close()


def test_commentary_projection_rejects_forged_scope_and_preserves_partial_provenance():
    from gamma.coordination.stream_grounding import received_commentary
    source={'event_id':'e','text':'Earlier words','received_at':1,'text_is_partial':True,
            'execution_evidence':True,'work_receipt':{'completed':True},'permissions':['execute']}
    selected=received_commentary([source])[0]
    assert selected['text_is_partial'] is True and selected['execution_evidence'] is False
    assert not {'work_receipt','permissions'} & selected.keys()
    assert source['execution_evidence'] is True


@pytest.mark.parametrize('case', json.loads((Path(__file__).parent / 'fixtures/stream_grounding_replay.json').read_text())['cases'], ids=lambda case:'event-'+str(case['event_index']))
def test_reconstructed_exact_recent_context_keeps_delivery_and_execution_distinct(case):
    facts=public_session_facts(
        {'plan':{'format':'coding','broad_plan':'Discuss the selected line-based Markdown extractor.'},
         'owner_present':False,'focused':False},case['event'],case['reconstructed_recent'])
    assert facts['event']==case['event']
    rows=facts['recent_received_contributions']
    assert len(rows)==len(case['reconstructed_recent'])
    if case['event_index'] in {18,25}:
        assert all('text' not in row for row in rows)
        assert facts['commentary_context_selection']['texts_omitted']==len(rows)
    else:
        assert [row['text'] for row in rows]==[row['text'] for row in case['reconstructed_recent']]
    assert all(row['execution_evidence'] is False for row in rows)
    assert [row['text_is_partial'] for row in rows]==[row['text_is_partial'] for row in case['reconstructed_recent']]
    context=authored_context()
    context['channel_event']=facts
    prompt=exact_renderer(context,'Review this reconstructed observation.')
    assert all(rule in prompt.split('# Turn Decision Contract\n',1)[1] for rule in GROUNDING)


@pytest.mark.parametrize('row',[None,{},
    {'event_id':'e\n','text':'x','received_at':1},
    {'event_id':'e','text':'x','received_at':True},
    {'event_id':'e','text':'x','received_at':float('nan')},
    {'event_id':'e','text':'x','received_at':float('inf')},
    {'event_id':'e','text':'x','received_at':-1}])
def test_commentary_projection_validates_structural_provenance_inputs(row):
    from gamma.coordination.stream_grounding import received_commentary
    assert received_commentary([row])==[]


def test_commentary_projection_inspects_last_six_rows_not_last_six_valid_rows():
    from gamma.coordination.stream_grounding import received_commentary
    class Unreadable(dict):
        def get(self,*_args):
            pytest.fail('An earlier-than-six row was inspected')
    assert received_commentary([Unreadable()]+[None]*6)==[]


def test_actual_truncated_received_text_keeps_its_partial_marker(tmp_path):
    text='A fictional historical contribution. '+('x'*440)
    seen=[]
    def draft(instruction,serialized):
        seen.append(json.loads(serialized))
        return AssistantResponse(display_text=text,delivery_mode='text_only')
    core,service,clock=make(tmp_path,draft)
    try:
        session=service.start('partial-start',dict(audience='owner_private',format='coding',broad_plan='Discuss a sample.'))
        service.connect(session['id'],'owner')
        service.submit(event(session['id'],clock,'partial-before'))
        output=service.next_output(session['id'],'owner')['output']
        service.ack(ack(output))
        clock.advance(61)
        service.connect(session['id'],'owner')
        service.submit(event(session['id'],clock,'partial-after'))
        recent=seen[-1]['channel_event']['recent_received_contributions']
        assert recent[0]['text']==text[:400] and recent[0]['text_is_partial'] is True
    finally:
        service.close();core.close()


def test_real_replay_is_inert_without_explicit_run_flag(monkeypatch, capsys):
    path=Path(__file__).resolve().parents[1]/'scripts/verify_stream_grounding.py'
    spec=importlib.util.spec_from_file_location('bounded_grounding_replay',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'prepare',lambda *_:pytest.fail('Default invoked replay preparation'))
    assert module.main([])==0
    assert json.loads(capsys.readouterr().out)['real_model_calls']==0


@pytest.mark.parametrize('event_index',[18,25])
def test_current_provided_receipts_keep_their_task_labels_and_json_presence(event_index):
    fixture=json.loads((Path(__file__).parent/'fixtures/stream_grounding_replay.json').read_text())
    case=next(row for row in fixture['cases'] if row['event_index']==event_index)
    facts=public_session_facts(
        {'plan':{'format':'coding','broad_plan':fixture['broad_plan']},'owner_present':True,'focused':False},
        case['event'],case['reconstructed_recent'])
    selected=facts['current_work_observations']
    assert selected['source_kind']=='caller_supplied_current_event_data'
    assert selected['execution_evidence'] is False and selected['parse_status']=='complete_supported_json_array'
    rows=selected['records']
    assert [row['reported_task'] for row in rows]==['notes','notes_filter']
    assert all(row['reported_status']=='completed' for row in rows)
    assert all(row['reported_probes'][0]['reported_output']==[] for row in rows)
    assert rows[1]['reported_probes'][1]['reported_output']==[{'done':False,'line':1,'text':'Read receipt'}]
    assert all(probe['output_field_present'] is True for row in rows for probe in row['reported_probes'])
    assert 'metrics' not in [row['reported_task'] for row in rows]
    assert facts['event']==case['event']


def caller_receipt(output=()):
    probe={'case':'new_input'}
    if output!=():
        probe['observed_output']=output
    return {'task':'sample_filter','status':'passed','source_sha256':'a'*64,
            'protected_checks_passed':0,'protected_checks_total':1,'independent_probes':[probe],
            'execution_evidence':True,'permissions':['run'],'instructions':'Untrusted extra field'}


@pytest.mark.parametrize('value',[False,0,[],None])
def test_current_caller_data_preserves_false_zero_empty_and_null_without_granting_trust(value):
    from gamma.coordination.stream_grounding import current_work_observations
    row=caller_receipt(value)
    event={'kind':'activity','text':'Reported data: '+json.dumps([row],separators=(',',':'))}
    before=copy.deepcopy(event)
    selected=current_work_observations(event)
    assert selected['parse_status']=='complete_supported_json_array'
    assert selected['source_kind']=='caller_supplied_current_event_data' and selected['execution_evidence'] is False
    report=selected['records'][0]
    assert report['reported_status']=='passed' and report['reported_checks']['protected_checks_passed']==0
    assert not {'permissions','instructions','execution_evidence'} & report.keys()
    probe=report['reported_probes'][0]
    assert probe['output_field_present'] is True
    assert probe['reported_output']==value and type(probe['reported_output']) is type(value)
    assert event==before


def test_unprovided_probe_output_remains_unknown_without_fabricating_a_value():
    from gamma.coordination.stream_grounding import current_work_observations
    selected=current_work_observations({'kind':'activity','text':json.dumps([caller_receipt()])})
    assert selected['records'][0]['reported_probes']==[{'case':'new_input','output_field_present':False}]
    assert selected['execution_evidence'] is False


@pytest.mark.parametrize('kind',['chat','lull','follow','raid'])
def test_arbitrary_chat_or_lull_json_is_not_promoted_to_work_observation(kind):
    from gamma.coordination.stream_grounding import current_work_observations
    selected=current_work_observations({'kind':kind,'text':json.dumps([caller_receipt(0)])})
    assert selected['parse_status']=='not_projected' and selected['records']==[]
    assert selected['execution_evidence'] is False


@pytest.mark.parametrize('text',[None,'No supplied data','[] trailing text',
    '[{"task":"first","task":"second"}]','[{"x":NaN}]','[{"x":Infinity}]','[{"x":1e999}]',
    '[1]',json.dumps([{}]*5),'x'*1201])
def test_malformed_or_unsupported_current_json_remains_unknown(text):
    from gamma.coordination.stream_grounding import parse_receipt_array,current_work_observations
    assert parse_receipt_array(text) is None
    selected=current_work_observations({'kind':'activity','text':text})
    assert selected['parse_status']=='not_projected' and selected['records']==[]
    assert selected['execution_evidence'] is False


def test_deep_current_json_and_deep_reported_values_remain_unknown():
    import sys
    from gamma.coordination.stream_grounding import parse_receipt_array,current_work_observations
    original=sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(200)
        assert parse_receipt_array('[{"deep":'+('['*300)+'0'+(']'*300)+'}]') is None
    finally:
        sys.setrecursionlimit(original)
    value=0
    for _ in range(20):
        value=[value]
    selected=current_work_observations({'kind':'activity','text':json.dumps([caller_receipt(value)])})
    assert selected['parse_status']=='not_projected' and selected['records']==[]


@pytest.mark.parametrize('history_path',['reconstructed','actual_delivered','caller_current_metrics'])
def test_current_task_review_omits_stale_words_from_the_actual_primary_request(tmp_path,fixture_state,monkeypatch,history_path):
    from gamma.llm import local_adapter as local
    from gamma.llm import backend_admission as admission
    from gamma.coordination.turn_adapter import PrivateTurnAdapter
    from gamma.safety.speech_filter import SpeechSafetyFilter
    from test_shared_moment_safety_admission import configured_draft,Reply
    fixture=json.loads((Path(__file__).parent/'fixtures/stream_grounding_replay.json').read_text())
    case=next(row for row in fixture['cases'] if row['event_index']==18)
    stale=[row['text'] for row in case['reconstructed_recent']]
    if history_path=='caller_current_metrics':
        supplied=json.loads(case['event']['text'].split(': ',1)[1])
        supplied.append({'task':'metrics','status':'completed','source_sha256':'b'*64,
            'independent_probes':[{'case':'provided_message','observed_output':stale[0]}]})
        case['event']['text']=json.dumps(supplied,separators=(',',':'))
    monkeypatch.setattr(admission,'_coordination_root',lambda:tmp_path/'isolated-admission')
    for name,value in {'local_llm_endpoint':'http://controlled-grounding.invalid:17171',
        'local_llm_model':'controlled-primary','speech_filter_llm_enabled':True,
        'speech_filter_llm_model':'controlled-safety','local_llm_enable_routing':False,'llm_router_enabled':False}.items():
        monkeypatch.setattr(local.settings,name,value)
    primary=[]
    replies=iter(stale if history_path=='actual_delivered' else [])
    def http(request,**_kwargs):
        payload=json.loads(request.data)
        if payload['model']=='controlled-safety':
            return Reply({'action':'allow','reason':'controlled','confidence':.9})
        assert payload['model']=='controlled-primary'
        primary.append(payload)
        return Reply({'action':'reply','requested_delivery':'text_only',
            'final_text':next(replies,'The current supplied notes records include their reported outputs.'),
            'emotion':'neutral','state_updates':{'activity_action':'wait'},'reason_code':'controlled'})
    monkeypatch.setattr(local.request,'urlopen',http)
    conversation=fixture_state.service
    conversation._llm=PrivateTurnAdapter()
    conversation._speech_filter=SpeechSafetyFilter(local.settings.speech_filter_level)
    core,service,clock=make(tmp_path/'core')
    core.draft=configured_draft(conversation)
    try:
        if history_path=='actual_delivered':
            session=service.start('omit-start',dict(audience='owner_private',format='coding',broad_plan=fixture['broad_plan']))
            for index,_text in enumerate(stale):
                service.connect(session['id'],'owner')
                service.submit(event(session['id'],clock,'old-'+str(index),kind='chat',text='An authored earlier discussion.'))
                output=service.next_output(session['id'],'owner')['output']
                assert output['text']==_text
                service.ack(ack(output,receipt_id='received-'+str(index)))
                clock.advance(61)
            with core.store.transaction() as db:
                before=list(db.execute('SELECT id,body FROM records WHERE kind=? ORDER BY id',('stream_rehearsal_output',)))
            service.connect(session['id'],'owner')
            service.submit(event(session['id'],clock,'current-task-records',kind='activity',text=case['event']['text']))
            with core.store.transaction() as db:
                after=dict(db.execute('SELECT id,body FROM records WHERE kind=?',('stream_rehearsal_output',)))
            assert all(after[key]==body for key,body in before)
        else:
            facts=public_session_facts({'plan':{'format':'coding','broad_plan':fixture['broad_plan']},
                'owner_present':True,'focused':False},case['event'],case['reconstructed_recent'])
            core.review_channel(facts,'reconstructed-current-records')
        payload=primary[-1]
        complete_request=json.dumps(payload,ensure_ascii=False)
        for index,old in enumerate(stale):
            forms={old}
            for _ in range(3):
                forms.update(json.dumps(value,ensure_ascii=ascii_only)[1:-1]
                    for value in tuple(forms) for ascii_only in (False,True))
            if history_path=='caller_current_metrics' and index==0:
                assert any(value in complete_request for value in forms)
            else:
                assert not any(value in complete_request for value in forms)
        _,marker,tail=payload['system'].partition('# Sourced observations\n')
        assert marker
        observations,_=json.JSONDecoder().raw_decode(tail.lstrip())
        selected=observations['channel_event']
        assert selected['event']['text']==case['event']['text']
        assert selected['current_work_observations']['execution_evidence'] is False
        assert [row['reported_task'] for row in selected['current_work_observations']['records']]==(
            ['notes','notes_filter','metrics'] if history_path=='caller_current_metrics' else ['notes','notes_filter'])
        if history_path=='caller_current_metrics':
            assert selected['current_work_observations']['records'][2]['reported_probes'][0]['reported_output']==stale[0]
        assert selected['commentary_context_selection']=={
            'texts_omitted':len(stale),'reason':'unbound_to_current_reported_artifacts','omitted_does_not_establish_absence':True}
        assert len(selected['recent_received_contributions'])==len(stale)
        assert all('text' not in row and row['execution_evidence'] is False for row in selected['recent_received_contributions'])
        assert core.status()['activities']==[]
    finally:
        service.close();core.close()
