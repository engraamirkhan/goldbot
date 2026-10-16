"""The dashboard API service (`python -m goldbot.ops.run api`). Moved verbatim from goldbot/ops/run.py, which
re-exports it."""
from __future__ import annotations


def run_api() -> None:
    import uvicorn

    from goldbot.api.app import create_app
    from goldbot.ops.health import start_heartbeat
    start_heartbeat("state", "api")
    uvicorn.run(create_app("state"), host="127.0.0.1", port=8787)
