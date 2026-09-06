"""Autonomous analytics manager (M7).

The manager extends the evidence-driven analytics agent (M4) toward
management-level requests: it decomposes a request into concrete
sub-questions (M7.1), runs each one through a grounded, read-only analyst run
(M7.2, D009: it never touches the database itself), may run one bounded
follow-up round after reviewing the evidence (M7.5), and synthesizes a
human-readable report grounded only in the accumulated evidence (M7.3).
"""

from app.manager.decompose import (
    MAX_SUB_QUESTIONS,
    DecompositionError,
    decompose_request,
    parse_sub_questions,
)
from app.manager.entrypoint import ManagerRunResult, run_manager
from app.manager.evidence import EvidenceRecord
from app.manager.inspect import (
    MAX_FOLLOW_UP_QUESTIONS,
    FollowUpError,
    parse_follow_up_questions,
)
from app.manager.llm import FakeManagerLLM, ManagerLLM, ManagerLLMClient, create_manager_llm
from app.manager.state import ManagerState, ManagerStatus
from app.manager.synthesize import extract_report_numbers, format_evidence, groundedness_violation

__all__ = [
    "MAX_FOLLOW_UP_QUESTIONS",
    "MAX_SUB_QUESTIONS",
    "DecompositionError",
    "EvidenceRecord",
    "FakeManagerLLM",
    "FollowUpError",
    "ManagerLLM",
    "ManagerLLMClient",
    "ManagerRunResult",
    "ManagerState",
    "ManagerStatus",
    "create_manager_llm",
    "decompose_request",
    "extract_report_numbers",
    "format_evidence",
    "groundedness_violation",
    "parse_follow_up_questions",
    "parse_sub_questions",
    "run_manager",
]
