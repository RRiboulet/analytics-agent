"""Unit tests for the M7.6 management-level evaluation extension."""

import pytest

from app.evaluation import manager_runner
from app.evaluation.manager_dataset import (
    load_manager_dataset,
    select_manager_cases,
)
from app.evaluation.manager_runner import (
    ManagerCaseResult,
    is_manager_passed,
    run_manager_case,
    structural_checks,
)
from app.evaluation.report import aggregate
from app.manager.entrypoint import ManagerRunResult
from app.manager.evidence import EvidenceRecord
from scripts.run_manager_evaluation import _record


def _case_result(**overrides):
    """A minimal graded manager case carrying real per-tool call counts."""
    fields = {
        "case_id": "manager-001",
        "question": "How did revenue trend?",
        "difficulty": "easy",
        "category": "trend",
        "status": "completed",
        "passed": True,
        "attempts": 1,
        "latency_seconds": 1.5,
        "tool_calls": {"query": 3, "list_tables": 1},
        "answer": "Revenue rose.",
    }
    fields.update(overrides)
    return ManagerCaseResult(**fields)


def test_manager_dataset_loads():
    cases = load_manager_dataset("data/evaluation/manager_v1.yaml")
    assert len(cases) >= 4
    assert cases[0].case_id == "manager-001"
    ids = {case.case_id for case in cases}
    assert len(ids) == len(cases)


def test_select_manager_cases():
    cases = load_manager_dataset("data/evaluation/manager_v1.yaml")
    selected = select_manager_cases(cases, difficulty="hard")
    assert all(c.difficulty == "hard" for c in selected)
    selected = select_manager_cases(cases, case_id="manager-002")
    assert len(selected) == 1


def test_structural_pass():
    result = ManagerRunResult(
        report="Final grounded report",
        status="completed",
        sub_questions=["q1", "q2"],
        evidence=[EvidenceRecord(sub_index=0, sub_question="q1", error=None)],
        attempts=1,
        state={"groundedness_error": None},
        follow_up_questions=["f1"],
    )
    checks = structural_checks(result)
    assert checks == {
        "decomposition_valid": True,
        "follow_up_valid": True,
        "completion_valid": True,
        "groundedness_valid": True,
        "sub_analyses_valid": True,
    }
    assert is_manager_passed(checks)


def test_structural_failures():
    bad = ManagerRunResult(
        report=None,
        status="failed",
        sub_questions=["q1", "q1", "", "q4", "q5"],
        evidence=[EvidenceRecord(sub_index=0, sub_question="q1", error="query failed")],
        attempts=2,
        state={"groundedness_error": "number 9 not grounded"},
        follow_up_questions=["f1", "f2", "f3"],
    )
    checks = structural_checks(bad)
    assert not checks["decomposition_valid"]
    assert not checks["follow_up_valid"]
    assert not checks["completion_valid"]
    assert not checks["groundedness_valid"]
    assert not checks["sub_analyses_valid"]
    assert not is_manager_passed(checks)


def test_manager_record_carries_tool_calls():
    """The benchmark script's own record must not drop the per-case counts."""
    record = _record(_case_result())
    assert record["tool_calls"] == {"query": 3, "list_tables": 1}


@pytest.mark.asyncio
async def test_graded_case_carries_the_runs_tool_calls(monkeypatch):
    """Regression: the graded case hardcoded ``tool_calls={}``, so the whole
    benchmark reported zero no matter how much a run actually spent."""

    async def fake_run_manager(request, out_dir=None):
        return ManagerRunResult(
            report="Revenue rose.",
            status="completed",
            sub_questions=["How did revenue trend?"],
            evidence=[EvidenceRecord(sub_index=0, sub_question="How did revenue trend?")],
            attempts=1,
            state={"status": "completed"},
            tool_calls={"query": 3, "list_tables": 1},
        )

    monkeypatch.setattr(manager_runner, "run_manager", fake_run_manager)
    case = load_manager_dataset("data/evaluation/manager_v1.yaml")[0]

    result = await run_manager_case(case)

    assert result.tool_calls == {"query": 3, "list_tables": 1}
    # …and it survives to the record the benchmark writes out.
    assert _record(result)["tool_calls"] == {"query": 3, "list_tables": 1}


def test_record_tool_calls_match_aggregate_total():
    """summary.json's total must equal the sum of the per-case records."""
    results = [
        _case_result(case_id="manager-001", tool_calls={"query": 3, "list_tables": 1}),
        _case_result(
            case_id="manager-002",
            tool_calls={"query": 2, "describe_table": 4},
            passed=False,
        ),
    ]
    summary = aggregate(results)
    records = [_record(r) for r in results]
    assert summary["total_tool_calls"] == sum(
        sum(record["tool_calls"].values()) for record in records
    )
    assert summary["total_tool_calls"] > 0


def test_manager_case_result_records_checks():
    result = ManagerRunResult(
        report="Report",
        status="completed",
        sub_questions=["q1"],
        evidence=[EvidenceRecord(sub_index=0, sub_question="q1", error=None)],
        attempts=1,
        state={"groundedness_error": None},
    )
    checks = structural_checks(result)
    assert is_manager_passed(checks) is True
    assert result.sub_questions == ["q1"]
