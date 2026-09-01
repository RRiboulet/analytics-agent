"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_telemetry_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never export traces to Langfuse.

    ``app.agent.tracing`` loads the project ``.env`` at import time (so the
    real ``LANGFUSE_*`` keys are present in ``os.environ`` during a pytest
    session). Without this fixture every test that builds an ``AgentTracer``
    or runs ``run_agent`` would silently export fake test runs — e.g. the
    stubbed ``test_run_agent_with_fake_components`` run for question "Q" —
    to the configured Langfuse backend. Tracing-enabled tests opt back in by
    setting the keys explicitly via their own ``monkeypatch``.
    """
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
