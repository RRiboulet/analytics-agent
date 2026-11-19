"""The web surface (M8 ticket 05): ASGI tests with an injected fake runner.

Every test here is hermetic: no MCP server, no database, no model. The endpoint
is driven through Starlette's test client with ``RunService`` injected, so the
tests pin the wire contract — frame names, the final payload's status/report/
elapsed/call counts, the terminal frame for a run that raised — and the page
hooks ticket 06 will render into.

What the tests do *not* prove: that a real manager run produces these events
(``tests/test_manager_progress.py`` owns the manager's event vocabulary) or that
a browser renders the page (manual check, spec Verification).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.progress import ProgressEmitter, ProgressEventType
from app.web.app import PAGE_MARKER, TOKEN_HEADER, create_app
from app.web.composition import RunMode, RunOutcome

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers and fakes
# ---------------------------------------------------------------------------


def parse_frames(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into ``(event name, JSON payload)`` pairs.

    Ignores comment frames (``sse-starlette`` pings) and tolerates CRLF.
    """
    frames: list[tuple[str, dict]] = []
    for block in re.split(r"\r?\n\r?\n", body.strip()):
        name = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if data:
            frames.append((name, json.loads("\n".join(data))))
    return frames


def default_outcome() -> RunOutcome:
    return RunOutcome(
        status="completed",
        report="Revenue is 10.5, grounded in the query result.",
        tool_calls={"query": 5, "list_tables": 1},
        sub_questions=["Which category drives revenue?"],
        follow_up_questions=[],
        evidence=[
            {
                "sub_index": 0,
                "sub_question": "Which category drives revenue?",
                "sql": "SELECT category, revenue FROM t",
                "rows": [{"revenue": 10.5}],
                "answer": "Revenue is 10.5.",
                "tool_calls": {"query": 4, "list_tables": 1},
            }
        ],
    )


class FakeRunner:
    """Runner that emits real progress events and returns a canned outcome.

    Emitting synchronously before returning is exactly the race the endpoint
    has to get right: all events must be drained before the terminal frame.
    """

    def __init__(
        self, *, outcome: RunOutcome | None = None, error: Exception | None = None
    ) -> None:
        self.outcome = default_outcome() if outcome is None else outcome
        self.error = error
        self.requests: list[str] = []

    async def __call__(self, request: str, sink) -> RunOutcome:
        self.requests.append(request)
        emitter = ProgressEmitter(sink)
        emitter.emit(ProgressEventType.RUN_STARTED, question=request)
        emitter.emit(
            ProgressEventType.ANALYSIS_COMPLETED,
            index=0,
            answer="Revenue is 10.5.",
            row_count=1,
        )
        if self.error is not None:
            raise self.error
        if self.outcome.status == "failed":
            emitter.emit(
                ProgressEventType.RUN_FAILED,
                status="failed",
                reason=self.outcome.error,
            )
        else:
            emitter.emit(
                ProgressEventType.RUN_COMPLETED,
                status=self.outcome.status,
                report=self.outcome.report,
            )
        return self.outcome


class SilentRunner:
    """Runner that emits nothing and returns a result (a minimal outcome)."""

    async def __call__(self, request: str, sink) -> RunOutcome:
        return RunOutcome(status="completed", report="done")


