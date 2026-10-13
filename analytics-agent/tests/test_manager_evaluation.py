"""Unit tests for the M7.6 management-level evaluation extension."""

from app.evaluation.manager_dataset import (
    load_manager_dataset,
    select_manager_cases,
)
from app.evaluation.manager_runner import is_manager_passed, structural_checks
from app.manager.entrypoint import ManagerRunResult
from app.manager.evidence import EvidenceRecord


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
