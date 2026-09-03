"""Explicit manager state shared across the (upcoming) manager workflow.

The manager composes analyst runs; its state tracks the management request,
the validated decomposition into sub-questions, the evidence accumulated from
those runs, and an observable status, mirroring the agent's state conventions
(``app/agent/state.py``). Nodes grow the state incrementally (``total=False``).
"""

from enum import StrEnum
from typing import TypedDict

from app.manager.evidence import EvidenceRecord


class ManagerStatus(StrEnum):
    """Observable status of a single manager run (PLAN M7 workflow)."""

    DECOMPOSING = "decomposing"
    RUNNING_SUB_ANALYSES = "running_sub_analyses"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"


class ManagerState(TypedDict, total=False):
    request: str
    # Validated decomposition (1..4 sub-questions, deduplicated) or the error
    # that made the request undecomposable.
    sub_questions: list[str]
    decomposition_error: str
    # Evidence accumulated from the analyst sub-runs, in execution order.
    evidence: list[EvidenceRecord]
    # Per-sub-question failures; sub-analysis failure is recorded and the run
    # continues (D009) — only when all sub-analyses fail does the manager fail.
    sub_analysis_errors: list[str]
    # The synthesized report (M7.3), grounded only in the evidence records.
    report: str
    status: ManagerStatus
