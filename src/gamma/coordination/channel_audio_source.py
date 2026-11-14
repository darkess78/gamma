"""Hardcoded backend joins for checked stream audio, never caller trust hooks."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .channel_audio_policy import checked_channel_text
from .personal import digest


class ChannelAudioOrigin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['private_stream']
    stream_output_id: str = Field(pattern=r'^stream-output-[a-f0-9]{32}$')
    event_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    session_id: str = Field(pattern=r'^stream-[a-f0-9]{32}$')
    review_id: str = Field(pattern=r'^stream-review-[a-f0-9]{32}$')
    review_context_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    response_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    text_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    generation: int = Field(ge=1, strict=True)
    sequence: int = Field(ge=0, strict=True)
    revision: int = Field(ge=0, strict=True)
    stream_lease: str = Field(pattern=r'^[a-f0-9]{32}$')
    consumer_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    consumer_lease: str = Field(pattern=r'^[a-f0-9]{32}$')
    consent_epoch: int = Field(ge=0, strict=True)
    output_epoch: int = Field(ge=0, strict=True)
    created_at: float = Field(ge=0, allow_inf_nan=False, strict=True)
    expires_at: float = Field(ge=0, allow_inf_nan=False, strict=True)


def checked_stream_records(store, db, stream_output_id, consumer_id, token, now):
    """Resolve the exact checked decision which produced an actual stream row."""
    output = store.get(db, 'stream_rehearsal_output', stream_output_id)
    if not output or output.get('consumer_id') != consumer_id:
        raise ValueError('checked_stream_output_unavailable')
    session = store.get(db, 'stream_session', output['session_id'])
    event = store.get(db, 'stream_rehearsal_event', output['event_id'])
    if (not session or not event or event.get('session_id') != session['id']
        or (event.get('result') or {}).get('output_id') != output['id']
        or (event.get('result') or {}).get('status') != 'ready'):
        raise ValueError('checked_stream_event_mismatch')
    review_id = 'stream-review-' + event['fingerprint'][:32]
    review = store.get(db, 'channel_review', review_id)
    brain = store.get(db, 'brain', 'state')
    consumer = session.get('consumer')
    if (session.get('status') != 'active' or not brain['awake']
        or not consumer or consumer.get('id') != consumer_id or now >= consumer['expires_at']
        or output.get('generation') != token[0] or session.get('generation') != token[0]
        or output.get('sequence') != brain['sequence'] or output.get('revision') != session['revision']
        or output.get('lease') != consumer.get('lease')
        or output.get('status') in {'cancelled', 'failed', 'expired'}
        or now >= output['expires_at'] or now >= session['expires_at']
        or not review or review.get('generation') != token[0]
        or review.get('sequence') != brain['sequence'] or now >= review['expires_at']):
        raise ValueError('checked_stream_source_stale')
    text = checked_channel_text(review, output)
    return output, session, event, review_id, review, text


def parse_channel_origin(value):
    if isinstance(value, dict) and value.get('kind') == 'shared_moment':
        from .channel_audio_moment_source import MomentAudioOrigin
        return MomentAudioOrigin.model_validate(value)
    return ChannelAudioOrigin.model_validate(value)


def current_channel_source(store, db, output, token, now):
    """Validate the actual persistent origin after every chunk and on reads/ACK."""
    request_id = output.get('request_id', output.get('id'))
    row = store.get(db, 'channel_audio_request', request_id)
    if row and (row.get('origin') or {}).get('kind') == 'shared_moment':
        from .channel_audio_moment_source import current_moment_source
        return current_moment_source(store, db, output, token, now)
    try:
        if not row or row.get('status') not in {'rendering', 'generated'}:
            return False
        origin = ChannelAudioOrigin.model_validate(row['origin'])
        source, session, event, review_id, review, text = checked_stream_records(
            store, db, origin.stream_output_id, origin.consumer_id, token, now)
        consumer = store.get(db, 'interaction_consumer', origin.consumer_id)
        consent = store.get(db, 'channel_audio_consent', origin.consumer_id)
        meta = store.get(db, 'interaction_meta', 'state')
        return (source.get('audio_request_id') == request_id
            and output.get('consumer_id', origin.consumer_id) == origin.consumer_id
            and output.get('consumer_lease', origin.consumer_lease) == origin.consumer_lease
            and output.get('generation', origin.generation) == origin.generation
            and output.get('sequence', origin.sequence) == origin.sequence
            and output.get('output_epoch', origin.output_epoch) == origin.output_epoch
            and output.get('channel', 'private_stream') == 'private_stream'
            and source['event_id'] == origin.event_id and source['session_id'] == origin.session_id
            and review_id == origin.review_id and review['context_sha256'] == origin.review_context_sha256
            and digest(review['response']) == origin.response_sha256 and digest(text) == origin.text_sha256
            and origin.generation == token[0] and origin.sequence == source['sequence']
            and origin.revision == session['revision'] and origin.stream_lease == session['consumer']['lease']
            and now < origin.expires_at and consumer is not None and consumer.get('connected') is True
            and consumer.get('generation') == token[0] and consumer.get('lease') == origin.consumer_lease
            and now < consumer['expires_at'] and consent is not None and consent.get('enabled') is True
            and consent.get('generation') == token[0] and consent.get('epoch') == origin.consent_epoch
            and consent.get('consumer_lease') == origin.consumer_lease
            and meta is not None and meta['output_epoch'] == origin.output_epoch)
    except (ValueError, KeyError, TypeError):
        return False


def invalidate_snapshot(db):
    from .store import Store
    for row in Store.list(db, 'channel_audio_consent', None):
        row.update(enabled=False, epoch=row['epoch'] + 1, consumer_lease='', reason='snapshot_no_replay')
        Store.put(db, 'channel_audio_consent', row['id'], row)
    for row in Store.list(db, 'channel_audio_request', None):
        row.update(status='cancelled', reason='snapshot_no_replay')
        Store.put(db, 'channel_audio_request', row['id'], row)


def redact_removed_reviews(store, db, review_ids, now):
    """Immediately erase concrete checked-channel payloads and owned PCM files."""
    import re
    from .store import private_path
    requests = set()
    for row in store.list(db, 'channel_audio_request', None):
        try:
            origin = parse_channel_origin(row['origin'])
        except (KeyError, ValueError, TypeError):
            continue
        if origin.review_id in review_ids:
            row.update(status='cancelled', reason='source_removed_no_replay')
            if now is not None:
                row.setdefault('redacted_at', now)
            store.put(db, 'channel_audio_request', row['id'], row)
            requests.add(row['id'])
    for row in store.list(db, 'interaction_request', None):
        if row.get('source_kind') == 'checked_channel' and row['id'] in requests:
            row['receipt'] = {'ok': True, 'status': 'expired', 'reason': 'source_removed_no_replay', 'output_id': None}
            store.put(db, 'interaction_request', row['id'], row)
    for row in store.list(db, 'interaction_output', None):
        if row.get('source_kind') != 'checked_channel' or row.get('request_id') not in requests:
            continue
        row.update(display_text='', speech_text='', audio_available=False, source_redacted=True)
        if now is not None:
            row.setdefault('redacted_at', now)
        if row['status'] not in {'played', 'delivered', 'cancelled', 'failed', 'expired'}:
            row.update(status='cancelled', reason='source_removed_no_replay')
        store.put(db, 'interaction_output', row['id'], row)
        if not re.fullmatch(r'output-[a-f0-9]{32}', row['id']):
            raise ValueError('invalid_checked_audio_output_id')
        private_path(store.root / 'interaction-audio' / (row['id'] + '.wav')).unlink(missing_ok=True)
