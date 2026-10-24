"""Recheck fictional examples; never train on private repository content."""
import ast
import hashlib
import json
from pathlib import Path

from .curriculum import discord_foundations
from .evaluation import evaluate_candidate
from .practice import _checks, _digest
from .sandbox import run_practice_check


SYSTEM = ('Implement the requested Python solution. Return ONLY JSON mapping '
          'solution.py to complete source. Use pure Python, no imports, I/O or introspection.')


def pure_source(source):
    if not isinstance(source, str) or not 1 <= len(source.encode()) <= 16000:
        raise ValueError('Invalid solution size')
    tree = ast.parse(source)
    forbidden = {'open', 'exec', 'eval', 'compile', '__import__', 'getattr', 'setattr',
                 'delattr', 'globals', 'locals', 'vars', 'dir', 'input', 'print',
                 'exit', 'quit', 'breakpoint', 'help', 'memoryview'}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError('Solution imports forbidden')
        if isinstance(node, ast.Name) and (node.id in forbidden or node.id.startswith('__')):
            raise ValueError('Solution introspection forbidden')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise ValueError('Solution private attributes forbidden')
    return source


def messages(goal, initial):
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps({'goal': goal, 'editable_files': initial}, ensure_ascii=False)}]


def collect(root, *, check=run_practice_check):
    root = Path(root)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('Unsafe practice storage')
    tasks = {t.id: t for t in discord_foundations()}
    samples, seen = [], set()
    for path in sorted(root.glob('*/candidate-solutions.json'))[:100]:
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 200000:
            continue
        bundle = json.loads(path.read_text())
        if bundle.get('version') != 1:
            continue
        for sample in bundle.get('samples', [])[:20]:
            task = tasks.get(sample.get('task_id'))
            # An extra owner_id bypass was outside the old exercise's checks.
            # Do not teach that sample until its stronger acceptance is available.
            if task is None or task.id == 'command_dispatch':
                continue
            files = sample.get('source_files')
            if not isinstance(files, dict) or files.keys() != task.files.keys():
                continue
            if any(files[p] != task.files[p] for p in task.files if p not in task.editable):
                continue
            if sample.get('source_sha256') != _digest(files):
                continue
            try:
                for p in task.editable:
                    pure_source(files[p])
            except (ValueError, SyntaxError):
                continue
            key = (task.id, _digest(files))
            if key in seen:
                continue
            checks = _checks(task, files, check)
            evaluation = evaluate_candidate(task, files, check=check, seed=54179)
            if not all(c['passed'] and not c['infrastructure_error'] for c in checks) or evaluation['passed'] is not True:
                continue
            seen.add(key)
            conversation = messages(task.instructions, {p: task.files[p] for p in task.editable})
            conversation.append({'role': 'assistant', 'content': json.dumps({p: files[p] for p in task.editable})})
            samples.append({'messages': conversation, 'task_id': task.id, 'run_id': bundle['run_id'],
                            'source_sha256': key[1], 'rechecks': checks, 'evaluation': evaluation})
            if len(samples) >= 50:
                return samples
    return samples


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()
