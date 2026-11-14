"""Meaning-based memory search: sentence embeddings on the CPU.

Uses BAAI/bge-small-en-v1.5 (MIT licence, 384 dimensions) exported to ONNX and
run with onnxruntime on the CPU, a couple of threads only, so it never touches
the GPU. The model directory holds ``onnx/model.onnx`` and ``tokenizer.json``;
point ``memory_embeddings_dir`` at it, or put it in ``data/models/bge-small-en-v1.5``.
Without the model, meaning-based search is off and keyword search works as before.

Vectors are cached in the ``memoryvector`` table (one row per fact or episode).
``python -m gamma.memory.embeddings --backfill`` fills it ahead of time; search
also embeds anything missing on first use.
"""
from __future__ import annotations

import argparse
import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_NAME = "bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_TOKENS = 256
THREADS = 2


class Embedder:
    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_dir / "onnx" / "model.onnx"), options,
                                            providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def embed(self, texts: list[str], *, query: bool = False, batch: int = 32) -> np.ndarray:
        out = []
        for start in range(0, len(texts), batch):
            chunk = [(QUERY_PREFIX + t) if query else t for t in texts[start:start + batch]]
            encoded = self.tokenizer.encode_batch(chunk)
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            cls = hidden[:, 0, :]  # bge uses the [CLS] vector
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        return np.vstack(out) if out else np.zeros((0, 384), dtype=np.float32)


def model_dir() -> Path | None:
    from ..config import settings
    configured = str(getattr(settings, "memory_embeddings_dir", "") or "").strip()
    for candidate in ([Path(configured).expanduser()] if configured else []) + [settings.data_dir / "models" / MODEL_NAME]:
        if (candidate / "onnx" / "model.onnx").is_file() and (candidate / "tokenizer.json").is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def _cached(path: str) -> Embedder:
    return Embedder(Path(path))


def get_embedder() -> Embedder | None:
    path = model_dir()
    if path is None:
        return None
    try:
        return _cached(str(path))
    except Exception:
        return None


# ---- vector cache ------------------------------------------------------------

SCHEMA = ("CREATE TABLE IF NOT EXISTS memoryvector (kind TEXT NOT NULL, record_id INTEGER NOT NULL, "
          "model TEXT NOT NULL, text_hash TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (kind, record_id))")


def _hash(text: str) -> str:
    return hashlib.sha1(f"{MODEL_NAME}|{text}".encode("utf-8")).hexdigest()


def similarities(engine, embedder: Embedder, query: str, kind: str, rows: list[tuple[int, str]]) -> dict[int, float]:
    """Cosine similarity of ``query`` to each (id, text) row.

    Rows whose text changed since they were embedded (an edit, or a reused id)
    are embedded again, so a stale vector is never used.
    """
    if not rows:
        return {}
    with engine.begin() as conn:
        conn.exec_driver_sql(SCHEMA)
        stored = {r[0]: (r[1], r[2]) for r in conn.exec_driver_sql(
            "SELECT record_id, text_hash, vector FROM memoryvector WHERE kind = ? AND model = ?", (kind, MODEL_NAME))}
        cached = {i: np.frombuffer(stored[i][1], dtype=np.float32) for i, t in rows
                  if i in stored and stored[i][0] == _hash(t)}
        missing = [(i, t) for i, t in rows if i not in cached]
        if missing:
            vectors = embedder.embed([t for _, t in missing]).astype(np.float32)
            for (record_id, text), vector in zip(missing, vectors):
                cached[record_id] = vector
                conn.exec_driver_sql("INSERT OR REPLACE INTO memoryvector VALUES (?, ?, ?, ?, ?)",
                                     (kind, record_id, MODEL_NAME, _hash(text), vector.tobytes()))
    q = embedder.embed([query], query=True)[0].astype(np.float32)
    return {record_id: float(np.dot(cached[record_id], q)) for record_id, _ in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed stored memories ahead of time (CPU).")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    from sqlmodel import Session, select
    from .models import EpisodicMemory, ProfileFact
    from .service import MemoryService
    embedder = get_embedder()
    if embedder is None:
        print("No embedding model found; see the module docstring.")
        return 1
    memory = MemoryService()
    with Session(memory._engine) as db:
        episodes = [(m.id, f"{m.summary} {m.tags}") for m in db.exec(select(EpisodicMemory))]
        facts = [(f.id, f"{f.category} {f.fact_text}") for f in db.exec(select(ProfileFact))]
    if args.backfill:
        similarities(memory._engine, embedder, "warm up", "episode", episodes)
        similarities(memory._engine, embedder, "warm up", "fact", facts)
    print(f"{len(episodes)} episodes and {len(facts)} facts have vectors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
