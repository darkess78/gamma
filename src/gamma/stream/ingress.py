"""Durable receipts around the shared brain queue, without replaying side effects."""
from concurrent.futures import CancelledError
import hashlib
import json
import os
import re
import socket
import threading

import psutil

from .ingress_store import IngressStore


class StreamIngress:
    def __init__(self, path):
        self.store = IngressStore(path)
        self.owner = json.dumps([socket.gethostname(), os.getpid(), psutil.Process().create_time()])
        self._pending = {}
        self._lock = threading.Lock()

    def reconcile(self):
        for owner in self.store.active_owners():
            host, pid, birth = json.loads(owner)
            if host != socket.gethostname():
                continue
            try:
                dead = psutil.Process(pid).create_time() != birth
            except psutil.NoSuchProcess:
                dead = True
            except psutil.AccessDenied:
                dead = False
            if dead:
                self.store.orphan(owner)

    def receipt(self, job_id):
        self.reconcile()
        receipt = self.store.get(job_id)
        if receipt:
            receipt.pop('owner', None)
            receipt['schema_version'] = 1
        return receipt

    def submit(self, event, *, speech, fast, submit):
        payload = event.model_dump(mode='json')
        # Transport timestamps and request correlation do not change event identity.
        payload.pop('occurred_at', None)
        payload['metadata'].pop('request_id', None)
        encoded = json.dumps([payload, speech, fast], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 65536 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event.event_id) or event.event_id == "status":
            raise ValueError('stream event exceeds ingress bounds')
        digest = hashlib.sha256(encoded).hexdigest()
        self.reconcile()
        receipt, fresh = self.store.claim(event.event_id, digest, self.owner)
        if not fresh:
            return {**self.receipt(event.event_id), 'duplicate': True}

        def execute(function):
            if not self.store.transition(event.event_id, self.owner, 'running'):
                raise RuntimeError('ingress ownership lost')
            return function()

        try:
            future = submit(execute)
        except BaseException:
            self.store.transition(event.event_id, self.owner, 'failed', 'queue_unavailable')
            raise
        with self._lock:
            self._pending[event.event_id] = future

        def finish(completed, *, delivery=False):
            terminal = True
            try:
                result = completed.result()
                if not delivery:
                    result.add_done_callback(lambda done: finish(done, delivery=True))
                    terminal = False
                    return
                self.store.transition(event.event_id, self.owner, 'completed')
            except CancelledError:
                # Only a decision still waiting in the queue can be cancelled safely.
                state = 'cancelled' if not delivery and completed.cancelled() else 'failed'
                self.store.transition(event.event_id, self.owner, state, 'delivery_cancelled' if delivery else 'queue_cancelled')
            except BaseException:
                self.store.transition(event.event_id, self.owner, 'failed', 'execution_failed')
            finally:
                if terminal:
                    with self._lock:
                        self._pending.pop(event.event_id, None)
        future.add_done_callback(finish)
        return {**self.receipt(event.event_id), 'duplicate': False}

    def cancel(self, job_id):
        with self._lock:
            future = self._pending.get(job_id)
        cancelled = future.cancel() if future is not None else False
        return {'cancelled': cancelled, 'receipt': self.receipt(job_id)}
