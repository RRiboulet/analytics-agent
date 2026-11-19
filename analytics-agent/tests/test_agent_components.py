"""Component tests for M4 support modules using stubs (no network/model)."""

import json
import subprocess
import sys as _sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from app.agent import entrypoint
from app.agent.capabilities import MCPCapabilities, parse_tool_result
from app.agent.graph import AgentServices, build_graph
from app.agent.llm import FakeLLM, LLMClient, LLMError, _request_payload, create_llm
from app.agent.tracing import PROJECT_ENV_PATH, AgentTracer, callbacks_for
from app.config import Settings, get_settings
from app.progress import NullSink, ProgressEmitter, ProgressEvent, ProgressEventType

# ---------------------------------------------------------------------------
# Entrypoint CLI / run_agent
# ---------------------------------------------------------------------------


def test_parse_args_plain_and_json() -> None:
    assert entrypoint._parse_args(["Which status?"]) == ("Which status?", False)
    assert entrypoint._parse_args(["--json", "Which status?"]) == ("Which status?", True)


@pytest.mark.asyncio
async def test_run_agent_with_fake_components(monkeypatch) -> None:
    class FakeLLM:
        async def generate_sql(self, *a, **k):  # type: ignore[no-untyped-def]
            return "SELECT 1"

        async def generate_answer(self, *a):  # type: ignore[no-untyped-def]
            return "answer"

    class FakeCaps:
        async def call_tool(self, name, args=None):  # type: ignore[no-untyped-def]
            if name == "query":
                return {"valid": True, "message": "ok", "entries": [{"n": 1}]}
            return {"valid": True, "message": "ok", "entries": []}

        async def close(self):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(entrypoint, "create_llm", FakeLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)
    res = await entrypoint.run_agent("Q")
    assert res.status == "completed"
    assert res.answer == "answer"
    assert res.sql is not None
    # The run reports the calls it actually spent (the web surface's final
    # payload reads these instead of estimating).
    assert res.tool_calls == {
        "search_metadata": 1,
        "list_tables": 1,
        "get_relationships": 1,
        "query": 1,
    }


@pytest.mark.asyncio
async def test_run_agent_surfaces_retrieval_error(monkeypatch) -> None:
    """A failed MCP discovery layer fails the run with the tool error surfaced."""

    class FailingDiscovery:
        async def call_tool(self, name, args=None):  # type: ignore[no-untyped-def]
            return {
                "valid": False,
                "message": "Tool error: connection refused",
                "entries": [],
            }

        async def close(self):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(entrypoint, "create_llm", FakeLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FailingDiscovery)
    res = await entrypoint.run_agent("Q")
    assert res.status == "failed"
    assert res.answer is None
    assert res.error is not None
    assert "search_metadata" in res.error


