"""Run the analytics agent over an evaluation dataset and judge each case.

Every case is a genuine agent run (real LLM, real MCP boundary). Judging is
deterministic: a case passes when the run completed AND the agent's result set
matches the reference SQL's result set. Structural metrics (attempts, tool
calls, latency, table relevance) are recorded alongside for reporting.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.capabilities import Capabilities
from app.agent.entrypoint import _run_question
from app.agent.llm import LLM
from app.agent.state import AgentState, AgentStatus
from app.evaluation.dataset import EvalCase
from app.evaluation.judges import compare_result_sets, referenced_tables


class CountingCapabilities:
    """Wraps the MCP capabilities and counts calls per tool.

    Delegating wrapper only: the underlying transport, results, and read-only
    boundary are untouched. Other attribute access is forwarded so the same
    lifecycle methods (e.g. ``close``) remain reachable.
    """

    def __init__(self, inner: Capabilities) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls[name] = self.calls.get(name, 0) + 1
        return await self._inner.call_tool(name, args)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


@dataclass
class CaseResult:
    """Evaluation outcome and structural metrics for one dataset case."""

    case_id: str
    question: str
    difficulty: str
    category: str
    status: str
    passed: bool
    attempts: int
    latency_seconds: float
    tool_calls: dict[str, int] = field(default_factory=dict)
    agent_sql: str | None = None
    comparison_detail: str = ""
    failure_reason: str = ""
    missing_tables: list[str] = field(default_factory=list)
    answer: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "difficulty": self.difficulty,
            "category": self.category,
            "status": self.status,
            "passed": self.passed,
            "attempts": self.attempts,
            "latency_seconds": self.latency_seconds,
            "tool_calls": self.tool_calls,
            "agent_sql": self.agent_sql,
            "comparison_detail": self.comparison_detail,
            "failure_reason": self.failure_reason,
            "missing_tables": self.missing_tables,
            "answer": self.answer,
        }


def _failure_reason(state: AgentState) -> str:
    """Extract the most specific error the run recorded."""
    return str(
        state.get("llm_error")
        or state.get("query_error")
        or state.get("validation_error")
        or (state.get("retrieval_errors") or ["unknown failure"])[0]
    )


async def _execute_reference(capabilities: Capabilities, sql: str) -> tuple[list[dict], str | None]:
    """Execute reference SQL through the read-only MCP boundary."""
    result = await capabilities.call_tool("query", {"sql": sql})
    if not result.get("valid", False):
        return [], f"reference SQL failed to execute: {result.get('message', 'unknown error')}"
    return result.get("entries") or [], None


class EvaluationRunner:
    """Sequentially runs and judges every dataset case."""

    def __init__(
        self, llm: LLM, capabilities: Capabilities, max_attempts: int, max_rows: int
    ) -> None:
        self._llm = llm
        self._capabilities = capabilities
        self._max_attempts = max_attempts
        self._max_rows = max_rows

    async def run_case(self, case: EvalCase) -> CaseResult:
        counting = CountingCapabilities(self._capabilities)
        start = time.monotonic()
        state = await _run_question(
            case.question,
            llm=self._llm,
            capabilities=counting,
            max_attempts=self._max_attempts,
            max_rows=self._max_rows,
            tracer=None,
        )
        latency = time.monotonic() - start
        status = state.get("status")
        agent_sql = state.get("bounded_sql") or state.get("sql")

        result = CaseResult(
            case_id=case.case_id,
            question=case.question,
            difficulty=case.difficulty,
            category=case.category,
            status=str(status),
            passed=False,
            attempts=state.get("attempts", 0),
            latency_seconds=round(latency, 3),
            tool_calls=dict(counting.calls),
            agent_sql=agent_sql,
            answer=state.get("answer"),
        )
        if status != AgentStatus.COMPLETED:
            result.failure_reason = _failure_reason(state)
            return result

        agent_rows = state.get("result") or []
        reference_rows, reference_error = await _execute_reference(
            self._capabilities, case.reference_sql
        )
        if reference_error:
            result.failure_reason = reference_error
            return result

        comparison = compare_result_sets(agent_rows, reference_rows, ordered=case.ordered)
        result.comparison_detail = comparison.detail
        result.passed = comparison.matches
        result.missing_tables = [
            table
            for table in case.expected_tables
            if table not in referenced_tables(agent_sql or "", case.expected_tables)
        ]
        return result

    async def run_all(
        self,
        cases: list[EvalCase],
        on_result: Callable[[CaseResult], None] | None = None,
    ) -> list[CaseResult]:
        """Run every case sequentially; ``on_result`` fires as soon as each
        case is judged so partial results survive an interrupted long run."""
        results: list[CaseResult] = []
        for case in cases:
            case_result = await self.run_case(case)
            results.append(case_result)
            if on_result is not None:
                on_result(case_result)
        return results
