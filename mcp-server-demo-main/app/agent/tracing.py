"""Agent tracing, currently via Langfuse.

Observability is core to the project (PLAN section 10). Tracing is integrated
through LangGraph's callback mechanism: when a Langfuse public key is present in
the environment, a Langchain callback handler is attached to the graph ``config``
so the full run (nodes, LLM calls, tool calls) is traced. When no key is set the
agent runs un-instrumented — strictly fail-open, so tests and CI never depend on
a telemetry backend.
"""

import os
from collections.abc import Sequence
from typing import Any

LANGFUSE_PUBLIC_KEY = "LANGFUSE_PUBLIC_KEY"


class AgentTracer:
    """Build LangGraph callbacks for observability, fail-open by default."""

    def callbacks(self) -> list[Any] | None:
        """Return callbacks to attach to the graph config, or None to disable."""
        if not os.environ.get(LANGFUSE_PUBLIC_KEY):
            return None
        # Imported lazily so module import never requires langfuse.
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]


def callbacks_for(tracer: AgentTracer | None) -> Sequence[Any] | None:
    """Normalize a tracer to a callbacks sequence (None when disabled)."""
    if tracer is None:
        return None
    return tracer.callbacks()
