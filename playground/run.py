"""Playground launcher — boots the harness FastAPI app + in-process agent.

  uv run python -m playground.run

Serves the web UI (if built into ``web/dist``), proxies ``POST /connect`` to the
remote supervoice, exposes ``/playground/*``, and runs the configured agent.
"""

from __future__ import annotations

import os
import sys

import uvicorn
from dotenv import load_dotenv
from loguru import logger
from playground.harness.api import build_app

load_dotenv()

_PORT = int(os.getenv("PLAYGROUND_PORT", "9100"))


def _check_keys() -> None:
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("Set OPENAI_API_KEY or ANTHROPIC_API_KEY before running.")
        sys.exit(1)


def main() -> None:
    """Run the playground server."""
    _check_keys()
    logger.info(f"[playground] up — http://localhost:{_PORT}")
    uvicorn.run(build_app(), host="127.0.0.1", port=_PORT, log_level="info")


if __name__ == "__main__":
    main()
