"""Evidence records: the grounded output of one analyst sub-run.

The manager never touches the database itself (D009) — every fact it later
reports must come from an executed, read-only analyst run. An
``EvidenceRecord`` captures exactly that grounded output for one sub-question:
the SQL the analyst executed, the actual result rows, the analyst's answer and
status, and any failure. The synthesis stage (M7.3) may use only what is
recorded here, and its deterministic groundedness check verifies that every
number in the report appears in some recorded result set.
"""

from dataclasses import dataclass
from typing import Any

from app.agent.state import AgentState


@dataclass
class EvidenceRecord:
    """Grounded evidence produced by one analyst sub-run.

    All fields originate from a real analyst run (the M4 agent); the manager
    never fabricates values. ``rows`` holds the actual query result rows as
    returned through the read-only MCP boundary (``None`` when the sub-run
    failed before producing results).
    """

    sub_index: int
    sub_question: str
    status: str | None = None
    sql: str | None = None
    rows: list[dict[str, Any]] | None = None
    answer: str | None = None
    error: str | None = None

    @classmethod
    def from_agent_state(
        cls, sub_index: int, sub_question: str, state: AgentState
    ) -> "EvidenceRecord":
        """Build an evidence record from a completed analyst run state.

        ``sql`` prefers the bounded query actually executed; ``error`` follows
        the same precedence the agent entrypoint reports to callers
        (LLM failure beats query failure beats validation failure).
        """
        status = state.get("status")
        return cls(
            sub_index=sub_index,
            sub_question=sub_question,
            status=str(status) if status is not None else None,
            sql=state.get("bounded_sql") or state.get("sql"),
            rows=state.get("result"),
            answer=state.get("answer"),
            error=(
                state.get("llm_error") or state.get("query_error") or state.get("validation_error")
            ),
        )
