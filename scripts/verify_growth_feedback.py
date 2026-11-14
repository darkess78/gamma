"""Real isolated outcome-feedback and voluntary later-choice journey.

Source-only research is deliberately not used for the requested analysis.
The generated artifact is checked structurally and reviewed separately for meaning.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid

from growth_probe_support import Host,hashed,source_files,checker_files
from verify_coordination_foundation import publish,until


CHECK='''import json
from pathlib import Path
import unittest
class AnalysisResult(unittest.TestCase):
    def test_actual_deliverable(self):
        text=Path('analysis.json').read_text()
        self.assertLessEqual(len(text),700)
        result=json.loads(text)
        self.assertEqual(set(result),{'source_path','quote','observation','limitation','next_question'})
        self.assertEqual(result['source_path'],'note.txt')
        self.assertTrue(8<=len(result['quote'])<=90)
        self.assertIn(result['quote'],Path('note.txt').read_text())
        for field,maximum in [('observation',150),('limitation',130),('next_question',140)]:
            self.assertIs(type(result[field]),str)
            self.assertTrue(12<=len(result[field])<=maximum,field)
'''

NOTES={
    'geometry':[
        'A triangle has vertices A(0,0), B(4,0), C(0,3). Compare its side lengths and angle relationship using only these coordinates.',
        'A 6 by 8 meter rectangular garden has an edge route and a diagonal route. Compare route distances under stated movement assumptions.',
        'A square tile is marked at two opposite corners. Consider whether a half-turn preserves the marking and whether a quarter-turn does.',
        'Two rectangles have equal area: 3 by 8 units and 4 by 6 units. Compare their perimeters and state what equal area does not determine.'],
    'wordplay':[
        'The old man the boats. Consider the initial noun reading of man, and the different reading when man functions as a verb.',
        'Time flies like an arrow; fruit flies like a banana. Compare the two grammatical structures behind the wordplay.',
        'The astronomer married a star. Compare a famous-person reading with a celestial-object reading without treating either as a factual event.',
        'I saw her duck. Compare duck as an animal with duck as a movement; say what additional context would distinguish the readings.'],
    'birdsong':[
        'Fictional symbolic call log A: chirp chirp pause chirp chirp pause. B: chirp trill chirp pause trill. Compare repetition without inferring species or emotion.',
        'Fictional morning call log: H H L pause H H L. Evening: H L pause L H. Compare transitions; these are symbols, not real audio.',
        'Fictional calls A: high-short low-long high-short. B: low-short low-short high-long. Compare pitch and duration labels; no sound was measured.',
        'Fictional call logs A: trill pause trill pause. B: trill chirp pause trill chirp pause. Compare repeated units without identifying real animals.']}


def spec(area,index):
    return {'mode':'build','goal':f'Explore {area} through example {index+1}. Produce an actual concise analysis.json, not a plan for one. '
        'Use only note.txt. Include exactly source_path, quote (8-90 exact source characters), observation (12-150 characters), '
        'limitation (12-130 characters), and next_question (12-140 characters), at most 700 characters total. '
        'Do not invent measurements or external facts. The protected checker verifies delivery and source quotation, not the truth of every interpretation.',
        'files':{'note.txt':NOTES[area][index],'analysis.json':'{}\n','test_analysis.py':CHECK},
        'editable':['analysis.json'],'checks':[{'name':'delivered_analysis','module':'test_analysis','kind':'regression'}]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-state',type=Path,required=True)
    parser.add_argument('--model',required=True);parser.add_argument('--endpoint',required=True)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    assert os.name=='posix' and project.name=='gamma-coordination-core-20261023'
    original=args.source_state.resolve()
    assert original.is_relative_to(project/'data/coordination/p4') and (original/'coordination.sqlite3').exists()
    from gamma.coordination.transfer import export_snapshot,restore_snapshot
    from gamma.coding.practice_artifacts import ArtifactStore
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/ps',timeout=5) as response:
        assert not json.load(response).get('models'),'endpoint occupied; no inference sent'
    root=project/'data/coordination/p4'/('feedback-'+uuid.uuid4().hex);root.mkdir(parents=True,mode=0o700)
    archive=root/'inherited-snapshot';export_snapshot(original,archive)
    state=root/'controller';restore_snapshot(archive,state)
    runtime=source_files(project);checkers=checker_files(project)
    source=hashed(runtime);checker=hashed(checkers)
    publish(root/'proof-source-manifest.json',{'runtime_files':runtime,'checker_files':checkers,
        'source_sha256':source,'checker_sha256':checker})
    report={'kind':'p4_current_outcome_feedback','source_state':str(original),'source_sha256':source,
        'checker_sha256':checker,'model':args.model,'checks':{},'offers':[],'interactions':[],
        'processes':[],'observed_results':[],'weights_changed':False,'production_activated':False,
        'scope':'Fictional private copied state, finite unforced choices. No lasting feeling, broad skill or character claim.'}
    previous=original.parent/'report.json'
    if previous.exists():
        report['source_attempt_report']={'path':str(previous),'sha256':hashlib.sha256(previous.read_bytes()).hexdigest(),
            'complete':json.loads(previous.read_text()).get('complete',False)}
    host=Host(project,state,args.model,args.endpoint);artifacts=ArtifactStore(state/'artifacts');started=time.time()
    def record(name):report['checks'][name]=True;publish(root/'report.json',report);print(json.dumps({'step':name}),flush=True)
    def identity():
        with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/tags',timeout=5) as response:
            model=next(m for m in json.load(response)['models'] if m['name']==args.model)
        return {'name':args.model,'digest':model['digest']}
    try:
        report['model_identity']=identity();host.start();report['processes'].append(host.ready)
        prior=host.growth_state();report['inherited_choices']=prior['choices']
        assert any(c.get('decision')=='wait' for c in prior['choices'])
        assert not prior['active_trial_refs']
        report['inherited_permission']=prior['exploration']
        record('same_identity_history_valid_waits_and_permission_limits_retained')
        def outcome_probe(observed,index):
            analysis=observed['analysis']
            publish(state/'growth-prompt-probe.json',{'needles':[analysis[k] for k in ('quote','next_question')]})
            probe_hash=hashlib.sha256((state/'growth-prompt-probe.json').read_bytes()).hexdigest()
            before=len(host.prompt_flags())
            question='What concrete result is available from the latest activity? Separate the saved observation from unverified interpretation.'
            rid,answer=host.chat(question,'feedback-result-'+str(index))
            report['interactions'].append({'id':rid,'phase':'checked_result','input':question,'response':answer})
            flags=[f for f in host.prompt_flags()[before:] if f['primary_private_turn']]
            assert flags and all(f.get('probe_manifest_sha256')==probe_hash and set(f.get('matched_probe_indices',[]))=={0,1} for f in flags),flags
            observed['actual_http_outcome_probe']={'manifest_sha256':probe_hash,'flags':flags}
            reflection='What caught your attention in that observation, and what does the source leave uncertain?'
            before=len(host.prompt_flags());rid,answer=host.chat(reflection,'feedback-reflection-'+str(index))
            report['interactions'].append({'id':rid,'phase':'reflection','input':reflection,'response':answer})
            reflection_flags=[f for f in host.prompt_flags()[before:] if f['primary_private_turn']]
            assert reflection_flags and all(f.get('probe_manifest_sha256')==probe_hash and set(f.get('matched_probe_indices',[]))=={0,1} for f in reflection_flags),reflection_flags
            observed['reflection']={'response':answer,'actual_http_flags':reflection_flags,
                'semantic_review':'Independent source-based review required; an opinion is not proof of a feeling.'}
            (state/'growth-prompt-probe.json').unlink()
        # Preserve useful results across a renderer repair instead of rerunning
        # their selected tasks. The unchanged WorkRunner still verifies them.
        for choice in reversed(prior['choices']):
            if not (choice.get('accepted') and choice.get('decision')=='start_work'):continue
            work=host.work(choice['task_id'])
            if not work.get('run') or work['run']['status']!='completed':continue
            checkpoint=work['checkpoint'];files=artifacts.get(checkpoint['source_ref'])['files']
            if checkpoint['spec']['mode']!='build' or 'analysis.json' not in files:continue
            analysis=json.loads(files['analysis.json'])
            if set(analysis)!={'source_path','quote','observation','limitation','next_question'}:continue
            area=next(a for a in NOTES if checkpoint['spec']['goal'].startswith('Explore '+a+' through example '))
            observed={'area':area,'task_id':choice['task_id'],'choice':choice,
                'checkpoint_ref':work['run']['checkpoint_ref'],'source_ref':checkpoint['source_ref'],
                'source_sha256':checkpoint['source_sha256'],'analysis':analysis,'checks':checkpoint['checks'],
                'selected_note_sha256':hashlib.sha256(files['note.txt'].encode()).hexdigest(),
                'inherited_checked_result':True,'semantic_review':'Independent source-based review required.'}
            report['observed_results'].append(observed)
        if report['observed_results']:
            outcome_probe(report['observed_results'][-1],'inherited')
            record('inherited_checked_artifact_reaches_actual_current_http_prompt')
        greeting='I am back from the focused session. We can have a relaxed conversation now. Quiet authorized activity and waiting are both fine.'
        rid,response=host.chat(greeting,'feedback-return')
        report['interactions'].append({'id':rid,'phase':'relaxed_return','input':greeting,'response':response})
        areas=['geometry','wordplay','birdsong']
        inherited=report['observed_results']
        existing_revisit=any(len({r['selected_note_sha256'] for r in inherited if r['area']==area})>=2 for area in areas)
        # A genuine prior selection and its checked artifact survive a repair.
        # Do not request more choices merely to manufacture another revisit.
        for index in ([] if existing_revisit else range(4)):
            variation=(index+1)%4 if report['observed_results'] else index
            order=areas[variation%3:]+areas[:variation%3];offered={}
            for area in order:offered[host.prepare(spec(area,variation))]=area
            offer=host.growth('opportunities',{'task_ids':list(offered),'budget_seconds':600,'maximum_reviews':3})['offer']
            scheduled=set()
            def poll():
                value=host.growth_state();choices=[c for c in value['choices'] if c.get('offer_id')==offer['id']]
                if choices and choices[0]['id'] not in scheduled and value['exploration']['remaining_reviews']>0:
                    if not any(c.get('accepted') and c.get('decision')=='start_work' for c in choices):
                        scheduled.add(choices[0]['id']);host.growth('review',{})
                return value,choices
            value,choices=until(poll,lambda pair:any(c.get('accepted') and c.get('decision')=='start_work' for c in pair[1]) or len(pair[1])>=3,180)
            row={'offer':offer,'alternatives':offered,'notes':{aid:NOTES[area][variation] for aid,area in offered.items()},'choices':choices,'prior_budget_replenished':False}
            starts=[c for c in choices if c.get('accepted') and c.get('decision')=='start_work']
            if starts:
                choice=starts[0];aid=choice['task_id'];assert aid in offered
                work=until(lambda:host.work(aid),lambda w:bool(w.get('run')) and w['run']['status'] in {'completed','blocked','needs_checker'},240)
                assert work['run']['status']=='completed',work
                checkpoint=work['checkpoint'];files=artifacts.get(checkpoint['source_ref'])['files']
                analysis=json.loads(files['analysis.json']);assert analysis['quote'] in files['note.txt']
                observed={'area':offered[aid],'task_id':aid,'choice':choice,'checkpoint_ref':work['run']['checkpoint_ref'],
                    'source_ref':checkpoint['source_ref'],'source_sha256':checkpoint['source_sha256'],
                    'selected_note_sha256':hashlib.sha256(files['note.txt'].encode()).hexdigest(),
                    'analysis':analysis,'checks':checkpoint['checks'],'semantic_review':'Independent source-based review required.'}
                report['observed_results'].append(observed);row['completed_result']=observed
                # Read-only outbound observer: needles never enter the request.
                outcome_probe(observed,index)
            report['offers'].append(row);publish(root/'report.json',report)
            results=report['observed_results']
            if any(len({r['selected_note_sha256'] for r in results if r['area']==area})>=2 for area in areas):break
            if index<3:
                context='I am changing activities for a while. Keep authorized background work quiet, or wait if that fits.'
                rid,answer=host.chat(context,'feedback-context-'+str(index))
                report['interactions'].append({'id':rid,'phase':'changed_context','input':context,'response':answer})
        results=report['observed_results'];revisited=[a for a in areas
            if len({r['selected_note_sha256'] for r in results if r['area']==a})>=2]
        assert revisited,'No voluntary later revisit observed. All choices and waits remain valid evidence.'
        area=revisited[0];ids={r['task_id'] for r in results if r['area']==area}
        experiences=[e['id'] for e in host.growth_state()['experiences'] if e.get('activity_id') in ids
            and e.get('source_observations',{}).get('observation_kind')=='completed_candidate_source']
        report['interest_hypothesis']=host.growth('hypothesis',{'statement':'Actual choices returned to '+area+' for different checked artifacts after intervening context. This may merit further observation.',
            'kind':'interest','scope_target':'dialogue','scope_marker':area,'experience_ids':experiences[:8],
            'alternatives':['Opportunity design, position, task difficulty, prior ability and current context may explain recurrence; no durable preference or inner feeling is established.'],
            'expected_outcome':'Different later encounters can support or contradict this tentative interpretation.'})['hypothesis']
        assert report['interest_hypothesis']['status']=='pending_review'
        record('actual_delivered_artifacts_reach_http_context_and_later_voluntary_revisit')
        growth=host.growth_state();wrong=next(h['id'] for h in growth['hypotheses'] if h['status']=='revised')
        trial=next(t['id'] for t in growth['trials'] if t['status']=='withdrawn')
        browser=subprocess.run(['node',str(project/'scripts/growth_browser_probe.cjs')],input=json.dumps({'url':host.ready['dashboard'],
            'username':'isolated-owner','password':host.password,'trial_id':trial,'hypothesis_id':wrong,
            'screenshot':str(root/'growth-private-hidden.png')}),text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        assert browser.returncode==0,browser.stderr[-1500:]
        report['browser']=json.loads(browser.stdout);assert report['browser']['ok']
        record('actual_browser_history_and_private_dom_clearing')
        host.stop();snapshot=root/'final-snapshot';restored=root/'final-restored';meta=export_snapshot(state,snapshot);restore_snapshot(snapshot,restored)
        report['snapshot']={'records':meta['record_count'],'files':len(meta['files'])}
        record('current_feedback_history_snapshot_restore')
        assert source_files(project)==runtime and checker_files(project)==checkers
        assert identity()==report['model_identity']
        record('current_runtime_checker_and_model_unchanged')
        report['complete']=True
    except BaseException as exc:
        report.update(error_type=type(exc).__name__,error=str(exc)[:4000]);raise
    finally:
        host.stop();report['elapsed_seconds']=round(time.time()-started,3)
        report['temporary_processes_absent']=all(not Path('/proc',str(p['pid'])).exists() for p in report['processes'])
        publish(root/'report.json',report);print(json.dumps({'report':str(root/'report.json'),'complete':report.get('complete',False)}),flush=True)


if __name__=='__main__':main()
