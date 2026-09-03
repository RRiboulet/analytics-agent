"""Unit tests for the manager synthesis stage (M7.3).

Covers the deterministic groundedness check (number extraction, evidence
value collection, tolerance semantics), evidence formatting, the synthesis
LLM capability (real client against a stubbed transport plus the fake), and
the graph integration: grounded reports ship, fabricated reports fail the
run without storing the report, and transient synthesis LLM failures retry
bounded. No network, no database, no live model.
"""

import json
from decimal import Decimal

import httpx

from app.agent.llm import LLMError
from app.manager.evidence import EvidenceRecord
from app.manager.graph import ManagerServices, build_manager_graph
from app.manager.llm import FakeManagerLLM, ManagerLLMClient
from app.manager.state import ManagerStatus
from app.manager.synthesize import (
    collect_evidence_values,
    extract_report_numbers,
    format_evidence,
    groundedness_violation,
)
from tests.test_manager_graph import StubAnalyst, _failed_sub_state, _ok_sub_state


def _evidence(rows: list[dict] | None) -> EvidenceRecord:
    return EvidenceRecord(sub_index=0, sub_question="q", rows=rows)


# ---------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------


def test_extract_plain_and_formatted_numbers() -> None:
    report = "Revenue was 1,234.56 (up 12.5%), costs -3.5, and 42 units."
    assert extract_report_numbers(report) == [1234.56, 12.5, -3.5, 42.0]


def test_extract_ignores_numbers_inside_words_and_versions() -> None:
    assert extract_report_numbers("See model R2D2 or version v1.2.3.") == []
    assert extract_report_numbers("Ending a sentence with 1.") == [1.0]


def test_extract_handles_multiple_thousands_separators() -> None:
    assert extract_report_numbers("Total: 1,234,567.89") == [1234567.89]


# ---------------------------------------------------------------------------
# Evidence value collection
# ---------------------------------------------------------------------------


def test_collect_numeric_cells_only() -> None:
    record = _evidence(
        [
            {"revenue": 10.5, "count": 3, "flag": True, "label": "2023-01-01", "n": None},
            {"revenue": Decimal("20.25")},
        ]
    )
    values = collect_evidence_values([record])
    assert sorted(values) == [3.0, 10.5, 20.25]  # bools/strings/None excluded


def test_collect_handles_missing_rows() -> None:
    assert collect_evidence_values([_evidence(None), EvidenceRecord(1, "q")]) == []


# ---------------------------------------------------------------------------
# Groundedness check
# ---------------------------------------------------------------------------


def test_report_without_numbers_is_trivially_grounded() -> None:
    assert groundedness_violation("No numbers here.", [_evidence([{"x": 1}])]) is None
    assert groundedness_violation("No numbers here.", []) is None


def test_grounded_report_passes() -> None:
    evidence = [_evidence([{"revenue": 1234.56}, {"share": 12.5}])]
    report = "Revenue was 1,234.56 with a 12.5% share."
    assert groundedness_violation(report, evidence) is None


def test_fabricated_number_violates() -> None:
    evidence = [_evidence([{"revenue": 10.5}])]
    violation = groundedness_violation("Revenue was 999.99.", evidence)
    assert violation is not None
    assert "999.99" in violation
    assert "groundedness violation" in violation


def test_tolerance_matches_the_m6_judges_semantics() -> None:
    evidence = [_evidence([{"revenue": 1000.0}])]
    # Within 1e-6 relative tolerance of 1000 -> 1000.001 passes, 1000.01 fails.
    assert groundedness_violation("Revenue 1000.001.", evidence) is None
    assert groundedness_violation("Revenue 1000.01.", evidence) is not None


def test_strings_and_bools_never_ground_numbers() -> None:
    evidence = [_evidence([{"label": "2023-01-01", "flag": True}])]
    assert groundedness_violation("In 2023 there were 1 cases.", evidence) is not None


def test_sign_matters_for_groundedness() -> None:
    evidence = [_evidence([{"delta": -3.5}])]
    assert groundedness_violation("The change was -3.5.", evidence) is None
    assert groundedness_violation("The change was 3.5.", evidence) is not None


# ---------------------------------------------------------------------------
# Evidence formatting
# ---------------------------------------------------------------------------


def test_format_evidence_success_block() -> None:
    record = EvidenceRecord(
        sub_index=2,
        sub_question="Revenue?",
        status="completed",
        sql="SELECT 1 LIMIT 100",
        rows=[{"revenue": 10.5}],
        answer="It is 10.5.",
    )
    text = format_evidence([record])
    assert "Sub-question 2: Revenue?" in text
    assert "Status: completed" in text
    assert "SELECT 1 LIMIT 100" in text
    assert json.dumps([{"revenue": 10.5}]) in text
    assert "Analyst answer: It is 10.5." in text


def test_format_evidence_failure_block_without_answer() -> None:
    record = EvidenceRecord(sub_index=0, sub_question="Q?", status="failed", error="query failed")
    text = format_evidence([record])
    assert "Status: FAILED — query failed" in text
    assert "SQL executed" not in text
    assert "Analyst answer" not in text


