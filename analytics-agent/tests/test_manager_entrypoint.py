"""Component tests for the manager entrypoint (M7.4): run_manager, CLI,
artifact writing and tracer-config propagation — all with fakes, no network,
no database, no live model, no telemetry export (conftest strips the keys).
"""

import json
import subprocess
import sys as _sys
from pathlib import Path

import pytest

from app.config import get_settings
from app.manager import entrypoint
from app.manager.entrypoint import ManagerRunResult, _run_error, run_manager
from app.manager.evidence import EvidenceRecord
from app.manager.llm import FakeManagerLLM, ManagerLLMClient, create_manager_llm
from app.manager.state import ManagerStatus

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAnalystLLM:
    """Analyst-protocol LLM (generate_sql / generate_answer)."""

    async def generate_sql(self, question, metadata, schema, *, prior_error=None):
        return "SELECT revenue FROM t"

    async def generate_answer(self, question, sql, result):
        return "the answer"


class FakeFullLLM(FakeManagerLLM):
    """Manager + analyst protocol LLM: what one shared client provides."""

    async def generate_sql(self, question, metadata, schema, *, prior_error=None):
        return "SELECT revenue FROM t"

    async def generate_answer(self, question, sql, result):
        return "the answer"


class FakeCaps:
    """Read-only MCP boundary stub returning one grounded revenue row."""

    async def call_tool(self, name, args=None):
        if name == "query":
            return {"valid": True, "message": "ok", "entries": [{"revenue": 10.5}]}
        return {"valid": True, "message": "ok", "entries": []}

    async def close(self):
        pass


class StubTracer:
    """Tracer stub: hands a sentinel config to every graph invocation."""

    def __init__(self) -> None:
        self.config = {"callbacks": ["sentinel"], "tags": ["analytics-agent"]}
        self.flushed = False

    def run_config(self, question):
        return self.config

    def flush(self):
        self.flushed = True


class StubGraph:
    def __init__(self, result: dict, captured: dict, services_box: dict | None = None) -> None:
        self._result = result
        self._captured = captured
        self._services_box = services_box

    async def ainvoke(self, state, config=None):
        self._captured["state"] = state
        self._captured["config"] = config
        if self._services_box is not None:
            # Exercise the composition seam so the analyst config is captured.
            await self._services_box["services"].run_analyst("Summarize sales.")
        return self._result


@pytest.fixture
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# _run_request / run_manager
# ---------------------------------------------------------------------------


async def test_run_request_passes_tracer_config_to_both_graphs(monkeypatch) -> None:
    captured_manager: dict = {}
    captured_analyst: dict = {}
    tracer = StubTracer()

    def fake_build_manager_graph(services):
        return StubGraph(
            {"status": ManagerStatus.COMPLETED, "report": "r"},
            captured_manager,
            services_box={"services": services},
        )

    def fake_build_graph(services):
        return StubGraph({"status": "completed"}, captured_analyst)

    monkeypatch.setattr(entrypoint, "build_manager_graph", fake_build_manager_graph)
    monkeypatch.setattr(entrypoint, "build_graph", fake_build_graph)

    state = await entrypoint._run_request(
        "Summarize sales.",
        llm=FakeManagerLLM(),
        analyst_llm=FakeAnalystLLM(),
        capabilities=FakeCaps(),
        manager_max_attempts=2,
        analyst_max_attempts=3,
        max_rows=100,
        tracer=tracer,
    )

    assert state["report"] == "r"
    assert captured_manager["state"] == {"request": "Summarize sales."}
    # Same config object reaches the manager graph and the analyst sub-run.
    assert captured_manager["config"] is tracer.config
    assert captured_analyst["config"] is tracer.config
    assert captured_analyst["state"]["question"] == "Summarize sales."


async def test_run_manager_end_to_end_with_fake_components(monkeypatch) -> None:
    monkeypatch.setattr(entrypoint, "create_manager_llm", FakeFullLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)

    # Decompose yields two sub-questions; the fake analyst answers each via
    # the stubbed MCP tools; the default report has no digits (trivially
    # grounded), so the run must complete with two evidence records.
    result = await run_manager("Summarize sales.")

    assert result.status == "completed"
    assert result.report is not None
    assert result.error is None
    assert len(result.sub_questions) >= 1
    assert len(result.evidence) == len(result.sub_questions)
    assert result.attempts == 1


