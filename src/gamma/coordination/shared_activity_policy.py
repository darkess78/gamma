"""Finite selected-source observations, not game truth or action authority."""
import math
import re


KINDS = {'osrs_screen', 'minecraft_state', 'selected_application'}


def bounded_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('invalid_shared_identifier')
    return value


def finite_number(value, *, minimum=0, maximum=None):
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and value >= minimum and (maximum is None or value <= maximum))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('invalid_shared_number')
    return value


def bounded_text(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum
            or any(ord(char) < 32 and char not in '\n\r\t' for char in value)):
        raise ValueError('invalid_shared_text')
    value = ' '.join(value.split())
    if not value and not empty:
        raise ValueError('invalid_shared_text')
    return value


def normalize_observation(payload, *, source_id, source_epoch, source_kind, now, maximum_age_seconds=12):
    keys = {'observation_id', 'source_id', 'source_epoch', 'sequence', 'captured_at',
            'kind', 'summary', 'confidence', 'content_sha256'}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError('invalid_shared_observation_fields')
    row = dict(payload)
    for key in ('observation_id', 'source_id'):
        bounded_identifier(row[key])
    for key in ('source_epoch', 'sequence'):
        if type(row[key]) is not int or row[key] < 0:
            raise ValueError('invalid_shared_sequence')
    if (row['source_id'] != source_id or row['source_epoch'] != source_epoch
            or row['kind'] != source_kind or row['kind'] not in KINDS):
        raise ValueError('shared_source_changed')
    # The small freshness helper below is independently worker-reviewed.
    fresh_timestamp(row['captured_at'], now=now, maximum_age_seconds=maximum_age_seconds)
    finite_number(row['confidence'], maximum=1)
    if not isinstance(row['content_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', row['content_sha256']):
        raise ValueError('invalid_shared_content_hash')
    row['summary'] = bounded_text(row['summary'], 600)
    return row


def fresh_timestamp(value, *, now, maximum_age_seconds=12):
    """Reviewed local-worker result; boundaries remain inclusive."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('value must be a number')
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError('now must be a number')
    if isinstance(maximum_age_seconds, bool) or not isinstance(maximum_age_seconds, (int, float)):
        raise ValueError('maximum_age_seconds must be a number')
    if not (float('-inf') < value < float('inf')):
        raise ValueError('value must be finite')
    if not (float('-inf') < now < float('inf')):
        raise ValueError('now must be finite')
    if not (float('-inf') < maximum_age_seconds < float('inf')):
        raise ValueError('maximum_age_seconds must be finite')
    if value < 0 or now < 0:
        raise ValueError('shared timestamps must be nonnegative')
    if not 1 <= maximum_age_seconds <= 60:
        raise ValueError('maximum_age_seconds must be between 1 and 60')
    if value > now + 2 or value < now - maximum_age_seconds:
        raise ValueError('stale_shared_observation')
    return value
