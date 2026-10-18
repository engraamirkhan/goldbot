"""TradingView alert receiver: reads what the owner's indicators plot (invite-only ones included).

Alert message body (set in the TradingView alert dialog, trigger "Once per bar close"; docs/RUNBOOK.md has the full
template). Every value is a quoted string because TradingView writes `NaN` for an empty plot, which is not JSON:
{"indicator":"K-Indi","version":"2.02","symbol":"{{ticker}}","tf":"{{interval}}","bar_time":"{{time}}",
 "fired_at":"{{timenow}}","price":"{{close}}","plots":{"plot_0":"{{plot_0}}","plot_1":"{{plot_1}}"},"secret":"<shared secret>"}
`signal` is optional (default "plot"); a body with only `signal`/`strength`/`payload` (the first format) still parses.

Security: TradingView IP allow-list (the TCP peer; behind the local proxy only the proxy's own, right-most
X-Forwarded-For entry) + constant-time shared-secret check + a body size limit + non-finite values refused. The secret
is never logged or echoed (validation errors list the field and the reason, never the input). The secret is checked
before the schema: an unauthenticated caller always gets the same 403. A per-hour row cap on the inbox (429) bounds
what a leaked secret can write.

"Persist before the ack": the alert is committed to the SQLite inbox (goldbot/webhook/inbox.py) before the 200; if
the write fails the answer is 503 so TradingView retries. Idempotency is durable, on (indicator, version, symbol, tf,
bar_time). The handler stays inside TradingView's 3-second limit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from goldbot.config import WebhookSettings
from goldbot.data.store import Store
from goldbot.webhook.inbox import TvInbox, sync_to_store

log = logging.getLogger(__name__)

TRADINGVIEW_IPS = {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}
LOCAL_PROXY_IPS = {"127.0.0.1", "::1"}
MAX_BODY_BYTES = 64 * 1024
MAX_PLOTS = 64
MAX_PLOT_KEY = 64
MAX_ABS_VALUE = 1e15          # far beyond any price, volume or oscillator; a larger number is a broken placeholder
_MISSING = {"", "nan", "n/a", "na", "null", "none"}


def parse_number(v: Any) -> float | None:
    """A plot or price value: numbers and numeric strings parse; "NaN"/"n/a"/"" (TradingView's empty plot) and a JSON
    NaN are None; infinity or an out-of-bounds number is an error (refused, never stored)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s.lower() in _MISSING:
            return None
        try:
            x = float(s)
        except ValueError:
            raise ValueError("not a number") from None
    elif isinstance(v, (int, float)):
        x = float(v)
    else:
        raise ValueError("not a number")
    if math.isnan(x):
        return None
    if not math.isfinite(x) or abs(x) > MAX_ABS_VALUE:
        raise ValueError("non-finite or out-of-bounds value")
    return x


class Alert(BaseModel):
    indicator: str = Field(min_length=1, max_length=100)
    version: str = Field(default="0", max_length=50)
    symbol: str = Field(min_length=1, max_length=50)
    tf: str = Field(min_length=1, max_length=10)
    bar_time: datetime
    fired_at: datetime
    signal: str = Field(default="plot", pattern="^(plot|bar|long|short|flat|touch|cross|heartbeat)$")
    strength: float = Field(default=0.0, ge=-MAX_ABS_VALUE, le=MAX_ABS_VALUE)
    price: float | None = None
    plots: dict[str, float | None] = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)
    secret: str

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, v: Any) -> float | None:
        return parse_number(v)

    @field_validator("plots", mode="before")
    @classmethod
    def _plots(cls, v: Any) -> dict[str, float | None]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("plots must be an object")
        if len(v) > MAX_PLOTS:
            raise ValueError(f"at most {MAX_PLOTS} plots")
        out: dict[str, float | None] = {}
        for k, x in v.items():
            key = str(k).strip()
            if not key or len(key) > MAX_PLOT_KEY:
                raise ValueError("plot names are 1 to 64 characters")
            out[key] = parse_number(x)
        return out


def signal_hash(a: Alert) -> str:
    key = f"{a.indicator}|{a.version}|{a.symbol}|{a.tf}|{a.bar_time.isoformat()}|{a.signal}"
    return hashlib.sha256(key.encode()).hexdigest()


def tf_seconds(tf: str) -> int:
    tf = tf.strip().upper()
    if tf.isdigit():
        return int(tf) * 60
    return {"D": 86400, "1D": 86400, "W": 604800, "1W": 604800}.get(tf, 0)


def _utc(x: datetime) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")