class FakeAnalystRunner:
    """Runner that emits the analyst graph's own event vocabulary.

    The sequence mirrors ``app/agent/graph.py`` for one clean attempt: started,
    metadata, generated/validated/executed SQL, the answer, then the terminal
    event. It carries no sub-question ``index`` — an analyst run has exactly one
    analysis — which is exactly the shape the page has to attribute itself.
    """

    def __init__(
        self,
        *,
        outcome: RunOutcome | None = None,
        error: Exception | None = None,
        sql: str = "SELECT 1 AS n LIMIT 100",
    ) -> None:
        self.outcome = default_analyst_outcome() if outcome is None else outcome
        self.error = error
        self.sql = sql
        self.requests: list[str] = []

    async def __call__(self, request: str, sink) -> RunOutcome:
        self.requests.append(request)
        emitter = ProgressEmitter(sink)
        emitter.emit(ProgressEventType.RUN_STARTED, question=request, attempt=1)
        emitter.emit(ProgressEventType.METADATA_RETRIEVED, attempt=1)
        emitter.emit(ProgressEventType.SQL_GENERATED, attempt=1, sql=self.sql)
        if self.error is not None:
            raise self.error
        if self.outcome.status == "failed":
            # A rejected SQL never reaches validation/execution; the run ends
            # with the failure reason, carrying the last generated SQL above.
            emitter.emit(
                ProgressEventType.RUN_FAILED,
                attempt=1,
                status="failed",
                reason=self.outcome.error,
            )
            return self.outcome
        emitter.emit(ProgressEventType.SQL_VALIDATED, attempt=1, sql=self.sql)
        emitter.emit(ProgressEventType.SQL_EXECUTED, attempt=1, sql=self.sql, row_count=1)
        emitter.emit(ProgressEventType.ANALYSIS_COMPLETED, attempt=1, answer=self.outcome.answer)
        emitter.emit(
            ProgressEventType.RUN_COMPLETED,
            attempt=1,
            status="completed",
            answer=self.outcome.answer,
        )
        return self.outcome


def default_analyst_outcome() -> RunOutcome:
    return RunOutcome(
        status="completed",
        report=None,
        answer="Revenue is 10.5.",
        tool_calls={"query": 1, "search_metadata": 1, "list_tables": 1},
        evidence=[
            {
                "sub_index": 0,
                "sub_question": "Which category drives revenue?",
                "status": "completed",
                "sql": "SELECT category, revenue FROM t LIMIT 100",
                "rows": [{"revenue": 10.5}],
                "answer": "Revenue is 10.5.",
                "error": None,
                "is_follow_up": False,
                "tool_calls": {"query": 1, "search_metadata": 1, "list_tables": 1},
            }
        ],
    )


def make_app(runner, *, token: str | None = None, mode: RunMode = RunMode.MANAGER):
    settings = Settings(_env_file=None, web_token=token)
    return create_app(settings=settings, runners={mode: runner})


def post_run(client: TestClient, **kwargs):
    body = {"request": "Which category drives revenue?", "mode": "manager"}
    body.update(kwargs.pop("json", {}))
    return client.post("/api/run", json=body, **kwargs)


# ---------------------------------------------------------------------------
# The stream contract
# ---------------------------------------------------------------------------


def test_run_endpoint_streams_progress_then_final() -> None:
    runner = FakeRunner()
    with TestClient(make_app(runner)) as client:
        response = post_run(client)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse_frames(response.text)
    assert [name for name, _ in frames] == [
        "progress",
        "progress",
        "progress",
        "final",
    ]

    progress = [payload for name, payload in frames if name == "progress"]
    assert [event["type"] for event in progress] == [
        ProgressEventType.RUN_STARTED,
        ProgressEventType.ANALYSIS_COMPLETED,
        ProgressEventType.RUN_COMPLETED,
    ]
    assert [event["seq"] for event in progress] == [1, 2, 3]
    assert progress[0]["question"] == "Which category drives revenue?"
    assert progress[1]["index"] == 0
    assert progress[1]["row_count"] == 1

    final = frames[-1][1]
    assert final["mode"] == "manager"
    assert final["status"] == "completed"
    assert final["report"] == runner.outcome.report
    assert final["error"] is None
    assert isinstance(final["elapsed_seconds"], float)
    assert final["elapsed_seconds"] >= 0
    # The real counts, not a client-side estimate.
    assert final["tool_calls"] == {"query": 5, "list_tables": 1}
    assert final["tool_call_total"] == 6
    # Per-record evidence travels with the final payload for ticket 06.
    assert final["evidence"][0]["tool_calls"] == {"query": 4, "list_tables": 1}
    assert final["evidence"][0]["rows"] == [{"revenue": 10.5}]
    assert runner.requests == ["Which category drives revenue?"]


def test_failed_outcome_streams_failure_and_final_payload() -> None:
    outcome = RunOutcome(
        status="failed",
        report=None,
        error="groundedness violation: 999 appears in no evidence",
        tool_calls={"query": 4},
        sub_questions=["Which category drives revenue?"],
    )
    runner = FakeRunner(outcome=outcome)
    with TestClient(make_app(runner)) as client:
        response = post_run(client)

    frames = parse_frames(response.text)
    assert [name for name, _ in frames] == [
        "progress",
        "progress",
        "progress",
        "final",
    ]
    # The run's own terminal event says why it failed...
    assert frames[2][1]["type"] == ProgressEventType.RUN_FAILED
    assert frames[2][1]["reason"] == outcome.error
    # ...and the final payload agrees, with no fabricated report.
    final = frames[-1][1]
    assert final["status"] == "failed"
    assert final["report"] is None
    assert final["error"] == outcome.error
    assert final["tool_calls"] == {"query": 4}


