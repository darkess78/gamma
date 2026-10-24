"""Nonsecret source discovery and immutable executor boundaries."""
from __future__ import annotations
import ast
import hashlib
import os
import re
from pathlib import Path

_LEARNING_FILES = {'src/gamma/coding/lessons.py', 'src/gamma/coding/lesson_history.py',
                   'src/gamma/coding/curriculum.py'}
_PROTECTED = ('src/gamma/coding/', 'src/gamma/improvement/', 'src/gamma/safety/',
              'src/gamma/persona/', 'src/gamma/supervisor/', 'src/gamma/resources/',
              'src/gamma/tools/', 'src/gamma/agent/', 'src/gamma/identity/', 'src/gamma/api/')


def allowed_source(path):
    if not isinstance(path, str) or not re.fullmatch(r'src/gamma/(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py', path):
        return False
    if path.endswith('/__init__.py') or path in {'src/gamma/config.py', 'src/gamma/main.py',
            'src/gamma/dashboard/auth.py', 'src/gamma/dashboard/main.py', 'src/gamma/dashboard/service.py',
            'src/gamma/dashboard/coding_routes.py', 'src/gamma/conversation/service.py'}:
        return False
    return path in _LEARNING_FILES or not path.startswith(_PROTECTED)


def source_path(root, path):
    if not allowed_source(path):
        raise ValueError('Protected or invalid source path')
    result = Path(root).absolute() / path
    if any(p.is_symlink() for p in (result, *result.parents)):
        raise ValueError('Unsafe source path')
    return result


def inventory(root):
    rows = []
    for directory, dirs, names in os.walk(Path(root) / 'src/gamma', followlinks=False):
        dirs[:] = [n for n in dirs if not n.startswith('.') and n != '__pycache__'
                   and not (Path(directory) / n).is_symlink()]
        for name in sorted(names):
            path = Path(directory) / name
            rel = str(path.relative_to(root)).replace('\\', '/')
            if not allowed_source(rel) or path.is_symlink() or path.stat().st_size > 64000:
                continue
            raw = source_path(root, rel).read_bytes()
            tree = ast.parse(raw.decode('utf-8'))
            rows.append({'path':rel, 'sha256':hashlib.sha256(raw).hexdigest(), 'size':len(raw),
                         'symbols':[n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))][:30]})
            if len(rows) > 400:
                raise ValueError('Source inventory exceeds the supported size')
    return sorted(rows, key=lambda row: row['path'])


def read_sources(root, paths, maximum_bytes=24000):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 8 or len(paths) != len(set(paths)):
        raise ValueError('Invalid source read')
    result = {}; size = 0
    for path in paths:
        target = source_path(root, path)
        raw = target.read_bytes()
        size += len(raw)
        if size > maximum_bytes:
            raise ValueError('Source read exceeds the context budget')
        result[path] = raw.decode('utf-8')
    return result


def validate_edits(edits, allowed_paths, maximum_bytes=64000):
    if not isinstance(edits, dict) or not edits or len(edits) > 12 or not set(edits) <= set(allowed_paths):
        raise ValueError('Source edit exceeds the task scope')
    if any(not allowed_source(p) or not isinstance(s, str) for p, s in edits.items()):
        raise ValueError('Invalid source edit')
    if sum(len(s.encode()) for s in edits.values()) > maximum_bytes:
        raise ValueError('Source edit exceeds size budget')
    for path, source in edits.items():
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                if any(m.split('.')[0] in {'pytest', 'unittest', 'builtins', 'inspect', 'ctypes'} for m in modules):
                    raise ValueError('Candidate cannot modify verification machinery')
            if isinstance(node, ast.Attribute) and node.attr in {'_exit', '__builtins__', '__import__', 'settrace', 'setprofile'}:
                raise ValueError('Candidate cannot bypass verification')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'exit':
                raise ValueError('Candidate cannot bypass verification')
            if isinstance(node, ast.Name) and node.id in {'exec', 'eval', '__import__', '__builtins__'}:
                raise ValueError('Candidate cannot bypass verification')
