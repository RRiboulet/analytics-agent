"""Locally-hosted embedding wrapper over fastembed.

The embeddings run entirely inside the container via ONNX (fastembed). The
default host-backed Ollama/API path is intentionally NOT supported: the model
is bundled and cached in a deterministic directory so a fresh environment can
reproduce the same vectors without external services.
"""

import asyncio
from functools import lru_cache
from typing import Any

import numpy as np
from fastembed import TextEmbedding

from app.config import get_settings


class MetadataEmbedder:
    """Stateless wrapper that encodes texts/queries into fixed-size vectors."""

    # Serializes the worker-thread embedding call. Created lazily on first
    # async use (never in __init__) so the process-wide embedder built at
    # import time carries no loop-bound state, and so subclasses that skip
    # __init__ (the test stubs below) still work.
    _embed_lock: asyncio.Lock | None = None

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=threads,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns a list of float vectors."""
        vectors = np.asarray(list(self._model.embed(texts)), dtype="float64")
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query/question."""
        (vector,) = self.embed([text])
        return vector

    def _lock(self) -> asyncio.Lock:
        """Return this instance's embedding lock, creating it on first use."""
        if self._embed_lock is None:
            self._embed_lock = asyncio.Lock()
        return self._embed_lock

    async def embed_query_async(self, text: str) -> list[float]:
        """Embed one query without blocking the server's event loop.

        fastembed's ONNX inference is synchronous CPU work, so it runs in a
        worker thread. The lock keeps that serialized: concurrent metadata
        searches must not race the single model instance.
        """
        async with self._lock():
            return await asyncio.to_thread(self.embed_query, text)


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
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [vectors[0]] * len(texts)

        def embed_query(self, text: str) -> list[float]:
            return vectors[0]

    return _StubEmbedder("unused")
