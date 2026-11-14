"""Sourced candidate growth shares the single coordinator's accepted-state owner."""
from copy import deepcopy
import hashlib
import json
import time
import uuid

from ..coding.practice_artifacts import ArtifactStore
from .personal import digest, identifier
from .growth_models import fields, text, strings, hash_ref, hypothesis_values, validate_scope, validate_comparison, frozen_cases
from .activity_observations import project_activity_observations


class GrowthState:
    def __init__(self,store,token,personal,clock=time.time):
        self.store,self.token,self.personal,self.clock=store,token,personal,clock
        self.artifacts=ArtifactStore(store.root/'artifacts')
        with store.transaction(token) as db:
            if not store.get(db,'growth_meta','state'):
                self._put(db,'growth_meta','state',{'schema_version':1,'created_at':clock(),
                    'scope':'isolated_candidate_only','weights_changed':False})

    def _put(self,db,kind,key,value):
        self.store.require_owner(db,self.token);self.store.put(db,kind,key,value)

    def _list(self,db,kind):return self.store.list(db,kind,None)

    def _get(self,db,kind,key):
        value=self.store.get(db,kind,key)
        if value is None:raise ValueError('unknown_growth_record')
        return value

    def _sources(self,db,source_ids,maximum=16):
        strings(source_ids,maximum,40,nonempty=True)
        roots=set(source_ids);values={};pending=list(source_ids)
        while pending:
            sid=pending.pop()
            if sid in values:continue
            source=self.store.get(db,'personal_source',sid)
            if source is None or source.get('scope')!='owner_private':
                raise ValueError('growth_source_unavailable')
            lived=(source.get('status')=='active' and source.get('source_kind') in
                ('private_conversation','verified_worker_observation'))
            authored=(sid not in roots and source.get('status')=='administrative'
                and source.get('source_kind')=='administrative_edit')
            if not lived and not authored:raise ValueError('growth_source_unavailable')
            values[sid]=source
            if len(values)>20000:raise ValueError('growth_source_graph_too_large')
            dependency_record=source.get('dependencies',{})
            if not isinstance(dependency_record,dict):raise ValueError('growth_source_unavailable')
            dependencies=dependency_record.get('sources',[])
            if not isinstance(dependencies,list) or len(dependencies)>20000:
                raise ValueError('growth_source_unavailable')
            for dependency in dependencies:text(dependency,40)
            pending.extend(set(dependencies))
        return [values[sid] for sid in source_ids]

    def _experience(self,db,values,*,origin):
        fields(values,{'source_ids','situation','options','action','expected_outcome','observed_outcome','uncertainty'})
        sources=self._sources(db,values['source_ids'])
        for key in ('situation','action','observed_outcome'):text(values[key],600)
        text(values['expected_outcome'],600,empty=True);text(values['uncertainty'],300)
        strings(values['options'],8,300)
        eid=identifier('exp',values)
        previous=self.store.get(db,'growth_experience',eid)
        if previous:return previous
        if len(self._list(db,'growth_experience'))>=1000:raise ValueError('growth_experience_capacity_reached')
        record={'id':eid,**deepcopy(values),'created_at':self.clock(),'status':'active','origin':origin,
            'source_kinds':list(dict.fromkeys(s['source_kind'] for s in sources)),
            'source_hashes':{s['id']:s.get('text_sha256') for s in sources},'content_sha256':digest(values),
            'interpretation_is_not_observed_fact':True}
        self._put(db,'growth_experience',eid,record);return record

    def _experiences(self,db,ids):
        strings(ids,8,40,nonempty=True)
        result=[]
        for eid in ids:
            exp=self._get(db,'growth_experience',eid)
            if exp['status']!='active':raise ValueError('growth_experience_unavailable')
            self._sources(db,exp['source_ids']);result.append(exp)
        return result

    def _hypothesis(self,db,values,*,origin,revises=None):
        values=hypothesis_values(values)
        experiences=self._experiences(db,values['experience_ids'])
        if len(self._list(db,'growth_hypothesis'))>=300:raise ValueError('growth_hypothesis_capacity_reached')
        hid=identifier('hyp',values,origin,revises)
        previous=self.store.get(db,'growth_hypothesis',hid)
        if previous:return previous
        record={'id':hid,**values,'source_ids':list(dict.fromkeys(s for e in experiences for s in e['source_ids'])),
            'status':'pending_review','created_at':self.clock(),'origin':origin,
            'revision':(self._get(db,'growth_hypothesis',revises)['revision']+1) if revises else 1,
            'revises_id':revises,'contradicting_experience_ids':[],'counter_notes':[],
            'is_tentative':True,'automatic_personality_change':False}
        self._put(db,'growth_hypothesis',hid,record);return record

    def _suspend(self,db,hypothesis_id,reason):
        for trial in self._list(db,'growth_trial'):
            if trial['hypothesis_id']==hypothesis_id and trial['status'] in {'active','accepted'}:
                trial.update(status='needs_review',suspension_reason=reason,suspended_at=self.clock())
                self._put(db,'growth_trial',trial['id'],trial)

    def _artifact_graph(self,ref):
        pending=[ref];seen=set()
        while pending:
            current=pending.pop();hash_ref(current)
            if current in seen:continue
            seen.add(current)
            try:value=self.artifacts.get(current)
            except (ValueError,OSError):continue
            if value.get('kind')=='growth_comparison':
                pending.append(hash_ref(value['frozen_ref']))
                for case in value['cases']:
                    pending.extend([hash_ref(case['baseline_ref']),hash_ref(case['candidate_ref'])])
                pending.extend(hash_ref(guard['evidence_ref']) for guard in value['regressions'])
            if len(seen)>400:raise ValueError('growth_artifact_graph_too_large')
        return seen

    def _erase_evaluations(self,db,affected_trials,extra_refs=()):
        evaluations=self._list(db,'growth_evaluation');removed=set(extra_refs)
        for trial in self._list(db,'growth_trial'):
            if trial['id'] in affected_trials and trial.get('frozen_ref'):removed.add(trial['frozen_ref'])
        for evaluation in evaluations:
            if evaluation['trial_id'] in affected_trials:
                removed.update(evaluation.get('artifact_refs',[]))
                if evaluation.get('evaluation_ref'):removed.update(self._artifact_graph(evaluation['evaluation_ref']))
        changed=True
        while changed:
            changed=False
            for evaluation in evaluations:
                refs=set(evaluation.get('artifact_refs',[]))
                if evaluation.get('evaluation_ref'):refs.update(self._artifact_graph(evaluation['evaluation_ref']))
                if refs&removed and not refs<=removed:
                    removed.update(refs);changed=True
                if refs&removed:affected_trials.add(evaluation['trial_id'])
        for ref in removed:
            hash_ref(ref);target=self.artifacts._target_path(ref)
            if target.is_symlink():raise ValueError('growth_artifact_storage_changed')
            target.unlink(missing_ok=True)
            self._put(db,'growth_evidence_tombstone',ref,{'id':ref,'removed_sha256':ref,
                'reason':'dependent_personal_source_removed','removed_at':self.clock()})
        for evaluation in evaluations:
            if evaluation['trial_id'] in affected_trials:
                evaluation.update(status='source_removed',summary={},redacted_at=self.clock())
                for key in ('evaluation_ref','frozen_ref','artifact_refs'):evaluation.pop(key,None)
                self._put(db,'growth_evaluation',evaluation['id'],evaluation)
        for trial in self._list(db,'growth_trial'):
            if trial['id'] in affected_trials:
                trial.update(status='source_removed',guidance='',review_condition='',review_rationale='',
                    scope_marker='',history=[],suspension_reason='source_removed',redacted_at=self.clock())
                for key in ('evaluation_ref','frozen_ref','acceptance_review'):trial.pop(key,None)
                self._put(db,'growth_trial',trial['id'],trial)

    def _orphan_erasure_refs(self,db):
        sources=self._list(db,'personal_source')
        active={s['id'] for s in sources if s.get('status')=='active' and s.get('scope')=='owner_private'
                and s.get('source_kind') in ('private_conversation','verified_worker_observation')}
        for sid in list(active):
            try:self._sources(db,[sid])
            except ValueError:active.remove(sid)
        erased=digest(sorted((s['id'],s.get('status')) for s in sources if s.get('status') in {'forgotten','deleted','pruned'}))
        inventory=digest(sorted(p.name for p in self.artifacts.root.glob('*.json')))
        meta=self._get(db,'growth_meta','state')
        previous=set(meta.get('known_active_source_ids',[]))
        should_scan=(not meta.get('orphan_artifacts_checked') or previous-active or meta.get('erased_sources_sha256')!=erased
                     or meta.get('artifact_inventory_sha256')!=inventory)
        removed=set()
        if should_scan:
            kinds={'growth_frozen_cases','growth_case_response','growth_regression_evidence','growth_comparison'}
            for path in self.artifacts.root.glob('*.json'):
                if path.is_symlink():raise ValueError('growth_artifact_storage_changed')
                try:value=self.artifacts.get(path.stem)
                except (ValueError,OSError):continue
                if value.get('kind') not in kinds:continue
                dependencies=value.get('source_ids')
                if value.get('kind')=='growth_comparison':
                    try:dependencies=self.artifacts.get(value['frozen_ref']).get('source_ids')
                    except (ValueError,OSError,KeyError):dependencies=None
                if not isinstance(dependencies,list) or not dependencies or any(not isinstance(sid,str) or sid not in active for sid in dependencies):
                    removed.update(self._artifact_graph(path.stem))
        meta.update(known_active_source_ids=sorted(active),erased_sources_sha256=erased,orphan_artifacts_checked=True,
                    artifact_inventory_sha256=inventory)
        self._put(db,'growth_meta','state',meta)
        return removed

    def _redact_control_receipts(self,db):
        """A historical idempotency response cannot retain erased derivatives."""
        kinds={'exp':'growth_experience','hyp':'growth_hypothesis','trial':'growth_trial','eval':'growth_evaluation'}
        def affected(value):
            if isinstance(value,dict):
                key=value.get('id')
                if isinstance(key,str) and key.partition('-')[0] in kinds:
                    canonical=self.store.get(db,kinds[key.partition('-')[0]],key)
                    if canonical is None or canonical.get('status')=='source_removed':return True
                for field,item in value.items():
                    if field in {'source_ids','evaluation_source_ids'} and isinstance(item,list) and item:
                        try:self._sources(db,item,maximum=256)
                        except ValueError:return True
                    if field in {'frozen_ref','evaluation_ref','baseline_ref','candidate_ref','evidence_ref'} and isinstance(item,str):
                        if self.store.get(db,'growth_evidence_tombstone',item):return True
                    if affected(item):return True
            elif isinstance(value,list):
                return any(affected(item) for item in value)
            return False
        for row in db.execute("SELECT id,body FROM records WHERE kind='growth_control'").fetchall():
            key,raw=row;receipt=json.loads(raw)
            if affected(receipt.get('result')):
                receipt['result']={'ok':True,'redacted':True,'reason':'growth_source_removed',
                    'effective_guidance':False}
                receipt['result_redacted_at']=self.clock()
                self._put(db,'growth_control',key,receipt)

    def sync(self,db):
        """Expiry and missing source content immediately remove effective guidance."""
        self.store.require_owner(db,self.token)
        removed=set();affected_trials=set()
        for exp in self._list(db,'growth_experience'):
            if exp['status']!='active':continue
            try:self._sources(db,exp['source_ids'])
            except ValueError:
                for sid in exp['source_ids']:
                    source=self.store.get(db,'personal_source',sid)
                    if source and source.get('status')=='active':
                        try:self._sources(db,[sid])
                        except ValueError:self.personal.forget(db,sid)
                exp.update(status='source_removed',situation='',options=[],action='',expected_outcome='',
                    observed_outcome='',uncertainty='source_unavailable',redacted_at=self.clock())
                for key in ('source_observations','result_ref','checkpoint_ref','source_ref'):
                    exp.pop(key,None)
                self._put(db,'growth_experience',exp['id'],exp);removed.add(exp['id'])
        for hypothesis in self._list(db,'growth_hypothesis'):
            live=True
            try:self._experiences(db,list(dict.fromkeys(hypothesis['experience_ids']+hypothesis['contradicting_experience_ids'])))
            except ValueError:live=False
            if not live and hypothesis['status']!='source_removed':
                hypothesis.update(status='source_removed',statement='',scope_marker='',alternatives=[],expected_outcome='',
                    counter_notes=[],redacted_at=self.clock())
                self._put(db,'growth_hypothesis',hypothesis['id'],hypothesis)
                for trial in self._list(db,'growth_trial'):
                    if trial['hypothesis_id']==hypothesis['id']:
                        affected_trials.add(trial['id'])
            elif hypothesis['status']=='needs_review':self._suspend(db,hypothesis['id'],'counter_evidence')
        for trial in self._list(db,'growth_trial'):
            if trial.get('evaluation_source_ids') and trial['status']!='source_removed':
                try:self._sources(db,trial['evaluation_source_ids'],maximum=256)
                except ValueError:affected_trials.add(trial['id'])
            if trial['status'] in {'active','accepted'} and self.clock()>=trial['expires_at']:
                trial.update(status='expired',expired_at=self.clock())
                self._put(db,'growth_trial',trial['id'],trial)
        for evaluation in self._list(db,'growth_evaluation'):
            if evaluation.get('status')!='source_removed' and evaluation.get('source_ids'):
                try:self._sources(db,evaluation['source_ids'],maximum=256)
                except ValueError:affected_trials.add(evaluation['trial_id'])
        orphan_refs=self._orphan_erasure_refs(db)
        if orphan_refs:
            for evaluation in self._list(db,'growth_evaluation'):
                if set(evaluation.get('artifact_refs',[]))&orphan_refs:affected_trials.add(evaluation['trial_id'])
            for trial in self._list(db,'growth_trial'):
                if trial.get('frozen_ref') in orphan_refs:affected_trials.add(trial['id'])
        if affected_trials or orphan_refs:self._erase_evaluations(db,affected_trials,orphan_refs)
        for choice in self._list(db,'growth_choice'):
            if not choice.get('source_ids') or choice.get('context_source_removed'):continue
            try:self._sources(db,choice['source_ids'],maximum=256)
            except ValueError:
                choice.update(task_goal='',reason_code='',context_source_removed=True,redacted_at=self.clock())
                self._put(db,'growth_choice',choice['id'],choice)
        self._redact_control_receipts(db)
        return {'removed_experience_ids':sorted(removed)}

    def active_refs(self,db):
        self.sync(db)
        return sorted(t['id']+':'+str(t['version']) for t in self._list(db,'growth_trial') if t['status'] in {'active','accepted'})

    def _activity_observed_at(self,db,activity):
        sid=identifier('src','activity',activity['id'],activity['observed_revision'])
        source=self.store.get(db,'personal_source',sid) or {}
        value=source.get('occurred_at',source.get('ingested_at',0))
        return value if type(value) in (int,float) else 0

    def context(self,db,target='dialogue',scope_text=''):
        # A current reader can observe already completed own projects without
        # claiming that their raw outcomes reached an older model prompt.
        current=(self.store.get(db,'brain','state') or {}).get('current_activity_id')
        completed=[a for a in self._list(db,'activity')
            if a.get('origin')=='self_exploration' and a.get('status')=='completed']
        completed.sort(key=lambda a:(a['id']!=current,-self._activity_observed_at(db,a),-a.get('created_at',0),a['id']))
        for activity in completed[:6]:self.observe_activity(db,activity)
        self.sync(db)
        if not isinstance(target,str) or target not in {'dialogue','work'} or not isinstance(scope_text,str):raise ValueError('invalid_growth_context_scope')
        trials=[t for t in self._list(db,'growth_trial') if t['status'] in {'active','accepted'}]
        guidance=[]
        if target=='dialogue':
            for trial in trials:
                if trial['scope_target']=='dialogue' and trial['scope_marker'].casefold() in scope_text.casefold():
                    hypothesis=self._get(db,'growth_hypothesis',trial['hypothesis_id'])
                    guidance.append({'trial_id':trial['id'],'version':trial['version'],'scope_marker':trial['scope_marker'],
                        'text':trial['guidance'],'kind':hypothesis['kind'],'expires_at':trial['expires_at'],
                        'source_ids':list(dict.fromkeys(hypothesis['source_ids']+trial.get('evaluation_source_ids',[]))),
                        'candidate_only':True,'authority_unchanged':True})
        experiences=[e for e in self._list(db,'growth_experience') if e['status']=='active']
        own=sorted((e for e in experiences if e.get('source_observations')),
            key=lambda e:(e.get('activity_id')!=current,-e.get('observed_at',0),-e.get('activity_created_at',0),e['id']))[:2]
        recent=[e for e in experiences if e not in own and not e.get('source_observations')]
        return {'guidance':guidance[:4],'active_trial_refs':sorted(t['id']+':'+str(t['version']) for t in trials),
            'hypotheses':[{'id':h['id'],'statement':h['statement'][:300],'kind':h['kind'],'status':h['status'],
                'source_ids':h['source_ids'],'tentative':True} for h in self._list(db,'growth_hypothesis')
                if h['status'] in {'pending_review','needs_review','in_trial','accepted'}][:6],
            'experiences':[{'id':e['id'],'situation':e['situation'][:160],'observed_outcome':e['observed_outcome'][:240],
                'uncertainty':e['uncertainty'],'source_ids':e['source_ids'],
                **({'source_observations':deepcopy(e['source_observations']),'activity_id':e.get('activity_id'),
                    'observed_at':e.get('observed_at',0)} if e.get('source_observations') else {})}
                for e in (own+recent)[:6]],
            'choices':[{k:(c[k][:160] if k=='task_goal' else [] if k=='source_ids' and c.get('context_source_removed') else c[k])
                       for k in ('id','opportunity_id','task_id','decision','observed_at','accepted','task_goal','source_ids','context_source_removed') if k in c}
                       for c in self._list(db,'growth_choice')[:6]],
            'weights_changed':False,'long_term_character_development_verified':False}

    def accept_turn(self,db,turn,response,proposals=()):
        sid=identifier('src','turn',turn['id'])
        source=self._get(db,'personal_source',sid)
        if source['status']!='active':return {'accepted':False,'reason':'growth_source_removed','hypotheses':[]}
        try:exp=self._experience(db,{'source_ids':[sid],'situation':turn['text'][:600] or 'Accepted private exchange',
            'options':[],'action':'private_dialogue','expected_outcome':'',
            'observed_outcome':(response.get('display_text') or 'No public text was delivered.')[:600],
            'uncertainty':'The user input is a report; the response is an observed output, not independent confirmation.'},origin='accepted_private_turn')
        except ValueError:return {'accepted':False,'reason':'growth_collection_unavailable','hypotheses':[]}
        receipts=[]
        for proposal in list(proposals)[:3]:
            try:
                values=proposal.model_dump() if hasattr(proposal,'model_dump') else dict(proposal)
                values=hypothesis_values(values,model=True);values['experience_ids']=[exp['id']]
                hypothesis=self._hypothesis(db,values,origin='model_hypothesis')
                receipts.append({'accepted':True,'hypothesis_id':hypothesis['id'],'status':'pending_review'})
            except (ValueError,TypeError):receipts.append({'accepted':False,'reason':'invalid_growth_hypothesis'})
        return {'experience_id':exp['id'],'hypotheses':receipts}

    def _prepared_activity_source(self,db,activity):
        """Stable admitted-work evidence, separate from any lived conversation."""
        sid=identifier('src','activity',activity['id'],-1)
        source=self.store.get(db,'personal_source',sid)
        if source is None:
            prepared={**activity,'observed_revision':-1,'status':'ready','reason':'prepared_scope_only',
                'checkpoint_ref':activity['initial_checkpoint_ref'],'observed_after_sleep':False,'task_results':[]}
            self.personal.observe_activity(db,prepared)
            source=self.store.get(db,'personal_source',sid)
            source['origin_kind']='admitted_work_source'
            source['text']=json.dumps({'id':activity['id'],'observation':'Admitted selected work files; execution is not implied.'},sort_keys=True)
            source['text_sha256']=digest(source['text'])
            self.personal._put(db,'personal_source',sid,source)
        return source

    def activity_observations(self,db,activity):
        """Expose checked work text only through active canonical source closure."""
        self.store.require_owner(db,self.token)
        try:observations=project_activity_observations(self.store,activity)
        except (ValueError,OSError,KeyError,TypeError):return None
        prepared=self._prepared_activity_source(db,activity)
        dependencies=[prepared['id']]
        sid=identifier('src','activity',activity['id'],activity['observed_revision'])
        source=self.store.get(db,'personal_source',sid)
        observed_at=self._activity_observed_at(db,activity) if source else self.clock()
        for choice in self._list(db,'growth_choice'):
            if (choice.get('accepted') is True and choice.get('task_id')==activity['id']
                and choice.get('observed_at',0)<=observed_at):
                dependencies.extend(choice.get('source_ids',[]))
        dependencies=list(dict.fromkeys(s for s in dependencies if s!=sid))
        if activity['observed_revision']>=0:
            self.personal.observe_activity(db,activity)
            source=self.store.get(db,'personal_source',sid)
            prior=source.get('dependencies',{}).get('sources',[])
            dependencies=list(dict.fromkeys(s for s in [*dependencies,*prior] if s!=sid))
            if source['status']=='active':
                source.setdefault('dependencies',{}).setdefault('sources',[])
                source['dependencies']['sources']=dependencies
                self.personal._put(db,'personal_source',sid,source)
        else:source=prepared
        try:
            self._sources(db,[sid])
        except ValueError:
            if source['status']=='active':self.personal.forget(db,sid)
            return None
        return {**observations,'source_ids':[sid],
            'observed_at':self._activity_observed_at(db,activity)}

    def observe_activity(self,db,activity):
        if activity.get('observed_revision',-1)<0:return None
        observations=None
        if activity.get('kind')=='practical' and activity.get('origin')=='self_exploration':
            observations=self.activity_observations(db,activity)
            if observations is None:return None
        sid=identifier('src','activity',activity['id'],activity['observed_revision'])
        source=self.store.get(db,'personal_source',sid)
        if not source or source['status']!='active':return None
        completed=observations if observations and activity.get('status')=='completed' else None
        values={'source_ids':completed['source_ids'] if completed else [sid],'situation':str(activity.get('goal') or 'Authorized worker activity')[:600],
            'options':[],'action':'observe_authorized_worker', 'expected_outcome':'The admitted activity goal, within its existing scope.',
            'observed_outcome':json.dumps({k:activity.get(k) for k in ('id','status','reason','observed_after_sleep')},sort_keys=True)[:600],
            'uncertainty':'Only the verified worker receipt is observed; no first-person experience or general skill is inferred.'}
        try:
            migrated=False
            previous=next((e for e in self._list(db,'growth_experience')
                if completed and e.get('status')=='active' and e.get('activity_id')==activity['id']
                and e.get('result_ref')==completed['result_ref'] and sid in e.get('source_ids',[])),None)
            if previous:
                self._sources(db,[sid]);experience=previous
                if experience['source_ids']!=[sid]:
                    migrated=True
                    experience['previous_provenance_sha256']=digest(experience['source_ids'])
                    experience.update(source_ids=[sid],source_kinds=[source['source_kind']],
                        source_hashes={sid:source.get('text_sha256')},content_sha256=digest(values))
            else:experience=self._experience(db,values,origin='verified_worker_observation')
            if completed:
                updates=dict(source_observations=completed,activity_id=activity['id'],
                    observed_at=completed['observed_at'],activity_created_at=activity['created_at'],
                    result_ref=completed['result_ref'],checkpoint_ref=completed['checkpoint_ref'],
                    source_ref=completed['source_ref'],observation_sha256=digest(completed))
                if migrated or any(experience.get(key)!=value for key,value in updates.items()):
                    experience.update(updates)
                    self._put(db,'growth_experience',experience['id'],experience)
            return experience
        except ValueError:return None

    def state(self,db):
        self.sync(db)
        return {'experiences':self._list(db,'growth_experience'),'hypotheses':self._list(db,'growth_hypothesis'),
            'trials':self._list(db,'growth_trial'),'evaluations':self._list(db,'growth_evaluation'),
            'choices':self._list(db,'growth_choice'),'active_trial_refs':self.active_refs(db),
            'scope':'isolated_owner_private','weights_changed':False,'production_activated':False}

    def control(self,db,action,values):
        self.store.require_owner(db,self.token);self.sync(db)
        if not isinstance(action,str) or not isinstance(values,dict):raise ValueError('invalid_growth_values')
        if action=='experience':return {'experience':self._experience(db,values,origin='owner_sourced_review')}
        if action=='hypothesis':return {'hypothesis':self._hypothesis(db,values,origin='owner_interpretation')}
        if action=='revise':
            fields(values,{'hypothesis_id','statement','alternatives','expected_outcome','experience_ids'}, {'scope_target','scope_marker'})
            previous=self._get(db,'growth_hypothesis',values['hypothesis_id'])
            updated={k:previous[k] for k in ('statement','kind','scope_target','scope_marker','alternatives','expected_outcome','experience_ids')}
            updated.update({k:v for k,v in values.items() if k!='hypothesis_id'})
            revised=self._hypothesis(db,updated,origin='owner_revision',revises=previous['id'])
            previous.update(status='revised',revised_by=revised['id'],revised_at=self.clock())
            self._put(db,'growth_hypothesis',previous['id'],previous);self._suspend(db,previous['id'],'hypothesis_revised')
            return {'hypothesis':revised,'previous_hypothesis_id':previous['id']}
        if action=='contradict':
            fields(values,{'hypothesis_id','experience_id','note'});text(values['note'],500)
            hypothesis=self._get(db,'growth_hypothesis',values['hypothesis_id'])
            self._experiences(db,[values['experience_id']])
            if hypothesis['status']=='source_removed':raise ValueError('growth_hypothesis_unavailable')
            if values['experience_id'] not in hypothesis['contradicting_experience_ids']:
                if len(hypothesis['contradicting_experience_ids'])>=8:raise ValueError('growth_counterexample_capacity_reached')
                hypothesis['contradicting_experience_ids'].append(values['experience_id'])
                hypothesis['counter_notes'].append({'experience_id':values['experience_id'],'note':values['note'],'at':self.clock()})
            hypothesis.update(status='needs_review',review_reason='counter_evidence')
            self._put(db,'growth_hypothesis',hypothesis['id'],hypothesis);self._suspend(db,hypothesis['id'],'counter_evidence')
            return {'hypothesis':hypothesis}
        if action=='trial':
            fields(values,{'hypothesis_id','guidance','scope_target','scope_marker','expires_seconds','review_condition','review_rationale','frozen_ref'})
            scope=validate_scope({'target':values['scope_target'],'marker':values['scope_marker'],'guidance':values['guidance'],
                'expires_seconds':values['expires_seconds'],'review_condition':values['review_condition'],'review_rationale':values['review_rationale']})
            hypothesis=self._get(db,'growth_hypothesis',values['hypothesis_id'])
            self._experiences(db,list(dict.fromkeys(hypothesis['experience_ids']+hypothesis['contradicting_experience_ids'])))
            if hypothesis['status'] not in {'pending_review','in_trial','accepted'}:raise ValueError('growth_hypothesis_needs_revision')
            if hypothesis['scope_target']!=scope['target'] or hypothesis['scope_marker']!=scope['marker']:
                raise ValueError('growth_trial_scope_mismatch')
            frozen_ref=hash_ref(values['frozen_ref']);frozen=frozen_cases(self.artifacts.get(frozen_ref))
            self._sources(db,frozen['source_ids'],maximum=256)
            if (frozen['scope_target']!=scope['target'] or frozen['scope_marker']!=scope['marker']
                or frozen['frozen_at']>self.clock()):raise ValueError('growth_trial_frozen_scope_mismatch')
            active=[t for t in self._list(db,'growth_trial') if t['status'] in {'active','accepted'}]
            if len(active)>=4 or any(t['scope_target']==scope['target'] and t['scope_marker'].casefold()==scope['marker'].casefold() for t in active):
                raise ValueError('growth_conflicting_active_scope')
            versions=[t['version'] for t in self._list(db,'growth_trial') if t['hypothesis_id']==hypothesis['id']]
            trial={'id':identifier('trial',hypothesis['id'],uuid.uuid4().hex),'hypothesis_id':hypothesis['id'],
                'version':max(versions,default=0)+1,'scope_target':scope['target'],'scope_marker':scope['marker'],
                'guidance':scope['guidance'],'guidance_sha256':hashlib.sha256(scope['guidance'].encode()).hexdigest(),
                'created_at':self.clock(),'expires_at':self.clock()+scope['expires_seconds'],'status':'active',
                'review_condition':scope['review_condition'],'review_rationale':scope['review_rationale'],
                'authority':'explicit_owner_candidate_trial','history':[],'weights_changed':False,'production_activated':False,
                'frozen_ref':frozen_ref}
            self._put(db,'growth_trial',trial['id'],trial)
            hypothesis['status']='in_trial';self._put(db,'growth_hypothesis',hypothesis['id'],hypothesis)
            return {'trial':trial}
        if action=='evaluate':
            fields(values,{'trial_id','evaluation_ref'});hash_ref(values['evaluation_ref'])
            trial=self._get(db,'growth_trial',values['trial_id'])
            if trial['status'] not in {'active','accepted'}:raise ValueError('growth_trial_not_active')
            comparison=self.artifacts.get(values['evaluation_ref'])
            frozen=self.artifacts.get(hash_ref(comparison.get('frozen_ref')))
            summary=validate_comparison(comparison,frozen,trial,self.artifacts)
            if comparison['finished_at']>self.clock():raise ValueError('growth_measurement_is_in_the_future')
            dependencies=list(frozen['source_ids'])
            for case in comparison['cases']:
                for phase in ('baseline','candidate'):
                    dependencies.extend(self.artifacts.get(case[phase+'_ref'])['source_ids'])
            for guard in comparison['regressions']:dependencies.extend(self.artifacts.get(guard['evidence_ref'])['source_ids'])
            dependencies=list(dict.fromkeys(dependencies));self._sources(db,dependencies,maximum=256)
            eid=identifier('eval',trial['id'],values['evaluation_ref'])
            evaluation={'id':eid,'trial_id':trial['id'],'trial_version':trial['version'],'evaluation_ref':values['evaluation_ref'],
                'frozen_ref':comparison['frozen_ref'],'summary':summary,'created_at':self.clock(),'status':'measured',
                'evidence_provenance':'trusted_owner_measurement','is_not_automatic_truth_or_promotion':True,
                'source_ids':dependencies,'artifact_refs':sorted(self._artifact_graph(values['evaluation_ref']))}
            self._put(db,'growth_evaluation',eid,evaluation)
            trial.update(evaluation_id=eid,evaluation_ref=values['evaluation_ref'],frozen_ref=comparison['frozen_ref'],evaluation_source_ids=dependencies)
            if summary['regressed_cases'] or summary['failed_guards'] or not summary['all_candidate_cases_passed']:
                trial.update(status='needs_review',suspension_reason='measured_regression',suspended_at=self.clock())
            self._put(db,'growth_trial',trial['id'],trial)
            return {'evaluation':evaluation,'trial':trial}
        if action in {'accept','withdraw'}:
            fields(values,{'trial_id','version','review_rationale'} if action=='accept' else {'trial_id','version','reason'})
            trial=self._get(db,'growth_trial',values['trial_id'])
            if type(values['version']) is not int or values['version']!=trial['version']:raise ValueError('growth_trial_version_mismatch')
            reason=values['review_rationale'] if action=='accept' else values['reason'];text(reason,500)
            if trial['status']=='source_removed':raise ValueError('growth_trial_source_removed')
            if action=='withdraw':
                trial.update(status='withdrawn',withdrawn_at=self.clock())
                trial['history'].append({'action':'owner_withdrawal','reason':reason,'at':self.clock(),'version':trial['version']})
            else:
                if trial['status']!='active' or not trial.get('evaluation_id'):raise ValueError('growth_accept_requires_current_measurement')
                evaluation=self._get(db,'growth_evaluation',trial['evaluation_id'])
                comparison=self.artifacts.get(evaluation['evaluation_ref'])
                summary=validate_comparison(comparison,self.artifacts.get(comparison['frozen_ref']),trial,self.artifacts)
                if not summary['improved_cases'] or summary['regressed_cases'] or summary['failed_guards'] or not summary['all_candidate_cases_passed']:
                    raise ValueError('growth_comparison_does_not_demonstrate_scoped_improvement')
                trial.update(status='accepted',accepted_at=self.clock(),acceptance_review={'rationale':reason,
                    'actor':'authenticated_owner','evidence_provenance':'trusted_owner_measurement','scope_limited':True})
                hypothesis=self._get(db,'growth_hypothesis',trial['hypothesis_id']);hypothesis['status']='accepted'
                self._put(db,'growth_hypothesis',hypothesis['id'],hypothesis)
            self._put(db,'growth_trial',trial['id'],trial);return {'trial':trial}
        raise ValueError('unsupported_growth_action')