@pytest.mark.asyncio
async def test_run_agent_degrades_gracefully_when_search_fails(monkeypatch) -> None:
    """A search_metadata-only failure completes schema-only and is not an error."""

    class SearchFailsDiscovery:
        async def call_tool(self, name, args=None):  # type: ignore[no-untyped-def]
            if name == "search_metadata":
                return {
                    "valid": False,
                    "message": "Tool error: metadata index unavailable",
                    "entries": [],
                }
            if name == "query":
                return {"valid": True, "message": "ok", "entries": [{"n": 1}]}
            return {"valid": True, "message": "ok", "entries": []}

        async def close(self):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(entrypoint, "create_llm", FakeLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", SearchFailsDiscovery)
    res = await entrypoint.run_agent("Q")
    assert res.status == "completed"
    assert res.answer is not None
    assert res.error is None  # degradation is not an error for the caller


def test_main_cli_json_output(monkeypatch, capsys) -> None:
    async def fake_run(_question):  # type: ignore[no-untyped-def]
        return entrypoint.RunResult(
            answer="a", status="completed", sql="SELECT 1", attempts=1, state={}
        )

    monkeypatch.setattr(entrypoint, "run_agent", fake_run)  # type: ignore[attr-defined]
    monkeypatch.setattr(_sys, "argv", ["prog", "--json", "What is order status?"])
    entrypoint.main()
    captured = capsys.readouterr().out
    assert "completed" in captured
    assert "SELECT 1" in captured


def test_main_cli_no_args_raises(monkeypatch, capsys) -> None:
    monkeypatch.setattr(_sys, "argv", ["prog"])
    with pytest.raises(SystemExit):
        entrypoint.main()


def test_main_cli_plain_output(monkeypatch, capsys) -> None:
    async def fake_run(_question):  # type: ignore[no-untyped-def]
        return entrypoint.RunResult(
            answer="a", status="completed", sql="SELECT 1", attempts=1, state={}
        )

    monkeypatch.setattr(entrypoint, "run_agent", fake_run)  # type: ignore[attr-defined]
    monkeypatch.setattr(_sys, "argv", ["prog", "What is the status?"])
    entrypoint.main()
    captured = capsys.readouterr().out
    assert "Status: completed" in captured
    assert "Answer: a" in captured
    assert "SQL: SELECT 1" in captured


def test_main_cli_reports_failure_error(monkeypatch, capsys) -> None:
    """A failed run prints the recorded error instead of crashing with a traceback."""

    async def fake_run(_question):  # type: ignore[no-untyped-def]
        return entrypoint.RunResult(
            answer=None,
            status="failed",
            sql=None,
            attempts=2,
            state={},
            error="LLM request timed out after 300s",
        )

    monkeypatch.setattr(entrypoint, "run_agent", fake_run)  # type: ignore[attr-defined]
    monkeypatch.setattr(_sys, "argv", ["prog", "--json", "Q"])
    entrypoint.main()
    captured = capsys.readouterr().out
    assert '"error"' in captured
    assert "timed out" in captured


def test_main_cli_plain_failure_output(monkeypatch, capsys) -> None:
    """Plain (non-JSON) mode prints the recorded error for a failed run."""

    async def fake_run(_question):  # type: ignore[no-untyped-def]
        return entrypoint.RunResult(
            answer=None,
            status="failed",
            sql=None,
            attempts=2,
            state={},
            error="LLM request timed out after 300s",
        )

    monkeypatch.setattr(entrypoint, "run_agent", fake_run)  # type: ignore[attr-defined]
    monkeypatch.setattr(_sys, "argv", ["prog", "Q"])
    entrypoint.main()
    captured = capsys.readouterr().out
    assert "Status: failed" in captured
    assert "Error: LLM request timed out" in captured
    assert "Answer: None" in captured


def test_agent_main_module_guard_runs() -> None:
    """Running `python -m app.agent` with no args hits the usage guard."""
    project_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [_sys.executable, "-m", "app.agent"], capture_output=True, text=True, cwd=project_root
    )
    assert proc.returncode != 0
    assert "Usage:" in (proc.stderr or "") or "Usage:" in (proc.stdout or "")


# ---------------------------------------------------------------------------
# parse_tool_result edge branches
# ---------------------------------------------------------------------------


def test_parse_tool_result_handles_dict_and_non_dict() -> None:
    # dict-with-text form
    assert parse_tool_result({"text": '{"a": 1}', "id": "x"}) == {"a": 1}
    # parsed JSON that is not a dict (list / scalar) -> clean failure
    out = parse_tool_result([{"type": "text", "text": "[1, 2, 3]"}])
    assert out["valid"] is False
    assert out["entries"] == []
    assert isinstance(parse_tool_result("not a list or dict"), dict)


@pytest.mark.asyncio
async def test_mcp_capabilities_unknown_tool_without_network() -> None:
    caps = MCPCapabilities(url="http://unused/mcp")
    caps._client = object()  # bypass _ensure_connected
    caps._tools = {}
    result = await caps.call_tool("nope", {})
    assert result == {"valid": False, "message": "Unknown tool: nope", "entries": []}


@pytest.mark.asyncio
async def test_mcp_capabilities_surfaces_tool_error() -> None:
    class BoomTool:
        name = "boom"

        async def ainvoke(self, _args):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    caps = MCPCapabilities(url="http://unused/mcp")
    caps._client = object()
    caps._tools = {"boom": BoomTool()}
    result = await caps.call_tool("boom", {})
    assert result["valid"] is False
    assert "boom" in result["message"]


