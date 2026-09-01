"""Reproducible evaluation of the analytics agent (M6).

The benchmark pairs each analytical question with a version-controlled
reference SQL statement. Judging is deterministic: the agent's SQL and the
reference SQL are executed through the same read-only MCP boundary and their
result sets are compared (order-insensitive by default, numeric tolerance).
No LLM is involved in judging.
"""

from app.evaluation.dataset import DatasetError, EvalCase, load_dataset
from app.evaluation.judges import ResultComparison, compare_result_sets
from app.evaluation.report import aggregate, render_markdown
from app.evaluation.runner import CaseResult, EvaluationRunner

__all__ = [
    "CaseResult",
    "DatasetError",
    "EvalCase",
    "EvaluationRunner",
    "ResultComparison",
    "aggregate",
    "compare_result_sets",
    "load_dataset",
    "render_markdown",
]
