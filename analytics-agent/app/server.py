import contextlib
from collections.abc import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings
from app.data_sources.postgres import PostgresClient
from app.embedder import get_embedder
from app.tools import register_tools


def create_mcp_server(client: PostgresClient) -> FastMCP:
    settings = get_settings()

    # stateless_http=True re-enters FastMCP's `lifespan=` on every single
    # request (see StreamableHTTPSessionManager._handle_stateless_request),
    # so the Postgres pool must NOT be opened/closed there. It is instead
    # owned by the outer ASGI app lifespan in create_asgi_app().
    mcp = FastMCP(
        "mcp-analytics-demo",
        json_response=True,
        host=settings.mcp_host,
        port=settings.mcp_port,
        stateless_http=True,
    )
    register_tools(mcp, client, get_embedder())

    @mcp.custom_route("/live", methods=["GET"])
    async def liveness(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "alive"})

    @mcp.custom_route("/ready", methods=["GET"])
    async def readiness(_request: Request) -> JSONResponse:
        try:
            await client.healthcheck()
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return mcp


def create_asgi_app() -> Starlette:
    """Build the deployable ASGI app with a process-lifetime Postgres pool."""
    settings = get_settings()
    client = PostgresClient(
        database_url=settings.database_url,
        query_timeout_seconds=settings.query_timeout_seconds,
        max_rows=settings.max_rows,
    )
    mcp = create_mcp_server(client)
    app = mcp.streamable_http_app()

    # streamable_http_app() hardcodes its own lifespan to start the
    # StreamableHTTP session manager's task group (required before any
    # request can be handled). Mounting this app under another Starlette
    # app would silently skip that lifespan, so instead we wrap it in
    # place: run our pool connect/close around the original lifespan.
    original_lifespan_context = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def combined_lifespan(app_: Starlette) -> AsyncIterator[None]:
        await client.connect()
        try:
            async with original_lifespan_context(app_):
                yield
        finally:
            await client.close()

    app.router.lifespan_context = combined_lifespan
    return app
