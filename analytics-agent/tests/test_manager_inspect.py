"""Unit tests for the manager inspect stage (M7.5).

Covers the bounded-follow-up decision: deterministic parsing of the inspect
output (0..2 questions, "NONE" is a valid outcome), the inspect LLM
capability (real client against a stubbed transport plus the fake), evidence
round labeling, and the graph integration — no-follow-up skips the round,
follow-ups run exactly one round before synthesis, follow-up failures are
recorded, transient inspect LLM errors retry into inspect, budget exhaustion
or unusable output degrades to no follow-up instead of failing a run that
holds valid evidence. No network, no database, no live model.
"""

import json

import httpx
import pytest

from app.agent.llm import LLMError
from app.manager.evidence import EvidenceRecord
from app.manager.graph import FOLLOW_UPS_EDGE, INSPECT, ManagerServices, build_manager_graph
from app.manager.inspect import (
    MAX_FOLLOW_UP_QUESTIONS,
    FollowUpError,
    parse_follow_up_questions,
)
from app.manager.llm import FakeManagerLLM, ManagerLLMClient
from app.manager.state import ManagerStatus
from app.manager.synthesize import format_evidence
from tests.test_manager_graph import StubAnalyst, _failed_sub_state, _ok_sub_state, _services

# ---------------------------------------------------------------------------
# Inspect output parsing
# ---------------------------------------------------------------------------


def test_parse_empty_output_means_no_follow_up() -> None:
    assert parse_follow_up_questions("") == []
    assert parse_follow_up_questions("\n\n") == []


def test_parse_no_follow_up_markers() -> None:
    assert parse_follow_up_questions("NONE") == []
    assert parse_follow_up_questions("NONE.") == []  # trailing punctuation stripped
    assert parse_follow_up_questions("n/a") == []
    assert parse_follow_up_questions("\nno follow-up\n") == []
    assert parse_follow_up_questions("No Follow Up") == []


def test_parse_markup_and_numbering_tolerated() -> None:
    raw = "1. What drives the anomaly?\n- Drill into the top seller?"
    assert parse_follow_up_questions(raw) == [
        "What drives the anomaly?",
        "Drill into the top seller?",
    ]
    assert parse_follow_up_questions("* Third question?") == ["Third question?"]


def test_parse_deduplicates_preserving_order() -> None:
    raw = "Drill into top seller?\nDrill into top seller?\nWhat drove the drop?"
    assert parse_follow_up_questions(raw) == [
        "Drill into top seller?",
        "What drove the drop?",
    ]


def test_parse_ignores_code_fence_and_blank_lines() -> None:
    raw = "```\nWhat drove the drop?\n```"
    assert parse_follow_up_questions(raw) == ["What drove the drop?"]


def test_parse_up_to_the_hard_cap() -> None:
    assert len(parse_follow_up_questions("Q1?\nQ2?")) == MAX_FOLLOW_UP_QUESTIONS


def test_parse_over_cap_raises_follow_up_error() -> None:
    raw = "\n".join(f"Q{i}?" for i in range(MAX_FOLLOW_UP_QUESTIONS + 1))
    with pytest.raises(FollowUpError, match="hard cap is 2"):
        parse_follow_up_questions(raw)


def test_parse_treats_unexpected_prose_as_a_question() -> None:
    # Same tolerance as decomposition: a plain non-marker line is a question
    # (the prompt instructs exactly NONE; anything else is taken literally).
    assert parse_follow_up_questions("Reviewing the findings, no drill-down is needed.") == [
        "Reviewing the findings, no drill-down is needed."
    ]


# ---------------------------------------------------------------------------
# Inspect LLM capability
# ---------------------------------------------------------------------------


async def test_fake_manager_llm_inspect_records_and_returns() -> None:
    llm = FakeManagerLLM(follow_up_raw="What drove the drop?")
    assert await llm.inspect("req", "evidence text") == "What drove the drop?"
    assert llm.inspect_calls == [("req", "evidence text")]


async def test_fake_manager_llm_inspect_raises_configured_error() -> None:
    llm = FakeManagerLLM(llm_error=LLMError("LLM down"))
    with pytest.raises(LLMError, match="LLM down"):
        await llm.inspect("req", "evidence text")


def _inspect_transport(captured: dict) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(200, json={"choices": [{"message": {"content": "NONE"}}]})

    return httpx.MockTransport(handler)


async def test_manager_llm_client_inspect_request_shape() -> None:
    captured: dict = {}
    client = ManagerLLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        max_tokens=4096,
        answer_max_tokens=64,  # inspect uses the smaller answer cap
        transport=_inspect_transport(captured),
    )
    raw = await client.inspect("Summarize sales.", "Sub-question 0: ...")
    assert raw == "NONE"  # whitespace stripped by the shared completion path
    payload = json.loads(captured["payload"])
    assert payload["model"] == "gemma"
    assert payload["max_tokens"] == 64  # bounded decision output, not the SQL budget
    system, user = (m["content"] for m in payload["messages"])
    assert "at most 2" in system
    assert "exactly the word NONE" in system
    assert "Summarize sales." in user
    assert "Sub-question 0: ..." in user


