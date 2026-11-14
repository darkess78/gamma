"""Finite source and measurement contracts for isolated candidate growth."""
from copy import deepcopy
import math
import re

SOURCE_RE = re.compile(r'src-[a-f0-9]{24}')
ID_RE = re.compile(r'(?:exp|hyp|trial|eval)-[a-f0-9]{24}')
HASH_RE = re.compile(r'[a-f0-9]{64}')
KINDS = {'judgment','interest','character','skill'}


def text(value, maximum, *, empty=False):
    if not isinstance(value,str) or len(value)>maximum or '\x00' in value or (not empty and not value.strip()):
        raise ValueError('invalid_growth_text')
    return value


def fields(value, required, optional=()):
    if not isinstance(value,dict) or not set(required)<=set(value) or set(value)-set(required)-set(optional):
        raise ValueError('invalid_growth_fields')
    return value


def strings(value, maximum=8, length=500, *, nonempty=False):
    if not isinstance(value,list) or len(value)>maximum or (nonempty and not value):
        raise ValueError('invalid_growth_list')
    for item in value:text(item,length)
    if len(value)!=len(set(value)):raise ValueError('duplicate_growth_values')
    return list(value)


def hash_ref(value):
    if not isinstance(value,str) or not HASH_RE.fullmatch(value):raise ValueError('invalid_growth_artifact_ref')
    return value


def source_ids(value):
    strings(value,256,40,nonempty=True)
    if any(not SOURCE_RE.fullmatch(item) for item in value):raise ValueError('invalid_growth_source_ids')
    return list(value)


