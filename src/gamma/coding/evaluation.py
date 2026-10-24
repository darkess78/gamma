"""Independent generated-case evaluation, withheld from learner feedback."""
from __future__ import annotations

import hashlib
import json
import random
import re
import secrets
import tempfile
from pathlib import Path


def _cases(identifier, rng):
    if identifier == "message_chunks":
        cases = []
        for index in range(40):
            text = ''.join(rng.choice('a🙂\n界é\u0301 ') for _ in range(rng.randrange(100)))
            limit = rng.randrange(1, 25)
            cases.append(((text, limit), [text[i:i+limit] for i in range(0, len(text), limit)], False))
        cases.extend(((text, limit), None, True) for text in ('', '🙂hello') for limit in (0, -1, -20))
        return 'chunks', cases
    if identifier == "retry_budget":
        cases = []
        for _ in range(80):
            error = rng.choice(['busy', 'rate_limit', 'timeout', 'auth', 'quota', '', None, [], {}])
            attempt = rng.choice([True, False, None, '1', -1, 0, 1, 4, 5, 10])
            maximum = rng.choice([True, False, None, '5', -1, 0, 1, 5, 10])
            expected = isinstance(error, str) and error in ('busy','rate_limit') and type(attempt) is int and type(maximum) is int and 1 <= attempt < maximum
            cases.append(((error, attempt, maximum), expected, False))
        return 'should_retry', cases
    if identifier not in ('owner_authority', 'command_dispatch'):
        raise ValueError('unsupported evaluation task')
    cases = []
    for _ in range(100):
        owner = rng.choice(['owner', '名前🙂', '', None, 1, True, []])
        user = rng.choice([owner, 'visitor', '', None, 1, True, [], {}])
        verified = rng.choice([True, False, 1, 0, 'yes', None, []])
        authorized = verified is True and isinstance(user,str) and isinstance(owner,str) and bool(user) and user == owner
        if identifier == 'owner_authority':
            cases.append(((user, owner, verified), bool(authorized), False))
        else:
            command = rng.choice(['wake','stop','other','',None,1,[],{}])
            request = {'user_id':user,'verified':verified,'command':command}
            malformed = rng.randrange(8)
            if malformed < 3: request.pop(('user_id','verified','command')[malformed])
            elif malformed == 3: request = None
            elif malformed == 4: request = []
            if not isinstance(request,dict) or set(request) != {'user_id','verified','command'} or not authorized or not isinstance(command,str):
                expected = {'status':'denied'}
            else:
                expected = {'status':'executed','command':command} if command in ('wake','stop') else {'status':'unsupported'}
            cases.append(((request,owner), expected, False))
    # Random cases must also exercise authorized successful execution.
    if identifier == 'command_dispatch':
        for owner in ('owner','名前🙂'):
            for command in ('wake','stop','other'):
                cases.append((({'user_id':owner,'verified':True,'command':command},owner),
                              {'status':'executed','command':command} if command in ('wake','stop') else {'status':'unsupported'},False))
    return ('can_command' if identifier == 'owner_authority' else 'dispatch'), cases


def evaluate_candidate(task, files, *, check, seed=None):
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = {'status':'blocked','passed':False,'source_sha256':digest,'checker_version':1,'reason':'evaluation_unavailable'}
    try:
        if not 1 <= len(files) <= 20 or sum(len(s.encode()) for s in files.values()) > 65536:
            raise ValueError('source size')
        if any(not isinstance(s,str) or not re.fullmatch(r'(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py', p) for p,s in files.items()):
            raise ValueError('source path')
        if 'evaluation_cases.py' in files or 'solution.py' not in files:
            raise ValueError('evaluation source scope')
        function, cases = _cases(task.id, random.Random(secrets.randbits(64) if seed is None else seed))
        fixture = f'from solution import {function} as target\ncases={cases!r}\n' + (
            'for args, expected, raises in cases:\n'
            ' if raises:\n'
            '  try: target(*args)\n'
            '  except ValueError: pass\n'
            '  else: raise AssertionError("expected ValueError")\n'
            ' else:\n'
            '  actual=target(*args)\n'
            '  assert type(actual) is type(expected) and actual == expected\n')
        protected = {**files, 'evaluation_cases.py': fixture}
        with tempfile.TemporaryDirectory(prefix='gamma-evaluation-') as directory:
            root=Path(directory)
            for name,source in protected.items():
                path=root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(source,encoding='utf-8')
            spec=type(task.checks[0])('holdout','feature','evaluation_cases')
            evidence=check(root,spec)
            if any(p.is_symlink() for p in root.rglob('*')):
                raise ValueError('modified evaluation')
            actual={p.relative_to(root).as_posix():p.read_text(encoding='utf-8') for p in root.rglob('*.py')}
            if actual != protected: raise ValueError('modified evaluation')
        if not isinstance(evidence,dict) or evidence.get('infrastructure_error') is True:
            return result
        valid = evidence.get('name') == spec.name and evidence.get('kind') == spec.kind
        passed = valid and evidence.get('passed') is True and type(evidence.get('exit_code')) is int and evidence['exit_code']==0
        result.update(status='passed' if passed else 'failed',passed=bool(passed),reason='cases_passed' if passed else 'cases_failed')
    except Exception:
        pass
    return result
