"""Private claim provenance supplied by resolved ingress, never model payloads."""
from dataclasses import dataclass
import json


@dataclass(frozen=True, slots=True)
class MemoryOrigin:
    reporter_name: str | None = None
    is_owner: bool = False
    source: str = 'unknown'
    resolved_via: str = 'unknown'
    privacy_scope: str = 'local_generic'
    session_id: str | None = None
    turn_id: str | None = None
    write_allowed: bool = True

    @classmethod
    def from_speaker(cls, speaker, *, source='unknown', privacy_scope='local_generic', session_id=None, turn_id=None):
        return cls(reporter_name=speaker.name if speaker is not None else None,
                   is_owner=speaker is not None and speaker.is_owner is True and speaker.trust == 'owner',
                   source=source, resolved_via=speaker.resolved_via if speaker is not None else 'unknown',
                   privacy_scope=privacy_scope, session_id=session_id, turn_id=turn_id,
                   write_allowed=speaker is None or speaker.memory_write_allowed)


def decode(value):
    try:
        parsed = json.loads(value) if value else {'kind': 'legacy'}
        return parsed if isinstance(parsed, dict) else {'kind': 'unknown'}
    except (ValueError, TypeError):
        return {'kind': 'unknown'}


def source_key(value):
    data = decode(value)
    return tuple(data.get(k) for k in ('kind', 'reporter_name', 'is_owner', 'source', 'privacy_scope'))


def merge_origin(previous, current):
    old, new = decode(previous), decode(current)
    for field in ('session_id', 'turn_id'):
        if new.get(field) is not None:
            old['latest_' + field] = new[field]
    return json.dumps(old, sort_keys=True)


def manual_origin(previous):
    """Label edited content as manual while retaining the original private source."""
    old = decode(previous)
    data = {'kind': 'manual', 'source': 'manual'}
    for field in ('reporter_name', 'source', 'kind', 'privacy_scope', 'resolved_via', 'is_owner', 'session_id', 'turn_id'):
        original = old.get('original_' + field)
        if original is None:
            original = old.get(field)
        if original is not None:
            data['original_' + field] = original
    return json.dumps(data, sort_keys=True)


def describe_record(record):
    """Use only in an already-authorized private retrieval view."""
    data = decode(getattr(record, 'attribution', None))
    kind, reporter = data.get('kind'), data.get('reporter_name')
    if kind == 'self_report' and reporter:
        label = f'Self-reported by {reporter}; unverified'
    elif kind == 'reported' and reporter:
        label = f'Reported by {reporter}; unverified'
    elif kind == 'manual':
        label = 'Manual record; unverified'
    elif kind == 'tool':
        label = 'Tool record, unknown reporter; unverified'
    else:
        label = 'Legacy/unknown origin; unverified'
    text = getattr(record, 'fact_text', None) or getattr(record, 'summary', '')
    return f'[{label}] {text}'


def prepare_candidate(candidate, origin):
    if origin is not None and type(origin) is not MemoryOrigin:
        raise TypeError('Memory origin must be internal ingress metadata')
    trusted = origin if origin is not None and origin.reporter_name else None
    subject_type, subject_name, kind = bind_subject(candidate, trusted)
    data = {'kind': kind, 'source': origin.source if origin else 'unknown',
            'privacy_scope': origin.privacy_scope if origin else 'local_generic'}
    if origin:
        if origin.reporter_name:
            data.update(reporter_name=origin.reporter_name, is_owner=origin.is_owner, resolved_via=origin.resolved_via)
        elif origin.source in {'manual', 'tool'}:
            data['kind'] = origin.source
        if origin.session_id is not None:
            data['session_id'] = origin.session_id
        if origin.turn_id is not None:
            data['turn_id'] = origin.turn_id
    return candidate.model_copy(update={'subject_type': subject_type, 'subject_name': subject_name}), json.dumps(data, sort_keys=True)

def bind_subject(candidate, origin):
    c = candidate
    t = getattr(c, "subject_type", None)
    n = getattr(c, "subject_name", None)
    n = (" ".join(str(n).split())[:120] or None) if n is not None else None
    if origin is None:
        return (t, n, "unknown")
    if t == "primary_user" and (n is None or n.strip().casefold() == str(origin.reporter_name).strip().casefold()):
        if origin.is_owner:
            return ("primary_user", None, "self_report")
        return ("other_person", origin.reporter_name, "self_report")
    if t == "other_person" and n is not None and n.strip().casefold() == str(origin.reporter_name).strip().casefold():
        if origin.is_owner:
            return ("primary_user", None, "self_report")
        return ("other_person", origin.reporter_name, "self_report")
    if n is not None and str(n).strip():
        n = " ".join(str(n).split())
        n = n[:120]
    return (t, n, "reported")
