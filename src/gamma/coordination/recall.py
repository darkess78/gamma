"""Small measured lexical recall with visibility checked before content access."""
import json
import math
import re
import unicodedata

_STOP = set('a an and are as at be but by can did do does for from had has have how i in is it its of on or that the this to was we what when where which who why you your our will would should could about am were with'.split())
_FIELDS = ('id', 'kind', 'topic', 'text', 'subject_id', 'scope', 'source_scope',
           'source_id', 'source_kind', 'certainty', 'confidence', 'updated_at')


def _tokens(text):
    if not isinstance(text, str):
        return set()
    words = re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text).casefold())
    return {w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w
            for w in words if w not in _STOP and len(w) > 1}


def _number(value, default=0):
    return float(value) if type(value) in {int, float} and math.isfinite(value) else default


def rank_records(records, query, *, audience='unbound', subject_id=None, limit=5, max_chars=4000):
    if type(limit) is not int or limit <= 0 or type(max_chars) is not int or max_chars < 2:
        return []
    wanted = _tokens(query)
    if not wanted:
        return []
    visible = []
    for row in records:
        if not isinstance(row, dict) or row.get('status') != 'active':
            continue
        if subject_id is not None and row.get('subject_id') != subject_id:
            continue
        allowed = {'public', 'owner_private'} if audience == 'owner_private' else {'public'}
        if row.get('scope') not in allowed or row.get('source_scope') not in allowed:
            continue
        topic, text = _tokens(row.get('topic')), _tokens(row.get('text'))
        if wanted & (topic | text):
            visible.append((row, topic, text))
    frequency = {word:sum(word in topic | text for _, topic, text in visible) for word in wanted}
    scored = []
    for row, topic, text in visible:
        score = sum((1.5 if word in topic else 1) * (1 + math.log((len(visible)+1)/(frequency[word]+1)))
                    for word in wanted & (topic | text))
        scored.append((score, row))
    scored.sort(key=lambda item:(-item[0], -_number(item[1].get('confidence')),
                                 -_number(item[1].get('updated_at')), str(item[1].get('id'))))
    result, seen = [], set()
    for _, row in scored:
        identifier = row.get('id')
        if not isinstance(identifier, str) or identifier in seen:
            continue
        item = {key:(row.get(key)[:120] if isinstance(row.get(key), str) else '')
                for key in _FIELDS if key not in {'text', 'confidence', 'updated_at'}}
        item['confidence'] = max(0, min(1, _number(row.get('confidence'), .5)))
        item['updated_at'] = _number(row.get('updated_at'))
        item['text'] = ''
        available = min(600, max_chars-len(json.dumps(result+[item], ensure_ascii=False)))
        if available < 12:
            continue
        text = row.get('text') if isinstance(row.get('text'), str) else ''
        start = min((text.casefold().find(word) for word in wanted if word in text.casefold()), default=0)
        start = max(0, start-80) if len(text) > available else 0
        item['text'] = ('...' if start else '') + text[start:start+available]
        if start+available < len(text):
            item['text'] += '...'
        while item['text'] and len(json.dumps(result+[item], ensure_ascii=False)) > max_chars:
            item['text'] = item['text'][:-1]
        if len(json.dumps(result+[item], ensure_ascii=False)) > max_chars:
            continue
        result.append(item); seen.add(identifier)
        if len(result) >= min(limit, 8):
            break
    return result
