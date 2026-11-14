"""What Shana's self-coding may change.

- Source: Python modules under ``src/gamma`` outside the protected areas
  (coding, safety, persona, tools, API, identity, supervisor and the like),
  plus a few named entry points. Shana can't rewrite her own guard rails.
- Tests: only new ``tests/test_*.py`` files. Existing tests are what proves
  nothing broke, so a task may never edit them to make them pass.
- Changed source must not use the tricks that fake a passing run (exiting the
  process, exec/eval, patching builtins, importing test machinery).
"""
from __future__ import annotations

import ast
import re

_LEARNING_FILES = {'src/gamma/coding/lessons.py', 'src/gamma/coding/lesson_history.py',
                   'src/gamma/coding/curriculum.py'}
_PROTECTED = ('src/gamma/coding/', 'src/gamma/improvement/', 'src/gamma/safety/',
              'src/gamma/persona/', 'src/gamma/supervisor/', 'src/gamma/resources/',
              'src/gamma/tools/', 'src/gamma/agent/', 'src/gamma/identity/', 'src/gamma/api/')
_PROTECTED_FILES = {'src/gamma/config.py', 'src/gamma/main.py', 'src/gamma/dashboard/auth.py',
                    'src/gamma/dashboard/main.py', 'src/gamma/dashboard/service.py',
                    'src/gamma/dashboard/coding_routes.py', 'src/gamma/conversation/service.py'}
_NEW_TEST = re.compile(r'tests/test_[a-z0-9_]{1,80}\.py')


def allowed_source(path):
    if not isinstance(path, str) or not re.fullmatch(r'src/gamma/(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py', path):
        return False
    if path.endswith('/__init__.py') or path in _PROTECTED_FILES:
        return False
    return path in _LEARNING_FILES or not path.startswith(_PROTECTED)


def editable(path: str, existing: set[str]) -> bool:
    """May a task change ``path``? ``existing`` is the repository's files before any task ran."""
    if allowed_source(path):
        return True
    return bool(_NEW_TEST.fullmatch(path or '')) and path not in existing


_BANNED_MODULES = {'pytest', 'unittest', 'builtins', 'inspect', 'ctypes'}
_BANNED_ATTRIBUTES = {'_exit', '__builtins__', '__import__', 'settrace', 'setprofile', 'exit'}
_BANNED_NAMES = {'exec', 'eval', '__import__', '__builtins__'}


def unsafe_source(path: str, source: str) -> str | None:
    """Why a changed source file can't be proposed, or None if it's fine. Tests aren't checked."""
    if not path.startswith('src/'):
        return None
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return f'{path} does not parse: {exc.msg}'
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            if any(m.split('.')[0] in _BANNED_MODULES for m in modules):
                return f'{path} imports test or interpreter machinery'
        if isinstance(node, ast.Attribute) and node.attr in _BANNED_ATTRIBUTES:
            return f'{path} uses {node.attr}'
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            return f'{path} uses {node.id}'
    return None
