"""Locally-hosted embedding wrapper over fastembed.

The embeddings run entirely inside the container via ONNX (fastembed). The
default host-backed Ollama/API path is intentionally NOT supported: the model
is bundled and cached in a deterministic directory so a fresh environment can
reproduce the same vectors without external services.
"""

import asyncio
import threading
from functools import lru_cache
from typing import Any

import numpy as np
from fastembed import TextEmbedding

from app.config import get_settings


class MetadataEmbedder:
    """Wrapper that encodes texts/queries into fixed-size vectors.

    Per-instance state is the loaded model (``TextEmbedding``, one ONNX session
    with its own thread pool) and ``_embed_lock``, the guard that serializes
    inference on it: the model is not safe to run from two threads at once, so
    concurrent metadata searches must take the guard.
    """

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=threads,
        )
        # A threading.Lock, deliberately: it is acquired *inside* the worker
        # thread, so cancelling the awaiting coroutine cannot release it while
        # ONNX is still running. An asyncio.Lock would be released by that
        # cancellation (letting a second search race the model), and it binds
        # itself to the first event loop that contends it.
        self._embed_lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns a list of float vectors."""
        vectors = np.asarray(list(self._model.embed(texts)), dtype="float64")
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query/question."""
        (vector,) = self.embed([text])
        return vector

    def _embed_query_serialized(self, text: str) -> list[float]:
        """Worker-thread body: hold the guard for the whole inference call."""
        with self._embed_lock:
            return self.embed_query(text)

    async def embed_query_async(self, text: str) -> list[float]:
        """Embed one query without blocking the server's event loop.

        fastembed's ONNX inference is synchronous CPU work, so it runs in a
        worker thread. The guard is taken there, not around the await, so
        concurrent metadata searches are serialized for as long as inference
        actually runs — including after the awaiting task is cancelled.
        """
        return await asyncio.to_thread(self._embed_query_serialized, text)


@lru_cache(maxsize=1)
def get_embedder() -> MetadataEmbedder:
    """Return the process-wide embedder, built once from configured settings."""
    settings = get_settings()
    return MetadataEmbedder(
        model_name=settings.embedding_model_name,
        cache_dir=settings.embedding_cache_dir,
    )


def create_test_embedder(vectors: list[list[float]]) -> "MetadataEmbedder":
    """Build a stub embedder that returns fixed vectors (used by tests)."""

    class _StubEmbedder(MetadataEmbedder):
        def __init__(self, *_: Any, **__: Any) -> None:
            # Skips the ONNX model load but keeps the remaining per-instance
            # state, so the real serialization path is still exercised.
            self._embed_lock = threading.Lock()

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [vectors[0]] * len(texts)

        def embed_query(self, text: str) -> list[float]:
            return vectors[0]

    return _StubEmbedder("unused")
