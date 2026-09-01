"""Locally-hosted embedding wrapper over fastembed.

The embeddings run entirely inside the container via ONNX (fastembed). The
default host-backed Ollama/API path is intentionally NOT supported: the model
is bundled and cached in a deterministic directory so a fresh environment can
reproduce the same vectors without external services.
"""

from functools import lru_cache
from typing import Any

import numpy as np
from fastembed import TextEmbedding

from app.config import get_settings


class MetadataEmbedder:
    """Stateless wrapper that encodes texts/queries into fixed-size vectors."""

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
