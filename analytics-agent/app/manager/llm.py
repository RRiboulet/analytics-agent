"""LLM capability for the analytics manager.

The manager makes discrete model calls (D007/D009): decompose a management
request into concrete sub-questions (M7.1) and, later, synthesize the report
from accumulated evidence (M7.3). ``ManagerLLMClient`` extends the agent's
``LLMClient`` to share the OpenAI-compatible transport, timeouts, token caps,
optional bearer auth and the traced-nested-runnable pattern — so manager runs
compose with the fail-open Langfuse tracing from M5 without any new
observability plumbing. The inherited analyst methods (``generate_sql`` /
``generate_answer``) are simply not used by the manager.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.llm import LLMClient, LLMError

_DECOMPOSE_SYSTEM = (
    "You are an analytics manager planning a multi-part analysis of the "
    "Olist e-commerce database. Decompose the user's management request into "
    "at most 4 concrete, self-contained analytical sub-questions, each "
    "answerable by a single read-only SQL query against the listed tables. "
    "Return ONLY the sub-questions, one per line, with no numbering, no "
    "bullets, no markdown, and no explanations."
)


class ManagerLLM(Protocol):
    """The model capability the manager workflow needs."""

    async def decompose(self, request: str, table_names: list[str]) -> str:
        """Return the raw model output decomposing `request` into sub-questions.

        ``table_names`` is the only schema hint: the decomposition stage must
        stay table-name-level (D009) — detailed schema discovery is the
        analyst's job. Raises ``LLMError`` on timeout/transport/HTTP/response
        failures.
        """
        ...


class ManagerLLMClient(LLMClient):
    """OpenAI-compatible manager client sharing the agent's LLM plumbing."""

    async def decompose(self, request: str, table_names: list[str]) -> str:
        user = f"Management request:\n{request}\n\nAvailable tables: {', '.join(table_names)}"
        return await self._traced_complete(
            "decompose", user, system=_DECOMPOSE_SYSTEM, max_tokens=self.max_tokens
        )


@dataclass
class FakeManagerLLM:
    """Deterministic manager LLM for tests.

    Returns ``raw`` verbatim (so tests can exercise the parser with bullets,
    fences, etc.) or raises the configured ``llm_error``. Calls are recorded
    for assertions.
    """

    raw: str = "Which product categories generated the most revenue?"
    llm_error: LLMError | None = None
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    async def decompose(self, request: str, table_names: list[str]) -> str:
        self.calls.append((request, tuple(table_names)))
        if self.llm_error is not None:
            raise self.llm_error
        return self.raw