def number(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('invalid_growth_time')
    return value


def validate_scope(value):
    fields(value,{'target','marker','guidance','expires_seconds','review_condition','review_rationale'})
    if value['target']!='dialogue':raise ValueError('growth_consumer_unavailable')
    text(value['marker'],100);text(value['guidance'],1200)
    if any(ord(c)<32 or ord(c)==127 for c in value['marker']):raise ValueError('invalid_growth_scope')
    if type(value['expires_seconds']) is not int or not 30<=value['expires_seconds']<=86400:
        raise ValueError('invalid_growth_expiry')
    text(value['review_condition'],500);text(value['review_rationale'],500)
    return deepcopy(value)


def hypothesis_values(value, *, model=False):
    required={'statement','kind','scope_target','scope_marker','alternatives','expected_outcome'}
    if not model:required.add('experience_ids')
    fields(value,required)
    text(value['statement'],800);text(value['scope_marker'],100);text(value['expected_outcome'],600)
    if (not isinstance(value['kind'],str) or not isinstance(value['scope_target'],str)
        or value['kind'] not in KINDS or value['scope_target'] not in {'dialogue','work'}):
        raise ValueError('invalid_growth_hypothesis_scope')
    if any(ord(c)<32 or ord(c)==127 for c in value['scope_marker']):raise ValueError('invalid_growth_scope')
    strings(value['alternatives'],4,500,nonempty=True)
    if not model:
        strings(value['experience_ids'],8,40,nonempty=True)
        if any(not re.fullmatch(r'exp-[a-f0-9]{24}',x) for x in value['experience_ids']):
            raise ValueError('invalid_growth_experience_ids')
    return deepcopy(value)


def frozen_cases(value):
    fields(value,{'kind','format','component','model','source_sha256','checker_sha256','frozen_at',
        'scope_target','scope_marker','source_ids','development_families','development_input_sha256','evaluation_families','cases'})
    source_ids(value['source_ids'])
    if value['kind']!='growth_frozen_cases' or type(value['format']) is not int or value['format']!=1 or value['component']!='dialogue_guidance':
        raise ValueError('invalid_growth_frozen_kind')
    if value['scope_target']!='dialogue':raise ValueError('growth_consumer_unavailable')
    text(value['scope_marker'],100);text(value['model'],200)
    hash_ref(value['source_sha256']);hash_ref(value['checker_sha256']);number(value['frozen_at'])
    development=strings(value['development_families'],20,100,nonempty=True)
    evaluation=strings(value['evaluation_families'],20,100,nonempty=True)
    if set(development)&set(evaluation):raise ValueError('growth_evaluation_family_leakage')
    development_inputs=strings(value['development_input_sha256'],100,64,nonempty=True)
    for sha in development_inputs:hash_ref(sha)
    cases=value['cases']
    if not isinstance(cases,list) or not 1<=len(cases)<=50:raise ValueError('invalid_growth_cases')
    seen=set();inputs=set()
    for case in cases:
        fields(case,{'id','family','input_sha256','expected_sha256'})
        text(case['id'],100);text(case['family'],100)
        hash_ref(case['input_sha256']);hash_ref(case['expected_sha256'])
        if case['id'] in seen or case['family'] not in evaluation:raise ValueError('invalid_growth_case_family')
        if case['input_sha256'] in development_inputs or case['input_sha256'] in inputs:
            raise ValueError('growth_evaluation_input_leakage')
        inputs.add(case['input_sha256'])
        seen.add(case['id'])
    if {c['family'] for c in cases}!=set(evaluation):raise ValueError('growth_unrepresented_family')
    return deepcopy(value)


def validate_comparison(comparison, frozen, trial, artifacts):
    """Inspect trusted-owner measurements, never treat integrity as proof of truth."""
    frozen=frozen_cases(frozen)
    fields(comparison,{'kind','format','trial_id','trial_version','guidance_sha256','frozen_ref','model',
        'source_sha256','checker_sha256','scope_target','scope_marker','measured','evaluator',
        'started_at','finished_at','cases','regressions'})
    if (comparison['kind']!='growth_comparison' or type(comparison['format']) is not int or comparison['format']!=1
        or comparison['measured'] is not True or comparison['evaluator']!='objective_code'):
        raise ValueError('growth_requires_objective_measurement')
    if (comparison['trial_id']!=trial['id'] or type(comparison['trial_version']) is not int
        or comparison['trial_version']!=trial['version'] or comparison['guidance_sha256']!=trial['guidance_sha256']
        or comparison['scope_target']!=trial['scope_target'] or comparison['scope_marker']!=trial['scope_marker']
        or frozen['scope_target']!=trial['scope_target'] or frozen['scope_marker']!=trial['scope_marker']):
        raise ValueError('growth_evaluation_trial_mismatch')
    hash_ref(comparison['frozen_ref'])
    if comparison['frozen_ref']!=trial['frozen_ref'] or artifacts.get(comparison['frozen_ref'])!=frozen:
        raise ValueError('growth_frozen_artifact_mismatch')
    for key in ('model','source_sha256','checker_sha256'):
        if comparison[key]!=frozen[key]:raise ValueError('growth_evaluation_component_mismatch')
    number(comparison['started_at']);number(comparison['finished_at'])
    if not (frozen['frozen_at']<=trial['created_at']<=comparison['finished_at']
            and frozen['frozen_at']<=comparison['started_at']<=comparison['finished_at']):
        raise ValueError('growth_evaluation_not_frozen_before_trial')
    if not isinstance(comparison['cases'],list) or len(comparison['cases'])!=len(frozen['cases']):
        raise ValueError('growth_evaluation_case_set_mismatch')
    expected={c['id']:c for c in frozen['cases']};seen=set();improved=[];regressed=[]
    for case in comparison['cases']:
        fields(case,{'id','family','input_sha256','expected_sha256','baseline_ref','candidate_ref','baseline_passed','candidate_passed'})
        if case['id'] in seen or case['id'] not in expected:raise ValueError('growth_evaluation_case_set_mismatch')
        seen.add(case['id']);definition=expected[case['id']]
        if any(case[key]!=definition[key] for key in ('family','input_sha256','expected_sha256')):
            raise ValueError('growth_evaluation_case_mismatch')
        for phase in ('baseline','candidate'):
            if type(case[phase+'_passed']) is not bool:raise ValueError('invalid_growth_check_flag')
            response=artifacts.get(hash_ref(case[phase+'_ref']))
            fields(response,{'kind','format','case_id','phase','model','source_sha256','input_sha256','response',
                'checker','trial_id','trial_version','guidance_sha256','source_ids'})
            source_ids(response['source_ids'])
            if (response['kind']!='growth_case_response' or type(response['format']) is not int or response['format']!=1
                or response['case_id']!=case['id'] or response['phase']!=phase or not isinstance(response['response'],dict)
                or response['model']!=frozen['model'] or response['source_sha256']!=frozen['source_sha256']
                or response['input_sha256']!=case['input_sha256']):raise ValueError('growth_response_binding_mismatch')
            checker=response['checker'];fields(checker,{'implementation_sha256','expected_sha256','passed'})
            if (checker['implementation_sha256']!=frozen['checker_sha256'] or checker['expected_sha256']!=case['expected_sha256']
                or type(checker['passed']) is not bool or checker['passed']!=case[phase+'_passed']):
                raise ValueError('growth_checker_binding_mismatch')
            if phase=='baseline':
                if response['trial_id'] is not None or type(response['trial_version']) is not int or response['trial_version']!=0 or response['guidance_sha256'] is not None:
                    raise ValueError('growth_baseline_contains_trial')
            elif (response['trial_id']!=trial['id'] or type(response['trial_version']) is not int or response['trial_version']!=trial['version']
                  or response['guidance_sha256']!=trial['guidance_sha256']):raise ValueError('growth_candidate_trial_mismatch')
        if not case['baseline_passed'] and case['candidate_passed']:improved.append(case['id'])
        if case['baseline_passed'] and not case['candidate_passed']:regressed.append(case['id'])
    regressions=comparison['regressions']
    if not isinstance(regressions,list) or len(regressions)!=3:raise ValueError('growth_missing_regression_evidence')
    names=set();failed=[]
    for guard in regressions:
        fields(guard,{'name','passed','evidence_ref'})
        if not isinstance(guard['name'],str) or guard['name'] not in {'identity','privacy','authority'} or guard['name'] in names or type(guard['passed']) is not bool:
            raise ValueError('invalid_growth_regression_guard')
        names.add(guard['name']);evidence=artifacts.get(hash_ref(guard['evidence_ref']))
        fields(evidence,{'kind','format','name','passed','checker_sha256','source_sha256','model','details','source_ids'})
        source_ids(evidence['source_ids'])
        if (evidence['kind']!='growth_regression_evidence' or type(evidence['format']) is not int or evidence['format']!=1
            or evidence['name']!=guard['name'] or type(evidence['passed']) is not bool or evidence['passed']!=guard['passed']
            or any(evidence[key]!=frozen[key] for key in ('model','source_sha256','checker_sha256'))
            or not isinstance(evidence['details'],dict)):
            raise ValueError('growth_regression_binding_mismatch')
        if not guard['passed']:failed.append(guard['name'])
    return {'improved_cases':improved,'regressed_cases':regressed,'failed_guards':failed,
        'all_candidate_cases_passed':all(c['candidate_passed'] for c in comparison['cases']),
        'evidence_provenance':'trusted_owner_measurement','truth_not_certified_by_hash':True}
