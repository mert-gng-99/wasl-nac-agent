"""Wasl - uvicorn entry point.

    uvicorn main:app --reload --port 8000

Runs with no configuration at all: the CAMARA client starts in simulator mode
and the agent falls back to its deterministic planner when no model key is set.
See INSTRUCTIONS.md to switch on Gemini or the live Nokia gateway.
"""

from core.server import app_from_env
from idea import SPEC

app = app_from_env(SPEC)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=bool(os.getenv("RELOAD")),
    )
