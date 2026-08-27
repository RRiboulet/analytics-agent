"""LLM abstraction for the analytics agent.

The agent needs two model calls, both grounded: generate a candidate read-only
query from the question + retrieved metadata, and turn the executed result into
an evidence-grounded answer. This module defines a small protocol and two
implementations:

* ``LLMClient`` — the real client, speaking OpenAI-compatible
  ``/chat/completions`` against whatever local server the operator exposes
  (llama.cpp or Ollama). It is stateless and config driven.
* ``FakeLLM`` — a deterministic in-memory stand-in used by unit tests so the
  graph can be exercised without a live model (or network).
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import get_settings

_SQL_SYSTEM = (
    "You are an analytics SQL assistant for the Olist e-commerce database. "
    "Write a single read-only SELECT query that answers the user's question. "
    "Return ONLY the SQL, with no explanation, no markdown fences, and no "
    "trailing semicolon. Do not use INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE. "
    "If the metadata below are insufficient, prefer a best-effort single "
    "SELECT over guessing columns; use the table and column names provided."
)

_ANSWER_SYSTEM = (
    "You are a data analyst. Given the user's question, the SQL that was run, "
    "and the actual returned rows, write a concise, evidence-based answer. "
    "Ground every numerical claim strictly in the returned rows. If the rows "
    "do not support a claim, say so. Do not invent numbers."
)


class LLM(Protocol):
    """The two model capabilities the agent graph needs."""

    async def generate_sql(
        self, question: str, metadata: str, schema: str, *, prior_error: str | None = None
    ) -> str:
        """Return a candidate read-only SQL query for `question`.

        ``prior_error`` carries the previous attempt's validation or execution
        failure so the model can correct a rejected query on retry.
        """
        ...

    async def generate_answer(self, question: str, sql: str, result: str) -> str:
        """Return a grounded natural-language answer from `result`."""
        ...


def _request_payload(
    model: str, system: str, user: str, *, timeout: float, max_tokens: int | None = None
) -> tuple[dict[str, Any], dict[str, float]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload, {"timeout": timeout}


class LLMClient:
    """Stateless OpenAI-compatible client for the configured local model."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
        transport: Any | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
        self.max_tokens = max_tokens or settings.llm_max_tokens
        # httpx transport override is supported so tests can stub the network.
        self._transport = transport

    async def _complete(self, system: str, user: str) -> str:
        payload, opts = _request_payload(
            self.model, system, user, timeout=self.timeout_seconds, max_tokens=self.max_tokens
        )
        client = httpx.AsyncClient(transport=self._transport)
        try:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, **opts)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        finally:
            await client.aclose()

    async def generate_sql(
        self, question: str, metadata: str, schema: str, *, prior_error: str | None = None
    ) -> str:
        user = (
            f"Question:\n{question}\n\nRelevant metadata:\n{metadata}\n\nSchema summary:\n{schema}"
        )
        if prior_error:
            user += (
                "\n\nThe previous attempt failed with this error. Correct your SQL to "
                f"resolve it — use only real columns/tables from the schema:\n{prior_error}"
            )
        return await self._complete(_SQL_SYSTEM, user)

    async def generate_answer(self, question: str, sql: str, result: str) -> str:
        user = f"Question:\n{question}\n\nSQL executed:\n{sql}\n\nRows returned:\n{result}"
        return await self._complete(_ANSWER_SYSTEM, user)


@dataclass
class FakeLLM:
    """Deterministic LLM for tests. Returns canned SQL / answers.

    ``sql_sequence`` (if non-empty) is consumed in order by ``generate_sql`` and
    is used to script retry recovery (invalid then valid, or repeated failures);
    otherwise ``sql`` is returned every time.
    """

    sql: str = "SELECT 1"
    answer: str = "canned answer"
    # Optional ordered responses to generate_sql.
    sql_sequence: list[str] = field(default_factory=list)
    # Pre-registered scripted responses keyed by question -> SQL.
    scripts: dict[str, str] = field(default_factory=dict)

    def _future_sql(self, question: str) -> str:
        if self.sql_sequence:
            return self.sql_sequence.pop(0)
        return self.scripts.get(question, self.sql)

    async def generate_sql(
        self, question: str, metadata: str, schema: str, *, prior_error: str | None = None
    ) -> str:
        return self._future_sql(question)

    async def generate_answer(self, question: str, sql: str, result: str) -> str:
        return self.answer
