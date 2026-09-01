"""Deterministic evaluation judges (no LLM in the judging path).

Result sets are compared as value multisets per row: column *aliases* are
ignored (an agent may legitimately name columns differently) but every value
must match. Numbers are compared with a relative tolerance because equivalent
aggregations may accumulate floats in a different order. For unordered
questions, rows are sorted by their canonical representation before a
positional comparison, which makes the match deterministic on both sides.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

# Relative numeric tolerance (0.0001%): covers float accumulation-order noise
# between equivalent aggregations without accepting real numerical errors.
RELATIVE_TOLERANCE = 1e-6


@dataclass
class ResultComparison:
    """Outcome of comparing an agent result set against the reference."""

    matches: bool
    detail: str


def _canonical_cell(value: object) -> tuple[str, float | str | None]:
    """Canonicalize one cell value for comparison.

    Numbers become floats (the MCP JSON boundary already delivers NUMERIC as
    float); everything else — text, ISO-8601 timestamps — is compared as the
    exact string it arrived as. Booleans are kept distinct from numbers.
    """
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", float(value))
    if isinstance(value, int | float | Decimal):
        return ("num", float(value))
    return ("str", str(value))


def _canonical_row(row: dict) -> tuple:
    """Canonical row: values only, sorted so column names/aliases are ignored."""
    return tuple(sorted(_canonical_cell(cell) for cell in row.values()))


def _cell_matches(agent: tuple, reference: tuple, tolerance: float) -> bool:
    kind_a, value_a = agent
    kind_b, value_b = reference
    if kind_a != kind_b:
        return False
    if kind_a == "num":
        return abs(value_a - value_b) <= tolerance * max(1.0, abs(value_b))
    return value_a == value_b


def compare_result_sets(
    agent_rows: list[dict],
    reference_rows: list[dict],
    *,
    ordered: bool = False,
    tolerance: float = RELATIVE_TOLERANCE,
) -> ResultComparison:
    """Compare the agent's result rows against the reference rows."""
    if len(agent_rows) != len(reference_rows):
        return ResultComparison(
            matches=False,
            detail=f"row count differs: agent={len(agent_rows)} reference={len(reference_rows)}",
        )
    if not reference_rows:
        return ResultComparison(matches=True, detail="both result sets are empty")

    agent_canonical = [_canonical_row(row) for row in agent_rows]
    reference_canonical = [_canonical_row(row) for row in reference_rows]
    if not ordered:
        agent_canonical.sort(key=repr)
        reference_canonical.sort(key=repr)

    for index, (agent_row, reference_row) in enumerate(
        zip(agent_canonical, reference_canonical, strict=False)
    ):
        if len(agent_row) != len(reference_row):
            return ResultComparison(
                matches=False,
                detail=f"row {index}: column count differs "
                f"(agent={len(agent_row)} reference={len(reference_row)})",
            )
        for agent_cell, reference_cell in zip(agent_row, reference_row, strict=False):
            if not _cell_matches(agent_cell, reference_cell, tolerance):
                return ResultComparison(
                    matches=False,
                    detail=f"row {index}: value mismatch {agent_cell!r} != {reference_cell!r}",
                )
    ordering = "ordered" if ordered else "order-insensitive"
    return ResultComparison(matches=True, detail=f"result sets match ({ordering})")


def referenced_tables(sql: str, candidates: list[str]) -> list[str]:
    """Return the candidate table names that appear in the SQL text.

    Used as a relevance diagnostic: which of the expected tables the agent's
    query actually touches. Matching is a word-boundary check, so substrings
    of longer identifiers are not counted.
    """
    found: list[str] = []
    for table in candidates:
        if re.search(rf"\b{re.escape(table)}\b", sql, re.IGNORECASE):
            found.append(table)
    return found
