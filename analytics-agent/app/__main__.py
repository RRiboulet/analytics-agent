import contextlib

import uvicorn

from app.config import get_settings
from app.server import create_asgi_app


def main() -> None:
    settings = get_settings()
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(create_asgi_app(), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
