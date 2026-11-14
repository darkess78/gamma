"""Validate exact finite exploration scope, based on a reviewed worker proposal."""
import math
import re


def validate_offer(task_ids, activities, now, budget_seconds, maximum_reviews):
    if (type(now) not in (int,float) or not math.isfinite(now)
        or type(budget_seconds) is not int or not 30<=budget_seconds<=1800
        or type(maximum_reviews) is not int or not 1<=maximum_reviews<=3
        or not isinstance(task_ids,list) or not 2<=len(task_ids)<=3
        or any(not isinstance(i,str) or not re.fullmatch(r'work-[a-f0-9]{32}',i) for i in task_ids)
        or len(set(task_ids))!=len(task_ids) or not isinstance(activities,list)):
        raise ValueError('invalid_exploration_scope')
    lookup={}
    for activity in activities:
        if not isinstance(activity,dict) or not isinstance(activity.get('id'),str) or activity['id'] in lookup:
            raise ValueError('invalid_or_duplicate_activity')
        lookup[activity['id']]=activity
    selected=[]
    for identifier in task_ids:
        activity=lookup.get(identifier)
        if (not activity or activity.get('kind')!='practical' or activity.get('status')!='ready'
            or activity.get('authorize_start') is not True or not isinstance(activity.get('goal'),str)
            or not activity['goal'].strip() or type(activity.get('expires_at')) not in (int,float)
            or not math.isfinite(activity['expires_at']) or activity['expires_at']<=now):
            raise ValueError('exploration_requires_ready_authorized_tasks')
        selected.append(activity)
    if len({a['goal'].strip().casefold() for a in selected})!=len(selected):
        raise ValueError('exploration_requires_distinct_goals')
    return {'task_ids':list(task_ids),'expires_at':min(now+budget_seconds,*[a['expires_at'] for a in selected]),
            'remaining_reviews':maximum_reviews}