async def test_run_manager_surfaces_groundedness_error(monkeypatch) -> None:
    monkeypatch.setattr(
        entrypoint,
        "create_manager_llm",
        lambda: FakeFullLLM(raw="Revenue?", report="Revenue was 999.99."),
    )
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)

    result = await run_manager("Summarize sales.")

    assert result.status == "failed"
    assert result.report is None
    assert result.error is not None
    assert "999.99" in result.error


async def test_run_error_precedence() -> None:
    assert _run_error({"decomposition_error": "bad plan"}) == "bad plan"
    assert _run_error({"groundedness_error": " fabricated"}) == " fabricated"
    assert _run_error({"llm_error": "429"}) == "429"
    assert (
        _run_error({"status": ManagerStatus.FAILED, "sub_analysis_errors": ["sub 0: x"]})
        == "sub 0: x"
    )
    # Partial sub-analysis failure on a completed run is not an error.
    assert (
        _run_error({"status": ManagerStatus.COMPLETED, "sub_analysis_errors": ["sub 0: x"]}) is None
    )
    assert _run_error({}) is None


async def test_run_manager_flushes_tracer_even_on_crash(monkeypatch) -> None:
    tracer = StubTracer()
    monkeypatch.setattr(entrypoint, "create_manager_llm", FakeFullLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)
    monkeypatch.setattr(entrypoint, "AgentTracer", lambda: tracer)

    def boom(services):
        raise RuntimeError("graph construction failed")

    monkeypatch.setattr(entrypoint, "build_manager_graph", boom)
    with pytest.raises(RuntimeError, match="graph construction failed"):
        await run_manager("Summarize sales.")
    assert tracer.flushed


# ---------------------------------------------------------------------------
# Artifacts (--out)
# ---------------------------------------------------------------------------


async def test_write_artifacts_report_and_evidence(tmp_path) -> None:
    state = {
        "status": ManagerStatus.COMPLETED,
        "attempts": 1,
        "sub_questions": ["Q1?"],
        "sub_analysis_errors": [],
        "decomposition_error": None,
        "groundedness_error": None,
        "report": "# Report\nRevenue 10.5",
        "evidence": [
            EvidenceRecord(
                sub_index=0,
                sub_question="Q1?",
                status="completed",
                sql="SELECT 1",
                rows=[{"revenue": 10.5}],
                answer="10.5",
            )
        ],
    }
    entrypoint._write_artifacts(tmp_path, "Summarize sales.", state)

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# Report")
    payload = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert payload["request"] == "Summarize sales."
    assert payload["status"] == "completed"
    assert payload["evidence"][0]["sub_question"] == "Q1?"
    assert payload["evidence"][0]["rows"] == [{"revenue": 10.5}]


async def test_write_artifacts_failed_run_has_no_report_file(tmp_path) -> None:
    state = {
        "status": ManagerStatus.FAILED,
        "attempts": 2,
        "sub_questions": [],
        "sub_analysis_errors": [],
        "decomposition_error": "no sub-questions",
        "groundedness_error": None,
        "evidence": [],
    }
    entrypoint._write_artifacts(tmp_path, "Summarize sales.", state)

    assert not (tmp_path / "report.md").exists()
    payload = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["decomposition_error"] == "no sub-questions"


async def test_run_manager_writes_out_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(entrypoint, "create_manager_llm", FakeFullLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)

    result = await run_manager("Summarize sales.", out_dir=tmp_path)
    assert result.status == "completed"
    assert (tmp_path / "evidence.json").exists()
    assert (tmp_path / "report.md").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_parse_args_variants() -> None:
    request, as_json, out = entrypoint._parse_args(["Summarize sales."])
    assert (request, as_json, out) == ("Summarize sales.", False, None)

    request, as_json, out = entrypoint._parse_args(["--json", "Summarize sales."])
    assert (request, as_json, out) == ("Summarize sales.", True, None)

    request, as_json, out = entrypoint._parse_args(["--out", "dir", "Summarize sales."])
    assert (request, as_json, out) == ("Summarize sales.", False, Path("dir"))

    with pytest.raises(SystemExit):
        entrypoint._parse_args([])  # missing request