# ---------------------------------------------------------------------------
# Evidence round labeling
# ---------------------------------------------------------------------------


def test_evidence_record_marks_follow_ups() -> None:
    record = EvidenceRecord.from_agent_state(
        2, "What drove the drop?", _ok_sub_state(), is_follow_up=True
    )
    assert record.is_follow_up is True
    assert record.answer == "found it"
    assert not EvidenceRecord.from_agent_state(0, "q", _ok_sub_state()).is_follow_up


def test_format_evidence_labels_follow_ups_distinctly() -> None:
    records = [
        EvidenceRecord(sub_index=0, sub_question="Revenue?", rows=[{"revenue": 10.5}]),
        EvidenceRecord(sub_index=1, sub_question="Why the drop?", is_follow_up=True, rows=[]),
    ]
    text = format_evidence(records)
    assert "Sub-question 0: Revenue?" in text
    assert "Follow-up question 1: Why the drop?" in text


# ---------------------------------------------------------------------------
# Graph integration
# ---------------------------------------------------------------------------


async def _invoke(services: ManagerServices, request: str = "Summarize sales.") -> dict:
    graph = build_manager_graph(services)
    return await graph.ainvoke({"request": request})


async def test_no_follow_up_goes_straight_to_synthesis() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = FakeManagerLLM(raw="Revenue by category?", report="Revenue was 10.5.")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED
    assert state["follow_up_questions"] == []
    assert state.get("follow_up_rounds", 0) == 0  # the round was never spent
    assert state.get("inspect_error") is None
    assert analyst.calls == ["Revenue by category?"]  # no follow-up analyst runs
    assert len(llm.report_calls) == 1  # synthesis still ran
    assert state["report"] == "Revenue was 10.5."
    assert len(llm.inspect_calls) == 1


async def test_inspect_receives_the_formatted_evidence() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = FakeManagerLLM(raw="Revenue by category?", report="ok")
    await _invoke(_services(llm, analyst))
    assert llm.inspect_calls[0][0] == "Summarize sales."
    assert "Sub-question 0: Revenue by category?" in llm.inspect_calls[0][1]


async def test_follow_up_round_runs_one_bounded_round_before_synthesis() -> None:
    analyst = StubAnalyst(
        {
            "Revenue by category?": _ok_sub_state(),
            "What share does the top seller hold?": _ok_sub_state("seller answer"),
        }
    )
    llm = FakeManagerLLM(
        raw="Revenue by category?",
        follow_up_raw="What share does the top seller hold?",
        report="ok",
    )
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED
    assert state["follow_up_questions"] == ["What share does the top seller hold?"]
    assert state["follow_up_rounds"] == 1  # single round, spent
    # Sub-analysis then exactly one follow-up round, in order.
    assert analyst.calls == ["Revenue by category?", "What share does the top seller hold?"]
    assert len(state["evidence"]) == 2
    follow_up = state["evidence"][1]
    assert follow_up.is_follow_up is True
    assert follow_up.sub_index == 1  # numbered after the sub-analyses
    assert follow_up.answer == "seller answer"
    assert state["evidence"][0].is_follow_up is False
    # The follow-up evidence flows into the synthesis grounding text.
    assert "Follow-up question 1: What share does the top seller hold?" in llm.report_calls[0][1]


async def test_report_grounded_in_follow_up_evidence_passes() -> None:
    analyst = StubAnalyst(
        {
            "Revenue by category?": _ok_sub_state(),
            "What share does the top seller hold?": {
                "status": "completed",
                "bounded_sql": "SELECT share LIMIT 100",
                "result": [{"share": 12.5}],
                "answer": "seller share",
            },
        }
    )
    llm = FakeManagerLLM(
        raw="Revenue by category?",
        follow_up_raw="What share does the top seller hold?",
        # 10.5 comes from the sub-analysis, 12.5 from the follow-up round.
        report="Revenue was 10.5 and the top seller held 12.5%.",
    )
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED
    assert state.get("groundedness_error") is None
    assert state["report"] == "Revenue was 10.5 and the top seller held 12.5%."


async def test_follow_up_at_cap_is_accepted() -> None:
    analyst = StubAnalyst(
        {
            "Revenue by category?": _ok_sub_state(),
            "One?": _ok_sub_state(),
            "Two?": _ok_sub_state(),
        }
    )
    llm = FakeManagerLLM(raw="Revenue by category?", follow_up_raw="One?\nTwo?", report="ok")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED
    assert state["follow_up_questions"] == ["One?", "Two?"]
    assert len(state["evidence"]) == 3


