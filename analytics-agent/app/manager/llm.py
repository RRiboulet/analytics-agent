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
from app.config import get_settings

_DECOMPOSE_SYSTEM = (
    "You are an analytics manager planning a multi-part analysis of the "
    "Olist e-commerce database. Decompose the user's management request into "
    "at most 4 concrete, self-contained analytical sub-questions, each "
    "answerable by a single read-only SQL query against the listed tables. "
    "Return ONLY the sub-questions, one per line, with no numbering, no "
    "bullets, no markdown, and no explanations."
)

_SYNTHESIZE_SYSTEM = (
    "You are a senior data analyst writing a management report. Using ONLY "
    "the evidence below (sub-questions, executed SQL, result rows and "
    "analyst answers), write a concise markdown report answering the "
    "management request. Ground every number strictly in the evidence "
    "result rows: copy numbers exactly as they appear — no rounding, no "
    "derived or computed numbers, no invented values, and no percentages "
    "unless a percentage appears in a result row. Do not use numbered "
    "lists or any numbering. If the evidence is insufficient for a claim, "
    "say so instead of filling the gap."
)


class ManagerLLM(Protocol):
    """The model capabilities the manager workflow needs."""

    async def decompose(self, request: str, table_names: list[str]) -> str:
        """Return the raw model output decomposing `request` into sub-questions.

        ``table_names`` is the only schema hint: the decomposition stage must
        stay table-name-level (D009) — detailed schema discovery is the
        analyst's job. Raises ``LLMError`` on timeout/transport/HTTP/response
        failures.
        """
        ...

    async def synthesize(self, request: str, evidence: str) -> str:
        """Return the raw model report grounded only in `evidence`.

        ``evidence`` is the formatted evidence text (see
        ``app.manager.synthesize.format_evidence``). Raises ``LLMError`` on
        timeout/transport/HTTP/response failures.
        """
        ...


class ManagerLLMClient(LLMClient):
    """OpenAI-compatible manager client sharing the agent's LLM plumbing."""

    async def decompose(self, request: str, table_names: list[str]) -> str:
        user = f"Management request:\n{request}\n\nAvailable tables: {', '.join(table_names)}"
        return await self._traced_complete(
            "decompose", user, system=_DECOMPOSE_SYSTEM, max_tokens=self.max_tokens
        )

    async def synthesize(self, request: str, evidence: str) -> str:
        user = f"Management request:\n{request}\n\nEvidence from the sub-analyses:\n{evidence}"
        return await self._traced_complete(
            "synthesize_report", user, system=_SYNTHESIZE_SYSTEM, max_tokens=self.max_tokens
        )


@dataclass
class FakeManagerLLM:
    """Deterministic manager LLM for tests.

    Returns ``raw``/``report`` verbatim (so tests can exercise the parser,
    the groundedness check, etc.) or raises the configured ``llm_error``.
    Calls are recorded for assertions.
    """

    raw: str = "Which product categories generated the most revenue?"
    # Default report contains no digits, so it is trivially grounded.
    report: str = "Report based on the recorded evidence."
    llm_error: LLMError | None = None
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    report_calls: list[tuple[str, str]] = field(default_factory=list)

    async def decompose(self, request: str, table_names: list[str]) -> str:
        self.calls.append((request, tuple(table_names)))
        if self.llm_error is not None:
            raise self.llm_error
        return self.raw

    async def synthesize(self, request: str, evidence: str) -> str:
        self.report_calls.append((request, evidence))
        if self.llm_error is not None:
            raise self.llm_error
        return self.report


def create_manager_llm() -> ManagerLLMClient:
    """Build the manager LLM client selected by ``LLM_PROVIDER``.

    Mirrors the agent's provider selection (``app.agent.llm.create_llm``):
    ``llamacpp`` (default) uses the local OpenAI-compatible server;
    ``openrouter`` uses the hosted API with the required
    ``OPENROUTER_API_KEY``. ``ManagerLLMClient`` extends ``LLMClient``, so
    the same instance can also serve as the analyst's LLM (one shared client
    for the whole manager run).
    """
    settings = get_settings()
    if settings.llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        return ManagerLLMClient(
            base_url=settings.openrouter_base_url,
            model=settings.openrouter_model,
            api_key=settings.openrouter_api_key,
        )
    return ManagerLLMClient()
