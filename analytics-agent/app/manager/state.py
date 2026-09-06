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
    RETRYING = "retrying"
    RUNNING_SUB_ANALYSES = "running_sub_analyses"
    # The inspector reviews the accumulated evidence and decides whether one
    # bounded follow-up round (<=1 round, <=2 questions, D009 Stage B) is
    # needed before synthesis.
    INSPECTING = "inspecting"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"


class ManagerState(TypedDict, total=False):
    request: str
    # Validated decomposition (1..4 sub-questions, deduplicated) or the error
    # that made the request undecomposable. ``llm_error`` carries a failed
    # decompose model call (timeout/transport/HTTP/rate limit) and is retried
    # bounded; ``decomposition_error`` is unusable model output, which fails
    # immediately (deterministic output does not improve on retry).
    sub_questions: list[str]
    decomposition_error: str
    llm_error: str
    # Groundedness violation message (M7.3): the synthesized report cited a
    # number that appears in no evidence result set. The run fails and the
    # report is never stored — never ship a fabricated report.
    groundedness_error: str
    # The rejected report text behind a groundedness violation. It is never
    # returned as the report (``report`` stays None) but is kept so the
    # failure is inspectable — evidence.json / traces — without needing
    # Langfuse: a violation message that names a number nobody can find in
    # the evidence is not enough to debug why the model fabricated it.
    report_attempt: str
    # Evidence accumulated from the analyst sub-runs, in execution order.
    evidence: list[EvidenceRecord]
    # Per-sub-question failures; sub-analysis failure is recorded and the run
    # continues (D009) — only when all sub-analyses fail does the manager fail.
    sub_analysis_errors: list[str]
    # The synthesized report (M7.3), grounded only in the evidence records.
    report: str
    status: ManagerStatus
    # Decompose retry counter (bounded by ManagerServices.max_attempts). The
    # budget is shared by every retryable stage (decompose/inspect/synthesize)
    # so it stays a single hard bound (D009).
    attempts: int
    # M7.5 — bounded follow-up round:
    # Validated follow-up questions (0..2) decided by the inspect stage, the
    # per-follow-up analyst failures, and the round counter (a single hard
    # bound of 1 — the graph never loops back to inspect).
    follow_up_questions: list[str]
    follow_up_errors: list[str]
    follow_up_rounds: int
    # Why the inspect stage produced no follow-ups (unusable model output or
    # an LLM failure after the retry budget): observable, never a run error.
    inspect_error: str
    # Which retryable stage a retry must re-enter (decompose/inspect/
    # synthesize), recorded by the failing node so the shared retry node can
    # route back precisely instead of guessing.
    retry_stage: str
