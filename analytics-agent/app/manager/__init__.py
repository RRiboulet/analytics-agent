"""Autonomous analytics manager (M7).

The manager extends the evidence-driven analytics agent (M4) toward
management-level requests: it decomposes a request into concrete
sub-questions (M7.1), runs each one through a grounded, read-only analyst run
(M7.2, D009: it never touches the database itself), and synthesizes a
human-readable report grounded only in the accumulated evidence (M7.3).
"""

from app.manager.decompose import (
    MAX_SUB_QUESTIONS,
    DecompositionError,
    decompose_request,
    parse_sub_questions,
)
from app.manager.evidence import EvidenceRecord
from app.manager.llm import FakeManagerLLM, ManagerLLM, ManagerLLMClient
from app.manager.state import ManagerState, ManagerStatus

__all__ = [
    "MAX_SUB_QUESTIONS",
    "DecompositionError",
    "EvidenceRecord",
    "FakeManagerLLM",
    "ManagerLLM",
    "ManagerLLMClient",
    "ManagerState",
    "ManagerStatus",
    "decompose_request",
    "parse_sub_questions",
]
