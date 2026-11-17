"""The loopback web surface for a run (M8).

``app.web`` serves one page and one streaming endpoint. It is a *client* of the
existing entrypoints: it composes a manager run, forwards that run's progress
events to the browser as Server-Sent Events, and ends the stream with a final
payload carrying the report and the run's real MCP call counts. It never
imports the database client, never builds SQL, and never changes the manager's
budgets or groundedness rules.

Entry points:

* ``python -m app.web`` — serve on ``WEB_HOST``/``WEB_PORT`` (loopback by
  default).
* ``python -m app.web --check`` — start, fetch the page, exit 0.
* ``create_app`` (``app.web.app``) — the ASGI app, with an injectable runner
  for hermetic tests.

Ticket 06 replaces the page's plain event log with the rich pipeline view;
ticket 07 adds analyst mode; ticket 08 wraps a run in the concurrency
semaphore. The seams for all three are in ``app.web.composition`` and
``app.web.stream``.
"""

from app.web.app import PAGE_MARKER, create_app

__all__ = ["PAGE_MARKER", "create_app"]
