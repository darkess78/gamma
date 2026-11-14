"""Pure compaction of optional private prompt context before provider calls."""
from copy import deepcopy


class PrivateContextBudgetError(ValueError):
    """Required identity, authority and input cannot fit the private model budget."""


def _excerpt(value, limit):
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:max(0, limit - 3)] + '...'


def _compact_source_observations(candidate, stage, omissions):
    """Trim optional previews while keeping retained text bound to its provenance."""
    owners=[(task,False) for task in (candidate.get('exploration') or {}).get('tasks',[])]
    owners.extend((exp,True) for exp in candidate.get('growth',{}).get('experiences',[]))
    for owner,outcome in owners:
        preview=owner.get('source_observations')
        if not isinstance(preview,dict) or not isinstance(preview.get('files'),list):continue
        files=preview['files']
        before=sum(len(f.get('excerpt','')) for f in files)
        drop=stage>=5 or stage==3 and not outcome
        if drop:kept=[]
        elif outcome:
            # A product's quoted span cannot stand in for the original input.
            # Keep both bounded views before optional checker/extra-file text.
            relevant=[file for file in files if file.get('role')!='protected_check']
            priority=[]
            for role in ('generated_work_product','provided_input'):
                file=next((item for item in relevant if item.get('role')==role),None)
                if file is not None:priority.append(file)
            kept=(priority+[file for file in relevant if file not in priority])[:2]
        else:kept=files[:1 if stage==2 else 2]
        for file in kept:
            # Completed source views are already bounded by their producer.
            # Retain the result and original input before optional context.
            if not outcome:
                old=file.get('excerpt','');file['excerpt']=_excerpt(old,120 if stage==1 else 80)
                if file['excerpt']!=old:file['excerpt_is_partial']=True
        removed_chars=before-sum(len(f.get('excerpt','')) for f in kept)
        removed_files=len(files)-len(kept)
        if removed_chars:omissions['source_observation_chars']=omissions.get('source_observation_chars',0)+removed_chars
        if removed_files:omissions['source_observation_files']=omissions.get('source_observation_files',0)+removed_files
        if drop:owner.pop('source_observations')
        else:
            preview['files']=kept
            if removed_files:preview['files_omitted']=preview.get('files_omitted',0)+removed_files


def _compact_shared_activity(candidate, stage, omissions):
    shared = candidate.get('shared_activity')
    if not isinstance(shared, dict):
        return
    if stage >= 3:
        if shared.get('observation') or shared.get('shared_goal'):
            omissions['shared_activity'] = omissions.get('shared_activity', 0) + 1
        candidate.pop('shared_activity', None)
        return
    limit = 160 if stage == 1 else 80
    old_goal = shared.get('shared_goal', '')
    shared['shared_goal'] = _excerpt(old_goal, limit)
    if shared['shared_goal'] != old_goal:
        shared['shared_goal_is_partial'] = True
    observation = shared.get('observation')
    if isinstance(observation, dict):
        original = observation.get('summary', '')
        observation['summary'] = _excerpt(original, limit)
        if observation['summary'] != original:
            observation['summary_is_partial'] = True
        omissions['shared_observation_chars'] = omissions.get('shared_observation_chars', 0) + len(original) - len(observation['summary'])
    shared['interpretation'] = 'Partial selected-source observations; omitted text is unknown, not absent.'


