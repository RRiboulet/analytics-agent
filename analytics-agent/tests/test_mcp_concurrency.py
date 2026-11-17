"""Unit tests for M8 ticket 01: the MCP server tolerates concurrent sessions.

Two properties make ticket 03's parallel fan-out a latency win rather than a
contention experiment:

* the asyncpg pool size comes from settings (``DB_POOL_MAX_SIZE``), not a
  literal, so concurrent sessions do not queue on five connections;
* metadata search's synchronous fastembed inference runs in a worker thread
  under an ``asyncio`` lock, so a slow embedding neither stalls the server's
  event loop nor races the model instance.

Everything here is hermetic: no database, network or model is touched.
"""

import asyncio
import threading
import time
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette

import app.server as server_mod
from app.config import Settings, get_settings
from app.data_sources.postgres import PostgresClient
from app.embedder import MetadataEmbedder
from app.tools.search_metadata import register as register_search_metadata


class FakeMCP:
    """Minimal stand-in for FastMCP that captures registered tool callables."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, **kwargs: Any):
        def decorator(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorator


# ---------------------------------------------------------------------------
# Pool size setting
# ---------------------------------------------------------------------------


def test_pool_max_size_defaults_to_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_POOL_MAX_SIZE", raising=False)
    assert Settings(_env_file=None).db_pool_max_size == 10


def test_pool_max_size_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "3")
    assert Settings(_env_file=None).db_pool_max_size == 3


def test_pool_max_size_must_leave_room_for_one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


async def test_client_connects_with_the_configured_pool_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    async def fake_create_pool(**kwargs: Any) -> object:
        recorded.update(kwargs)
        return object()

    monkeypatch.setattr("app.data_sources.postgres.asyncpg.create_pool", fake_create_pool)

    # 2 is below the old literal of 5: a smaller configured value is honored,
    # not silently raised back to it.
    client = PostgresClient("postgresql://unused", 1.0, 100, pool_max_size=2)
    await client.connect()

    assert recorded["max_size"] == 2
    assert recorded["min_size"] == 1
    assert recorded["command_timeout"] == 1.0


async def test_client_defaults_to_the_setting_default(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {}

    async def fake_create_pool(**kwargs: Any) -> object:
        recorded.update(kwargs)
        return object()

    monkeypatch.setattr("app.data_sources.postgres.asyncpg.create_pool", fake_create_pool)

    client = PostgresClient("postgresql://unused", 1.0, 100)
    await client.connect()

    assert recorded["max_size"] == Settings(_env_file=None).db_pool_max_size


def test_asgi_app_sizes_the_pool_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    class FakeServer:
        def streamable_http_app(self) -> Starlette:
            return Starlette()

    monkeypatch.setenv("DB_POOL_MAX_SIZE", "7")
    monkeypatch.setattr(server_mod, "PostgresClient", RecordingClient)
    monkeypatch.setattr(server_mod, "create_mcp_server", lambda client: FakeServer())
    get_settings.cache_clear()
    try:
        server_mod.create_asgi_app()
    finally:
        get_settings.cache_clear()

    assert captured["pool_max_size"] == 7


# ---------------------------------------------------------------------------
# Embedding off the event loop
# ---------------------------------------------------------------------------


class _SlowEmbedder(MetadataEmbedder):
    """Blocking stub embedder: sleeps like ONNX inference would compute."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list[str] = []
        self.thread_id: int | None = None

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        self.thread_id = threading.get_ident()
        time.sleep(self.delay)
        return [float(len(text))]


async def test_embedding_does_not_block_the_event_loop() -> None:
    embedder = _SlowEmbedder(delay=0.25)

    embed_task = asyncio.create_task(embedder.embed_query_async("revenue by category"))
    # Let the embedding coroutine start and hand its work to the thread pool.
    await asyncio.sleep(0)

    # A second coroutine on the same loop makes progress while the embedding
    # is still in flight. If the embedding ran on the loop, this await could
    # not return until it finished.
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=5)
    assert not embed_task.done()

    question = "revenue by category"
    assert await embed_task == [float(len(question))]
    assert embedder.calls == [question]
    # It really ran off the loop thread, not just after a yield.
    assert embedder.thread_id != threading.get_ident()


# ---------------------------------------------------------------------------
# Concurrent metadata searches
# ---------------------------------------------------------------------------


class _OverlapRecordingEmbedder(MetadataEmbedder):
    """Stub embedder that records how many inferences run at the same time."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.questions: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.questions.append(text)
        try:
            # Long enough that two unserialized calls would overlap.
            time.sleep(0.02)
            return [float(len(text))]
        finally:
            with self._guard:
                self.active -= 1


class _EchoClient(PostgresClient):
    """Fake client whose hits carry the query vector it was called with."""

    def __init__(self) -> None:
        super().__init__("postgresql://unused", 1.0, 100)
        self.limits: list[int] = []

    async def search_metadata(self, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        self.limits.append(limit)
        return [{"id": f"vector:{query_vector[0]}", "similarity": 0.5}]


async def test_concurrent_metadata_searches_are_correct_and_serialized() -> None:
    mcp = FakeMCP()
    client = _EchoClient()
    embedder = _OverlapRecordingEmbedder()
    register_search_metadata(mcp, client, embedder)  # type: ignore[arg-type]
    tool = mcp.tools["search_metadata"]

    # Different lengths, so each search must get its own vector back.
    questions = ["orders revenue", "late deliveries"]
    results = await asyncio.gather(*(tool(question=q) for q in questions))

    assert [r.structuredContent["valid"] for r in results] == [True, True]
    assert [r.structuredContent["entries"][0]["id"] for r in results] == [
        f"vector:{float(len(q))}" for q in questions
    ]
    assert client.limits == [5, 5]
    # One inference at a time: the model instance was never raced.
    assert embedder.max_active == 1
    assert sorted(embedder.questions) == sorted(questions)


async def test_concurrent_metadata_searches_mask_embedding_errors() -> None:
    mcp = FakeMCP()
    client = _EchoClient()

    class _BrokenEmbedder(MetadataEmbedder):
        def __init__(self) -> None:
            pass

        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("model crashed")

    register_search_metadata(mcp, client, _BrokenEmbedder())  # type: ignore[arg-type]
    tool = mcp.tools["search_metadata"]

    results = await asyncio.gather(*(tool(question=q) for q in ["a", "b"]))

    for result in results:
        assert result.structuredContent["valid"] is False
        assert result.structuredContent["message"] == "The database is temporarily unavailable."
    assert client.limits == []