def test_raising_runner_emits_terminal_failed_frame() -> None:
    runner = FakeRunner(error=RuntimeError("mcp transport died"))
    with TestClient(make_app(runner)) as client:
        response = post_run(client)

    assert response.status_code == 200
    frames = parse_frames(response.text)
    # The manager emits no terminal progress event when it raises; the stream
    # supplies one, and never a final payload.
    assert [name for name, _ in frames] == [
        "progress",
        "progress",
        "failed",
    ]
    failure = frames[-1][1]
    assert failure["status"] == "failed"
    assert failure["error"] == "RuntimeError: mcp transport died"
    assert isinstance(failure["elapsed_seconds"], float)


def test_run_with_no_events_still_ends_with_final() -> None:
    with TestClient(make_app(SilentRunner())) as client:
        response = post_run(client)

    frames = parse_frames(response.text)
    assert [name for name, _ in frames] == ["final"]
    assert frames[0][1]["status"] == "completed"
    assert frames[0][1]["report"] == "done"


# ---------------------------------------------------------------------------
# Analyst mode: one question, no manager stages (spec R7/D5)
# ---------------------------------------------------------------------------


ANALYST_STAGE_TYPES = [
    ProgressEventType.RUN_STARTED,
    ProgressEventType.METADATA_RETRIEVED,
    ProgressEventType.SQL_GENERATED,
    ProgressEventType.SQL_VALIDATED,
    ProgressEventType.SQL_EXECUTED,
    ProgressEventType.ANALYSIS_COMPLETED,
    ProgressEventType.RUN_COMPLETED,
]


