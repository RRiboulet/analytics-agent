"""Run the web surface: ``python -m app.web [--check]``.

Without ``--check`` it serves the page on ``WEB_HOST``/``WEB_PORT`` (loopback,
port 8001 by default) until interrupted. With ``--check`` it starts the same
app on an ephemeral loopback port, fetches the page, and exits 0 — a CI-friendly
proof that the surface boots without a model, a database or an MCP server, since
nothing is run.

Manual check (loopback only):

    uv run python -m app.web            # then open http://127.0.0.1:8001
    uv run python -m app.web --check
"""

import argparse
import asyncio
import contextlib
import sys
import urllib.request

import uvicorn

from app.config import get_settings
from app.web.app import PAGE_MARKER, create_app


def _fetch_page(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read().decode("utf-8")


def _probe_host(host: str) -> str:
    """The address to dial for a server bound to ``host`` (bracket IPv6)."""
    if host in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if host in {"::", "[::]"}:
        return "[::1]"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


async def _check(host: str) -> None:
    """Start, serve the page, stop — without running anything a run needs.

    Binds port 0 so a check never collides with a dev server already on
    ``WEB_PORT`` (or with another test process) and never depends on a free
    fixed port.
    """
    config = uvicorn.Config(create_app(), host=host, port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if serving.done():
                serving.result()  # surface a bind/startup error unchanged
                raise RuntimeError("web server exited before it started")
            await asyncio.sleep(0.01)
        bound_port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://{_probe_host(host)}:{bound_port}/"
        status, body = await asyncio.to_thread(_fetch_page, url)
        if status != 200 or PAGE_MARKER not in body:
            raise RuntimeError(f"GET {url} returned {status} without the page form")
        print(f"web check ok: served {url} ({len(body)} bytes)")
    finally:
        server.should_exit = True
        await serving


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.web",
        description="Serve the analytics run page on loopback.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Start on an ephemeral loopback port, fetch the page, exit 0.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    if args.check:
        try:
            asyncio.run(_check(settings.web_host))
        except Exception as error:
            raise SystemExit(f"web check failed: {error}") from error
        return
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(create_app(settings=settings), host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