@pytest.mark.asyncio
async def test_mcp_capabilities_close_clears_state() -> None:
    caps = MCPCapabilities(url="http://unused/mcp")
    caps._client = object()
    caps._tools = {"x": object()}
    await caps.close()
    assert caps._client is None
    assert caps._tools == {}


# ---------------------------------------------------------------------------
# LLMClient against a stubbed httpx transport
# ---------------------------------------------------------------------------


def _fake_transport() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  SELECT 1  "}}]})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_llm_client_generates_sql_and_answer() -> None:
    client = LLMClient(
        base_url="http://llm/v1", model="gemma", timeout_seconds=5, transport=_fake_transport()
    )
    sql = await client.generate_sql("q", "meta", "schema")
    assert sql == "SELECT 1"  # whitespace stripped
    ans = await client.generate_answer("q", "SELECT 1", "[]")
    assert ans == "SELECT 1"


def test_request_payload_shape() -> None:
    payload, opts = _request_payload("m", "sys", "usr", timeout=1.0)
    assert payload["model"] == "m"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["content"] == "usr"
    assert payload["temperature"] == 0
    assert "max_tokens" not in payload  # omitted when not configured

    capped, _ = _request_payload("m", "sys", "usr", timeout=1.0, max_tokens=4096)
    assert capped["max_tokens"] == 4096
    assert opts == {"timeout": 1.0}


def test_request_payload_omits_reasoning_effort_when_unset() -> None:
    payload, _ = _request_payload("m", "sys", "usr", timeout=1.0)
    # The local llama.cpp path is configured with --reasoning on instead, so an
    # unconfigured effort must not add any parameter to the request.
    assert "reasoning_effort" not in payload


def test_request_payload_includes_reasoning_effort_when_set() -> None:
    payload, _ = _request_payload("m", "sys", "usr", timeout=1.0, reasoning_effort="high")
    assert payload["reasoning_effort"] == "high"


