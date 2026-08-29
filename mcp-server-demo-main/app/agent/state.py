"""Explicit agent state shared across the LangGraph nodes.

The state carries everything the workflow needs to understand where it is and
to ground its final answer in real query results (never fabricated values).
Status is a first-class field so an agent run's progress is observable, per the
project plan (PLAN section 9).
"""

from enum import StrEnum
from typing import Any, TypedDict


class AgentStatus(StrEnum):
    """Observable status of a single agent run."""

    PLANNING = "planning"
    RETRIEVING_METADATA = "retrieving_metadata"
    GENERATING_SQL = "generating_sql"
    VALIDATING_SQL = "validating_sql"
    EXECUTING_SQL = "executing_sql"
    ANALYZING_RESULTS = "analyzing_results"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


# total=False so partial dicts returned by nodes grow the state incrementally.
class AgentState(TypedDict, total=False):
    question: str
    # Retrieved metadata documents (search_metadata hits) and the concise
    # schema summary the LLM needs to plan a query.
    metadata: list[dict[str, Any]]
    relevant_tables: list[str]
    schema: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    # SQL working set: the candidate query, any validation failure, any
    # execution failure, and the bounded query actually run.
    sql: str
    bounded_sql: str
    validation_error: str
    query_error: str
    # Set when the model call itself failed (timeout / transport / HTTP /
    # malformed response). LLM failures retry up to the attempt limit and then
    # fail cleanly instead of crashing the run with a raw httpx traceback.
    llm_error: str
    # Per-tool failures during metadata retrieval (transport errors, unknown
    # tools, or masked infrastructure failures returned as valid=false). A
    # non-empty list routes the run back into retrieval (bounded retry) instead
    # of generating SQL from a possibly empty schema; an empty list on retry
    # means the retry targets SQL generation, not retrieval.
    retrieval_errors: list[str]
    # Query results and the analysis/answer derived from them.
    result: list[dict[str, Any]]
    analysis: str
    answer: str
    status: AgentStatus
    attempts: int
