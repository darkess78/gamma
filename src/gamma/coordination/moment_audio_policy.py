"""Reviewed worker canonical-text guard; no new text or audio authority."""
import hashlib


class MomentSpeechLimit(ValueError):
    """Checked display text stays available, but it cannot be spoken whole."""


def checked_moment_text(review, moment):
    if not isinstance(review, dict):
        raise ValueError('review must be a dict')
    if not isinstance(moment, dict):
        raise ValueError('moment must be a dict')

    if review.get('channel') != 'shared_activity':
        raise ValueError('review channel must be shared_activity')
    if review.get('status') != 'completed':
        raise ValueError('review status must be completed')

    response = review.get('response')
    if not isinstance(response, dict):
        raise ValueError('response must be a dict')
    if response.get('response_action') not in ('reply', 'acknowledge'):
        raise ValueError('response action must be reply or acknowledge')
    if response.get('delivery_mode') != 'text_only':
        raise ValueError('delivery mode must be text_only')

    if any(response.get(key) not in (None, '') for key in ('speech_text', 'audio_path')):
        raise ValueError('checked text must not contain audio authority')
    display_text = response.get('display_text')
    if not isinstance(display_text, str):
        raise ValueError('display_text must be a string')
    if display_text != display_text.strip():
        raise ValueError('display_text must equal its strip')
    if len(display_text) == 0:
        raise ValueError('display_text must be nonempty')
    if len(display_text) > 1600:
        raise MomentSpeechLimit('moment_speech_text_limit')

    if moment.get('status') != 'generated':
        raise ValueError('moment status must be generated')
    if moment.get('redacted'):
        raise ValueError('moment must not be redacted')
    if moment.get('own_text_redacted'):
        raise ValueError('moment must not be own_text_redacted')

    result = moment.get('result')
    if not isinstance(result, dict):
        raise ValueError('result must be a dict')

    text = result.get('text')
    if not isinstance(text, str):
        raise ValueError('result.text must be a string')
    if text != display_text:
        raise ValueError('result.text must equal display_text')

    sha = result.get('text_sha256')
    if not isinstance(sha, str):
        raise ValueError('text_sha256 must be a string')
    expected = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if sha != expected:
        raise ValueError('text_sha256 mismatch')

    return display_text
