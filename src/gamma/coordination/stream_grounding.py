"""Bound prior received words without promoting them to execution evidence."""
from copy import deepcopy
import hashlib
import json
import math
import re


def received_commentary(rows):
    """A text receipt establishes delivery, never the truth of its contents."""
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[-6:]:
        if not isinstance(row, dict):
            continue
        event_id, text, received_at = (row.get(key) for key in ('event_id', 'text', 'received_at'))
        if (not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', event_id)
                or not isinstance(text, str) or not text.strip()
                or type(received_at) not in (int, float) or received_at < 0
                or type(received_at) is float and not math.isfinite(received_at)):
            continue
        result.append({'event_id': event_id, 'text': text[:400], 'received_at': received_at,
            'source_kind': 'assistant_commentary', 'evidence_scope': 'consumer_reported_text_received_only',
            'execution_evidence': False,
            'text_is_partial': len(text) > 400 or row.get('text_is_partial') is True})
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('nonfinite_json_constant')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite_json_number')
    return number


def _structure_bound(value, depth_limit):
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 256 or depth > depth_limit:
            raise ValueError('reported_json_structure_bound')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def parse_receipt_array(text):
    """Reviewed worker parser: a complete JSON tail is data, never a receipt claim."""
    if not isinstance(text, str) or len(text) > 1200:
        return None
    start = text.find('[')
    if start < 0:
        return None
    try:
        rows = json.loads(text[start:], object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant, parse_float=_finite_float)
        _structure_bound(rows, 20)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return None
    if not isinstance(rows, list) or len(rows) > 4 or any(not isinstance(row, dict) for row in rows):
        return None
    return rows


def _bounded_output(value):
    _structure_bound(value, 12)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 2048:
        raise ValueError('reported_output_bound')
    return deepcopy(value)


def current_work_observations(event):
    """Expose literal current caller data separately from earlier assistant words.

    Parsing a complete supplied array cannot establish that an action happened,
    its report is accurate, an artifact is correct, or a status label is trusted.
    Unsupported shape stays unknown rather than becoming a claim of absence.
    """
    result = {'source_kind': 'caller_supplied_current_event_data', 'execution_evidence': False,
              'parse_status': 'not_projected', 'records': []}
    if not isinstance(event, dict) or event.get('kind') != 'activity':
        return result
    text = event.get('text')
    rows = parse_receipt_array(text)
    if rows is None:
        return result
    selected = []
    try:
        for row in rows:
            task, status, sha = (row.get(key) for key in ('task', 'status', 'source_sha256'))
            if (not isinstance(task, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', task)
                    or not isinstance(status, str) or not status.strip() or len(status) > 40
                    or not isinstance(sha, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', sha)):
                return result
            probes = row.get('independent_probes')
            if not isinstance(probes, list) or len(probes) > 8:
                return result
            checked = {}
            for key in ('protected_checks_passed', 'protected_checks_total'):
                if key in row:
                    if type(row[key]) is not int or not 0 <= row[key] <= 10000:
                        return result
                    checked[key] = row[key]
            reported = []
            for probe in probes:
                if (not isinstance(probe, dict) or not isinstance(probe.get('case'), str)
                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', probe['case'])):
                    return result
                present = 'observed_output' in probe
                value = {'case': probe['case'], 'output_field_present': present}
                if present:
                    value['reported_output'] = _bounded_output(probe['observed_output'])
                reported.append(value)
            selected.append({'reported_task': task, 'reported_status': status, 'reported_artifact_sha256': sha,
                             'reported_checks': checked, 'reported_probes': reported})
        return {**result, 'parse_status': 'complete_supported_json_array', 'records': selected,
                'event_text_sha256': hashlib.sha256(text.encode()).hexdigest()}
    except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError):
        return result
