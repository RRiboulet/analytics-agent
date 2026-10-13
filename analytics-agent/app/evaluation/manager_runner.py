"""Run and structurally grade management-level evaluation cases (M7.6).

Grading is pure structural: a case passes when the manager run completes
with a report, its decomposition stays inside the D009 budgets (1-4
sub-questions, 0-2 follow-ups), every sub-analysis is grounded, and the
report survives the deterministic groundedness check. There is no reference
SQL/result comparison — management scenarios are open-ended by design.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.runner import CaseResult
from app.manager.entrypoint import ManagerRunResult, run_manager


@dataclass
class ManagerCaseResult(CaseResult):
    """Structural outcome of a management-level evaluation case."""

    # D009 budgets.
    decomposition_valid: bool = False
    follow_up_valid: bool = False
    groundedness_valid: bool = False
    completion_valid: bool = False
    sub_analyses_valid: bool = False
    sub_questions: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)


def structural_checks(result: ManagerRunResult) -> dict[str, bool]:
    """Compute the structural pass/fail checks for a manager run."""
    state = result.state
    sub_questions = result.sub_questions or []
    follow_ups = result.follow_up_questions or []
    evidence = result.evidence or []

    decomposition_valid = (
        1 <= len(sub_questions) <= 4
        and all(isinstance(q, str) and q.strip() for q in sub_questions)
        and len(sub_questions) == len(set(sub_questions))
    )
    follow_up_valid = (
        len(follow_ups) <= 2
        and all(isinstance(q, str) and q.strip() for q in follow_ups)
        and len(follow_ups) == len(set(follow_ups))
    )
    completion_valid = result.status == "completed" and bool(result.report)
    groundedness_error = state.get("groundedness_error") if hasattr(state, "get") else None
    groundedness_valid = completion_valid and groundedness_error is None
    sub_analyses_valid = bool(evidence) and all(
        getattr(record, "error", None) is None for record in evidence
    )
    return {
        "decomposition_valid": decomposition_valid,
        "follow_up_valid": follow_up_valid,
        "completion_valid": completion_valid,
        "groundedness_valid": groundedness_valid,
        "sub_analyses_valid": sub_analyses_valid,
    }


def is_manager_passed(checks: dict[str, bool]) -> bool:
    return all(checks.values())


def _failure_reason(result: ManagerRunResult, checks: dict[str, bool]) -> str:
    if result.error:
        return result.error
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        return "structural failure: " + ", ".join(failed)
    return ""


async def run_manager_case(
    case: Any,
    *,
    out_dir: str | None = None,
) -> ManagerCaseResult:
    """Run one management scenario end-to-end and grade it structurally."""
    start = time.monotonic()
    artifact_dir = f"{out_dir}/{case.case_id}" if out_dir else None
    result = await run_manager(case.request, out_dir=artifact_dir)
    latency = round(time.monotonic() - start, 3)
    checks = structural_checks(result)
    passed = is_manager_passed(checks)
    status = result.status or "unknown"
    return ManagerCaseResult(
        case_id=case.case_id,
        question=case.request,
        difficulty=case.difficulty,
        category=case.category,
        status=str(status),
        passed=passed,
        attempts=result.attempts,
        latency_seconds=latency,
        tool_calls={},
        agent_sql=None,
        comparison_detail="; ".join(f"{k}={v}" for k, v in checks.items()),
        failure_reason=_failure_reason(result, checks),
        missing_tables=[],
        answer=result.report,
        decomposition_valid=checks["decomposition_valid"],
        follow_up_valid=checks["follow_up_valid"],
        groundedness_valid=checks["groundedness_valid"],
        completion_valid=checks["completion_valid"],
        sub_analyses_valid=checks["sub_analyses_valid"],
        sub_questions=list(result.sub_questions),
        follow_up_questions=list(result.follow_up_questions),
    )


class ManagerEvaluationRunner:
    """Sequentially runs and structurally grades management cases."""

    def __init__(self, out_dir: str | None = None) -> None:
        self._out_dir = out_dir

    async def run_case(self, case: Any) -> ManagerCaseResult:
        return await run_manager_case(case, out_dir=self._out_dir)

    async def run_all(
        self,
        cases: list[Any],
        on_result: Any = None,
    ) -> list[ManagerCaseResult]:
        results: list[ManagerCaseResult] = []
        for case in cases:
            case_result = await self.run_case(case)
            results.append(case_result)
            if on_result is not None:
                on_result(case_result)
        return results
