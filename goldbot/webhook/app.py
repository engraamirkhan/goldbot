"""TradingView alert receiver.

Alert message body (set in the TradingView alert dialog, "once per bar close"):
{"indicator":"K-Indi","version":"2.02","symbol":"{{ticker}}","tf":"{{interval}}","bar_time":"{{time}}",
 "fired_at":"{{timenow}}","signal":"long","strength":1.0,"secret":"<shared secret>"}

Security: TradingView IP allow-list + constant-time shared-secret check + content-hash idempotency.
The handler queues and returns within TradingView's 3-second limit.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import queue
import threading
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

TRADINGVIEW_IPS = {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}


class Alert(BaseModel):
    indicator: str
    version: str = "0"
    symbol: str
    tf: str
    bar_time: datetime
    fired_at: datetime
    signal: str = Field(pattern="^(long|short|flat|touch|cross|heartbeat)$")
    strength: float = 0.0
    payload: dict = Field(default_factory=dict)
    secret: str


def signal_hash(a: Alert) -> str:
    key = f"{a.indicator}|{a.version}|{a.symbol}|{a.tf}|{a.bar_time.isoformat()}|{a.signal}"
    return hashlib.sha256(key.encode()).hexdigest()


def tf_seconds(tf: str) -> int:
    tf = tf.strip().upper()
    if tf.isdigit():
        return int(tf) * 60
    return {"D": 86400, "1D": 86400, "W": 604800, "1W": 604800, "240": 14400, "60": 3600, "15": 900}.get(tf, 0)


def to_row(a: Alert, received_utc: datetime) -> dict:
    bt = pd.Timestamp(a.bar_time).tz_convert("UTC") if pd.Timestamp(a.bar_time).tzinfo else pd.Timestamp(a.bar_time, tz="UTC")
    fa = pd.Timestamp(a.fired_at).tz_convert("UTC") if pd.Timestamp(a.fired_at).tzinfo else pd.Timestamp(a.fired_at, tz="UTC")
    sec = tf_seconds(a.tf)
    intrabar = (fa - bt).total_seconds() < sec if sec else False
    return {
        "ts_utc": bt, "bar_time": bt, "fired_at": fa, "received_utc": pd.Timestamp(received_utc),
        "available_utc": pd.Timestamp(received_utc), "latency_s": (pd.Timestamp(received_utc) - bt).total_seconds() - sec,
        "indicator": a.indicator, "version": a.version, "symbol": a.symbol, "tf": a.tf, "signal": a.signal,
        "strength": a.strength, "payload": a.payload, "intrabar": intrabar, "signal_hash": signal_hash(a),
        "origin": "webhook",
    }


def create_app(secret: str | None = None, sink: queue.Queue[dict[str, Any]] | None = None, enforce_ip: bool = True) -> FastAPI:
    secret = secret or os.environ.get("TV_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError("TV_WEBHOOK_SECRET not set")
    q: queue.Queue[dict[str, Any]] = sink or queue.Queue()
    app = FastAPI(title="goldbot webhook")
    app.state.queue = q
    seen: set[str] = set()
    app.state.seen = seen
    lock = threading.Lock()

    @app.post("/tv")
    async def tv(req: Request, alert: Alert) -> dict[str, bool]:
        client_ip = req.headers.get("x-forwarded-for", req.client.host if req.client else "").split(",")[0].strip()
        if enforce_ip and client_ip not in TRADINGVIEW_IPS:
            raise HTTPException(403, "ip not allowed")
        if not hmac.compare_digest(alert.secret, secret):
            raise HTTPException(403, "bad secret")
        row = to_row(alert, datetime.now(timezone.utc))
        with lock:
            if row["signal_hash"] in seen:
                return {"ok": True, "dup": True}
            seen.add(row["signal_hash"])
        q.put(row)  # the store writer drains this queue; respond immediately
        return {"ok": True, "dup": False, "intrabar": row["intrabar"]}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "queued": q.qsize()}

    return app
