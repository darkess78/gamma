import re

_HEX40 = re.compile(r'^[0-9a-f]{40}$')
_HEX64 = re.compile(r'^[0-9a-f]{64}$')

def _is_str(v):
    return isinstance(v, str) and len(v) > 0

def _valid_task(t):
    if not isinstance(t, dict):
        return False
    if not _is_str(t.get('id')):
        return False
    if not _is_str(t.get('fixture_sha256')) or not _HEX64.fullmatch(t['fixture_sha256']):
        return False
    if t.get('sandboxed') is not True:
        return False
    if t.get('infrastructure_error') is not False:
        return False
    if not isinstance(t.get('passed'), bool):
        return False
    return True

def _valid_report(r):
    if not isinstance(r, dict):
        return False
    if not _is_str(r.get('checkpoint')):
        return False
    if not _is_str(r.get('revision')) or not _HEX40.fullmatch(r['revision']):
        return False
    tasks = r.get('tasks')
    if not isinstance(tasks, list) or len(tasks) < 8:
        return False
    ids = []
    for t in tasks:
        if not _valid_task(t):
            return False
        ids.append(t['id'])
    if len(ids) != len(set(ids)):
        return False
    return True

def compare(baseline, candidate):
    try:
        if not _valid_report(baseline) or not _valid_report(candidate):
            return {'passed': False, 'reason': 'malformed report'}
        if baseline['checkpoint'] != candidate['checkpoint']:
            return {'passed': False, 'reason': 'checkpoint mismatch'}
        if baseline['revision'] != candidate['revision']:
            return {'passed': False, 'reason': 'revision mismatch'}
        if not _is_str(candidate.get('adapter_sha256')) or not _HEX64.fullmatch(candidate['adapter_sha256']):
            return {'passed': False, 'reason': 'invalid adapter_sha256'}
        bt, ct = baseline['tasks'], candidate['tasks']
        if len(bt) != len(ct):
            return {'passed': False, 'reason': 'task length mismatch'}
        for i in range(len(bt)):
            if bt[i]['id'] != ct[i]['id']:
                return {'passed': False, 'reason': 'task id mismatch'}
            if bt[i]['fixture_sha256'] != ct[i]['fixture_sha256']:
                return {'passed': False, 'reason': 'fixture mismatch'}
        gained = sum(1 for b, c in zip(bt, ct) if not b['passed'] and c['passed'])
        lost = sum(1 for b, c in zip(bt, ct) if b['passed'] and not c['passed'])
        if lost > 0:
            return {'passed': False, 'reason': 'regression detected'}
        if gained < 1:
            return {'passed': False, 'reason': 'no gain'}
        if sum(t['passed'] for t in ct) * 5 < len(ct) * 4:
            return {'passed': False, 'reason': 'candidate coding accuracy is below the 80 percent pilot floor'}
        return {'passed': True, 'reason': 'ok'}
    except Exception:
        return {'passed': False, 'reason': 'malformed input'}