def test_format_evidence_success_block_without_answer() -> None:
    record = EvidenceRecord(sub_index=1, sub_question="Q?", status="completed", rows=[{"x": 1}])
    text = format_evidence([record])
    assert "Status: completed" in text
    assert "Analyst answer" not in text


# ---------------------------------------------------------------------------
# Synthesis LLM capability
# ---------------------------------------------------------------------------


async def test_fake_manager_llm_synthesize_returns_report_and_records() -> None:
    llm = FakeManagerLLM(report="# Report")
    assert await llm.synthesize("req", "evidence text") == "# Report"
    assert llm.report_calls == [("req", "evidence text")]


async def test_fake_manager_llm_synthesize_raises_configured_error() -> None:
    llm = FakeManagerLLM(llm_error=LLMError("LLM down"))
    try:
        await llm.synthesize("req", "evidence")
    except LLMError as error:
        assert "LLM down" in str(error)
    else:
        raise AssertionError("expected LLMError")


def _synthesize_transport(captured: dict) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(200, json={"choices": [{"message": {"content": "  Report  "}}]})

    return httpx.MockTransport(handler)


async def test_manager_llm_client_synthesize_request_shape() -> None:
    captured: dict = {}
    client = ManagerLLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        max_tokens=4096,
        transport=_synthesize_transport(captured),
    )
    raw = await client.synthesize("Summarize sales.", "Sub-question 0: ...")
    assert raw == "Report"  # whitespace stripped by the shared completion path
    payload = json.loads(captured["payload"])
    assert payload["model"] == "gemma"
    assert payload["max_tokens"] == 4096
    system, user = (m["content"] for m in payload["messages"])
    assert system.startswith("You are a senior data analyst")
    assert "no derived or computed numbers" in system
    assert "Summarize sales." in user
    assert "Sub-question 0: ..." in user


# ---------------------------------------------------------------------------
# Graph integration
# ---------------------------------------------------------------------------


class FlakySynthesisLLM(FakeManagerLLM):
    """Succeeds at decompose; fails the first ``fail_count`` synthesize calls."""

    fail_count: int

    def __init__(self, **kwargs: object) -> None:
        self.fail_count = kwargs.pop("fail_count", 1)
        super().__init__(**kwargs)

    async def synthesize(self, request: str, evidence: str) -> str:
        self.report_calls.append((request, evidence))
        if len(self.report_calls) <= self.fail_count:
            raise LLMError("HTTP 429 too many requests")
        return self.report


async def _run(llm, analyst, *, max_attempts: int = 2) -> dict:
    services = ManagerServices(
        llm=llm,
        run_analyst=analyst,
        max_attempts=max_attempts,
        table_names=["orders"],
    )
    graph = build_manager_graph(services)
    return await graph.ainvoke({"request": "Summarize sales."})


async def test_grounded_report_completes_and_is_stored() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = FakeManagerLLM(raw="Revenue by category?", report="Revenue was 10.5.")
    state = await _run(llm, analyst)

    assert state["status"] is ManagerStatus.COMPLETED
    assert state["report"] == "Revenue was 10.5."
    assert state["groundedness_error"] is None
    assert llm.report_calls[0][1].startswith("Sub-question 0")  # evidence was passed


async def test_fabricated_report_fails_without_storing_it() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = FakeManagerLLM(raw="Revenue by category?", report="Revenue was 999.99.")
    state = await _run(llm, analyst)

    assert state["status"] is ManagerStatus.FAILED
    assert state.get("report") is None  # never ship a fabricated report
    assert "999.99" in state["groundedness_error"]
    assert len(llm.report_calls) == 1  # deterministic violation: no retry


async def test_transient_synthesis_llm_error_retries_into_synthesis() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = FlakySynthesisLLM(raw="Revenue by category?", report="All good.")
    state = await _run(llm, analyst, max_attempts=2)

    assert state["status"] is ManagerStatus.COMPLETED
    assert state["report"] == "All good."
    assert len(llm.report_calls) == 2  # first failed, retry succeeded
    assert len(llm.calls) == 1  # retry targeted synthesis, not decompose


async def test_persistent_synthesis_llm_error_fails_bounded() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = FlakySynthesisLLM(raw="Revenue by category?", report="All good.", fail_count=10)
    state = await _run(llm, analyst, max_attempts=2)

    assert state["status"] is ManagerStatus.FAILED
    assert "HTTP 429" in state["llm_error"]
    assert len(llm.report_calls) == 2  # bounded by the shared attempt budget
    assert len(llm.calls) == 1  # decompose was not re-run


async def test_all_failed_sub_analyses_skip_synthesis() -> None:
    analyst = StubAnalyst({"Revenue by category?": _failed_sub_state("query failed")})
    llm = FakeManagerLLM(raw="Revenue by category?")
    state = await _run(llm, analyst)

    assert state["status"] is ManagerStatus.FAILED
    assert len(llm.report_calls) == 0  # nothing to synthesize from
