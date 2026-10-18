"""The TradingView webhook service (`python -m goldbot.ops.run webhook`). Moved verbatim from goldbot/ops/run.py,
which re-exports it.

It listens on loopback only: the public address is the reverse proxy in front (Tailscale Funnel on path /tv), which
appends the caller's address to X-Forwarded-For; the allow-list reads only that right-most entry, and only from the
loopback peer. `GOLDBOT_TV_ENFORCE_IP=0` turns the TradingView IP allow-list off when the proxy cannot show the
caller's address (the 256-bit secret then authenticates alone)."""
from __future__ import annotations

import os


def run_webhook() -> None:
    import uvicorn

    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.ops import accounts
    from goldbot.webhook.app import create_app
    secret = accounts.get_credential("tradingview-webhook-secret", "TradingView webhook shared secret")
    enforce = os.environ.get("GOLDBOT_TV_ENFORCE_IP", "1") != "0"
    # uvicorn's own proxy-header rewriting is off: the app reads the header itself, from loopback only
    uvicorn.run(create_app(secret, enforce_ip=enforce, state_dir=os.environ.get("GOLDBOT_STATE", "state"),
                           store=Store(load_settings().data_root)),
                host="127.0.0.1", port=8443, proxy_headers=False)
