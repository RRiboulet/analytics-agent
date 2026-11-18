"""The page renders the manager pipeline live (M8 ticket 06, spec R6).

Server-side only: these tests read ``app/web/static/index.html`` as text and
assert the page carries a rendering for every event type the server actually
emits — the list is derived from ``app.progress.ProgressEventType`` at test
time, so adding an event type to the server without rendering it fails here
instead of silently streaming an event nobody draws.

They also pin the final payload's fields the page consumes (evidence records
with their rows and per-record call counts), the element hooks ticket 05's
tests already assert on, the visual distinctions the ticket requires
(follow-up evidence, a degraded inspect stage, a rejected report, truncation),
and that no secret or API-key name reaches the page.

What these tests do *not* prove: that a browser renders the page correctly.
That is a DOM-execution question (no test runner here) and the ticket's manual
checklist; ``tests/test_web_app.py`` proves the stream the page consumes.
"""

from pathlib import Path

import pytest

from app.progress import ProgressEventType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_FILE = PROJECT_ROOT / "app" / "web" / "static" / "index.html"

# The final payload ``app/web/stream.py`` serializes (``_final_frame``). Hardcoded
# on purpose: it is the page's contract, and a page field no server sends (or a
# server field the page stopped reading) is exactly what this test looks for.
FINAL_PAYLOAD_FIELDS = [
    "mode",
    "evidence",
    "status",
    "report",
    "answer",
    "report_attempt",
    "error",
    "elapsed_seconds",
    "tool_calls",
    "tool_call_total",
    "sub_questions",
    "follow_up_questions",
]

# Every field of one evidence record (``app/manager/evidence.py``): the ticket
# requires all of them per analysis, so the page must name each one.
EVIDENCE_RECORD_FIELDS = [
    "sub_index",
    "sub_question",
    "status",
    "sql",
    "rows",
    "answer",
    "error",
    "is_follow_up",
    "tool_calls",
]


@pytest.fixture(scope="module")
def page() -> str:
    assert INDEX_FILE.is_file(), INDEX_FILE
    return INDEX_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Every event type the server emits has a rendering
# ---------------------------------------------------------------------------


def test_page_renders_every_progress_event_type(page: str) -> None:
    """Each event type in ``ProgressEventType`` is dispatched by the page.

    Derived from the enum rather than a hardcoded list: a future event type
    (the queued event spec R8 describes, for instance) cannot go unrendered
    without failing this test.
    """
    missing = [
        event_type.value for event_type in ProgressEventType if f'"{event_type.value}"' not in page
    ]
    assert missing == [], f"no rendering for event types: {missing}"


def test_page_dispatches_by_event_type_and_ignores_unknown_ones(page: str) -> None:
    """One handler per known type, and a lookup that tolerates the unknown."""
    assert "const handlers = {" in page
    assert "handlers[data.type]" in page
    # The fallback must be a no-op, not a throw: the stream keeps flowing.
    assert 'typeof handler === "function"' in page
    assert "ignored on purpose" in page


def test_analyst_stage_events_stamp_the_sub_question_index(page: str) -> None:
    """Nested analyst stages land on the card their ``index`` names."""
    for stage in [
        "sql.generated",
        "sql.validated",
        "sql.executed",
        "analysis.completed",
        "run.retrying",
    ]:
        assert f'"{stage}"' in page, stage
    assert "row.dataset.index = String(card.index)" in page
    assert "row.dataset.stage = kind" in page


# ---------------------------------------------------------------------------
# The final payload, evidence records, and their rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", FINAL_PAYLOAD_FIELDS)
def test_page_reads_the_final_payload_field(page: str, field: str) -> None:
    assert field in page, field


@pytest.mark.parametrize("field", EVIDENCE_RECORD_FIELDS)
def test_page_renders_every_evidence_record_field(page: str, field: str) -> None:
    assert field in page, field


def test_rows_are_backfilled_into_the_live_cards_not_duplicated(page: str) -> None:
    """Rows arrive only in ``final``: the record enriches the existing card.

    Cards are keyed by sub-question index, and the final payload's records go
    through the same ``ensureCard`` used by ``manager.analysis_started`` — so a
    completed run has one card per record, never two.
    """
    assert "const cards = new Map();" in page
    assert "cards.set(index, card)" in page
    assert "cards.get(index)" in page
    assert "ensureCard(record.index" in page
    # The final payload's evidence is what carries the rows, and it is applied
    # through the same path as the live event.
    assert "data.evidence" in page
    assert "applyEvidenceRecord" in page
    assert "renderRows(card, record.rows" in page


def test_result_rows_render_as_a_table_and_truncation_is_visible(page: str) -> None:
    assert 'make("table", "rows-table")' in page
    assert 'make("th", "", column)' in page
    # Server truncation is stated, derived from the LIMIT the agent bounded the
    # executed query with, and the page's own preview cap is stated too.
    assert "rowCapFromSql" in page
    assert "LIMIT" in page
    assert "truncated" in page
    assert "ROW_PREVIEW_LIMIT" in page
    assert "Showing the first" in page


# ---------------------------------------------------------------------------
# The states the ticket requires to be visible and distinct
# ---------------------------------------------------------------------------


def test_follow_up_evidence_is_visually_distinct(page: str) -> None:
    assert "manager.analysis_started" in page
    assert "card--follow-up" in page
    assert "badge--followup" in page
    assert "Follow-up" in page
    assert "round-divider" in page


