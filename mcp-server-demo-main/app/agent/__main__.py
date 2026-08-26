"""Allow ``python -m app.agent --json '<question>'``."""

from app.agent.entrypoint import main

if __name__ == "__main__":
    main()
