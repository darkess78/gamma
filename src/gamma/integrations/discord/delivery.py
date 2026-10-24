"""Discord reply gating and message sizing.

This module provides a dependency-free delivery gate and message chunking
utilities. It operates on immutable inputs and performs no I/O.
"""


def approved_reply(result):
    if not isinstance(result, dict):
        return None

    decision = result.get('decision')
    if not isinstance(decision, dict):
        return None

    decision_action = decision.get('decision')
    if decision_action not in ('reply', 'acknowledge'):
        return None

    safety = result.get('safety_decision')
    if not isinstance(safety, dict):
        return None
    if safety.get('action') != 'allow':
        return None
    if safety.get('blocked') is not False:
        return None
    if safety.get('playback_approved') is not True:
        return None
    if safety.get('review_pending'):
        return None

    assistant_response = result.get('assistant_response')
    if not isinstance(assistant_response, dict):
        return None

    response_action = assistant_response.get('response_action')
    if response_action not in ('reply', 'acknowledge'):
        return None
    if response_action != decision_action:
        return None
    if assistant_response.get('delivery_mode') not in ('speech', 'text_only'):
        return None

    text = assistant_response.get('display_text')
    if not isinstance(text, str):
        return None
    if len(text) == 0 or text.isspace():
        return None
    if len(text) > 16000:
        return None

    try:
        text.encode('utf-16-le')
    except UnicodeEncodeError:
        return None

    return text


def message_chunks(text, limit=2000):
    if not isinstance(text, str):
        raise ValueError("Invalid text input")

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Invalid limit input")
    if limit < 2 or limit > 2000:
        raise ValueError("Limit out of bounds")

    try:
        if len(text) > 16000:
            raise ValueError("Oversize input")
        text.encode('utf-16-le')
    except UnicodeEncodeError:
        raise ValueError("Invalid Unicode input")

    if text == '':
        return []

    chunks = []
    current_chunk = []
    current_len = 0

    for char in text:
        char_len = len(char.encode('utf-16-le')) // 2
        if current_len + char_len > limit:
            chunks.append(''.join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(char)
        current_len += char_len

    if current_chunk:
        chunks.append(''.join(current_chunk))

    return chunks
