"""Component tests for M4 support modules using stubs (no network/model)."""

import subprocess
import sys as _sys
from pathlib import Path

import httpx
import pytest

from app.agent import entrypoint
from app.agent.capabilities import MCPCapabilities, parse_tool_result
from app.agent.llm import LLMClient, _request_payload
from app.agent.tracing import AgentTracer, callbacks_for

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

    monkeypatch.setattr(entrypoint, "LLMClient", FakeLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)
    res = await entrypoint.run_agent("Q")
    assert res.status == "completed"
    assert res.answer == "answer"
    assert res.sql is not None


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


# ---------------------------------------------------------------------------
# Tracing (fail-open)
# ---------------------------------------------------------------------------


def test_tracer_disabled_and_callbacks_for(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert AgentTracer().callbacks() is None
    assert callbacks_for(None) is None
    assert callbacks_for(AgentTracer()) is None


def test_tracer_enabled_gives_callback(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr("langfuse.langchain.CallbackHandler", lambda: "fake-handler")
    assert AgentTracer().callbacks() == ["fake-handler"]
    assert callbacks_for(AgentTracer()) == ["fake-handler"]