# ---------------------------------------------------------------------------
# LLM provider selection (llamacpp default / openrouter)
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_settings_cache():
    """Isolate env-driven get_settings() (lru_cached) for provider tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # rebuilt from the restored env on next call


def test_create_llm_llamacpp_is_the_default(monkeypatch, _reset_settings_cache) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "llamacpp")
    monkeypatch.setenv("LLM_BASE_URL", "http://llamacpp:8080/v1")
    monkeypatch.setenv("LLM_MODEL", "gemma-4")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = create_llm()
    assert isinstance(client, LLMClient)
    assert client.base_url == "http://llamacpp:8080/v1"
    assert client.model == "gemma-4"
    assert client.api_key is None  # local path stays unauthenticated


def test_create_llm_openrouter_uses_openrouter_settings(monkeypatch, _reset_settings_cache) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    client = create_llm()
    assert client.base_url == "https://openrouter.ai/api/v1"
    assert client.model == "openai/gpt-4o-mini"
    assert client.api_key == "sk-test"


def test_create_llm_openrouter_requires_api_key(monkeypatch, _reset_settings_cache) -> None:
    # An empty env var beats a developer's project .env (pydantic-settings
    # precedence), making the test deterministic even when OPENROUTER_API_KEY
    # is present in .env; the factory must treat an empty key as missing.
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        create_llm()


def test_settings_reject_unknown_provider(monkeypatch, _reset_settings_cache) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama-cloud")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_reject_unknown_reasoning_effort(monkeypatch, _reset_settings_cache) -> None:
    # Models advertise a discrete effort list (deepseek-v4.1-flash: low/high/max),
    # so an unsupported level must fail at startup rather than 400 mid-run.
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium-high")
    with pytest.raises(ValidationError):
        Settings()


def test_default_token_caps_leave_room_for_a_reasoning_pass() -> None:
    # Reasoning tokens are billed against the caps, so a default sized for a
    # plain chat model truncates a high-effort pass to an empty completion.
    # Assert the declared defaults (not a resolved Settings instance) so a
    # developer's project .env cannot mask a regression in the defaults.
    max_tokens = Settings.model_fields["llm_max_tokens"].default
    answer_max_tokens = Settings.model_fields["llm_answer_max_tokens"].default
    assert max_tokens == 16384
    assert answer_max_tokens == 8192
    assert answer_max_tokens < max_tokens


def test_main_cli_reports_configuration_error(monkeypatch) -> None:
    """A misconfigured provider fails the CLI fast with a clean message."""

    async def failing(_question: str) -> Any:
        raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")

    monkeypatch.setattr(entrypoint, "run_agent", failing)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit, match="Configuration error"):
        entrypoint.main(["Q"])


@pytest.mark.asyncio
async def test_llm_client_sends_auth_headers_only_with_api_key() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["title"] = request.headers.get("X-Title")
        return httpx.Response(200, json={"choices": [{"message": {"content": "SELECT 1"}}]})

    with_key = LLMClient(
        base_url="http://llm/v1",
        model="m",
        timeout_seconds=5,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    await with_key.generate_sql("q", "meta", "schema")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["title"] == "analytics-agent"

    captured.clear()
    without_key = LLMClient(
        base_url="http://llm/v1",
        model="m",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    await without_key.generate_sql("q", "meta", "schema")
    assert captured["auth"] is None  # no auth header on the local path
    assert captured["title"] is None


# ---------------------------------------------------------------------------
# End-to-end observability: nested runs are visible in the graph trace
# ---------------------------------------------------------------------------


class _TracedToolArgs(BaseModel):
    sql: str = ""


class TracedStubCapabilities:
    """Stub whose call_tool goes through a real langchain StructuredTool.

    This mirrors MCPCapabilities, whose tools are langchain runnables, so a
    spy callback attached to the graph config must see each MCP tool call.
    """

    def __init__(self) -> None:
        async def search(**_kw: object) -> dict:
            return {"valid": True, "message": "ok", "entries": []}

        async def query(**_kw: object) -> str:
            return json.dumps({"valid": True, "message": "ok", "entries": [{"n": 1}]})

        search.__doc__ = "search metadata"
        query.__doc__ = "run query"
        self._tools = {
            "search_metadata": StructuredTool.from_function(
                coroutine=search, name="search_metadata", args_schema=_TracedToolArgs
            ),
            "list_tables": StructuredTool.from_function(
                coroutine=search, name="list_tables", args_schema=_TracedToolArgs
            ),
            "get_relationships": StructuredTool.from_function(
                coroutine=search, name="get_relationships", args_schema=_TracedToolArgs
            ),
            "query": StructuredTool.from_function(
                coroutine=query, name="query", args_schema=_TracedToolArgs
            ),
        }

    async def call_tool(self, name: str, args: dict | None = None) -> dict:
        result = await self._tools[name].ainvoke(args or {})
        if name == "query":
            # Mirror the MCP adapter's content-block shape for parse_tool_result.
            return parse_tool_result([{"type": "text", "text": str(result)}])
        return {"valid": True, "message": "ok", "entries": []}

    async def close(self) -> None:
        pass


class RunSpyHandler(BaseCallbackHandler):
    """Records nested run names visible through the callback mechanism."""

    def __init__(self) -> None:
        self.chain_runs: list[str | None] = []

    def on_chain_start(
        self, serialized: Any, inputs: Any, *, run_id: Any, name: str | None = None, **kw: Any
    ) -> None:
        self.chain_runs.append(name)


@pytest.mark.asyncio
async def test_graph_trace_contains_llm_runs_and_tool_calls() -> None:
    """M5: LLM calls and MCP tool calls appear as nested runs in the trace.

    Uses the real LLMClient against a stubbed transport and a stub capabilities
    whose tools are real runnables, with a spy handler in the graph config.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "SELECT 1"}}]})

    llm = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    services = AgentServices(
        llm=llm, capabilities=TracedStubCapabilities(), max_attempts=3, max_rows=10
    )
    graph = build_graph(services)
    spy = RunSpyHandler()
    state = await graph.ainvoke(
        {"question": "q", "status": "planning", "attempts": 0}, config={"callbacks": [spy]}
    )
    assert state["status"] == "completed"
    # The node runnable and the nested LLM run share the name 'generate_sql';
    # it must appear at least twice (node + nested LLM call).
    assert spy.chain_runs.count("generate_sql") >= 2
    assert "generate_answer" in spy.chain_runs  # nested LLM call


