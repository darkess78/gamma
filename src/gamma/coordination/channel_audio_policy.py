"""Reviewed local-worker guard for canonical checked channel text."""


def checked_channel_text(review, output):
    if not isinstance(review, dict):
        raise ValueError('review must be a dict')
    if review.get('channel') != 'private_stream':
        raise ValueError('channel must be private_stream')
    if review.get('status') != 'completed':
        raise ValueError('status must be completed')
    response = review.get('response')
    if not isinstance(response, dict):
        raise ValueError('response must be a dict')
    if response.get('response_action') not in ('reply', 'acknowledge'):
        raise ValueError('response_action must be reply or acknowledge')
    if response.get('delivery_mode') != 'text_only':
        raise ValueError('delivery_mode must be text_only')
    speech_text = response.get('speech_text')
    if speech_text is not None and speech_text != '':
        raise ValueError('speech_text must be absent, None, or empty')
    audio_path = response.get('audio_path')
    if audio_path is not None and audio_path != '':
        raise ValueError('audio_path must be absent, None, or empty')
    display_text = response.get('display_text')
    if not isinstance(display_text, str):
        raise ValueError('display_text must be a str')
    canonical = display_text.strip()
    if not canonical:
        raise ValueError('display_text must be nonempty after strip')
    if len(canonical) > 1600:
        raise ValueError('display_text canonical length must be <= 1600')
    if not isinstance(output, dict):
        raise ValueError('output must be a dict')
    if not isinstance(output.get('text'), str):
        raise ValueError('output.text must be a str')
    if output['text'] != canonical:
        raise ValueError('output.text must exactly equal canonical display text')
    return canonical
