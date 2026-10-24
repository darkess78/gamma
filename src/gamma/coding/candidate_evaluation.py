"""Frozen coding holdouts excluded from adapter training and repair feedback."""
import json
import tempfile
from pathlib import Path

from .candidate_data import messages, pure_source
from .practice import PracticeCheck
from .sandbox import run_practice_check
from .candidate_data import file_digest


def holdouts():
    return [
        ('merge_ranges', 'Implement merge_ranges(intervals): merge overlapping or touching [start,end] integer ranges, sorted by start. Do not mutate input; empty returns [].',
         'merge_ranges', [(([[5,7],[1,3],[3,5],[10,11]],), [[1,7],[10,11]]), (([],), []), (([[2,2],[1,1]],), [[1,1],[2,2]])]),
        ('unique_recent', 'Implement unique_recent(items, limit): return at most limit distinct hashable items, keeping the most recent occurrence, ordered oldest to newest. Nonpositive limit returns [].',
         'unique_recent', [(([1,2,1,3,2],2),[3,2]), ((['a','b','a'],3),['b','a']), (([1],0),[])]),
        ('transpose_ragged', 'Implement transpose_ragged(rows, fill=None): transpose ragged lists padding missing positions with fill. Empty rows returns []. Do not mutate input.',
         'transpose_ragged', [(([[1,2],[3],[]],),[[1,3,None],[2,None,None]]), (([],),[]), (([[],[]],),[]), (([[1],[2,3]],0),[[1,2],[0,3]])]),
        ('stable_groups', 'Implement stable_groups(pairs): pairs are [key,value]. Return [[key,[values...]],...] preserving first key order and value order. Keys are strings.',
         'stable_groups', [(([['b',1],['a',2],['b',3]],),[['b',[1,3]],['a',[2]]]), (([],),[])]),
        ('topology', 'Implement topology(graph): dict string node -> list prerequisite nodes, all nodes present. Return lexicographically smallest topological ordering, or None for a cycle. Empty graph returns [].',
         'topology', [(({'c':['a','b'],'a':[],'b':['a']},),['a','b','c']), (({'a':['b'],'b':['a']},),None), (({'b':[],'a':[]},),['a','b']), (({},),[])]),
        ('deep_overlay', 'Implement deep_overlay(base, patch): recursively merge dictionaries, other patch values replace base values, lists replace entirely. Return a fresh structure with no input mutation.',
         'deep_overlay', [(({'a':{'b':1,'c':2},'x':[1]},{'a':{'b':3},'x':[2]}),{'a':{'b':3,'c':2},'x':[2]}), (({}, {'a':None}), {'a':None})]),
        ('run_lengths', 'Implement run_lengths(items): consecutive equal items become [item,count] lists, preserving order. Empty returns [].',
         'run_lengths', [(([1,1,2,1,1,1],),[[1,2],[2,1],[1,3]]), (([],),[]), ((['🙂','🙂','x'],),[['🙂',2],['x',1]])]),
        ('strict_record', 'Implement strict_record(value): return True only for a dict with exactly keys name and count, nonempty string name and exact integer count >=0. Return False for all malformed values without raising.',
         'strict_record', [(({'name':'x','count':0},),True), (({'name':'x','count':True},),False), ((None,),False), (({'name':'x','count':1,'extra':0},),False), (({'name':[],'count':0},),False)]),
    ]


def evaluate(generate, checkpoint, revision, output, *, check=run_practice_check):
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {'checkpoint': checkpoint, 'revision': revision, 'tasks': []}
    for identifier, goal, function, cases in holdouts():
        fixture = ('import copy\nfrom solution import ' + function + ' as target\n'
                   + 'cases=' + repr(cases) + '\n'
                   + 'for args, expected in cases:\n before=copy.deepcopy(args)\n'
                   + ' actual=target(*args)\n assert actual==expected,(actual,expected)\n assert args==before\n')
        if identifier == 'strict_record':
            fixture += ' assert type(actual) is bool\n'
        if identifier == 'deep_overlay':
            fixture += "original={'a':{'c':2}}\nresult=target(original,{})\nresult['a']['c']=9\nassert original=={'a':{'c':2}}\n"
        evidence = output / identifier
        evidence.mkdir(mode=0o700)
        (evidence / 'fixture.py').write_text(fixture)
        record = {'id': identifier, 'fixture_sha256': file_digest(evidence / 'fixture.py'),
                  'sandboxed': True, 'passed': False, 'infrastructure_error': False}
        try:
            response = generate(messages(goal, {'solution.py': ''}))
            (evidence / 'response.txt').write_text(response)
            files = json.loads(response)
            if not isinstance(files, dict) or set(files) != {'solution.py'}:
                raise ValueError('Malformed solution')
            source = pure_source(files['solution.py'])
            with tempfile.TemporaryDirectory(prefix='gamma-heldout-') as temporary:
                workspace = Path(temporary)
                (workspace / 'solution.py').write_text(source)
                (workspace / 'evaluation_cases.py').write_text(fixture)
                result = check(workspace, PracticeCheck('heldout', 'feature', 'evaluation_cases'))
            record.update(passed=result['passed'] is True,
                          infrastructure_error=result['infrastructure_error'] is not False,
                          output_sha256=result['output_sha256'])
        except (ValueError, SyntaxError, TypeError, KeyError) as exc:
            record['error'] = type(exc).__name__
        report['tasks'].append(record)
    return report