# ---------------------------------------------------------------------------
# LLMClient failure translation (the httpx.ReadTimeout crash reported in M4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_client_translates_read_timeout_to_llm_error() -> None:
    # httpx.MockTransport has no timeout semantics, so raise the exact
    # exception the real stack produces (httpx.ReadTimeout, previously fatal
    # for the agent against a slow local model) from the handler and assert it
    # is translated into a typed, recoverable LLMError.
    def timeout_handler(request):
        raise httpx.ReadTimeout("timed out")

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    assert "timed out" in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_translates_transport_error_to_llm_error() -> None:
    # Connection failures (LLM server not running / unreachable) are the
    # common case of the reported crash; they must surface as a typed,
    # recoverable LLMError rather than a raw httpx exception.
    def connect_handler(request):
        raise httpx.ConnectError("connection refused")

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(connect_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    assert "LLM request failed" in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_translates_http_error_to_llm_error() -> None:
    def err_handler(request):
        return httpx.Response(500, text="boom")

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(err_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    assert "HTTP 500" in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_translates_malformed_response_to_llm_error() -> None:
    def malformed_handler(request):
        return httpx.Response(200, json={"choices": []})

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(malformed_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    assert "Unexpected LLM response" in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_translates_null_content_to_llm_error() -> None:
    # Some OpenAI-compatible endpoints return ``message.content: null``
    # (reasoning-only or truncated completions). This previously crashed the
    # whole manager run with `AttributeError: 'NoneType' object has no
    # attribute 'strip'`; it must surface as a typed, retryable LLMError.
    def null_content_handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(null_content_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    assert "no usable text content" in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_translates_non_text_content_to_llm_error() -> None:
    def list_content_handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "text"}]}}]})

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(list_content_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    assert "no usable text content" in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_rejects_empty_content_as_llm_error() -> None:
    # A reasoning model that spends the whole output cap on chain-of-thought
    # finishes with finish_reason=length and empty content. Returning "" made
    # that look like an empty SQL string downstream; it must be a typed,
    # retryable error that names the cause.
    def truncated_handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "We need answer simple."},
                    }
                ]
            },
        )

    client = LLMClient(
        base_url="http://llm/v1",
        model="deepseek-v4.1-flash",
        timeout_seconds=5,
        transport=httpx.MockTransport(truncated_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_sql("q", "meta", "schema")
    message = str(excinfo.value)
    assert "empty text content" in message
    assert "finish_reason='length'" in message
    assert "reasoning_content present" in message
    assert "LLM_MAX_TOKENS" in message


@pytest.mark.asyncio
async def test_llm_client_empty_content_without_reasoning_still_fails() -> None:
    def blank_handler(request):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "  "}}]}
        )

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(blank_handler),
    )
    with pytest.raises(LLMError) as excinfo:
        await client.generate_answer("q", "SELECT 1", "[]")
    assert "empty text content" in str(excinfo.value)
    assert "reasoning_content present" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_llm_client_sends_configured_reasoning_effort(
    monkeypatch, _reset_settings_cache
) -> None:
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    get_settings.cache_clear()
    seen: dict[str, dict] = {}

    def recorder(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(
        base_url="http://llm/v1",
        model="deepseek-v4.1-flash",
        timeout_seconds=5,
        transport=httpx.MockTransport(recorder),
    )
    assert client.reasoning_effort == "high"
    await client.generate_sql("q", "meta", "schema")
    assert seen["payload"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_llm_client_appends_prior_error_to_prompt() -> None:
    """On retry the real client feeds the previous attempt's error back to the model."""
    seen: dict[str, dict] = {}

    def recorder(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        transport=httpx.MockTransport(recorder),
    )
    await client.generate_sql("q", "meta", "schema", prior_error="column nope does not exist")
    user = seen["payload"]["messages"][1]["content"]
    assert "previous attempt failed" in user
    assert "column nope does not exist" in user


@pytest.mark.asyncio
async def test_llm_client_uses_separate_answer_token_cap() -> None:
    seen: dict[str, dict] = {}

    def recorder(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        max_tokens=100,
        answer_max_tokens=20,
        transport=httpx.MockTransport(recorder),
    )
    await client.generate_sql("q", "meta", "schema")
    assert seen["payload"]["max_tokens"] == 100
    await client.generate_answer("q", "SELECT 1", "[]")
    assert seen["payload"]["max_tokens"] == 20


# ---------------------------------------------------------------------------
# Tracing (fail-open)
# ---------------------------------------------------------------------------


def test_tracer_disabled_and_callbacks_for(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert AgentTracer().callbacks() is None
    assert callbacks_for(None) is None
    assert callbacks_for(AgentTracer()) is None


def test_tracer_run_config_disabled(monkeypatch) -> None:
    """Without credentials the run config is None (un-instrumented run)."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert AgentTracer().run_config("What?") is None


def test_tracer_requires_complete_credentials(monkeypatch) -> None:
    """A public key alone cannot export traces, so it must not enable tracing."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracer = AgentTracer()
    assert tracer.callbacks() is None
    assert tracer.run_config("What?") is None


def test_tracer_enabled_gives_callback(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("langfuse.langchain.CallbackHandler", lambda: "fake-handler")
    tracer = AgentTracer()
    assert tracer.callbacks() == ["fake-handler"]
    assert callbacks_for(tracer) == ["fake-handler"]


def test_tracer_run_config_shape(monkeypatch) -> None:
    """Enabled tracing yields callbacks + tags + question metadata in one config."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("langfuse.langchain.CallbackHandler", lambda: "fake-handler")
    config = AgentTracer().run_config("Revenue by category?")
    assert config is not None
    assert config["callbacks"] == ["fake-handler"]
    assert "analytics-agent" in config["tags"]
    assert config["metadata"] == {"question": "Revenue by category?"}


def test_tracer_reuses_handler_across_calls(monkeypatch) -> None:
    """The same handler instance serves the run config and the final flush."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("langfuse.langchain.CallbackHandler", object)
    tracer = AgentTracer()
    first = tracer.callbacks()
    assert tracer.callbacks() == first  # cached, not a second instance
    assert tracer.run_config("Q")["callbacks"] == first


def test_tracer_survives_handler_init_failure(monkeypatch) -> None:
    """Fail-open: a raising handler constructor disables tracing, never breaks the run."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    def boom() -> None:
        raise RuntimeError("langfuse misconfigured")

    monkeypatch.setattr("langfuse.langchain.CallbackHandler", boom)
    tracer = AgentTracer()
    assert tracer.callbacks() is None
    assert tracer.run_config("What?") is None
    tracer.flush()  # must be a safe no-op


def test_tracer_flush_disabled_is_noop() -> None:
    AgentTracer().flush()


def test_tracer_flush_swallows_handler_errors(monkeypatch) -> None:
    class BadHandler:
        def flush(self) -> None:
            raise RuntimeError("export failed")

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("langfuse.langchain.CallbackHandler", BadHandler)
    tracer = AgentTracer()
    tracer.callbacks()  # handler created (fails open if it raised)
    tracer.flush()  # must not raise even though the handler's flush fails


def test_tracer_env_file_resolves_to_project_root() -> None:
    """The .env is located via the module path, not the CWD.

    Regression test: launching `python -m app.agent` from outside the project
    directory used to silently find no .env (CWD-based lookup) and disable
    tracing while the run itself still worked on default settings.
    """
    assert Path(__file__).resolve().parents[1] / ".env" == PROJECT_ENV_PATH


def test_settings_env_file_resolves_to_project_root() -> None:
    """Settings resolve .env from the project root, consistent with the tracer."""

    assert Path(Settings.model_config["env_file"]) == (  # type: ignore[arg-type]
        Path(__file__).resolve().parents[1] / ".env"
    )


def test_tracer_flushes_created_handler(monkeypatch) -> None:
    flushed: list[bool] = []

    class GoodHandler:
        def flush(self) -> None:
            flushed.append(True)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("langfuse.langchain.CallbackHandler", GoodHandler)
    tracer = AgentTracer()
    tracer.callbacks()
    tracer.flush()
    assert flushed == [True]


# ---------------------------------------------------------------------------
# Progress events: event shape, emitter sequencing, fail-open delivery (M8)
# ---------------------------------------------------------------------------


def test_progress_event_as_dict_keeps_set_fields_only() -> None:
    event = ProgressEvent(
        type=ProgressEventType.SQL_EXECUTED, seq=3, attempt=1, sql="SELECT 1", row_count=0
    )
    assert event.as_dict() == {
        "type": "sql.executed",
        "seq": 3,
        "attempt": 1,
        "sql": "SELECT 1",
        "row_count": 0,
    }
    # Unset (None) fields are omitted, not serialised as nulls.
    assert "answer" not in event.as_dict()


def test_progress_emitter_assigns_monotonic_sequence_numbers() -> None:
    recorded: list[ProgressEvent] = []

    class Sink:
        def emit(self, event: ProgressEvent) -> None:
            recorded.append(event)

    emitter = ProgressEmitter(Sink())
    assert emitter.enabled is True
    emitter.emit(ProgressEventType.RUN_STARTED, question="Q")
    emitter.emit(ProgressEventType.RUN_COMPLETED, status="completed", answer="a")

    assert [event.seq for event in recorded] == [1, 2]
    assert [event.type for event in recorded] == ["run.started", "run.completed"]
    assert emitter.seq == 2


def test_progress_emitter_without_a_sink_costs_nothing() -> None:
    emitter = ProgressEmitter()
    assert emitter.enabled is False
    assert emitter.emit(ProgressEventType.RUN_STARTED, question="Q") is None
    assert emitter.seq == 0


def test_progress_emitter_rejects_a_malformed_event_with_or_without_a_sink() -> None:
    """A malformed event is a producer bug, not something a sink decides.

    The event is constructed before the sink is consulted, so an unknown field
    fails identically in a run with no sink and in a run with one — otherwise
    the bug would only appear the day someone attaches a consumer.
    """

    class Sink:
        def emit(self, event: ProgressEvent) -> None:
            pass

    with pytest.raises(TypeError):
        ProgressEmitter().emit(ProgressEventType.RUN_STARTED, not_a_field="x")
    with pytest.raises(TypeError):
        ProgressEmitter(Sink()).emit(ProgressEventType.RUN_STARTED, not_a_field="x")


def test_progress_emitter_swallows_sink_errors_and_keeps_numbering() -> None:
    """Fail-open (R10): a raising sink is swallowed; sequencing stays consistent."""

    class BoomSink:
        def emit(self, event: ProgressEvent) -> None:
            raise RuntimeError("sink exploded")

    emitter = ProgressEmitter(BoomSink())
    assert emitter.emit(ProgressEventType.RUN_STARTED) is not None
    assert emitter.emit(ProgressEventType.METADATA_RETRIEVED) is not None
    assert emitter.seq == 2


def test_null_sink_discards_events() -> None:
    assert NullSink().emit(ProgressEvent(type="run.started", seq=1)) is None


@pytest.mark.asyncio
async def test_run_agent_with_raising_sink_matches_no_sink(monkeypatch) -> None:
    """R10 at the public seam: status, answer and error are unaffected."""

    class FailingQuery:
        async def call_tool(self, name, args=None):  # type: ignore[no-untyped-def]
            if name == "query":
                return {"valid": False, "message": "Query failed: bad column", "entries": []}
            return {"valid": True, "message": "ok", "entries": []}

        async def close(self):  # type: ignore[no-untyped-def]
            pass

    class BoomSink:
        def emit(self, event: ProgressEvent) -> None:
            raise RuntimeError("sink exploded")

    monkeypatch.setattr(entrypoint, "create_llm", FakeLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FailingQuery)
    plain = await entrypoint.run_agent("Q")
    guarded = await entrypoint.run_agent("Q", sink=BoomSink())

    assert (guarded.status, guarded.answer, guarded.error) == (
        plain.status,
        plain.answer,
        plain.error,
    )
    assert plain.status == "failed"
    assert plain.answer is None
    assert "bad column" in (plain.error or "")
