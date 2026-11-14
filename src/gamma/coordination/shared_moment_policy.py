"""Reviewed worker guard, with strict missing-field and deadline validation."""
import math

from ..errors import GammaError


BINDINGS = ('generation', 'owner', 'sequence', 'source_epoch', 'source_id', 'kind',
            'goal_source_id', 'goal', 'mode', 'label')
FLAGS = ('enabled', 'awake', 'sources_live', 'source_available', 'deadline_active', 'foreground_active')


class MomentInterrupted(GammaError):
    """Known pre-dispatch cancellation, distinct from an unknown provider result."""


def moment_guard(snapshot, current, now):
    """Historical purpose. Pixel changes never renew or revoke this authority."""
    if not isinstance(snapshot, dict) or not isinstance(current, dict):
        raise ValueError('invalid_moment_authority')
    if not set(BINDINGS) <= snapshot.keys() or not (set(BINDINGS) | set(FLAGS)) <= current.keys():
        raise ValueError('missing_moment_authority_fields')
    for number in (now, snapshot.get('deadline'), current.get('source_expires_at')):
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
            raise ValueError('invalid_moment_authority_time')
    if any(type(current[key]) is not bool for key in FLAGS):
        raise ValueError('invalid_moment_authority_flag')
    if any(snapshot[key] != current[key] for key in BINDINGS):
        return 'context_changed'
    if now >= snapshot['deadline'] or not current['deadline_active']:
        return 'processing_deadline'
    if now >= current['source_expires_at']:
        return 'source_expired'
    if not all(current[key] for key in ('enabled', 'awake', 'sources_live', 'source_available')):
        return 'source_or_owner_unavailable'
    if current['foreground_active']:
        return 'foreground_active'
    return None
