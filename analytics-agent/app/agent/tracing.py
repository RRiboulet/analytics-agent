"""Agent tracing, currently via Langfuse.

Observability is core to the project (PLAN section 10). Tracing is integrated
through LangGraph's callback mechanism: when Langfuse credentials are present
in the environment, a Langchain callback handler is attached to the graph
``config`` so the full run (nodes, LLM calls, tool calls, state updates) is
traced. When credentials are absent — or when anything in the tracer setup
fails — the agent runs un-instrumented: strictly fail-open, so tests and CI
never depend on a telemetry backend and telemetry can never break a run.

Within an active trace the following are captured end-to-end:

* the complete agent run (one trace per ``run_agent`` call, tagged
  ``analytics-agent`` and carrying the question as trace metadata);
* every LangGraph node with its state input/output (so the generated SQL,
  retrieved metadata, validation/execution errors, retry count and final
  answer are all visible per node);
* both LLM calls (``generate_sql`` / ``generate_answer``) as nested runs with
  prompt and completion;
* every MCP tool call (``search_metadata``, ``list_tables``,
  ``get_relationships``, ``query``) with arguments and result.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# The tracer reads the environment directly (not via pydantic Settings), so
# load the project .env. The path is resolved from this module's location
# (project root), NOT the current working directory: the app package is
# installed in the venv, so `python -m app.agent` works from anywhere, and a
# CWD-based lookup silently found no .env (disabling tracing) when launched
# outside the project directory. Non-overriding by default: real shell
# environment variables keep precedence, and this is a no-op when the file
# does not exist or has the keys commented out.
PROJECT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(PROJECT_ENV_PATH)

LANGFUSE_PUBLIC_KEY = "LANGFUSE_PUBLIC_KEY"
LANGFUSE_SECRET_KEY = "LANGFUSE_SECRET_KEY"

# Tag applied to every traced agent run so traces can be filtered in Langfuse.
RUN_TAGS = ["analytics-agent", "v2"]


class AgentTracer:
    """Build LangGraph observability config for one run, fail-open by default.

    The tracer is instantiated per agent run. It keeps the (lazily created)
    callback handler so the same handler instance serves the graph invocation
    and the final flush.
    """

    def __init__(self) -> None:
        self._handler: Any | None = None

    @staticmethod
    def _enabled() -> bool:
        """Tracing is enabled only with a complete set of Langfuse credentials.

        Both keys are required: without the secret key the Langfuse client
        cannot authenticate and traces would silently never be exported.
        """
        return bool(os.environ.get(LANGFUSE_PUBLIC_KEY) and os.environ.get(LANGFUSE_SECRET_KEY))

    def callbacks(self) -> list[Any] | None:
        """Return callbacks to attach to the graph config, or None to disable."""
        if not self._enabled():
            return None
        # Fail-open: if the SDK is missing, misconfigured, or its constructor
        # raises for any reason, tracing is disabled for this run instead of
        # breaking the analytics run it is supposed to observe.
        try:
            from langfuse.langchain import CallbackHandler  # noqa: PLC0415

            if self._handler is None:
                self._handler = CallbackHandler()
            return [self._handler]
        except Exception:
            self._handler = None
            return None

    def run_config(self, question: str) -> dict[str, Any] | None:
        """Config for the graph invocation: callbacks + tags + trace metadata.

        Returns ``None`` when tracing is disabled, in which case the graph
        runs with no observability config at all (un-instrumented).
        """
        callbacks = self.callbacks()
        if callbacks is None:
            return None
        return {"callbacks": callbacks, "tags": list(RUN_TAGS), "metadata": {"question": question}}

    def flush(self) -> None:
        """Flush pending trace events; never fails the run."""
        handler = self._handler
        if handler is None:
            return
        try:
            flush = getattr(handler, "flush", None)
            if flush is not None:
                flush()
        except Exception:
            pass


def callbacks_for(tracer: AgentTracer | None) -> Sequence[Any] | None:
    """Normalize a tracer to a callbacks sequence (None when disabled)."""
    if tracer is None:
        return None
    return tracer.callbacks()
