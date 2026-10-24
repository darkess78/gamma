import re

_WORD_MAP = {'7': 'seven'}


def _normalize(text):
    if not text:
        return []
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    return [_WORD_MAP.get(t, t) for t in tokens]


def word_error_rate(reference, hypothesis):
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    n, m = len(ref), len(hyp)
    if n == 0:
        return float(m)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[m] / max(1, n)
