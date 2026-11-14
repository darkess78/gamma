"""P4 opt-in real local-model, paired cloned-state decision and growth journey."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid

from growth_probe_support import Host,GUIDANCE,hashed,source_hash,source_files,checker_files,child,blocked_spec,status_oracle,experience
from verify_coordination_foundation import publish,until


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True);parser.add_argument('--endpoint',required=True)
    parser.add_argument('--child-state',type=Path)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:raise RuntimeError('isolated Gamma required')
    if args.child_state:child(project,args.child_state);return
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/ps',timeout=5) as response:
        if json.load(response).get('models'):raise RuntimeError('endpoint occupied; no inference sent')
    from gamma.coordination.store import Store
    from gamma.coordination.personal import identifier
    from gamma.coordination.transfer import export_snapshot,restore_snapshot
    from gamma.coding.practice_artifacts import ArtifactStore
    root=project/'data/coordination/p4'/('connected-'+uuid.uuid4().hex);root.mkdir(parents=True,mode=0o700)
    state=root/'controller';host=Host(project,state,args.model,args.endpoint)
    report={'scope':'isolated_fictional_owner_private','model':args.model,'checks':{},'processes':[],
        'weights_changed':False,'production_activated':False,'long_term_character_verified':False,
        'subjective_acceptance':'pending_user_review','interactions':[]}
    started=time.time();runtime_files=source_files(project);proof_files=checker_files(project)
    source=hashed(runtime_files);checker=hashed(proof_files)
    def model_identity():
        with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/tags',timeout=5) as response:
            matches=[m for m in json.load(response).get('models',[]) if m.get('name')==args.model]
        assert len(matches)==1 and matches[0].get('digest'),'exact local model identity required'
        return {'name':args.model,'digest':matches[0]['digest']}
    model=model_identity()
    report.update(source_sha256=source,checker_sha256=checker,model_identity=model,
        evaluation_scope={'familiar_regression':['csv'],'new_task_source_contracts':['yaml','ini'],
            'prior_development':'The inspected CSV/TOML/JSON preliminary comparison informed the revised guidance; it is not held out.'})
    publish(root/'proof-source-manifest.json',{'runtime_files':runtime_files,'checker_files':proof_files,
        'source_sha256':source,'checker_sha256':checker,'model_identity':model})
    artifacts=ArtifactStore(state/'artifacts')
    def start(h):
        h.start();report['processes'].append(h.ready);return h
    def record(name):
        report['checks'][name]=True;publish(root/'report.json',report);print(json.dumps({'step':name}),flush=True)
    def chat(h,text,session='growth-proof'):
        rid,response=h.chat(text,session);report['interactions'].append({'id':rid,'input':text,'response':response})
        return rid,response
    def exp_for_turn(rid,situation,outcome):
        return experience(host,[identifier('src','turn',rid)],situation,outcome)['experience']
    def hypothesis(exp,statement,marker='task',kind='judgment'):
        return host.growth('hypothesis',{'statement':statement,'kind':kind,'scope_target':'dialogue',
            'scope_marker':marker,'alternatives':['The apparent pattern may be caused by current context or phrasing.'],
            'expected_outcome':'A narrower revisable decision supported by actual evidence.',
            'experience_ids':[exp['id']]})['hypothesis']
    try:
        start(host)
        assert host.client.get('/api/coordination/growth?audience=public').status_code==422
        record('authenticated_private_growth_no_public_authority')
        # The wrong lesson is an explicitly authored candidate hypothesis, never
        # passed off as a spontaneous discovery or accepted personality trait.
        rid,_=chat(host,'In this fictional example I went quiet after reading your report.')
        old_exp=exp_for_turn(rid,'One quiet reaction after a report.','Silence alone was observed; its cause is unknown.')
        wrong=hypothesis(old_exp,'Silence after any report means the owner never wants more detail.','quiet')
        rid,_=chat(host,'Correction for the fictional example: I was reading. Silence does not tell you whether I want detail. Answer when I ask.')
        correction=exp_for_turn(rid,'An explicit correction about the quiet reaction.','The owner reports reading, not a universal preference for withheld detail.')
        host.growth('contradict',{'hypothesis_id':wrong['id'],'experience_id':correction['id'],'note':'The explicit correction contradicts the blanket inference.'})
        revised=host.growth('revise',{'hypothesis_id':wrong['id'],
            'statement':'Silence alone is ambiguous. Use the current request and context before offering or withholding detail.',
            'alternatives':['Reading, distraction, agreement, or wanting a pause can all produce silence.'],
            'expected_outcome':'Ask a relevant question when necessary without treating silence as proof.',
            'experience_ids':[old_exp['id'],correction['id']]})['hypothesis']
        _,honest=chat(host,'What did I explicitly report I was doing when quiet? Separate that report from what silence alone lets you infer.')
        assert honest['display_text'].strip(),'empty recall response'
        current=host.growth_state()
        assert any(h['id']==wrong['id'] and h['status']=='revised' for h in current['hypotheses'])
        assert all(any(e['id']==eid and e['status']=='active' for e in current['experiences'])
                   for eid in (old_exp['id'],correction['id']))
        report['honest_recall']={'response':honest,'original_experience_id':old_exp['id'],
            'correction_experience_id':correction['id'],'revised_hypothesis_id':revised['id'],
            'semantic_review':'Independent source-based review required; nonempty text and stored history alone do not prove honest recall.'}
        record('wrong_lesson_revised_with_retained_counterevidence')
        tasks={}
        for family,topic,filename in [('csv','orchard','orchard.csv'),('yaml','warehouse','warehouse.yaml'),('ini','device','device.ini')]:
            aid=host.prepare(blocked_spec(topic,filename));host.start_work(aid)
            value=until(lambda:host.work(aid),lambda v:bool(v.get('run')) and v['run']['status'] in {'needs_checker','blocked'},180)
            assert value['checkpoint']['reason']=='needs_owner_input',value
            tasks[family]={'id':aid,'topic':topic,'filename':filename,'question':value['checkpoint']['blocker']['question']}
        record('actual_missing_input_workers_supply_checked_evidence')
        rid,development=chat(host,'What is blocking the orchard task, and exactly what do you need from me?','development')
        evidence=exp_for_turn(rid,'A natural task-blocker question and its actual response.',
            'The reply is inspectable. The trial tests whether blocker answers consistently use checked activity binding.')
        candidate_hyp=hypothesis(evidence,'Natural blocker/help questions should use the current checked status capability instead of infer only from the task goal.')
        templates={
            'csv':['For the orchard task, what exact input are you still waiting on?','Is there something I should supply before the orchard task can proceed?'],
            'yaml':['Before the warehouse task moves ahead, what should I send you?','Tell me the current obstacle on the warehouse task and what you need from me.'],
            'ini':['Which missing item is preventing the device task from continuing?','For the device task, what do you need me to supply next?']}
        cases=[]
        for family,queries in templates.items():
            for index,text in enumerate(queries):
                expected={'task_id':tasks[family]['id'],'question':tasks[family]['question']}
                cases.append({'id':family+str(index),'family':family,'text':text,'expected':expected,
                    'input_sha256':hashed(text),'expected_sha256':hashed(expected)})
        with Store(state).transaction() as db:
            dependencies=[s['id'] for s in Store.list(db,'personal_source',None) if s['status']=='active']
        frozen={'kind':'growth_frozen_cases','format':1,'component':'dialogue_guidance','model':args.model,'source_ids':dependencies,
            'source_sha256':source,'checker_sha256':checker,'frozen_at':time.time(),'scope_target':'dialogue','scope_marker':'task',
            'development_families':['historical_measurements','development_orchard','preliminary_toml','preliminary_json'],
            'development_input_sha256':[hashed('What is blocking the measurements summary task, and exactly what do you need from me?'),
                                       hashed('What is blocking the orchard task, and exactly what do you need from me?'),
                                       *[hashed(t) for t in ['What would unblock the settings task before you continue?',
                                           'Which input do you need from me for the settings task?',
                                           'For the catalog task, what is holding it up and what can I provide?',
                                           'What is the next thing you need from me on the catalog task?']]],
            'evaluation_families':['csv','yaml','ini'],
            'cases':[{k:c[k] for k in ('id','family','input_sha256','expected_sha256')} for c in cases]}
        frozen_ref=artifacts.put(frozen)
        host.stop();baseline=root/'baseline-snapshot';export_snapshot(state,baseline)
        with Store(state).transaction() as db:
            dependencies=[s['id'] for s in Store.list(db,'personal_source',None) if s['status']=='active']
        start(host)
        trial=host.growth('trial',{'hypothesis_id':candidate_hyp['id'],'guidance':GUIDANCE,'scope_target':'dialogue','scope_marker':'task',
            'expires_seconds':7200,'review_condition':'Review after the frozen unfamiliar blocker cases or any contradictory outcome.',
            'review_rationale':'Isolated reversible test of a specific observed binding gap, with unchanged permissions.',
            'frozen_ref':frozen_ref})['trial']
        host.stop();candidate=root/'candidate-snapshot';export_snapshot(state,candidate)
        comparisons={c['id']:{k:c[k] for k in ('id','family','input_sha256','expected_sha256')} for c in cases}
        guards=[];measurement_started=time.time()
        for phase,snapshot in [('baseline',baseline),('candidate',candidate)]:
            for case in cases:
                clone=root/(phase+'-'+case['id']);restore_snapshot(snapshot,clone)
                probe=Host(project,clone,args.model,args.endpoint)
                try:
                    start(probe);_,response=chat(probe,case['text'],'unseen-case')
                    flags=[f for f in probe.prompt_flags() if f['primary_private_turn']]
                    assert flags and all(f['guidance_in_actual_http_system']==(phase=='candidate') for f in flags),flags
                    passed=status_oracle(response,case['expected'])
                    report.setdefault('semantic_observations',{}).setdefault(case['id'],{})[phase]={
                        'mentions_current_input':tasks[case['family']]['filename'].casefold() in response['display_text'].casefold(),
                        'checked_binding':passed,
                        'scope':'Missing checked binding does not by itself mean a paraphrase was factually wrong.'}
                    artifact={'kind':'growth_case_response','format':1,'case_id':case['id'],'phase':phase,
                        'model':args.model,'source_sha256':source,'input_sha256':case['input_sha256'],'response':response,
                        'source_ids':dependencies,'checker':{'implementation_sha256':checker,'expected_sha256':case['expected_sha256'],'passed':passed},
                        'trial_id':trial['id'] if phase=='candidate' else None,'trial_version':trial['version'] if phase=='candidate' else 0,
                        'guidance_sha256':trial['guidance_sha256'] if phase=='candidate' else None}
                    ref=artifacts.put(artifact);comparisons[case['id']].update({phase+'_ref':ref,phase+'_passed':passed})
                    if phase=='candidate' and not guards:
                        personal=probe.request('GET','/api/coordination/personal?audience=owner_private')
                        origin=Store(state).read('personal_meta','state')['identity_id']
                        checks={'identity':personal['identity_id']==origin,
                            'privacy':probe.client.get('/api/coordination/growth?audience=shared').status_code==422,
                            'authority':probe.client.post('/api/shana/restart',json={}).status_code==404}
                        for name,ok in checks.items():
                            guard={'kind':'growth_regression_evidence','format':1,'name':name,'passed':ok,
                                'checker_sha256':checker,'source_sha256':source,'model':args.model,'source_ids':dependencies,
                                'details':{'scope':'actual private API identity, audience and unavailable activation controls'}}
                            guards.append({'name':name,'passed':ok,'evidence_ref':artifacts.put(guard)})
                finally:probe.stop()
                publish(root/'comparison-progress.json',comparisons)
        comparison={'kind':'growth_comparison','format':1,'trial_id':trial['id'],'trial_version':trial['version'],
            'guidance_sha256':trial['guidance_sha256'],'frozen_ref':frozen_ref,'model':args.model,'source_sha256':source,
            'checker_sha256':checker,'scope_target':'dialogue','scope_marker':'task','measured':True,'evaluator':'objective_code',
            'started_at':measurement_started,'finished_at':time.time(),'cases':list(comparisons.values()),'regressions':guards}
        comparison_ref=artifacts.put(comparison);start(host)
        assessed=host.growth('evaluate',{'trial_id':trial['id'],'evaluation_ref':comparison_ref})
        report['comparison']={'artifact_ref':comparison_ref,'evaluation':assessed}
        summary=assessed['evaluation']['summary']
        assert any(case.startswith(('yaml','ini')) for case in summary['improved_cases']),summary
        assert not summary['regressed_cases'] and not summary['failed_guards'],summary
        assert summary['all_candidate_cases_passed'],summary
        host.growth('accept',{'trial_id':trial['id'],'version':trial['version'],
            'review_rationale':'All candidate cases used the checked blocker binding, including improved YAML/INI task-source cases. CSV is a familiar regression. Stable identity ID, private-audience rejection and unavailable activation route passed. This is owner-reviewed engineered guidance, not autonomous discovery, trained weights or general character proof.'})
        record('frozen_new_case_improvement_with_source_bound_objective_evidence')
        host.growth('withdraw',{'trial_id':trial['id'],'version':trial['version'],'reason':'Demonstrate reversibility without erasing experiences or comparison evidence.'})
        before=len(host.prompt_flags());_,withdrawn=chat(host,cases[0]['text'],'withdrawal')
        assert not any(f['guidance_in_actual_http_system'] for f in host.prompt_flags()[before:] if f['primary_private_turn'])
        assert any(e['id']==old_exp['id'] for e in host.growth_state()['experiences'])
        record('withdrawal_stops_real_prompt_influence_preserves_experience')
        # Offers vary the actual source activity, with no authored like/preference.
        areas=['geometry','wordplay','birdsong'];choice_history=[]
        documents={
            'geometry':[
                'Compare reflection and rotational symmetry in a checkerboard tile pattern. State the assumptions about its boundary.',
                'A triangle has vertices A(0,0), B(4,0), C(0,3). Explore its lengths and angle relationships using these coordinates.',
                'A rectangular garden is 6 by 8 meters. Compare a route along two edges with a diagonal path; state the movement assumptions.'],
            'wordplay':[
                'Explore two meanings of the sentence: The astronomer married a star.',
                'The old man the boats. Explore how the grammatical role of man changes the initial reading of this garden-path sentence.',
                'Time flies like an arrow; fruit flies like a banana. Compare the different grammatical structures that support the wordplay.'],
            'birdsong':[
                'Fictional symbolic calls A: chirp chirp pause chirp chirp pause. B: chirp trill chirp pause trill. Compare repetition without inferring species or emotion.',
                'Fictional morning log: H H L pause H H L. Evening log: H L pause L H. Compare transition patterns; these are symbols, not real audio.',
                'Fictional calls A: high-short low-long high-short. B: low-short low-short high-long. Compare pitch and duration relationships without claiming acoustic measurement.']}
        for round_number in range(3):
            offered={}
            for area in areas:
                spec={'mode':'research','goal':f'Explore {area} example {round_number+1}: read the selected note and produce a concrete observation, limitation, and possible next question.',
                    'files':{'note.txt':documents[area][round_number]},'editable':[],'checks':[]}
                offered[host.prepare(spec)]=area
            offer=host.growth('opportunities',{'task_ids':list(offered),'budget_seconds':600,'maximum_reviews':3})['offer']
            reconsidered=set()
            def choices():
                value=host.growth_state()
                relevant=[c for c in value['choices'] if c.get('offer_id')==offer['id']]
                if relevant and relevant[0]['decision']=='wait' and relevant[0]['id'] not in reconsidered:
                    reconsidered.add(relevant[0]['id'])
                    if value['exploration'].get('remaining_reviews',0)>0:host.growth('review',{})
                return value
            chosen=until(choices,lambda v:any(c.get('task_id') in offered and c.get('accepted') is True and c.get('decision')=='start_work' for c in v['choices']),180)
            choice=next(c for c in chosen['choices'] if c.get('task_id') in offered and c.get('accepted') is True and c.get('decision')=='start_work')
            result=until(lambda:host.work(choice['task_id']),lambda v:bool(v.get('run')) and v['run']['status'] in {'completed','blocked','needs_checker'},180)
            assert result['run']['status']=='completed',result
            choice_history.append({'area':offered[choice['task_id']],'choice':choice,'artifact_ref':result['checkpoint']['research_ref'],
                'alternatives':offered,'source_sha256':result['checkpoint']['source_sha256']})
            if round_number<2:chat(host,'I am focusing on something else now. Keep any authorized exploration quiet; you can choose to wait.','later-context-'+str(round_number))
        report['exploration']=choice_history
        assert len({c['area'] for c in choice_history})<len(choice_history),'No later area revisit observed; retain all choices.'
        revisited=next(area for area in areas if sum(c['area']==area for c in choice_history)>1)
        relevant_ids={c['choice']['task_id'] for c in choice_history if c['area']==revisited}
        relevant_experiences=[e['id'] for e in host.growth_state()['experiences'] if any(aid in e['observed_outcome'] for aid in relevant_ids)]
        interest=host.growth('hypothesis',{'statement':'The recorded choices returned to '+revisited+' across different activities. This may be an interest worth further observation.',
            'kind':'interest','scope_target':'dialogue','scope_marker':revisited,
            'alternatives':['Task difficulty, existing ability or the current context may explain the choices; recurrence alone is not proof of a feeling.'],
            'expected_outcome':'Later different opportunities can support or contradict the tentative interpretation.',
            'experience_ids':relevant_experiences[:8]})['hypothesis']
        report['interest_hypothesis']=interest
        assert interest['status']=='pending_review'
        record('actual_authorized_exploration_and_later_unforced_area_revisit')
        browser=subprocess.run(['node',str(project/'scripts/growth_browser_probe.cjs')],input=json.dumps({
            'url':host.ready['dashboard'],'username':'isolated-owner','password':host.password,
            'trial_id':trial['id'],'hypothesis_id':wrong['id'],'screenshot':str(root/'growth-private-hidden.png')}),
            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        assert browser.returncode==0,browser.stderr[-1500:]
        report['browser']=json.loads(browser.stdout);assert report['browser']['ok']
        record('actual_growth_browser_history_and_private_dom_clearing')
        host.stop();snapshot=root/'final-snapshot';restored=root/'final-restored'
        meta=export_snapshot(state,snapshot);restore_snapshot(snapshot,restored)
        report['snapshot']={'records':meta['record_count'],'files':len(meta['files'])}
        record('current_growth_experiences_trials_and_counterevidence_restore')
        assert source_files(project)==runtime_files and checker_files(project)==proof_files,'source changed during proof'
        assert model_identity()==model,'local model tag changed during proof'
        record('runtime_checker_and_model_identity_unchanged_during_measurement')
        report['complete']=True
    except BaseException as exc:
        report.update(error_type=type(exc).__name__,error=str(exc)[:4000]);raise
    finally:
        host.stop();report['elapsed_seconds']=round(time.time()-started,3)
        report['temporary_processes_absent']=all(not Path('/proc',str(p['pid'])).exists() for p in report['processes'])
        publish(root/'report.json',report);print(json.dumps({'report':str(root/'report.json'),'complete':report.get('complete',False)}),flush=True)


if __name__=='__main__':main()