def compact_coordination_context(
    observations: dict, *, user_text: str, render_prompt,
    usable_input_tokens: int = 6144, reserve_tokens: int = 128,
) -> tuple[dict, dict]:
    """Measure a pure exact system renderer, then compact optional context only.

    ``render_prompt`` must render the complete system string without changing its
    input or performing provider calls. The user text is measured unchanged.
    Callers must fail closed if required state cannot fit after optional stages.
    Core must bind source dependencies before calling this prompt-only selector.
    The bounded current owner profile is required alongside Shana's identity.
    """
    if type(observations) is not dict or not isinstance(user_text, str):
        raise ValueError('invalid_private_context')
    if type(usable_input_tokens) is not int or type(reserve_tokens) is not int or not 0 <= reserve_tokens < usable_input_tokens:
        raise ValueError('invalid_private_context_budget')
    candidate = deepcopy(observations)
    personal = candidate.get('personal', {})
    identity = personal.get('identity', {})
    protected_fields = set(identity.get('field_sources', {}))
    protected_owner = deepcopy(personal.get('owner'))
    omissions = {'recent_exchanges':0, 'people':0, 'recall':0, 'identity_fields':0,
                 'unfinished_and_decisions':0, 'machine_source_ids':0,
                 'growth_hypotheses':0,'growth_experiences':0,'growth_choices':0}

    def measured(stage):
        # This is the same character estimator used by llm.capabilities.
        if protected_owner is not None:
            personal['owner'] = deepcopy(protected_owner)
        system = render_prompt(deepcopy(candidate))
        if not isinstance(system, str):
            raise TypeError('private_context_renderer_must_return_text')
        tokens = max(1, (len(system) + len(user_text) + 3) // 4)
        if tokens <= usable_input_tokens - reserve_tokens:
            return candidate, {'compacted':stage > 0, 'stage':stage, 'estimated_input_tokens':tokens,
                               'usable_input_tokens':usable_input_tokens, 'reserved_input_tokens':reserve_tokens,
                               'omitted_optional_context':dict(omissions)}
        return None

    result = measured(0)
    if result is not None:
        return result
    for stage in (1, 2, 3):
        growth=candidate.get('growth',{})
        for key in ('hypotheses','experiences','choices'):
            if key in growth:
                before=len(growth[key])
                outcomes=[exp for exp in growth[key] if exp.get('source_observations',{}).get('files')] if key=='experiences' else []
                outcome=next((exp for exp in outcomes if exp['source_observations'].get('activity_id')==candidate.get('current_activity_id')),
                             outcomes[0] if outcomes else None)
                growth[key]=[outcome] if outcome else growth[key][:1] if stage==1 else []
                omissions['growth_'+key]+=before-len(growth[key])
        exploration=candidate.get('exploration')
        if exploration:
            for task in exploration.get('tasks',[]):
                if 'goal' in task:task['goal']=_excerpt(task['goal'],160 if stage==1 else 80)
        _compact_source_observations(candidate,stage,omissions)
        _compact_shared_activity(candidate,stage,omissions)
        # These IDs already bound the turn's invalidation dependencies in Core.
        # Kept recall/decision records retain their own source and provenance.
        omissions['machine_source_ids'] += len(personal.pop('recent_source_ids', []))
        for key, cap in (('recent_exchanges', 2 if stage == 1 else 0),):
            rows = candidate.get(key, [])
            kept = rows[-cap:] if cap else []
            omissions[key] += len(rows) - len(kept)
            for row in kept:
                for field in ('user', 'assistant'):
                    if field in row:
                        row[field] = _excerpt(row[field], 160)
            if key in candidate:
                candidate[key] = kept
        for key, caps, limit in (('people', (4, 2, 0), 80), ('recall', (3, 2, 0), 240 if stage == 1 else 160)):
            rows = personal.get(key, [])
            kept = rows[:caps[stage - 1]]
            omissions[key] += len(rows) - len(kept)
            for row in kept:
                for field in (('name', 'relationship') if key == 'people' else ('text',)):
                    if field in row:
                        row[field] = _excerpt(row[field], 60 if field == 'name' else limit)
            if key in personal:
                personal[key] = kept
        unfinished = personal.get('unfinished_and_decisions', [])
        if stage == 3 and 'unfinished_and_decisions' in personal:
            personal['unfinished_and_decisions'] = unfinished[:3]
            omissions['unfinished_and_decisions'] += len(unfinished) - len(unfinished[:3])
        for row in personal.get('unfinished_and_decisions', []):
            if 'text' in row:
                row['text'] = _excerpt(row['text'], 160 if stage == 1 else 80) if stage < 3 else ''
        if 'working_notes' in candidate:
            candidate['working_notes'] = ({key:_excerpt(value,180) for key,value in candidate['working_notes'].items()}
                                          if stage == 1 else {})
        for activity in candidate.get('activities', []):
            for field, limit in (('requested_goal',120), ('next_step',80), ('reason',80)):
                if field not in activity:
                    continue
                if stage == 3 or stage == 2 and field != 'reason':
                    activity.pop(field)
                else:
                    activity[field] = _excerpt(activity[field], limit)
        for exercise in candidate.get('offline_practice_capability', {}).get('available_exercises', []):
            if 'purpose' in exercise:
                if stage > 1:
                    exercise.pop('purpose')
                else:
                    exercise['purpose'] = _excerpt(exercise['purpose'],80)
        for task in candidate.get('practical_work',{}).get('tasks',[]):
            if 'goal' in task:
                if stage>1:
                    task.pop('goal')
                else:
                    task['goal']=_excerpt(task['goal'],100)
        optional = {'scenario_notes', 'first_message', 'example_dialogue'}
        if stage > 1:
            optional.add('description')
        for field in optional - protected_fields:
            if field in identity.get('fields', {}):
                identity['fields'].pop(field)
                omissions['identity_fields'] += 1
        result = measured(stage)
        if result is not None:
            return result
    if personal.get('unfinished_and_decisions'):
        omissions['unfinished_and_decisions'] += len(personal['unfinished_and_decisions'])
        personal['unfinished_and_decisions'] = []
        result = measured(4)
        if result is not None:
            return result
    # A current completed result survives the optional history/menu reductions.
    # It is still optional when exact identity, authority and user input cannot
    # otherwise fit. Omit it explicitly before the final fail-closed decision.
    growth=candidate.get('growth',{})
    if any(exp.get('source_observations') for exp in growth.get('experiences',[])):
        _compact_source_observations(candidate,5,omissions)
        omissions['growth_experiences']+=len(growth['experiences'])
        growth['experiences']=[]
        result=measured(5)
        if result is not None:
            return result
    raise PrivateContextBudgetError('private_context_budget_exhausted')
