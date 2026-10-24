import time
from collections import OrderedDict
from threading import RLock
import math


class ObservationInbox:
    def __init__(self, capacity=32, clock=time.monotonic):
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be int")
        if capacity < 1 or capacity > 256:
            raise ValueError("capacity must be 1..256")
        self._capacity = capacity
        self._clock = clock
        self._lock = RLock()
        self._store = OrderedDict()
        self._coalesced = 0
        self._expired = 0
        self._dropped = 0

    def _validate(self, key, text, goal_id, ttl_seconds):
        if isinstance(key, bool) or not isinstance(key, str):
            raise TypeError("key must be str")
        if not (1 <= len(key) <= 80):
            raise ValueError("key must be 1..80 chars")
        if isinstance(text, bool) or not isinstance(text, str):
            raise TypeError("text must be str")
        if not (1 <= len(text) <= 2000):
            raise ValueError("text must be 1..2000 chars")
        if isinstance(goal_id, bool) or not isinstance(goal_id, int):
            raise TypeError("goal_id must be int")
        if goal_id < 1:
            raise ValueError("goal_id must be positive int")
        if isinstance(ttl_seconds, bool):
            raise TypeError("ttl_seconds must be number")
        if not isinstance(ttl_seconds, (int, float)):
            raise TypeError("ttl_seconds must be number")
        if not math.isfinite(ttl_seconds):
            raise ValueError("ttl_seconds must be finite")
        if not (1.0 <= ttl_seconds <= 3600.0):
            raise ValueError("ttl_seconds must be 1..3600")

    def _prune(self):
        now = self._clock()
        to_del = [k for k, v in self._store.items() if v['expires_at'] <= now]
        for k in to_del:
            del self._store[k]
            self._expired += 1
        return len(to_del)

    def offer(self, *, key, text, goal_id, ttl_seconds=120):
        self._validate(key, text, goal_id, ttl_seconds)
        with self._lock:
            self._prune()
            now = self._clock()
            expires_at = now + ttl_seconds
            ident = (goal_id, key)
            if ident in self._store:
                self._store[ident]['text'] = text
                self._store[ident]['expires_at'] = expires_at
                self._store.move_to_end(ident)
                self._coalesced += 1
                return {"accepted": True, **self._status()}
            if len(self._store) >= self._capacity:
                self._store.popitem(last=False)
                self._dropped += 1
            self._store[ident] = {'key': key, 'text': text, 'goal_id': goal_id, 'expires_at': expires_at}
            return {"accepted": True, **self._status()}

    def take(self, goal_id):
        with self._lock:
            self._prune()
            results = []
            to_del = []
            for ident, item in self._store.items():
                if item['goal_id'] == goal_id and len(results) < 4:
                    results.append({'key': item['key'], 'text': item['text'], 'goal_id': goal_id, 'expires_at': item['expires_at']})
                    to_del.append(ident)
            for ident in to_del:
                del self._store[ident]
            return results

    def status(self):
        with self._lock:
            self._prune()
            return self._status()

    def _status(self):
        return {
            'count': len(self._store),
            'capacity': self._capacity,
            'coalesced': self._coalesced,
            'expired': self._expired,
            'dropped': self._dropped,
            'overloaded': self._dropped > 0,
        }

    def clear(self):
        with self._lock:
            self._store.clear()