def test_analyst_mode_streams_stages_then_final_with_answer() -> None:
    """Analyst events and the terminal payload, with no manager stage."""
    runner = FakeAnalystRunner()
    with TestClient(make_app(runner, mode=RunMode.ANALYST)) as client:
        response = post_run(client, json={"mode": "analyst"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse_frames(response.text)
    assert [name for name, _ in frames] == ["progress"] * 7 + ["final"]

    progress = [payload for name, payload in frames if name == "progress"]
    assert [event["type"] for event in progress] == ANALYST_STAGE_TYPES
    assert [event["seq"] for event in progress] == list(range(1, 8))
    # The first progress frame names the mode (ticket 07 scope), and only it.
    assert progress[0]["mode"] == "analyst"
    assert all("mode" not in event for event in progress[1:])
    assert progress[0]["question"] == "Which category drives revenue?"
    assert progress[4]["row_count"] == 1
    # No manager stage ever appears in an analyst run.
    assert not [e for e in progress if str(e["type"]).startswith("manager.")]

    final = frames[-1][1]
    assert final["mode"] == "analyst"
    assert final["status"] == "completed"
    assert final["answer"] == "Revenue is 10.5."
    assert final["report"] is None
    assert final["error"] is None
    assert isinstance(final["elapsed_seconds"], float)
    assert final["elapsed_seconds"] >= 0
    assert final["tool_calls"] == {"query": 1, "search_metadata": 1, "list_tables": 1}
    assert final["tool_call_total"] == 3
    # SQL, rows and call counts travel with the terminal evidence payload;
    # no decomposition/follow-up stages exist for an analyst run.
    assert final["sub_questions"] == []
    assert final["follow_up_questions"] == []
    record = final["evidence"][0]
    assert record["sql"] == "SELECT category, revenue FROM t LIMIT 100"
    assert record["rows"] == [{"revenue": 10.5}]
    assert record["answer"] == "Revenue is 10.5."
    assert record["tool_calls"] == {"query": 1, "search_metadata": 1, "list_tables": 1}
    assert runner.requests == ["Which category drives revenue?"]


def test_manager_mode_still_dispatches_the_manager_runner() -> None:
    """With both runners registered, the mode selects the runner — nothing else."""
    manager = FakeRunner()
    analyst = FakeAnalystRunner()
    settings = Settings(_env_file=None)
    app = create_app(
        settings=settings,
        runners={RunMode.ANALYST: analyst, RunMode.MANAGER: manager},
    )
    with TestClient(app) as client:
        response = post_run(client, json={"mode": "manager"})

    frames = parse_frames(response.text)
    assert [name for name, _ in frames] == ["progress", "progress", "progress", "final"]
    final = frames[-1][1]
    assert final["mode"] == "manager"
    assert final["report"] == manager.outcome.report
    assert final["answer"] is None
    assert manager.requests == ["Which category drives revenue?"]
    assert analyst.requests == []


def test_analyst_mode_unknown_to_this_process_is_rejected() -> None:
    """An analyst request to a manager-only app is a 400 and starts no run."""
    runner = FakeRunner()
    with TestClient(make_app(runner)) as client:
        response = post_run(client, json={"mode": "analyst"})

    assert response.status_code == 400
    body = response.json()
    assert "analyst" in body["error"]
    assert body["modes"] == ["manager"]
    assert runner.requests == []


def test_failing_analyst_run_streams_failure_with_the_last_sql() -> None:
    """A failed analyst run carries the SQL it generated, never a blank answer."""
    last_sql = "DELETE FROM orders"
    outcome = RunOutcome(
        status="failed",
        report=None,
        answer=None,
        error="UnsafeQueryError: only SELECT statements are allowed",
        tool_calls={"search_metadata": 1, "list_tables": 1},
        evidence=[
            {
                "sub_index": 0,
                "sub_question": "Which category drives revenue?",
                "status": "failed",
                "sql": last_sql,
                "rows": None,
                "answer": None,
                "error": "UnsafeQueryError: only SELECT statements are allowed",
                "is_follow_up": False,
                "tool_calls": {"search_metadata": 1, "list_tables": 1},
            }
        ],
    )
    runner = FakeAnalystRunner(outcome=outcome, sql=last_sql)
    with TestClient(make_app(runner, mode=RunMode.ANALYST)) as client:
        response = post_run(client, json={"mode": "analyst"})

    frames = parse_frames(response.text)
    progress = [payload for name, payload in frames if name == "progress"]
    assert progress[-1]["type"] == ProgressEventType.RUN_FAILED
    assert progress[-1]["reason"] == outcome.error
    # The generated SQL was streamed before the failure, so the page shows it.
    assert progress[2]["sql"] == last_sql

    final = frames[-1][1]
    assert final["mode"] == "analyst"
    assert final["status"] == "failed"
    assert final["answer"] is None
    assert final["report"] is None
    assert final["error"] == outcome.error
    record = final["evidence"][0]
    assert record["sql"] == last_sql
    assert record["rows"] is None
    assert record["error"] == outcome.error


def test_empty_analyst_answer_carries_its_sql_instead_of_rendering_blank() -> None:
    """An empty answer travels as ``None`` with its SQL, so nothing renders blank."""
    outcome = RunOutcome(
        status="completed",
        report=None,
        answer=None,
        error=None,
        tool_calls={"query": 1},
        evidence=[
            {
                "sub_index": 0,
                "sub_question": "Which category drives revenue?",
                "status": "completed",
                "sql": "SELECT category FROM t LIMIT 100",
                "rows": [],
                "answer": None,
                "error": None,
                "is_follow_up": False,
                "tool_calls": {"query": 1},
            }
        ],
    )
    runner = FakeAnalystRunner(outcome=outcome)
    with TestClient(make_app(runner, mode=RunMode.ANALYST)) as client:
        response = post_run(client, json={"mode": "analyst"})

    final = parse_frames(response.text)[-1][1]
    assert final["answer"] is None
    assert final["evidence"][0]["sql"] == "SELECT category FROM t LIMIT 100"
    assert final["evidence"][0]["rows"] == []


# ---------------------------------------------------------------------------
# Request validation and the optional token
# ---------------------------------------------------------------------------


def test_token_is_off_by_default() -> None:
    assert Settings(_env_file=None, web_token=None).web_token is None
    with TestClient(make_app(FakeRunner(), token=None)) as client:
        assert post_run(client).status_code == 200


def test_token_setting_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_TOKEN", "s3cret-token")
    settings = Settings(_env_file=None)
    assert settings.web_token == "s3cret-token"
    app = create_app(settings=settings, runners={RunMode.MANAGER: FakeRunner()})

    with TestClient(app) as client:
        assert post_run(client).status_code == 401
        assert post_run(client, headers={TOKEN_HEADER: "wrong"}).status_code == 401
        # Both accepted forms: the header the page sends, and a query
        # parameter for curl/scripts.
        assert post_run(client, headers={TOKEN_HEADER: "s3cret-token"}).status_code == 200
        assert (
            client.post(
                "/api/run?token=s3cret-token",
                json={"request": "Q", "mode": "manager"},
            ).status_code
            == 200
        )


def test_unknown_mode_is_rejected() -> None:
    runner = FakeRunner()
    with TestClient(make_app(runner)) as client:
        response = post_run(client, json={"mode": "bogus"})

    assert response.status_code == 400
    body = response.json()
    assert "bogus" in body["error"]
    # The available modes are reported; the injected app only has the manager.
    assert body["modes"] == ["manager"]
    # Rejected before a run starts: nothing reached the runner.
    assert runner.requests == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": b"not json", "headers": {"Content-Type": "application/json"}},
        {"json": {"request": "   ", "mode": "manager"}},
        {"json": {"request": 3, "mode": "manager"}},
        {"json": {"mode": "manager"}},
    ],
)
def test_invalid_request_body_is_rejected(kwargs) -> None:
    with TestClient(make_app(FakeRunner())) as client:
        assert client.post("/api/run", **kwargs).status_code == 400


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_page_is_served_with_the_hooks_ticket_06_needs() -> None:
    with TestClient(make_app(FakeRunner())) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert PAGE_MARKER in body
    for hook in [
        'id="request"',
        'id="submit"',
        'id="status"',
        'id="pipeline"',
        'id="events"',
        'id="result"',
        'id="final-status"',
        'id="final-elapsed"',
        'id="final-tool-calls"',
        'id="report-heading"',
        'data-renderer="plain"',
        "dataset.eventType",
        "dataset.index",
        "dataset.isFollowUp",
    ]:
        assert hook in body, hook
    # The mode is a visible choice sent with the request (ticket 07), and the
    # single question is the default (the fast first interaction).
    for hook in [
        'id="mode-analyst"',
        'id="mode-manager"',
        'name="mode"',
        'value="analyst"',
        'value="manager"',
        'type="radio"',
    ]:
        assert hook in body, hook
    assert 'type="hidden"' not in body
    # Analyst stage events carry no index; the page attributes them itself and
    # must not fall back to the manager's decomposition/inspect panels.
    assert 'currentMode === "analyst"' in body
    # One endpoint, no bundler: the page posts JSON and reads the SSE stream.
    assert '"POST"' in body
    assert "/api/run" in body
    assert "text/event-stream" in body
    # The page never carries a secret of its own.
    assert "s3cret" not in body


def test_page_is_served_even_when_a_token_is_required(monkeypatch) -> None:
    monkeypatch.setenv("WEB_TOKEN", "s3cret-token")
    app = create_app(settings=Settings(_env_file=None), runners={RunMode.MANAGER: FakeRunner()})
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert PAGE_MARKER in response.text
    assert "s3cret-token" not in response.text


# ---------------------------------------------------------------------------
# Boundaries: no database client, and --check boots the surface
# ---------------------------------------------------------------------------


def test_web_layer_imports_no_database_client_or_run_machinery() -> None:
    """Importing the web app pulls in neither PostgreSQL nor a run stack.

    The manager entrypoint is imported lazily by the default runner, so the
    process can start (and ``--check`` pass) without the model/MCP machinery.
    """
    code = "\n".join(
        [
            "import sys",
            "import app.web",
            "banned = ['app.data_sources', 'app.server', 'app.sql_safety',",
            "          'app.manager', 'app.agent', 'asyncpg']",
            "loaded = sorted(",
            "    name for name in sys.modules",
            "    if any(name == root or name.startswith(root + '.') for root in banned)",
            ")",
            "print(','.join(loaded))",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_check_mode_starts_serves_the_page_and_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.web", "--check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "web check ok" in result.stdout
