"""Bounded repository coding orchestration."""
import ast
import hashlib
import json
import math
import re
import time


def _ordered(tasks):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 10:
        raise ValueError('Invalid task plan')
    lookup = {}
    for task in tasks:
        if not isinstance(task, dict) or not re.fullmatch('[a-z][a-z0-9_-]{0,79}', str(task.get('id', ''))):
            raise ValueError('Invalid task ID')
        if task['id'] in lookup: raise ValueError('Duplicate task')
        paths = task.get('editable')
        if not isinstance(paths, list) or not paths or len(paths) != len(set(paths)):
            raise ValueError('Invalid editable scope')
        if any(not isinstance(p, str) or not re.fullmatch(r'(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py', p) for p in paths):
            raise ValueError('Invalid source path')
        lookup[task['id']] = task
    ordered, active, visited = [], set(), set()
    def visit(task):
        identifier = task['id']
        if identifier in active: raise ValueError('Cyclic task plan')
        if identifier in visited: return
        active.add(identifier)
        deps = task.get('depends_on', [])
        if not isinstance(deps, list): raise ValueError('Invalid dependencies')
        for dep in deps:
            if not isinstance(dep, str) or dep not in lookup: raise ValueError('Unknown dependency')
            visit(lookup[dep])
        active.remove(identifier)
        visited.add(identifier)
        ordered.append(task)
    for task in tasks: visit(task)
    return ordered


def run_project(goal, *, plan, read, propose, check, review, promote,
                maximum_attempts=5, budget_seconds=300, checkpoint=lambda:None, event=lambda x:None, checker=None):
    if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 10:
        raise ValueError('Invalid attempt limit')
    if not math.isfinite(budget_seconds) or not 1 <= budget_seconds <= 1800:
        raise ValueError('Invalid time budget')
    deadline = time.monotonic() + budget_seconds
    def boundary():
        checkpoint()
        if time.monotonic() >= deadline: raise TimeoutError('Budget exhausted')
    boundary()
    ordered = _ordered(plan(goal))
    results, completed = [], set()
    for task in ordered:
        item = {'id':task['id'], 'status':'needs_checker', 'attempts':0, 'evidence':[], 'source_hashes':{}}
        results.append(item)
        if any(dep not in completed for dep in task.get('depends_on', [])):
            item['status'] = 'blocked_dependency'
            continue
        feedback = []
        try:
            boundary()
            files = dict(read(task))
            if not set(task['editable']) <= files.keys(): raise ValueError('Source missing')
            for attempt in range(1, maximum_attempts + (2 if checker is not None else 0) + 1):
                item['attempts'] = attempt
                if attempt == maximum_attempts + 1:
                    boundary()
                    event({'stage':'checker','task_id':task['id'],'attempt':attempt})
                    critique=checker(dict(task),dict(files),list(feedback))
                    if not isinstance(critique,dict) or not isinstance(critique.get('guidance'),str) or not critique['guidance'].strip() or not critique.get('evidence'):
                        raise ValueError('Checker did not provide grounded guidance')
                    item['checker']=critique
                    feedback.append({'stage':'checker','result':critique})
                    task={**task,'instructions':critique['guidance']}
                try:
                    boundary()
                    event({'stage':'coding','task_id':task['id'],'attempt':attempt})
                    edits = propose(task, {p:files[p] for p in task['editable']}, list(feedback))
                    if not isinstance(edits, dict) or not edits or not set(edits) <= set(task['editable']):
                        raise ValueError('Proposal scope violation')
                    if any(not isinstance(s, str) for s in edits.values()): raise ValueError('Invalid source')
                    candidate = {**files, **edits}
                    if sum(len(s.encode()) for s in candidate.values()) > 65536: raise ValueError('Source too large')
                    for path, source in edits.items(): ast.parse(source, filename=path)
                    files = candidate
                except (ValueError, SyntaxError) as exc:
                    feedback.append({'stage':'proposal','error':type(exc).__name__})
                    continue
                failed = False
                for stage, callback in (('testing',check), ('independent_review',review), ('applying',promote)):
                    boundary()
                    event({'stage':stage,'task_id':task['id'],'attempt':attempt})
                    outcome = callback(task, dict(files))
                    valid = isinstance(outcome, dict) and outcome.get('passed') is True and bool(outcome.get('evidence'))
                    item['evidence'].append({'stage':stage, 'result':outcome})
                    if not valid:
                        feedback.append({'stage':stage,'result':outcome})
                        failed = True
                        # Promotion may have partially applied an external action. Never replay it.
                        if stage == 'applying': item['status'] = 'blocked'
                        break
                if item['status'] == 'blocked': break
                if failed: continue
                item['status'] = 'completed'
                item['source_hashes'] = {p:hashlib.sha256(s.encode()).hexdigest() for p,s in files.items()}
                completed.add(task['id'])
                break
        except Exception as exc:
            item.update(status='blocked', error=type(exc).__name__)
        event({'stage':item['status'],'task_id':task['id'],'attempt':item['attempts']})
    return {'goal':goal, 'passed':all(t['status']=='completed' for t in results), 'tasks':results}
