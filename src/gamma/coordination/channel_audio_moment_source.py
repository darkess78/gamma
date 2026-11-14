"""Sealed joins for already checked historical moments, using P5 audio transport."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .personal import digest
from .shared_moment_policy import BINDINGS
from .shared_moments import MOMENT, checked_review
from .store import Store


class MomentAudioOrigin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['shared_moment']
    moment_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    review_id: str = Field(pattern=r'^moment-review-[a-f0-9]{32}$')
    review_context_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    response_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    text_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    moment_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    owner: str = Field(pattern=r'^[a-f0-9]{32}$')
    generation: int = Field(ge=1, strict=True)
    sequence: int = Field(ge=0, strict=True)
    consumer_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    consumer_lease: str = Field(pattern=r'^[a-f0-9]{32}$')
    consent_epoch: int = Field(ge=0, strict=True)
    output_epoch: int = Field(ge=0, strict=True)
    created_at: float = Field(ge=0, allow_inf_nan=False, strict=True)
    expires_at: float = Field(ge=0, allow_inf_nan=False, strict=True)


def request_id(moment_id):
    return 'moment-audio-' + digest(moment_id)[:32]


def moment_fingerprint(moment):
    return digest({key: moment[key] for key in ('fingerprint', 'snapshot', 'capture', 'source_ids',
                                              'watch_id', 'watch_lease')})


def checked_moment_records(store, db, moment_id, token, now, *, admitting=False):
    from .moment_audio_policy import checked_moment_text
    moment = store.get(db, MOMENT, moment_id)
    if not moment or not checked_review(store, db, moment):
        raise ValueError('checked_moment_unavailable')
    review = store.get(db, 'channel_review', moment['review_id'])
    text = checked_moment_text(review, moment)
    snapshot, capture = moment['snapshot'], moment['capture']
    meta, brain = store.get(db, 'shared_meta', 'state'), store.get(db, 'brain', 'state')
    current = {'generation': token[0], 'owner': token[1], 'sequence': brain['sequence'],
               **{key: meta[key] for key in BINDINGS if key not in {'generation', 'owner', 'sequence'}}}
    if (not meta['enabled'] or not brain['awake'] or any(snapshot[key] != current[key] for key in BINDINGS)
            or now >= meta['expires_at'] or now >= review['expires_at']
            or not capture or capture['content_sha256'] != (store.get(db, 'personal_source', moment['personal_source_id']) or {}).get('image_sha256')
            or not moment['source_ids'] or moment['personal_source_id'] not in moment['source_ids']
            or any((store.get(db, 'personal_source', sid) or {}).get('status') not in {'active', 'administrative'} for sid in moment['source_ids'])
            or any(turn['status'] == 'pending' for turn in store.list(db, 'turn', None))):
        raise ValueError('checked_moment_source_stale')
    watch = None
    if moment.get('watch_id'):
        watch = store.get(db, 'shared_watch', moment['watch_id'])
        final = watch and watch['status'] == 'exhausted' and watch.get('last_completed') == moment_id
        if (not watch or (watch['status'] != 'active' and not final)
                or watch['lease'] != moment['watch_lease'] or watch['generation'] != token[0]
                or watch['source_epoch'] != snapshot['source_epoch'] or watch['source_id'] != snapshot['source_id']
                or now >= watch['expires_at']
                or (admitting or not final) and now >= watch['availability_expires_at']):
            raise ValueError('checked_moment_watch_stale')
    return moment, review, text, meta, watch


def claim_moment(audio, db, moment_id, consumer_id):
    interaction, store, now = audio.interaction, audio.store, float(audio.clock())
    consumer = interaction._consumer(db, consumer_id, touch=True)
    consent = store.get(db, 'channel_audio_consent', consumer_id)
    if (audio._closed or not consent or consent['enabled'] is not True
            or consent['consumer_lease'] != consumer['lease'] or consent['generation'] != audio.core.token[0]):
        raise ValueError('channel_audio_listen_not_enabled')
    moment, review, text, meta, watch = checked_moment_records(store, db, moment_id, audio.core.token, now, admitting=True)
    from .audio_policy import speech_chunks
    try:
        speech_chunks(text)
    except ValueError as exc:
        from .moment_audio_policy import MomentSpeechLimit
        raise MomentSpeechLimit('moment_speech_chunk_limit') from exc
    # The actual service's wall and monotonic admission guard is mandatory.
    service = getattr(getattr(audio.core, 'shared_activity', None), 'moments', None)
    from .shared_moments import SharedMoments
    if not isinstance(service, SharedMoments) or service._guard(db, moment):
        raise ValueError('checked_moment_acceptance_closed')
    receipt = moment.get('audio_text_receipt') or {}
    if (receipt.get('consumer_id') != consumer_id or receipt.get('consumer_lease') != consumer['lease']
            or receipt.get('generation') != audio.core.token[0]
            or receipt.get('text_sha256') != moment['result']['text_sha256']):
        raise ValueError('checked_moment_same_consumer_text_receipt_required')
    deadline = min(now + 180, review['expires_at'], meta['expires_at'], watch['expires_at'] if watch else float('inf'))
    if now + 10 >= deadline:
        raise ValueError('checked_channel_delivery_window_too_short')
    origin = MomentAudioOrigin(kind='shared_moment', moment_id=moment_id, review_id=moment['review_id'],
        review_context_sha256=review['context_sha256'], response_sha256=digest(review['response']),
        text_sha256=digest(text), moment_sha256=moment_fingerprint(moment), owner=audio.core.token[1],
        generation=audio.core.token[0], sequence=moment['snapshot']['sequence'], consumer_id=consumer_id,
        consumer_lease=consumer['lease'], consent_epoch=consent['epoch'], output_epoch=interaction._meta(db)['output_epoch'],
        created_at=now, expires_at=float(deadline))
    rid = request_id(moment_id)
    moment['audio_request_id'] = rid
    store.put(db, MOMENT, moment_id, moment)
    row = {'id': rid, 'origin': origin.model_dump(), 'status': 'rendering',
           'fingerprint': digest(origin.model_dump()), 'reason': 'canonical_checked_moment_audio'}
    store.put(db, 'channel_audio_request', rid, row)
    store.put(db, 'interaction_request', rid, {'id': rid, 'fingerprint': row['fingerprint'], 'receipt': None,
        'source_kind': 'checked_channel', 'created_at': now, 'expires_at': deadline, 'output_ids': []})
    interaction_meta = interaction._meta(db)
    interaction_meta['synthesizing_request_id'] = rid
    store.put(db, 'interaction_meta', 'state', interaction_meta)
    return row, {**review['response'], 'display_text': text, 'speech_text': text, 'delivery_mode': 'speech'}


def current_moment_source(store, db, output, token, now):
    rid = output.get('request_id', output.get('id'))
    row = store.get(db, 'channel_audio_request', rid)
    try:
        if not row or row.get('status') not in {'rendering', 'generated'}:
            return False
        origin = MomentAudioOrigin.model_validate(row['origin'])
        moment, review, text, meta, watch = checked_moment_records(store, db, origin.moment_id, token, now)
        consumer = store.get(db, 'interaction_consumer', origin.consumer_id)
        consent = store.get(db, 'channel_audio_consent', origin.consumer_id)
        receipt = moment.get('audio_text_receipt') or {}
        audio_meta = store.get(db, 'interaction_meta', 'state')
        return (moment.get('audio_request_id') == rid and request_id(moment['id']) == rid
            and row['fingerprint'] == digest(origin.model_dump())
            and moment_fingerprint(moment) == origin.moment_sha256
            and output.get('channel', 'shared_moment') == 'shared_moment'
            and output.get('consumer_id', origin.consumer_id) == origin.consumer_id
            and output.get('consumer_lease', origin.consumer_lease) == origin.consumer_lease
            and output.get('generation', origin.generation) == origin.generation
            and output.get('sequence', origin.sequence) == origin.sequence
            and output.get('output_epoch', origin.output_epoch) == origin.output_epoch
            and origin.owner == token[1] and origin.generation == token[0]
            and origin.sequence == moment['snapshot']['sequence'] and review['id'] == origin.review_id
            and review['context_sha256'] == origin.review_context_sha256
            and digest(review['response']) == origin.response_sha256 and digest(text) == origin.text_sha256
            and origin.created_at < origin.expires_at <= min(origin.created_at + 180, review['expires_at'], meta['expires_at'],
                watch['expires_at'] if watch else float('inf'))
            and now < origin.expires_at
            and receipt.get('consumer_id') == origin.consumer_id and receipt.get('consumer_lease') == origin.consumer_lease
            and receipt.get('generation') == token[0] and receipt.get('text_sha256') == moment['result']['text_sha256']
            and consumer is not None and consumer.get('connected') is True and consumer.get('generation') == token[0]
            and consumer.get('lease') == origin.consumer_lease and now < consumer['expires_at']
            and consent is not None and consent.get('enabled') is True and consent.get('generation') == token[0]
            and consent.get('consumer_lease') == origin.consumer_lease and consent.get('epoch') == origin.consent_epoch
            and audio_meta is not None and audio_meta['output_epoch'] == origin.output_epoch)
    except (ValueError, KeyError, TypeError):
        return False


def accepted_audio_deadline(store, db, moment, now):
    rid = moment.get('audio_request_id')
    if not rid:
        return None
    request = store.get(db, 'channel_audio_request', rid)
    origin = (request or {}).get('origin') or {}
    brain = store.get(db, 'brain', 'state') or {}
    token = (brain.get('generation'), brain.get('owner'))
    if current_moment_source(store, db, {'request_id': rid}, token, now):
        return origin['expires_at']
    return None


def revoke_selection_audio(db, meta=None, *, watch_id=None):
    """Eager cleanup on explicit source/goal/mode/watch controls; no new authority."""
    reviews = set()
    for request in Store.list(db, 'channel_audio_request', None):
        origin = request.get('origin') or {}
        if origin.get('kind') != 'shared_moment':
            continue
        moment = Store.get(db, MOMENT, origin.get('moment_id'))
        snapshot = (moment or {}).get('snapshot') or {}
        revoked = watch_id is not None and (moment or {}).get('watch_id') == watch_id
        if meta is not None:
            revoked = revoked or not meta['enabled'] or any(snapshot.get(key) != meta[key] for key in BINDINGS
                            if key not in {'generation', 'owner', 'sequence'})
        if revoked:
            reviews.add(origin.get('review_id'))
    if reviews:
        from .channel_audio_source import redact_removed_reviews
        from pathlib import Path
        from types import SimpleNamespace
        database = next(item[2] for item in db.execute('PRAGMA database_list') if item[1] == 'main')
        store = SimpleNamespace(root=Path(database).parent, list=Store.list, put=Store.put, get=Store.get)
        redact_removed_reviews(store, db, reviews, None)
