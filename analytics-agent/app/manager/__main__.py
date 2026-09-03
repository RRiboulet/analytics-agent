"""Allow ``python -m app.manager [--json] [--out DIR] '<request>'``."""

from app.manager.entrypoint import main

if __name__ == "__main__":
    main()