async def test_follow_up_failure_is_recorded_and_continues() -> None:
    analyst = StubAnalyst(
        {
            "Revenue by category?": _ok_sub_state(),
            "Why the drop?": _failed_sub_state("query failed"),
        }
    )
    llm = FakeManagerLLM(raw="Revenue by category?", follow_up_raw="Why the drop?", report="ok")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED  # primary evidence exists
    assert state["follow_up_errors"] == ["follow-up 0 (Why the drop?): query failed"]
    assert state["evidence"][1].error == "query failed"
    assert len(llm.report_calls) == 1  # still synthesized


async def test_unusable_inspect_output_degrades_to_no_follow_up() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    over_cap = "\n".join(f"Q{i}?" for i in range(MAX_FOLLOW_UP_QUESTIONS + 1))
    llm = FakeManagerLLM(raw="Revenue by category?", follow_up_raw=over_cap, report="ok")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED  # never fails a valid run
    assert "hard cap is 2" in state["inspect_error"]
    assert state["follow_up_questions"] == []
    assert analyst.calls == ["Revenue by category?"]  # no follow-up ran
    assert len(llm.inspect_calls) == 1  # deterministic output: no retry


class FlakyInspectLLM(FakeManagerLLM):
    """Fails the first ``fail_count`` inspect calls, succeeds afterwards."""

    def __init__(self, **kwargs: object) -> None:
        self.fail_count = kwargs.pop("fail_count", 1)
        super().__init__(**kwargs)

    async def inspect(self, request: str, evidence: str) -> str:
        self.inspect_calls.append((request, evidence))
        if len(self.inspect_calls) <= self.fail_count:
            raise LLMError("HTTP 429 too many requests")
        return self.follow_up_raw


class InspectDownLLM(FakeManagerLLM):
    """Inspect always fails with an LLMError; decompose and synthesize work."""

    async def inspect(self, request: str, evidence: str) -> str:
        self.inspect_calls.append((request, evidence))
        raise LLMError("HTTP 503 inspect down")


async def test_transient_inspect_llm_error_retries_into_inspect() -> None:
    analyst = StubAnalyst(
        {
            "Revenue by category?": _ok_sub_state(),
            "What drove the drop?": _ok_sub_state(),
        }
    )
    llm = FlakyInspectLLM(
        raw="Revenue by category?", follow_up_raw="What drove the drop?", report="ok"
    )
    state = await _invoke(_services(llm, analyst, max_attempts=2))

    assert state["status"] is ManagerStatus.COMPLETED
    assert len(llm.inspect_calls) == 2  # first failed, retry succeeded
    assert len(llm.calls) == 1  # retry targeted inspect, not decompose
    assert state["follow_up_questions"] == ["What drove the drop?"]
    assert analyst.calls == ["Revenue by category?", "What drove the drop?"]
    assert state.get("inspect_error") is None
    assert len(llm.report_calls) == 1


async def test_persistent_inspect_llm_error_degrades_instead_of_failing() -> None:
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})
    llm = InspectDownLLM(raw="Revenue by category?", report="Revenue was 10.5.")
    state = await _invoke(_services(llm, analyst, max_attempts=2))

    assert state["status"] is ManagerStatus.COMPLETED  # evidence exists -> report ships
    assert len(llm.inspect_calls) == 2  # bounded retries, then degrade
    assert "inspect LLM call failed" in state["inspect_error"]
    assert state["follow_up_questions"] == []
    assert state.get("llm_error") is None  # cleared: not a caller-facing error
    assert analyst.calls == ["Revenue by category?"]  # no follow-up ran
    assert state["report"] == "Revenue was 10.5."  # primary evidence still synthesized


async def test_inspect_retry_shares_decompose_attempt_budget() -> None:
    # A decompose retry consumes the shared budget, so a single inspect
    # failure at max_attempts=2 degrades immediately without another retry.
    analyst = StubAnalyst({"Revenue by category?": _ok_sub_state()})

    class FlakyDecompose(InspectDownLLM):
        async def decompose(self, request: str, table_names: list[str]) -> str:
            self.calls.append((request, tuple(table_names)))
            if len(self.calls) == 1:
                raise LLMError("HTTP 429")
            return self.raw

    flaky = FlakyDecompose(raw="Revenue by category?")
    state = await _invoke(_services(flaky, analyst, max_attempts=2))

    assert state["status"] is ManagerStatus.COMPLETED
    assert len(flaky.calls) == 2  # decompose retried once (budget now spent)
    assert len(flaky.inspect_calls) == 1  # no retry left: inspect degraded directly
    assert "inspect LLM call failed" in state["inspect_error"]


