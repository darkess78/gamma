"""The local model a coding job talks to: background priority, owner controls honoured.

Each call:

- waits at ``job.checkpoint()`` (pause, stop, time budget) before anything is sent;
- waits until Ollama has no model loaded, so it never competes with Shana's
  conversation or other GPU work (the old idle rule, unchanged);
- takes the shared backend slot as a background caller, so a waiting
  conversation turn always goes first (``llm.backend_admission``);
- unloads the model afterwards (``keep_alive: 0``).

Nothing is retried after it was sent: a transport failure stops the job
rather than risk a duplicate generation.
"""
from __future__ import annotations

import time

from .learner import LocalPracticeLearner

PROMPT_LIMIT = 40000  # bytes of system + user text
CONTEXT_TOKENS = 16384
REPLY_TOKENS = 3072


class BackgroundModel:
    def __init__(self, endpoint: str, model: str, job) -> None:
        self.endpoint, self.model, self.job = endpoint, model, job
        LocalPracticeLearner(endpoint=endpoint, model=model)  # validates a local endpoint and model name

    def generate_reply(self, *, system_prompt: str, user_text: str) -> str:
        job = self.job
        job.checkpoint()
        if len((system_prompt + user_text).encode()) > PROMPT_LIMIT:
            raise ValueError('Model prompt exceeds the supported budget')
        client = LocalPracticeLearner(endpoint=self.endpoint, model=self.model, timeout_seconds=120)

        def ready() -> bool:
            # Runs while holding the backend slot; a paused owner must not keep it.
            if not job.ready():
                return False
            try:
                client.require_idle()
            except RuntimeError:
                return False
            return job.ready()

        client.checkpoint = job.checkpoint
        client.admission_ready = ready
        stage, waiting = job.record.get('stage'), False
        while True:
            job.checkpoint()
            try:
                client.require_idle()
                break
            except RuntimeError:
                if not waiting:
                    job.event('waiting_for_gpu', 'Waiting until no model is loaded')
                    waiting = True
                end = time.monotonic() + 2
                while time.monotonic() < end:
                    job.checkpoint()
                    time.sleep(0.1)
        if waiting:
            job.event(stage or 'working', 'The GPU is free; continuing')
        job.checkpoint()
        response = client._json('/api/chat', {
            'model': self.model, 'stream': False, 'format': 'json', 'keep_alive': 0,
            'think': 'low' if self.model.startswith('gpt-oss') else False,
            'options': {'num_ctx': CONTEXT_TOKENS, 'num_predict': REPLY_TOKENS, 'temperature': 0.1},
            'messages': [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_text}]})
        job.record['last_transport'] = {k: response.get(k) for k in ('done', 'done_reason', 'eval_count', 'total_duration')}
        job.record['model_calls'] = job.record.get('model_calls', 0) + 1
        job.save()
        if response.get('done') is not True or response.get('done_reason') == 'length':
            raise ValueError('Incomplete local model response')
        content = (response.get('message') or {}).get('content')
        if not isinstance(content, str):
            raise ValueError('Invalid model response')
        return content

