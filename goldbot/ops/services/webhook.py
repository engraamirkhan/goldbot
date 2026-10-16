"""The TradingView webhook service (`python -m goldbot.ops.run webhook`). Moved verbatim from goldbot/ops/run.py,
which re-exports it."""
from __future__ import annotations


def run_webhook() -> None:
    import uvicorn

    from goldbot.ops import accounts
    from goldbot.webhook.app import create_app
    secret = accounts.get_credential("tradingview-webhook-secret", "TradingView webhook shared secret")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=8443)