async def test_all_failed_sub_analyses_skip_inspect_and_synthesis() -> None:
    analyst = StubAnalyst({"Revenue by category?": _failed_sub_state("query failed")})
    llm = FakeManagerLLM(raw="Revenue by category?")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.FAILED
    assert len(llm.inspect_calls) == 0
    assert len(llm.report_calls) == 0


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------


def test_manager_graph_includes_inspect_nodes() -> None:
    graph = build_manager_graph(_services(FakeManagerLLM()))
    nodes = set(graph.get_graph().nodes)
    assert {
        "decompose",
        "run_sub_analyses",
        "inspect",
        "run_follow_ups",
        "synthesize",
        "retry",
        "fail",
    } <= nodes


def test_m75_edge_constants_are_stable() -> None:
    assert INSPECT == "inspect"
    assert FOLLOW_UPS_EDGE == "follow_ups"
    assert MAX_FOLLOW_UP_QUESTIONS == 2


# ---------------------------------------------------------------------------
# Entrypoint integration (run_manager, artifacts, CLI)
# ---------------------------------------------------------------------------

from app.manager import entrypoint  # noqa: E402
from app.manager.entrypoint import ManagerRunResult, _run_error  # noqa: E402
from tests.test_manager_entrypoint import FakeCaps, FakeFullLLM  # noqa: E402


async def test_run_manager_end_to_end_with_follow_up(monkeypatch) -> None:
    monkeypatch.setattr(
        entrypoint,
        "create_manager_llm",
        lambda: FakeFullLLM(raw="Revenue by category?", follow_up_raw="What drove the drop?"),
    )
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)

    result = await entrypoint.run_manager("Summarize sales.")

    assert result.status == "completed"
    assert result.error is None
    assert result.sub_questions == ["Revenue by category?"]
    assert result.follow_up_questions == ["What drove the drop?"]
    assert len(result.evidence) == 2  # one sub-analysis + one follow-up
    assert result.evidence[0].is_follow_up is False
    assert result.evidence[1].is_follow_up is True
    assert result.state["follow_up_rounds"] == 1


async def test_write_artifacts_include_follow_up_fields(tmp_path) -> None:
    state = {
        "status": ManagerStatus.COMPLETED,
        "attempts": 1,
        "sub_questions": ["Q1?"],
        "sub_analysis_errors": [],
        "decomposition_error": None,
        "groundedness_error": None,
        "follow_up_questions": ["F1?"],
        "follow_up_errors": ["follow-up 0 (F1?): query failed"],
        "follow_up_rounds": 1,
        "inspect_error": None,
        "report": "# Report",
        "evidence": [
            EvidenceRecord(
                sub_index=0,
                sub_question="Q1?",
                rows=[{"revenue": 10.5}],
            ),
            EvidenceRecord(
                sub_index=1,
                sub_question="F1?",
                is_follow_up=True,
                rows=[],
            ),
        ],
    }
    entrypoint._write_artifacts(tmp_path, "Summarize sales.", state)

    payload = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert payload["follow_up_questions"] == ["F1?"]
    assert payload["follow_up_errors"] == ["follow-up 0 (F1?): query failed"]
    assert payload["follow_up_rounds"] == 1
    assert payload["inspect_error"] is None
    assert payload["evidence"][1]["is_follow_up"] is True
    assert (tmp_path / "report.md").exists()


def _follow_up_result() -> ManagerRunResult:
    return ManagerRunResult(
        report="# Report",
        status="completed",
        sub_questions=["Q1?"],
        evidence=[EvidenceRecord(0, "Q1?")],
        attempts=1,
        follow_up_questions=["What drove the drop?"],
        state={"status": ManagerStatus.COMPLETED},
        error=None,
    )


def test_main_prints_follow_ups(monkeypatch, capsys) -> None:
    async def fake_run_manager(request, out_dir=None):
        return _follow_up_result()

    monkeypatch.setattr(entrypoint, "run_manager", fake_run_manager)
    entrypoint.main(["Summarize sales."])
    out = capsys.readouterr().out
    assert "Follow-ups: 1" in out
    assert "  - What drove the drop?" in out


def test_main_json_includes_follow_ups(monkeypatch, capsys) -> None:
    async def fake_run_manager(request, out_dir=None):
        return _follow_up_result()

    monkeypatch.setattr(entrypoint, "run_manager", fake_run_manager)
    entrypoint.main(["--json", "Summarize sales."])
    payload = json.loads(capsys.readouterr().out)
    assert payload["follow_up_questions"] == ["What drove the drop?"]


def test_run_error_ignores_inspect_degradation_on_completed_run() -> None:
    # A degraded inspect stage is observable, not an error for the caller:
    # the run completed with a grounded report from the primary evidence.
    state = {
        "status": ManagerStatus.COMPLETED,
        "inspect_error": "inspect LLM call failed: HTTP 503",
        "follow_up_questions": [],
    }
    assert _run_error(state) is None
