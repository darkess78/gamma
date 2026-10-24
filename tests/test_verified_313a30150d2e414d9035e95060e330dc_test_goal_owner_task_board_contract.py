"""Independent frozen tests; never provided to Gamma during its first run."""
from copy import deepcopy
import json
import random
import pytest
from gamma.task_board import analyze_tasks


def empty():
    return {'ready': [], 'waiting': {}, 'running': [], 'failed': [], 'done': []}


def test_empty_and_defaults():
    assert analyze_tasks([]) == empty()
    expected = empty(); expected['ready'] = ['one']
    assert analyze_tasks([{'id': 'one', 'notes': 'ignore extra metadata'}]) == expected


def test_product_scenario_with_failed_prerequisite_and_priority():
    tasks = [
        {'id':'deploy', 'depends_on':['test','review'], 'priority':100},
        {'id':'code', 'status':'done'},
        {'id':'test', 'depends_on':['code'], 'priority':2},
        {'id':'review', 'status':'failed'},
        {'id':'docs', 'priority':2},
        {'id':'hotfix', 'priority':9},
        {'id':'inflight', 'status':'running', 'depends_on':['review']},
    ]
    assert analyze_tasks(tasks) == {'ready':['hotfix','test','docs'],
        'waiting':{'deploy':['test','review']},'running':['inflight'],
        'failed':['review'],'done':['code']}


def test_dependency_order_forward_references_and_exact_ids():
    tasks=[{'id':'child','depends_on':['A','a','完成']}, {'id':'a','status':'done'},
           {'id':'完成','status':'running'}, {'id':'A','status':'failed'}]
    assert analyze_tasks(tasks) == {'ready':[],'waiting':{'child':['A','完成']},
        'running':['完成'],'failed':['A'],'done':['a']}


def test_input_unchanged_and_return_is_detached():
    tasks=[{'id':'a','depends_on':[],'notes':{'keep':[1]}},
           {'id':'b','depends_on':['a']}]
    before=deepcopy(tasks); result=analyze_tasks(tasks)
    assert tasks==before
    result['ready'].clear(); result['waiting']['b'].append('invented')
    assert tasks==before
    assert analyze_tasks(tasks)['waiting']=={'b':['a']}


@pytest.mark.parametrize('bad', [None, {}, 'tasks', 1, True, [None], [[]],
    [{}], [{'id':None}], [{'id':False}], [{'id':[]}], [{'id':'  '}],
    [{'id':'x'},{'id':'x'}], [{'id':'x','status':None}],
    [{'id':'x','status':[]}], [{'id':'x','status':'DONE'}],
    [{'id':'x','priority':True}], [{'id':'x','priority':1.2}],
    [{'id':'x','priority':None}], [{'id':'x','depends_on':None}],
    [{'id':'x','depends_on':()}], [{'id':'x','depends_on':'y'}],
    [{'id':'x','depends_on':[{}]}], [{'id':'x','depends_on':[' ']}],
    [{'id':'x','depends_on':['y','y']},{'id':'y'}],
    [{'id':'x','depends_on':['missing']}],
    [{'id':'x','status':'done','depends_on':['missing']}],
    [{'id':'x','depends_on':['x']}],
    [{'id':'x','status':'done','depends_on':['y']},{'id':'y','status':'done','depends_on':['x']}],
    [{'id':'safe'}, {'id':'a','depends_on':['b']},{'id':'b','depends_on':['a']}],
])
def test_invalid_records_fail_as_value_error_without_mutation(bad):
    original=deepcopy(bad)
    with pytest.raises(ValueError): analyze_tasks(bad)
    assert bad==original


def test_long_chain_and_cycle_are_not_recursion_limited():
    tasks=[{'id':str(i),'depends_on':[str(i-1)] if i else [],'status':'done' if i<1999 else 'todo'} for i in range(2000)]
    result=analyze_tasks(tasks)
    assert result['ready']==['1999'] and len(result['done'])==1999
    tasks[0]['depends_on']=['1999']
    with pytest.raises(ValueError): analyze_tasks(tasks)


