"""The Starlette app: one static page and one streaming endpoint (spec D4).

Routes:

* ``GET /`` — the page (one static file, no framework, no build step).
* ``POST /api/run`` — start a run and stream it as Server-Sent Events, ending
  with a ``final`` payload (or a ``failed`` frame when the run raised). A
  visitor beyond ``WEB_MAX_CONCURRENT_RUNS`` gets a ``queued`` frame first and
  then the same stream once a slot frees (spec R8).

The web layer is deliberately thin: it validates and authenticates the request,
hands it to ``RunService`` (the composition seam) and serializes what comes
back. It holds no database connection, builds no SQL and knows nothing about
the manager's budgets or groundedness rules.

Auth is optional and off by default: with ``WEB_TOKEN`` unset (or empty) every
request is accepted; when it is set, the run endpoint requires the token as an
``X-Web-Token`` header or a ``token`` query parameter, compared in constant
time. The static page is always served — it contains no secret, and the token
itself is supplied by whoever opened the page (``?token=...`` makes the page
forward it as the header).
"""

import secrets
from collections.abc import Mapping
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from app.config import Settings, get_settings
from app.web.composition import RunMode, RunRunner, RunService
from app.web.stream import QueueSink, stream_run

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# A stable marker ``--check`` and the tests assert on: the page that carries
# the form the next ticket renders into.
PAGE_MARKER = 'id="run-form"'
# The header a browser (or a script) may use instead of ``?token=``.
TOKEN_HEADER = "x-web-token"


def _authorized(request: Request, settings: Settings) -> bool:
    """True when the optional shared token is absent or matches.

    An unset (or empty) ``WEB_TOKEN`` disables the check — the default, and the
    only sensible one for a loopback demo. When set, both forms are accepted:
    a header (what the page sends) and a query parameter (what ``curl`` uses).
    Comparison is constant time and the token is never echoed anywhere.
    """
    expected = settings.web_token
    if not expected:
        return True
    supplied = request.headers.get(TOKEN_HEADER) or request.query_params.get("token")
    if supplied is None:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


async def _read_run_request(
    request: Request, service: RunService
) -> tuple[RunMode, str] | JSONResponse:
    """Parse and validate ``{"request": ..., "mode": ...}``.

    Returns the run to start, or a 400 response naming what was wrong. The JSON
    shape is the page's own contract (spec D4); a mode the process cannot run
    yet is rejected instead of silently falling back, so ticket 07's arrival is
    the only thing that changes this behavior.
    """
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

    text = body.get("request")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"error": "'request' must be a non-empty string"}, status_code=400)

    modes = [mode.value for mode in service.modes()]
    raw_mode = body.get("mode", RunMode.MANAGER.value)
    try:
        mode = RunMode(raw_mode)
    except ValueError:
        return JSONResponse(
            {"error": f"unknown mode {raw_mode!r}", "modes": modes}, status_code=400
        )
    if mode not in service.modes():
        return JSONResponse(
            {"error": f"mode {mode.value!r} is not available", "modes": modes},
            status_code=400,
        )
    return mode, text.strip()


def create_app(
    *,
    settings: Settings | None = None,
    runners: Mapping[RunMode, RunRunner] | None = None,
) -> Starlette:
    """Build the ASGI app.

    ``settings`` defaults to the process settings (``WEB_HOST``/``WEB_PORT``/
    ``WEB_TOKEN``/``WEB_MAX_CONCURRENT_RUNS``). ``runners`` is the test seam: the
    ASGI tests inject a fake runner and never touch MCP, the database or a
    model. The production default is the manager entrypoint. The concurrency
    limit is process-wide because one app runs in one process.
    """
    resolved = settings if settings is not None else get_settings()
    service = RunService(runners, max_concurrent_runs=resolved.web_max_concurrent_runs)

    async def index(_request: Request) -> Response:
        return FileResponse(INDEX_FILE, media_type="text/html; charset=utf-8")

    async def run(request: Request) -> Response:
        if not _authorized(request, resolved):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        parsed = await _read_run_request(request, service)
        if isinstance(parsed, JSONResponse):
            return parsed
        mode, text = parsed
        sink = QueueSink()
        return EventSourceResponse(
            stream_run(service, mode, text, sink),
            # No caching, and no proxy buffering in front of the stream (the
            # devcontainer has none, but a future one must not batch frames).
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app = Starlette(
        routes=[
            Route("/", index, methods=["GET"]),
            Route("/api/run", run, methods=["POST"]),
        ]
    )
    app.state.settings = resolved
    app.state.run_service = service
    return app
