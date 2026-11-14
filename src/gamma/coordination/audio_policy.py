"""Conservative echo comparison, only used during acknowledged active playback."""
import re
import unicodedata

from ..voice.reply_chunking import split_reply_text


def likely_playback_echo(transcript, playback_text):
    if not isinstance(transcript, str) or not isinstance(playback_text, str):
        return False
    if not transcript.strip() or not playback_text.strip():
        return False
    if len(transcript) > 8000 or len(playback_text) > 8000:
        return False
    tokens = re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', transcript).casefold())
    playback = re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', playback_text).casefold())
    if len(tokens) < 3 or sum(map(len, tokens)) < 12:
        return False
    return any(playback[i:i + len(tokens)] == tokens for i in range(len(playback) - len(tokens) + 1))


def speech_chunks(text):
    """Split only accepted speech, preserving every normalized word in <=4 chunks."""
    normalized = ' '.join(text.split())
    if not normalized:
        return []
    if len(normalized) > 1600:
        raise ValueError('private_speech_text_limit')
    chunks = split_reply_text(normalized, max_chunks=4)
    if len(chunks[0]) > 160:
        words = normalized.split()
        first = []
        while words and len(' '.join([*first, words[0]])) <= 160:
            first.append(words.pop(0))
        if not first:
            raise ValueError('private_speech_first_chunk_limit')
        chunks = [' '.join(first)] + split_reply_text(' '.join(words), max_chunks=3)
    if len(chunks) > 4 or ' '.join(chunks) != normalized:
        raise ValueError('private_speech_chunk_integrity')
    return chunks
