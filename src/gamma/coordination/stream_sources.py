"""Persistent checked-review bindings and dependent transport erasure."""
import copy
import re

from .channel_audio_policy import checked_channel_text


def output_review(store, db, output):
    """Resolve legacy rows by their actual event, never an arbitrary review ID."""
    event = store.get(db, 'stream_rehearsal_event', output.get('event_id'))
    if (not event or event.get('session_id') != output.get('session_id')
        or (event.get('result') or {}).get('output_id') != output.get('id')
        or not re.fullmatch(r'[a-f0-9]{64}', event.get('fingerprint', ''))):
        return None
    review_id = 'stream-review-' + event['fingerprint'][:32]
    if output.get('review_id', review_id) != review_id:
        return None
    return store.get(db, 'channel_review', review_id)


def output_payload_valid(store, db, output):
    if output.get('source_redacted'):
        return False
    try:
        checked_channel_text(output_review(store, db, output), output)
        return True
    except (ValueError, TypeError):
        return False


def redact_output(row, now):
    """Reviewed worker helper: erase selected payload, preserve safe receipts."""
    out = copy.deepcopy(row)
    out['text'] = ''
    out['source_redacted'] = True
    if 'redacted_at' not in out:
        out['redacted_at'] = now
    if out.get('status') in {'ready', 'released', 'started'}:
        out['status'] = 'cancelled'
        out['reason'] = 'source_removed_no_replay'
    return out


def redact_removed_reviews(store, db, now):
    """Extend existing channel-review erasure to its concrete dependent caches."""
    removed = {r['id'] for r in store.list(db, 'channel_review', None) if r.get('status') == 'redacted'}
    events = set()
    for output in store.list(db, 'stream_rehearsal_output', None):
        review = output_review(store, db, output)
        if review and review['id'] in removed:
            store.put(db, 'stream_rehearsal_output', output['id'], redact_output(output, now))
            events.add(output['event_id'])
    for event_id in events:
        event = store.get(db, 'stream_rehearsal_event', event_id)
        # Retain the output link and fingerprint as a no-replay tombstone.
        event['result'].update(status='cancelled', reason='source_removed_no_replay')
        store.put(db, 'stream_rehearsal_event', event_id, event)
    for key, raw in list(db.execute('SELECT id,body FROM records WHERE kind=?', ('stream_control',))):
        import json
        row = json.loads(raw)
        result = row.get('result') or {}
        caught = result.get('catch_up')
        if isinstance(caught, list):
            result['catch_up'] = [item for item in caught if item.get('event_id') not in events]
            if result['catch_up'] != caught:
                store.put(db, 'stream_control', key, row)
    from .channel_audio_source import redact_removed_reviews as redact_audio
    redact_audio(store, db, removed, now)
