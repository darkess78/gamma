"""Core facts owned by the state transaction; original Markdown stays untouched."""
from pathlib import Path

from ..config import settings
from .documents import MAX_DOCUMENT_BYTES, StateDocuments


class CoreMemoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.data_dir / "core_memories.md"
        self.documents = StateDocuments()

    @staticmethod
    def _legacy(path):
        if not path.exists():
            return {"text": "# Core Memories\n\n"}
        with path.open("rb") as source:
            raw = source.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError("core memory source exceeds size limit")
        return {"text": raw.decode("utf-8")}

    def load(self):
        return self.documents.load("core_memories", self.path, legacy_loader=self._legacy)["text"]

    def append(self, fact: str, *, session=None) -> bool:
        cleaned = " ".join(str(fact).split())
        if not cleaned:
            return False
        if len(cleaned) > 4000:
            raise ValueError("core fact exceeds size limit")
        inserted = False
        def transform(payload):
            nonlocal inserted
            text = payload["text"]
            if f"- {cleaned}" not in {line.strip() for line in text.splitlines()}:
                payload["text"] = text.rstrip() + f"\n- {cleaned}\n"
                inserted = True
            return payload
        self.documents.mutate("core_memories", self.path, transform, session=session, legacy_loader=self._legacy)
        return inserted