def test_degraded_inspect_stage_shows_its_reason(page: str) -> None:
    assert "manager.inspected" in page
    assert "Degraded" in page
    assert "degraded" in page
    # The reason is rendered, not swallowed.
    assert "data.reason" in page


def test_groundedness_verdict_renders_both_outcomes(page: str) -> None:
    assert "manager.groundedness" in page
    assert '"grounded"' in page
    assert '"violated"' in page
    assert "Violated" in page


def test_rejected_report_is_presented_as_rejected_never_as_the_report(page: str) -> None:
    assert "report_attempt" in page
    assert "REJECTED" in page
    # The rejected text goes to its own element, and the report element is
    # hidden when there is no report.
    assert "rejectedReport.textContent" in page
    assert "show(rejected, showRejected)" in page
    assert "const showRejected = Boolean(attempt) && !report && !isAnalyst;" in page


def test_failure_states_are_rendered(page: str) -> None:
    # The run's own terminal failure event, the stream's ``failed`` frame, and a
    # sub-analysis that failed while the run continued.
    assert '"run.failed"' in page
    assert 'name === "failed"' in page
    assert "the run continues" in page
    # A stream that ends without a terminal frame is reported as unknown, not
    # silently left looking finished.
    assert "showTruncatedStream" in page
    assert "without a terminal frame" in page


def test_page_starts_clean_on_a_fresh_load_or_a_resubmit(page: str) -> None:
    """No run history: the page holds only the current run."""
    assert "function reset()" in page
    assert "cards.clear();" in page
    assert "reset();" in page
    # No persistence of any kind.
    for forbidden in ["localStorage", "sessionStorage", "document.cookie", "indexedDB"]:
        assert forbidden not in page, forbidden


def test_page_is_one_file_with_no_build_step_or_framework(page: str) -> None:
    assert "<style>" in page
    assert "<script>" in page
    assert page.count("<script") == 1
    assert " src=" not in page
    assert "import " not in page
    assert "fetch(" in page


def test_analyst_mode_has_no_manager_scaffolding(page: str) -> None:
    """One question streams its stages into one card, never the manager view.

    Analyst stage events carry no sub-question index, so the page attributes
    them to the single card itself. The final payload's manager fallbacks are
    gated on the mode: an analyst run must not reveal empty decomposition,
    inspect or synthesis panels, and its answer is presented as an answer, not
    as a report.
    """
    # The visible mode choice and the default (the fast first interaction).
    assert 'name="mode" value="analyst" checked' in page
    assert 'name="mode" value="manager"' in page
    # A missing index belongs to the single analyst card; manager events do not.
    assert 'if (currentMode === "analyst") return analystCard();' in page
    # Manager-only fallbacks and sections are gated on the mode.
    assert "if (!isAnalyst) {" in page
    assert 'reportHeading.textContent = isAnalyst ? "Answer" : "Report";' in page
    # An empty answer is stated, never left as a blank card.
    assert "The run finished without an answer." in page
    # The answer field is read from the final payload.
    assert "data.answer" in page


# ---------------------------------------------------------------------------
# The hooks ticket 05 exposed stay put
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hook",
    [
        # Ticket 05's hooks: the form, the status line, the result summary.
        'id="run-form"',
        'data-endpoint="/api/run"',
        'id="mode-analyst"',
        'id="mode-manager"',
        'name="mode"',
        'value="analyst"',
        'value="manager"',
        'id="request"',
        'id="submit"',
        'id="status"',
        'id="pipeline"',
        'id="events"',
        'id="result"',
        'id="final-status"',
        'id="final-elapsed"',
        'id="final-tool-calls"',
        'id="final-error"',
        'id="report"',
        'data-renderer="plain"',
        'data-renderer="pipeline"',
        "dataset.eventType",
        "dataset.index",
        "dataset.isFollowUp",
        # Ticket 06's pipeline hooks.
        'id="request-panel"',
        'id="request-text"',
        'id="decomposition"',
        'id="decomposition-note"',
        'id="sub-questions"',
        'id="analyses"',
        'id="analysis-cards"',
        'id="inspect"',
        'id="inspect-verdict"',
        'id="inspect-questions"',
        'id="synthesis"',
        'id="synthesis-status"',
        'id="groundedness-panel"',
        'id="groundedness"',
        'id="groundedness-reason"',
        'id="rejected"',
        'id="rejected-report"',
        'id="report-section"',
        'id="report-heading"',
        'id="tool-call-breakdown"',
        'id="notes"',
        'id="log"',
    ],
)
def test_page_carries_the_render_hooks(page: str, hook: str) -> None:
    assert hook in page, hook


def test_page_posts_json_and_reads_the_sse_stream(page: str) -> None:
    assert 'method: "POST"' in page
    assert 'Accept: "text/event-stream"' in page
    assert '"Content-Type": "application/json"' in page
    assert 'name === "progress"' in page
    assert 'name === "final"' in page
    # The optional shared token is forwarded, never baked into the page.
    assert "X-Web-Token" in page
    assert "?token=" in page or 'get("token")' in page


# ---------------------------------------------------------------------------
# Nothing secret reaches the browser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "api_key",
        "api-key",
        "apikey",
        "API_KEY",
        "secret",
        "Secret",
        "password",
        "Password",
        "Bearer",
        "sk-",
        "OPENAI",
        "ANTHROPIC",
        "OPENROUTER",
        "LANGFUSE",
        "DATABASE_URL",
        "ADMIN_DATABASE_URL",
    ],
)
def test_page_carries_no_secret_or_api_key_name(page: str, forbidden: str) -> None:
    assert forbidden not in page, forbidden