def to_row(a: Alert, received_utc: datetime) -> dict:
    bt, fa, rx = _utc(a.bar_time), _utc(a.fired_at), _utc(received_utc)
    sec = tf_seconds(a.tf)
    intrabar = (fa - bt).total_seconds() < sec if sec else False
    # {{time}} is the bar's open: its values are known at the close at the earliest, and never before we received them
    available = max(bt + pd.Timedelta(seconds=sec), rx)
    return {
        "ts_utc": bt, "bar_time": bt, "fired_at": fa, "received_utc": rx, "available_utc": available,
        "latency_s": (rx - bt).total_seconds() - sec,
        "indicator": a.indicator, "version": a.version, "symbol": a.symbol, "tf": a.tf, "signal": a.signal,
        "strength": a.strength, "price": a.price, "plots": a.plots, "payload": a.payload, "intrabar": intrabar,
        "signal_hash": signal_hash(a), "origin": "webhook",
    }


def caller_ip(req: Request, trust_local_proxy: bool) -> str:
    """The address the allow-list judges: the TCP peer. Only when the peer is this host's own proxy (Tailscale Funnel
    on loopback) is X-Forwarded-For read, and then only its right-most entry, the one that proxy appended; the
    entries to its left are whatever the caller wrote. Anyone else's header is ignored."""
    peer = req.client.host if req.client else ""
    if trust_local_proxy and peer in LOCAL_PROXY_IPS:
        fwd = req.headers.get("x-forwarded-for", "")
        return fwd.split(",")[-1].strip() if fwd else ""
    return peer


def create_app(secret: str | None = None, inbox: TvInbox | None = None, enforce_ip: bool = True,
               trust_local_proxy: bool = True, state_dir: str = "state", store: Store | None = None,
               max_rows_per_hour: int = WebhookSettings().max_rows_per_hour) -> FastAPI:
    """`store`: when given, each new alert is also copied into the `tv_indicators` dataset after the answer has gone.
    `max_rows_per_hour`: settings.webhook; the inbox takes at most this many rows an hour, then 429."""
    row_cap = max_rows_per_hour
    secret = secret or os.environ.get("TV_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError("TV_WEBHOOK_SECRET not set")
    box = inbox if inbox is not None else TvInbox(os.path.join(state_dir, "tv.db"))
    app = FastAPI(title="goldbot webhook")
    app.state.inbox = box
    sync_lock = threading.Lock()

    def sync() -> None:
        if store is None:
            return
        with sync_lock:
            try:
                sync_to_store(box, store)
            except Exception:
                log.exception("tv_indicators sync failed; the inbox keeps the alerts and the next alert retries")

    @app.post("/tv")
    async def tv(req: Request, background: BackgroundTasks) -> JSONResponse:
        if enforce_ip and caller_ip(req, trust_local_proxy) not in TRADINGVIEW_IPS:
            raise HTTPException(403, "ip not allowed")
        declared = req.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise HTTPException(413, "body too large")
        body = b""
        async for chunk in req.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(413, "body too large")
        # authenticate first: read only "secret" from the parsed JSON, constant-time compare, one uniform 403 for
        # anything that does not carry it (bad JSON, wrong shape, missing or wrong secret), so no schema oracle
        try:
            parsed = json.loads(body)
            given = parsed.get("secret") if isinstance(parsed, dict) else None
        except ValueError:
            given = None
        if not isinstance(given, str) or not hmac.compare_digest(given.encode(), secret.encode()):
            raise HTTPException(403, "bad secret")
        try:
            alert = Alert.model_validate_json(body)
        except ValidationError as exc:
            # field and reason only: the default error echoes the input, and the input carries the secret
            detail = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]}
                      for e in exc.errors(include_input=False, include_url=False)]
            return JSONResponse({"detail": detail}, status_code=422)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        if await run_in_threadpool(box.count_since, since) >= row_cap:
            # the body is never logged: it carries the secret
            log.warning("tv inbox row cap reached (%d rows in the last hour): alert refused with 429", row_cap)
            raise HTTPException(429, "inbox row cap reached")
        row = to_row(alert, datetime.now(timezone.utc))
        try:
            new = await run_in_threadpool(box.put, row)
        except Exception:
            log.exception("tv inbox write failed (%s %s %s)", alert.indicator, alert.symbol, alert.tf)
            raise HTTPException(503, "inbox write failed, retry") from None
        if not new:
            return JSONResponse({"ok": True, "dup": True})
        background.add_task(run_in_threadpool, sync)
        return JSONResponse({"ok": True, "dup": False, "intrabar": row["intrabar"]})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "stored": await run_in_threadpool(box.count)}

    return app
