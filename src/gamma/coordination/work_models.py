"""Strict portable specifications for private candidate work, never live source."""
from copy import deepcopy
import re
import sys


_RESERVED = set(sys.stdlib_module_names) | {'unittest', 'runpy', 'json', 'sys', 'os', 'site', 'pathlib', 'subprocess',
             'importlib', 'builtins', 'sitecustomize', 'usercustomize', 'encodings'}


def safe_relative(name):
    if (not isinstance(name, str) or not 1 <= len(name) <= 200 or not name.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or any(char in name for char in ('\\', ':')) or name.startswith('/') or '//' in name):
        raise ValueError('invalid_work_file_path')
    parts = name.split('/')
    for part in parts:
        stem = part.split('.')[0].lower()
        if (part in {'', '.', '..'} or part.endswith((' ', '.')) or part.startswith('.')
            or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_. -]*', part)
            or re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])', stem)
            or stem in _RESERVED or part.lower().endswith('.local.toml')
            or part.lower() in {'app.local.toml', 'voices.local.toml'}):
            raise ValueError('invalid_work_file_path')
    return name


def validate_spec(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump()
    if not isinstance(value, dict) or set(value) != {'mode', 'goal', 'files', 'editable', 'checks'}:
        raise ValueError('invalid_work_spec_fields')
    value = deepcopy(value)
    if (not isinstance(value['mode'], str) or value['mode'] not in {'build', 'research'} or not isinstance(value['goal'], str)
        or not value['goal'].strip() or len(value['goal']) > 6000):
        raise ValueError('invalid_work_goal_or_mode')
    files = value['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= 20:
        raise ValueError('invalid_work_files')
    for path, text in files.items():
        safe_relative(path)
        if not isinstance(text, str):
            raise ValueError('work_files_must_be_utf8_text')
    portable = {path.casefold() for path in files}
    if len(portable) != len(files) or any(
        '/'.join(path.split('/')[:index]).casefold() in portable
        for path in files for index in range(1, len(path.split('/')))):
        raise ValueError('work_file_paths_conflict')
    if sum(len(text.encode('utf-8')) for text in files.values()) > 60000:
        raise ValueError('work_files_too_large')
    editable = value['editable']
    if (not isinstance(editable, list) or any(not isinstance(path, str) for path in editable)
        or len(set(editable)) != len(editable) or not set(editable) <= files.keys()):
        raise ValueError('invalid_work_editable_scope')
    checks = value['checks']
    if not isinstance(checks, list) or len(checks) > 6:
        raise ValueError('invalid_work_checks')
    modules, names = set(), set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'name', 'module', 'kind'}
            or not isinstance(check['name'], str) or not 1 <= len(check['name']) <= 100
            or not check['name'].strip() or check['name'] in names
            or not isinstance(check['module'], str)
            or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', check['module'], re.ASCII)
            or check['module'] in modules or not isinstance(check['kind'], str)
            or check['kind'] not in {'feature', 'regression'}):
            raise ValueError('invalid_work_check_contract')
        path = check['module'].replace('.', '/') + '.py'
        safe_relative(path)
        if path not in files or path in editable:
            raise ValueError('work_checks_must_be_protected')
        parts = path.split('/')
        if any('/'.join(parts[:index] + ['__init__.py']) in editable for index in range(1, len(parts))):
            raise ValueError('work_check_package_must_be_protected')
        modules.add(check['module']); names.add(check['name'])
    if value['mode'] == 'build' and (not editable or not checks):
        raise ValueError('work_build_requires_edits_and_checks')
    if value['mode'] == 'research' and (editable or checks):
        raise ValueError('work_research_cannot_implement')
    return value


class WorkSpec:
    def __init__(self, **values):
        self._values = validate_spec(values)

    @classmethod
    def model_validate(cls, values):
        return cls(**validate_spec(values))

    def model_dump(self):
        return deepcopy(self._values)
