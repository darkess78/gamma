"""Per-call serialization choice, sharing the same model and private Core."""
from contextlib import contextmanager
from contextvars import ContextVar

from ..llm.local_adapter import LocalLLMAdapter
from .turn_schema import private_turn_schema


class PrivateTurnAdapter(LocalLLMAdapter):
    def __init__(self, *, keep_alive: str | int | None = None):
        options={'think':False,'maximum_output_tokens':1024,'maximum_context_tokens':8192,'keep_alive':keep_alive}
        super().__init__(**options,json_schema=private_turn_schema())
        self._compact_adapter=LocalLLMAdapter(**options,json_schema=private_turn_schema(compact=True))
        self._compact=ContextVar('private_interaction_serialization',default=False)

    @contextmanager
    def interaction_turn(self,enabled):
        token=self._compact.set(bool(enabled))
        try:yield
        finally:self._compact.reset(token)

    def generate_reply(self,*args,**kwargs):
        if self._compact.get():return self._compact_adapter.generate_reply(*args,**kwargs)
        return super().generate_reply(*args,**kwargs)