def _patched_run_manager(monkeypatch) -> ManagerRunResult:
    async def fake_run_manager(request, out_dir=None):
        return ManagerRunResult(
            report="# Report",
            status="completed",
            sub_questions=["Q1?", "Q2?"],
            evidence=[EvidenceRecord(0, "Q1?"), EvidenceRecord(1, "Q2?")],
            attempts=1,
            state={"status": ManagerStatus.COMPLETED},
            error=None,
        )

    monkeypatch.setattr(entrypoint, "run_manager", fake_run_manager)
    return ManagerRunResult(
        report="# Report",
        status="completed",
        sub_questions=["Q1?", "Q2?"],
        evidence=[EvidenceRecord(0, "Q1?"), EvidenceRecord(1, "Q2?")],
        attempts=1,
        state={"status": ManagerStatus.COMPLETED},
        error=None,
    )


def test_main_json_output(monkeypatch, capsys) -> None:
    _patched_run_manager(monkeypatch)
    entrypoint.main(["--json", "Summarize sales."])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert payload["sub_questions"] == ["Q1?", "Q2?"]
    assert payload["error"] is None


def test_main_plain_output(monkeypatch, capsys) -> None:
    _patched_run_manager(monkeypatch)
    entrypoint.main(["Summarize sales."])
    out = capsys.readouterr().out
    assert "Status: completed" in out
    assert "Sub-analyses: 2" in out
    assert "  - Q1?" in out
    assert "# Report" in out


def test_main_plain_output_without_report(monkeypatch, capsys) -> None:
    async def fake_run_manager(request, out_dir=None):
        return ManagerRunResult(
            report=None,
            status="failed",
            sub_questions=[],
            evidence=[],
            attempts=2,
            state={"status": ManagerStatus.FAILED},
            error="bad plan",
        )

    monkeypatch.setattr(entrypoint, "run_manager", fake_run_manager)
    entrypoint.main(["Summarize sales."])
    out = capsys.readouterr().out
    assert "Status: failed" in out
    assert "Error: bad plan" in out
    assert "(no report produced)" in out


def test_main_passes_out_dir(monkeypatch, tmp_path, capsys) -> None:
    seen: dict = {}

    async def fake_run_manager(request, out_dir=None):
        seen["out_dir"] = out_dir
        return ManagerRunResult(
            report=None,
            status="failed",
            sub_questions=[],
            evidence=[],
            attempts=1,
            state={},
            error="x",
        )

    monkeypatch.setattr(entrypoint, "run_manager", fake_run_manager)
    entrypoint.main(["--out", str(tmp_path), "Summarize sales."])
    assert seen["out_dir"] == tmp_path


def test_main_reports_configuration_error(monkeypatch) -> None:
    async def fake_run_manager(request, out_dir=None):
        raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")

    monkeypatch.setattr(entrypoint, "run_manager", fake_run_manager)
    with pytest.raises(SystemExit, match="Configuration error"):
        entrypoint.main(["Summarize sales."])


def test_manager_main_module_guard_runs() -> None:
    """Running `python -m app.manager` with no args hits the usage guard."""
    project_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [_sys.executable, "-m", "app.manager"], capture_output=True, text=True, cwd=project_root
    )
    assert proc.returncode != 0
    assert "usage" in (proc.stderr or "").lower()


# ---------------------------------------------------------------------------
# create_manager_llm provider selection
# ---------------------------------------------------------------------------


def test_create_manager_llm_llamacpp_is_the_default(monkeypatch, _reset_settings_cache) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "llamacpp")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = create_manager_llm()
    assert isinstance(client, ManagerLLMClient)
    assert client.base_url == get_settings().llm_base_url
    assert client.api_key is None  # local server: no auth


def test_create_manager_llm_openrouter_selection(monkeypatch, _reset_settings_cache) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    client = create_manager_llm()
    assert client.model == "openai/gpt-4o-mini"
    assert client.api_key == "sk-test"
    assert client.base_url.endswith("openrouter.ai/api/v1")


def test_create_manager_llm_openrouter_requires_api_key(monkeypatch, _reset_settings_cache) -> None:
    # An empty env var beats a developer's project .env (pydantic-settings
    # precedence), mirroring the agent factory test.
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        create_manager_llm()
