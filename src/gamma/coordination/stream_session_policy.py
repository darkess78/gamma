"""Mechanical rolling budget, separate from Shana's contribution judgment."""
import math


def _number(value, *, minimum=0, maximum=None, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < minimum
        or positive and value <= minimum or maximum is not None and value > maximum):
        raise ValueError('invalid_stream_budget_number')
    return value


def speech_budget(records, *, now, proposed_seconds, minimum_gap_seconds=8,
                  maximum_seconds_per_minute=35):
    """Return a resource guard, never an instruction to speak.

    Durations are conservative estimates until a real audio adapter supplies
    measured playback. A receipt is a consumer report, not proof of hearing.
    """
    _number(now)
    _number(proposed_seconds, maximum=60, positive=True)
    _number(minimum_gap_seconds, maximum=60, positive=True)
    _number(maximum_seconds_per_minute, maximum=60, positive=True)
    if not isinstance(records, list) or len(records) > 128:
        raise ValueError('invalid_stream_budget_records')
    for record in records:
        if not isinstance(record, dict) or set(record) != {'started_at', 'duration_seconds'}:
            raise ValueError('invalid_stream_budget_record')
        _number(record['started_at'], maximum=now)
        _number(record['duration_seconds'], maximum=60, positive=True)
    recent = [record for record in records if record['started_at'] >= now - 60]
    if any(now - record['started_at'] < minimum_gap_seconds for record in recent):
        return 'minimum_gap'
    if sum(record['duration_seconds'] for record in recent) + proposed_seconds > maximum_seconds_per_minute:
        return 'speech_budget'
    return 'eligible'
