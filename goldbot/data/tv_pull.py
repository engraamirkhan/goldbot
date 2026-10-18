"""TradingView direct pull: the owner's saved layouts, their indicators and bars, read from TradingView's chart
websocket with the owner's session cookie (owner decision 2026-10-18, ADR 0030).

    python -m goldbot.ops.run tv-pull sync      # one pass: discover, backfill what is missing, add the new closed bars
    python -m goldbot.ops.run tv-pull follow    # a pass after every 15m bar close (poll_interval_s + poll_offset_s)
    python -m goldbot.ops.run tv-pull status    # last pass, cookie state, studies seen; no network

The only owner step is storing the two browser cookies (`python -m goldbot.ops.accounts set tradingview-sessionid`
and `... set tradingview-sessionid-sign`, hidden prompts) and switching `tv_pull.enabled` on in settings.local.yaml.
Everything else is automatic:

* Discovery. The saved layouts (`/my-charts/`) are read, and every Pine study on them with its saved inputs
  (charts-storage `sources`) becomes a `StudySpec`; re-read every `discover_every_s`, cached in the pull log between.
* Metadata. The script's metaInfo (pine-facade) names each plot (`plots[].id`, the style title, the plot type and a
  colourer's target). It is kept in `tv_study_meta` keyed by `meta_hash8` (sha256 of the study id, version, inputs and
  plots), so a catalog derives `tv_{study}_{meta_hash8}_{tf}_{plot_id}` with no dashboard naming. The script's compiled
  source (`ilTemplate`, the author's work) is sent to TradingView to compute the study and never stored.
* Rows. "Output lands in the same store the plots reader uses": `tv_indicators`, the `inbox.long_rows` schema plus
  `origin`; bars as indicator `chart` (version = the chart symbol, plots open/high/low/close/volume), each study as
  indicator = its short name, version = `meta_hash8`, plot = the plot id. Closed bars only: a bar still forming at
  receipt is dropped, a bar opening after now fails the pass (wrong clock), nothing written for that timeframe.
* "Every join to bars via asof_join on available_utc". `pull_live` (received within `live_max_lag_s` of the close):
  `available_utc` = the receipt time. `pull_history` (backfill): bar close + the p95 latency of the live bars over
  the last 30 days (`history_latency_s`), `history_latency_default_s` until `min_live_samples` exist. First receipt
  wins: a bar already stored is never rewritten; a re-read bar whose values differ is counted as a repaint.
* Polite. One request at most every `min_interval_s`; history pages capped per timeframe; a timeframe is polled
  only once a new bar can have closed; `max_requests_per_day` over a rolling 24 h; after a failed pass the next waits
  `backoff_base_s` doubling to `backoff_max_s` (an expired cookie waits the maximum unless a new cookie is stored).
* Fail soft. A pass never raises: it records its result in the pull log (state/tv.db: `tv_pull_runs`,
  `tv_pull_state`, `tv_study_meta`) and returns a `PassReport`. The sign-in state goes through
  goldbot/ops/tv_pull_health.py: `record_ok` after every successful authenticated pass, `record_expired` when
  TradingView refuses the cookie (no auth token on the page, 401/403, an "unauthorized" protocol answer) or none is
  stored; that module owns the `tv_auth` row, the health WARN and the single owner notice per expiry. The webhook and
  alert inbox never import this module.
* Read-only and private: see goldbot/data/tv_wire.py (allow lists, redaction). The cookie is read from the keyring at
  each pass and held only in memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import secrets
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import pandas as pd
from pydantic import Field, SecretStr

from goldbot.base import FrozenRecord, Record, UtcTimestamp, load_record, load_record_json
from goldbot.config import TvPullSettings
from goldbot.data.store import Store
from goldbot.data.timeutil import epoch_ns
from goldbot.data.tv_csv import normalize_tf
from goldbot.data.tv_wire import (
    SITE,
    GuardedSocket,
    ReadOnlyNetwork,
    ReadOnlyViolation,
    TvAuthError,
    TvNetwork,
    TvPullError,
    decode,
    encode,
    encode_raw,
    is_heartbeat,
    redact,
    secret_parts,
)
from goldbot.db.connection import connect, write
from goldbot.ops.tv_pull_health import record_expired, record_ok
from goldbot.webhook.app import tf_seconds
from goldbot.webhook.inbox import TABLE, safe_symbol, source_of

log = logging.getLogger("goldbot.tv_pull")

ORIGIN_LIVE, ORIGIN_HISTORY = "pull_live", "pull_history"
ORIGINS = (ORIGIN_LIVE, ORIGIN_HISTORY)
CHART = "chart"
OHLCV = ("open", "high", "low", "close", "volume")
NA_ABOVE = 1e99                         # TradingView sends na as 1e100
STUDY_TYPE = "Script@tv-scripting-101!"
STORAGE = "https://charts-storage.tradingview.com/charts-storage/get/layout"
PINE = "https://pine-facade.tradingview.com/pine-facade/translate"
LATENCY_WINDOW = pd.Timedelta(days=30)
_ERRORS = {"critical_error", "protocol_error", "symbol_error", "series_error", "study_error"}
_INPUT = re.compile(r"in_\d+")
_REFUSED = re.compile(r"unauthori[sz]ed|forbidden|auth[_ ]?token|not[_ ]logged|sign[_ ]?in required", re.I)


def _utc(t: Any) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _num(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) and abs(v) < NA_ABOVE else math.nan


def _epoch_s(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    return epoch_ns(pd.DatetimeIndex(pd.to_datetime(ts, utc=True))) // 10**9


# ------------------------------------------------------------------------------------------------- records
class PlotMeta(FrozenRecord):
    id: str
    title: str
    type: str
    target: str | None = None            # a colourer's plot


class StudyMeta(FrozenRecord):
    """What the study is, from its metaInfo and the layout's saved inputs; `meta_hash8` keys its rows and catalog."""

    study_id: str                         # pine id, e.g. PUB;abc
    name: str
    version: str                          # the script's own version
    inputs: dict[str, Any]
    plots: list[PlotMeta]
    colourers: dict[str, str]             # colourer plot id -> the plot it colours
    meta_hash8: str

    @classmethod
    def build(cls, *, study_id: str, name: str, version: str, inputs: dict[str, Any], plots: list[PlotMeta],
              colourers: dict[str, str]) -> StudyMeta:
        body = {"study_id": study_id, "version": version, "inputs": inputs, "plots": [p.model_dump() for p in plots]}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
        return cls(study_id=study_id, name=name, version=version, inputs=inputs, plots=plots, colourers=colourers,
                   meta_hash8=digest)


class StudySpec(FrozenRecord):
    """A study to pull: found on a saved layout, or named on the command line."""

    study_id: str
    version: str = "last"
    inputs: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None


class TfReport(FrozenRecord):
    symbol: str
    tf: str
    bars_received: int = Field(ge=0)      # history depth is measured: what TradingView actually served
    pages: int = Field(ge=0)
    oldest_bar: UtcTimestamp | None = None
    newest_bar: UtcTimestamp | None = None
    dropped_forming: int = Field(ge=0)
    new_bars: int = Field(ge=0)
    live_bars: int = Field(ge=0)
    history_bars: int = Field(ge=0)
    repaint_mismatch_bars: int = Field(ge=0)
    studies: int = Field(ge=0)
    rows: int = Field(ge=0)


class PassReport(FrozenRecord):
    ok: bool
    skipped: bool = False
    auth_expired: bool = False
    reason: str = ""
    started_utc: UtcTimestamp
    finished_utc: UtcTimestamp
    requests: int = Field(default=0, ge=0)
    studies: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)   # studies whose source TradingView did not serve
    per_tf: list[TfReport] = Field(default_factory=list)
    history_latency_s: float = 0.0
    history_latency_measured: bool = False

    @classmethod
    def failed(cls, started: pd.Timestamp, finished: pd.Timestamp, reason: str, *, requests: int,
               auth_expired: bool = False) -> PassReport:
        return cls(ok=False, reason=reason, started_utc=started, finished_utc=finished, requests=requests,
                   auth_expired=auth_expired)

    def text(self) -> str:
        if not self.ok:
            return f"tv-pull: {'skipped' if self.skipped else 'FAILED'}: {self.reason}"
        lines = [f"tv-pull: ok, {self.requests} requests, {len(self.studies)} studies "
                 f"({', '.join(self.studies) or 'none'}); history latency {self.history_latency_s:.0f} s "
                 f"({'measured' if self.history_latency_measured else 'default'})"]
        lines += [f"  {t.symbol} {t.tf}: {t.bars_received} bars received ({t.pages} history pages, "
                  f"{t.oldest_bar.isoformat() if t.oldest_bar is not None else '-'} .. "
                  f"{t.newest_bar.isoformat() if t.newest_bar is not None else '-'}), {t.new_bars} new "
                  f"({t.live_bars} live, {t.history_bars} history), {t.repaint_mismatch_bars} repainted, "
                  f"{t.rows} rows" for t in self.per_tf]
        return "\n".join(lines)


class FollowState(Record):
    """In-memory state of a `follow` loop: compiled study sources (never written anywhere) and the cookie that failed
    sign-in, so a newly stored cookie is tried at once instead of after the expiry backoff."""

    compiled: dict[str, tuple[StudyMeta, str, dict[str, str], float]] = Field(default_factory=dict)
    failed_cookie: str | None = None


# ------------------------------------------------------------------------------------------------- the pull log
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tv_pull_runs (
    id INTEGER PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT NOT NULL, ok INTEGER NOT NULL,
    auth_expired INTEGER NOT NULL, requests INTEGER NOT NULL, reason TEXT NOT NULL, report TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS tv_pull_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS tv_study_meta (
    meta_hash8 TEXT PRIMARY KEY, study_id TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL,
    meta TEXT NOT NULL, first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL
) STRICT;
"""


class PullLog:
    """The pull's durable record in state/tv.db (SQLite WAL, beside the alert inbox; no JSON state files)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        return connect(self.path, "core")

    def record(self, rep: PassReport) -> None:
        conn = self._conn()
        try:
            with write(conn):
                conn.execute("INSERT INTO tv_pull_runs (started_utc, finished_utc, ok, auth_expired, requests, reason,"
                             " report) VALUES (?,?,?,?,?,?,?)",
                             (rep.started_utc.isoformat(), rep.finished_utc.isoformat(), int(rep.ok),
                              int(rep.auth_expired), rep.requests, rep.reason, rep.model_dump_json()))
        finally:
            conn.close()

    def _runs(self, limit: int = 64) -> list[sqlite3.Row]:
        conn = self._conn()
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM tv_pull_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()

    def last(self) -> PassReport | None:
        rows = self._runs(1)
        return load_record_json(PassReport, rows[0]["report"], source="tv.db tv_pull_runs") if rows else None

    def next_allowed(self, cfg: TvPullSettings) -> pd.Timestamp | None:
        """When the next pass may start after failures (None: now). A pass that never reached the network (no cookie)
        does not back off; a cookie TradingView refused waits `backoff_max_s`."""
        rows = self._runs()
        if not rows or rows[0]["ok"] or rows[0]["requests"] == 0:
            return None
        fails = 0
        for r in rows:
            if r["ok"]:
                break
            fails += 1
        delay = cfg.backoff_max_s if rows[0]["auth_expired"] else \
            min(cfg.backoff_base_s * 2 ** min(fails - 1, 30), cfg.backoff_max_s)
        return _utc(rows[0]["finished_utc"]) + pd.Timedelta(seconds=delay)

    def requests_since(self, since: pd.Timestamp) -> int:
        conn = self._conn()
        try:
            r = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM tv_pull_runs WHERE started_utc >= ?",
                             (_utc(since).isoformat(),)).fetchone()
            return int(r[0])
        finally:
            conn.close()

    def set_state(self, key: str, value: str, now: pd.Timestamp) -> None:
        conn = self._conn()
        try:
            with write(conn):
                conn.execute("INSERT INTO tv_pull_state (key, value, updated_utc) VALUES (?,?,?) ON CONFLICT (key) DO "
                             "UPDATE SET value = excluded.value, updated_utc = excluded.updated_utc",
                             (key, value, _utc(now).isoformat()))
        finally:
            conn.close()

    def get_state(self, key: str) -> str | None:
        conn = self._conn()
        try:
            r = conn.execute("SELECT value FROM tv_pull_state WHERE key = ?", (key,)).fetchone()
            return None if r is None else str(r[0])
        finally:
            conn.close()

    def auth_state(self) -> str | None:
        """The sign-in state goldbot/ops/tv_pull_health.py keeps in `tv_auth` ("ok" | "expired"; None: never ran)."""
        conn = self._conn()
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'tv_auth'").fetchone() is None:
                return None
            r = conn.execute("SELECT status FROM tv_auth WHERE id = 1").fetchone()
            return None if r is None else str(r[0])
        finally:
            conn.close()

    def save_meta(self, m: StudyMeta, now: pd.Timestamp) -> None:
        conn = self._conn()
        try:
            with write(conn):
                conn.execute("INSERT INTO tv_study_meta (meta_hash8, study_id, name, version, meta, first_seen_utc, "
                             "last_seen_utc) VALUES (?,?,?,?,?,?,?) ON CONFLICT (meta_hash8) DO UPDATE SET "
                             "last_seen_utc = excluded.last_seen_utc",
                             (m.meta_hash8, m.study_id, m.name, m.version, m.model_dump_json(), _utc(now).isoformat(),
                              _utc(now).isoformat()))
        finally:
            conn.close()

    def metas(self) -> list[StudyMeta]:
        conn = self._conn()
        try:
            return [load_record_json(StudyMeta, r[0], source="tv.db tv_study_meta")
                    for r in conn.execute("SELECT meta FROM tv_study_meta ORDER BY first_seen_utc, meta_hash8")]
        finally:
            conn.close()


# ------------------------------------------------------------------------------------------------- pacing
class Pacer:
    """At most one request every `min_interval_s` (monotonic clock); counts the requests."""

    def __init__(self, min_interval_s: float, monotonic: Callable[[], float], sleep: Callable[[float], None]):
        self.min_interval_s, self._mono, self._sleep = min_interval_s, monotonic, sleep
        self._last: float | None = None
        self.count = 0

    def wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval_s - (self._mono() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._mono()
        self.count += 1


# ------------------------------------------------------------------------------------------------- pages
def _auth(net: ReadOnlyNetwork, pacer: Pacer, hint: str) -> tuple[str, str | None]:
    pacer.wait()
    page = net.get(SITE + "/")
    m = re.search(r'"auth_token"\s*:\s*"([^"]+)"', page)
    if m is None:
        raise TvAuthError(f"TradingView did not accept the session cookie (expired or signed out): store fresh "
                          f"ones with {hint}")
    net.add_secret(m.group(1))
    uid = re.search(r'"id"\s*:\s*(\d+)', page)
    return m.group(1), uid.group(1) if uid else None


def _studies_in(obj: Any) -> Iterator[StudySpec]:
    """Pine studies in a layout's saved sources: any object whose `inputs` carry a `pineId`."""
    if isinstance(obj, dict):
        inputs = obj.get("inputs")
        if isinstance(inputs, dict) and isinstance(inputs.get("pineId"), str):
            yield StudySpec(study_id=inputs["pineId"], version=str(inputs.get("pineVersion") or "last"),
                            inputs={k: v for k, v in inputs.items() if _INPUT.fullmatch(k)},
                            name=obj.get("shortDescription") or obj.get("description"))
            return
        for v in obj.values():
            yield from _studies_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _studies_in(v)


def _spec_key(s: StudySpec) -> str:
    return json.dumps([s.study_id, s.version, s.inputs], sort_keys=True, default=str)


def discover(net: ReadOnlyNetwork, pacer: Pacer, user_id: str | None, max_studies: int) -> list[StudySpec]:
    """Every Pine study on the owner's saved layouts, with the layout's inputs (read-only GETs)."""
    pacer.wait()
    listing = json.loads(net.get(SITE + "/my-charts/"))
    layouts = listing if isinstance(listing, list) else (listing.get("results") or listing.get("charts") or [])
    found: dict[str, StudySpec] = {}
    if user_id is None:
        log.info("tv pull: no user id on the signed-in page; layouts not read")
        return []
    for lay in layouts:
        lid = str(lay.get("image_url") or "") if isinstance(lay, dict) else ""
        if not lid:
            continue
        pacer.wait()
        token = json.loads(net.get(f"{SITE}/chart-token/?image_url={quote(lid)}&user_id={user_id}")).get("token")
        if not token:
            continue
        net.add_secret(str(token))
        pacer.wait()
        sources = json.loads(net.get(f"{STORAGE}/{quote(lid)}/sources?chart_id=_shared&jwt={token}"))
        for spec in _studies_in(sources):
            found.setdefault(_spec_key(spec), spec)
        if len(found) >= max_studies:
            break
    return list(found.values())[:max_studies]


def fetch_meta(net: ReadOnlyNetwork, pacer: Pacer, spec: StudySpec) -> tuple[StudyMeta, str, dict[str, str]]:
    """(metadata, compiled source, input types) of one study. The compiled source is the author's: memory only."""
    pacer.wait()
    body = json.loads(net.get(f"{PINE}/{quote(spec.study_id, safe='')}/{quote(spec.version, safe='')}"))
    res = body.get("result") if isinstance(body, dict) else None
    if not isinstance(res, dict) or not body.get("success", True) or not isinstance(res.get("ilTemplate"), str) \
            or not res["ilTemplate"]:
        raise TvPullError(f"no compiled source for {spec.study_id} (access revoked or the script was removed)")
    il: str = res["ilTemplate"]
    meta = res.get("metaInfo") or {}
    declared = [i for i in meta.get("inputs") or [] if isinstance(i, dict) and _INPUT.fullmatch(str(i.get("id")))]
    defaults = {str(i["id"]): i.get("defval") for i in declared}
    types = {str(i["id"]): str(i.get("type") or "") for i in declared}
    inputs = {**defaults, **{k: v for k, v in spec.inputs.items() if k in defaults}}
    styles = meta.get("styles") or {}
    plots = [PlotMeta(id=str(p["id"]), title=str((styles.get(p["id"]) or {}).get("title") or p["id"]),
                      type=str(p.get("type") or ""), target=p.get("target"))
             for p in meta.get("plots") or [] if isinstance(p, dict) and p.get("id")]
    colourers = {p.id: p.target for p in plots if "colorer" in p.type and p.target}
    name = str(meta.get("shortDescription") or meta.get("description") or spec.name or spec.study_id)
    version = str((meta.get("pine") or {}).get("version") or spec.version)
    return (StudyMeta.build(study_id=spec.study_id, name=name, version=version, inputs=inputs, plots=plots,
                            colourers=colourers), il, types)


# ------------------------------------------------------------------------------------------------- the chart
class _Chart:
    """One chart session on the socket: a series, its history pages and its studies."""

    def __init__(self, sock: GuardedSocket, pacer: Pacer, monotonic: Callable[[], float], timeout_s: float):
        self.sock, self.pacer, self._mono, self.timeout_s = sock, pacer, monotonic, timeout_s
        self.cs = "cs_" + secrets.token_hex(6)
        self.bars: dict[int, list[float]] = {}
        self.studies: dict[str, dict[int, list[float]]] = {}
        self.done: set[str] = set()

    def _send(self, method: str, params: list[Any]) -> None:
        self.sock.send(encode(method, params))

    def _pump(self, key: str, what: str) -> None:
        deadline = self._mono() + self.timeout_s
        while key not in self.done:
            left = deadline - self._mono()
            try:
                if left <= 0:
                    raise TimeoutError
                raw = self.sock.recv(left)
            except TimeoutError:
                raise TvPullError(f"no answer to {what} within {self.timeout_s:g} s") from None
            for payload in decode(raw):
                self._on(payload)

    def _on(self, payload: str) -> None:
        if is_heartbeat(payload):
            self.sock.send(encode_raw(payload))
            return
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        if not isinstance(msg, dict) or "m" not in msg:
            return                                          # the session hello
        m, p = msg["m"], msg.get("p") or []
        if m in _ERRORS:
            text = f"TradingView answered {m}: {json.dumps(p[1:])[:200]}"
            raise (TvAuthError if _REFUSED.search(text) else TvPullError)(text)
        if m in ("timescale_update", "du") and len(p) > 1 and isinstance(p[1], dict):
            for sid, data in p[1].items():
                if not isinstance(data, dict):
                    continue
                if sid == "$prices":
                    for row in data.get("s") or []:
                        v = list(row.get("v") or [])
                        if v:
                            self.bars[int(v[0])] = [_num(x) for x in (v[1:6] + [None] * 5)[:5]]
                elif sid in self.studies:
                    for row in data.get("st") or []:
                        v = list(row.get("v") or [])
                        if v:
                            self.studies[sid][int(v[0])] = [_num(x) for x in v[1:]]
        elif m == "series_completed":
            self.done.add("$prices")
        elif m == "study_completed" and len(p) > 1:
            self.done.add(str(p[1]))

    def load(self, symbol: str, tf: str, n: int, stop_at: int | None, max_pages: int) -> int:
        """The newest `n` bars, then history pages until the store's newest bar is reached (`stop_at`), history
        ends, or `max_pages`. Returns the pages requested."""
        self._send("chart_create_session", [self.cs, ""])
        self._send("switch_timezone", [self.cs, "Etc/UTC"])
        self._send("resolve_symbol", [self.cs, "ser_1", "=" + json.dumps({"adjustment": "splits", "symbol": symbol})])
        self.pacer.wait()
        self._send("create_series", [self.cs, "$prices", "s1", "ser_1", tf, n, ""])
        self._pump("$prices", f"create_series {symbol} {tf}")
        pages = 0
        while pages < max_pages and self.bars:
            if stop_at is not None and min(self.bars) <= stop_at:
                break
            before = len(self.bars)
            self.done.discard("$prices")
            self.pacer.wait()
            self._send("request_more_data", [self.cs, "$prices", n])
            self._pump("$prices", f"request_more_data {symbol} {tf}")
            pages += 1
            if len(self.bars) == before:
                break                                       # the start of the history this plan serves
        return pages

    def study(self, meta: StudyMeta, il: str, types: dict[str, str], idx: int) -> dict[int, list[float]]:
        sid = f"st_{idx}"
        self.studies[sid] = {}
        inputs: dict[str, Any] = {"text": il, "pineId": meta.study_id, "pineVersion": meta.version}
        inputs.update({k: {"v": v, "f": True, "t": types.get(k, "")} for k, v in meta.inputs.items()})
        self.pacer.wait()
        self._send("create_study", [self.cs, sid, "st1", "$prices", STUDY_TYPE, inputs])
        self._pump(sid, f"create_study {meta.name}")
        return self.studies[sid]

    def close(self) -> None:
        try:
            self._send("chart_delete_session", [self.cs])
        except TvPullError:
            pass


# ------------------------------------------------------------------------------------------------- the store
def _read(store: Store, source: str, symbol: str, start: pd.Timestamp | None = None) -> pd.DataFrame:
    if not (store.table_dir(TABLE) / f"source={source}" / f"symbol={symbol}").exists():
        return pd.DataFrame()
    return store.read(TABLE, source=source, symbol=symbol, start=start)


def _pulled(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    if df.empty or "origin" not in df.columns:
        return df.iloc[0:0]
    return df[(df["tf"].astype(str) == tf) & df["origin"].isin(ORIGINS)]


def history_latency_s(store: Store, cfg: TvPullSettings, *, now: pd.Timestamp) -> tuple[float, bool]:
    """(p95 seconds from bar close to receipt of the live-pulled bars over LATENCY_WINDOW, measured?); the default
    until `min_live_samples` live bars exist."""
    lags: list[np.ndarray] = []
    for chart_sym, store_sym in cfg.symbols.items():
        df = _read(store, source_of(CHART, chart_sym), safe_symbol(store_sym), _utc(now) - LATENCY_WINDOW)
        if df.empty or "origin" not in df.columns:
            continue
        df = df[(df["origin"] == ORIGIN_LIVE) & (df["plot"] == "close")]
        if df.empty:
            continue
        width = df["tf"].astype(str).map(tf_seconds).to_numpy(dtype=float)
        lag = (_epoch_s(df["received_at"]) - _epoch_s(df["bar_time"])).astype(float) - width
        lags.append(lag)
    all_lags = np.concatenate(lags) if lags else np.array([])
    if len(all_lags) >= cfg.min_live_samples:
        return float(np.quantile(all_lags, 0.95)), True
    return float(cfg.history_latency_default_s), False


class _Written(FrozenRecord):
    new: list[int]
    live: int
    mismatched: int
    rows: int


def _write(store: Store, *, indicator: str, version: str, symbol: str, tf: str, values: dict[int, list[float]],
           names: Sequence[str], closes: dict[int, float], received: pd.Timestamp, cfg: TvPullSettings,
           latency_s: float) -> _Written:
    """Append the bars of `values` not yet stored (first receipt wins) and count the stored ones that changed."""
    if not values:
        return _Written(new=[], live=0, mismatched=0, rows=0)
    source = source_of(indicator, version)
    times = sorted(values)
    start = pd.Timestamp(times[0], unit="s", tz="UTC")
    held = _pulled(_read(store, source, symbol, start), tf)
    mismatched = 0
    stored: set[int] = set()
    if not held.empty:
        held = held.assign(_t=_epoch_s(held["bar_time"]))
        stored = set(int(t) for t in held["_t"])
        old = held.drop_duplicates(["_t", "plot"]).pivot(index="_t", columns="plot", values="value")
        for t in sorted(stored & set(times)):
            a = np.array([old.at[t, n] if n in old.columns else np.nan for n in names], dtype=float)
            b = np.array(values[t][:len(names)] + [np.nan] * (len(names) - len(values[t])), dtype=float)
            if not np.allclose(a, b, rtol=0.0, atol=1e-9, equal_nan=True):
                mismatched += 1
    new = [t for t in times if t not in stored]
    if not new:
        return _Written(new=[], live=0, mismatched=mismatched, rows=0)
    width = tf_seconds(tf)
    bar = pd.to_datetime(np.array(new, dtype="int64"), unit="s", utc=True)
    close = bar + pd.Timedelta(seconds=width)
    live = (received - close) <= pd.Timedelta(seconds=cfg.live_max_lag_s)
    available = (close + pd.Timedelta(seconds=latency_s)).where(~live, received)
    base = pd.DataFrame({
        "ts_utc": bar, "bar_time": bar, "available_utc": available, "fired_at": close, "received_at": received, "indicator": indicator, "version": version, "tf": tf,
        "price": [closes.get(t, math.nan) for t in new], "intrabar": False,
        "origin": np.where(live, ORIGIN_LIVE, ORIGIN_HISTORY)})
    frames = []
    for k, name in enumerate(names):
        frames.append(base.assign(plot=name, value=[values[t][k] if k < len(values[t]) else math.nan for t in new]))
    df = pd.concat(frames, ignore_index=True)
    df["price"] = df["price"].astype("float64")
    df["value"] = df["value"].astype("float64")
    store.append(TABLE, df, source=source, symbol=symbol)
    return _Written(new=new, live=int(live.sum()), mismatched=mismatched, rows=len(df))


def _newest(store: Store, source: str, symbol: str, tf: str) -> int | None:
    held = _pulled(_read(store, source, symbol), tf)
    return None if held.empty else int(_epoch_s(held["bar_time"]).max())


# ------------------------------------------------------------------------------------------------- one pass
def _specs(cfg: TvPullSettings, net: ReadOnlyNetwork, pacer: Pacer, plog: PullLog, uid: str | None,
           started: pd.Timestamp, extra: Sequence[StudySpec]) -> list[StudySpec]:
    specs = list(extra)
    if cfg.discover:
        cached, at = plog.get_state("discovered"), plog.get_state("discovered_utc")
        if cached is not None and at is not None and (started - _utc(at)).total_seconds() < cfg.discover_every_s:
            found = [load_record(StudySpec, x, source="tv.db tv_pull_state") for x in json.loads(cached)]
        else:
            found = discover(net, pacer, uid, cfg.max_studies)
            plog.set_state("discovered", json.dumps([s.model_dump() for s in found], default=str), started)
            plog.set_state("discovered_utc", started.isoformat(), started)
        specs += found
    unique: dict[str, StudySpec] = {}
    for s in specs:
        unique.setdefault(_spec_key(s), s)
    return list(unique.values())[:cfg.max_studies]


def _compiled(net: ReadOnlyNetwork, pacer: Pacer, spec: StudySpec, cfg: TvPullSettings, state: FollowState | None,
              mono: float) -> tuple[StudyMeta, str, dict[str, str]]:
    key = _spec_key(spec)
    if state is not None and key in state.compiled and mono - state.compiled[key][3] < cfg.discover_every_s:
        meta, il, types, _ = state.compiled[key]
        return meta, il, types
    meta, il, types = fetch_meta(net, pacer, spec)
    if state is not None:
        state.compiled = {**state.compiled, key: (meta, il, types, mono)}
    return meta, il, types


def _pass(cfg: TvPullSettings, *, store: Store, plog: PullLog, net: ReadOnlyNetwork, pacer: Pacer,
          now: Callable[[], pd.Timestamp], monotonic: Callable[[], float], started: pd.Timestamp, hint: str,
          state: FollowState | None, extra: Sequence[StudySpec], timeframes: Sequence[str]) -> PassReport:
    token, uid = _auth(net, pacer, hint)
    studies = []
    unavailable: list[str] = []
    for spec in _specs(cfg, net, pacer, plog, uid, started, extra):
        try:
            meta, il, types = _compiled(net, pacer, spec, cfg, state, monotonic())
        except (TvAuthError, ReadOnlyViolation):
            raise
        except (TvPullError, ValueError, KeyError) as exc:   # one revoked or removed script never stops the others
            unavailable.append(f"{spec.study_id}: {redact(str(exc), net.secrets)[:120]}")
            continue
        plog.save_meta(meta, _utc(now()))
        studies.append((meta, il, types))
    latency, measured = history_latency_s(store, cfg, now=started)
    per_tf: list[TfReport] = []
    sock = net.open_socket()
    try:
        sock.send(encode("set_auth_token", [token]))
        for chart_sym, store_sym in cfg.symbols.items():
            for tf in timeframes:
                rep = _pull_tf(cfg, store=store, plog=plog, sock=sock, pacer=pacer, now=now, monotonic=monotonic,
                               started=started, chart_sym=chart_sym, symbol=safe_symbol(store_sym), tf=tf,
                               studies=studies, latency=latency)
                if rep is not None:
                    per_tf.append(rep)
    finally:
        sock.close()
    return PassReport(ok=True, started_utc=started, finished_utc=_utc(now()), requests=pacer.count,
                      studies=[f"{m.name}@{m.meta_hash8}" for m, _, _ in studies], unavailable=unavailable,
                      per_tf=per_tf,
                      history_latency_s=latency, history_latency_measured=measured)


def _pull_tf(cfg: TvPullSettings, *, store: Store, plog: PullLog, sock: GuardedSocket, pacer: Pacer,
             now: Callable[[], pd.Timestamp], monotonic: Callable[[], float], started: pd.Timestamp, chart_sym: str,
             symbol: str, tf: str, studies: list[tuple[StudyMeta, str, dict[str, str]]],
             latency: float) -> TfReport | None:
    width = tf_seconds(tf)
    newest = _newest(store, source_of(CHART, chart_sym), symbol, tf)
    t0 = int(_epoch_s(pd.DatetimeIndex([started]))[0])
    empty_at = plog.get_state(f"empty:{chart_sym}:{tf}")
    if newest is not None and (t0 < newest + 2 * width or (bool(empty_at) and t0 - float(empty_at or 0) < 0.9 * width)):
        return None                                       # no new bar can have closed since the last look
    chart = _Chart(sock, pacer, monotonic, cfg.timeout_s)
    try:
        pages = chart.load(chart_sym, tf, cfg.bars_per_request, newest, cfg.max_pages_per_tf)
        values = [(m, chart.study(m, il, types, i + 1)) for i, (m, il, types) in enumerate(studies)]
    finally:
        chart.close()
    received = _utc(now())
    rs = int(_epoch_s(pd.DatetimeIndex([received]))[0])
    times = sorted(chart.bars)
    if times and times[-1] > rs:
        raise TvPullError(f"future bar: the {chart_sym} {tf} bar opening "
                          f"{pd.Timestamp(times[-1], unit='s', tz='UTC').isoformat()} is after now "
                          f"({received.isoformat()}): a wrong clock or timezone; nothing written")
    closed = [t for t in times if t + width <= rs]
    closes = {t: chart.bars[t][3] for t in closed}

    def put(indicator: str, version: str, vals: dict[int, list[float]], names: Sequence[str]) -> _Written:
        return _write(store, indicator=indicator, version=version, symbol=symbol, tf=tf, values=vals, names=names,
                      closes=closes, received=received, cfg=cfg, latency_s=latency)

    bars = put(CHART, chart_sym, {t: chart.bars[t] for t in closed}, OHLCV)
    rows, mismatched = bars.rows, bars.mismatched
    cut = times[cfg.study_warmup_bars] if len(times) > cfg.study_warmup_bars else None
    for meta, vals in values:
        if cut is None:
            continue
        keep = {t: vals[t] for t in closed if t in vals and t >= cut}
        w = put(meta.name, meta.meta_hash8, keep, [p.id for p in meta.plots])
        rows, mismatched = rows + w.rows, mismatched + w.mismatched
    plog.set_state(f"empty:{chart_sym}:{tf}", "" if bars.new else str(t0), started)   # a fruitless look waits a bar
    return TfReport(symbol=chart_sym, tf=tf, bars_received=len(times), pages=pages,
                    oldest_bar=pd.Timestamp(times[0], unit="s", tz="UTC") if times else None,
                    newest_bar=pd.Timestamp(times[-1], unit="s", tz="UTC") if times else None,
                    dropped_forming=len(times) - len(closed), new_bars=len(bars.new), live_bars=bars.live,
                    history_bars=len(bars.new) - bars.live, repaint_mismatch_bars=mismatched, studies=len(values),
                    rows=rows)


def run_pass(cfg: TvPullSettings, *, store: Store, log: PullLog, network: TvNetwork, cookie: SecretStr | None,
             now: Callable[[], pd.Timestamp] | None = None, monotonic: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep, state: FollowState | None = None,
             extra: Sequence[StudySpec] = (), timeframes: Sequence[str] | None = None) -> PassReport:
    """One pass, fail soft: never raises for a network, protocol, cookie or data fault; the result is recorded in the
    pull log and returned. Skipped passes (backoff, daily budget) are returned, not recorded."""
    clock = now or (lambda: pd.Timestamp.now(tz="UTC"))
    started = _utc(clock())
    hint = set_hint(cfg)
    tfs = [normalize_tf(t) for t in (timeframes or cfg.timeframes)]
    secret = cookie.get_secret_value().strip() if cookie is not None else ""
    if not secret:
        rep = PassReport.failed(started, started, f"no TradingView session cookie in the keyring: store it with {hint}",
                                requests=0, auth_expired=True)
        record_expired(log.path.parent, started)            # no cookie: the sign-in cannot work (one notice)
        if state is not None:
            state.failed_cookie = ""                         # any cookie stored next is tried at once
        log.record(rep)
        _announce(rep)
        return rep
    blocked = log.next_allowed(cfg)
    if blocked is not None and started < blocked:
        last = log.last()
        fresh = (state is not None and last is not None and last.auth_expired and state.failed_cookie is not None
                 and state.failed_cookie != secret)
        if not fresh:
            return PassReport(ok=False, skipped=True, started_utc=started, finished_utc=started,
                              reason=f"backing off after a failed pass until {blocked.isoformat()}")
    used = log.requests_since(started - pd.Timedelta(hours=24))
    if used >= cfg.max_requests_per_day:
        return PassReport(ok=False, skipped=True, started_utc=started, finished_utc=started,
                          reason=f"daily request budget reached ({used} of {cfg.max_requests_per_day} in 24 h)")
    net = ReadOnlyNetwork(network, secret, secret_parts(secret))
    pacer = Pacer(cfg.min_interval_s, monotonic, sleep)
    try:
        rep = _pass(cfg, store=store, plog=log, net=net, pacer=pacer, now=clock, monotonic=monotonic,
                    started=started, hint=hint, state=state, extra=extra, timeframes=tfs)
    except TvAuthError as exc:
        rep = PassReport.failed(started, _utc(clock()), redact(str(exc), net.secrets)[:400], requests=pacer.count,
                                auth_expired=True)
        record_expired(log.path.parent, rep.finished_utc)   # one owner notice per expiry (ops/tv_pull_health.py)
        if state is not None:
            state.failed_cookie = secret
    except Exception as exc:  # fail soft (the alert path is independent); the message is redacted, never chained
        rep = PassReport.failed(started, _utc(clock()), redact(f"{type(exc).__name__}: {exc}", net.secrets)[:400],
                                requests=pacer.count)
    else:
        record_ok(log.path.parent, rep.finished_utc)        # re-arms the expiry notice
        if state is not None:
            state.failed_cookie = None
    log.record(rep)
    _announce(rep)
    return rep


def set_hint(cfg: TvPullSettings) -> str:
    return (f"python -m goldbot.ops.accounts set {cfg.sessionid_key} and set {cfg.sessionid_sign_key} (hidden "
            f"prompts; the browser cookies sessionid and sessionid_sign)")


def _announce(rep: PassReport) -> None:
    if rep.ok:
        log.info("tv pull ok: %d requests, %d new bars", rep.requests, sum(t.new_bars for t in rep.per_tf))
    else:
        log.warning("tv pull failed: %s", rep.reason)


# ------------------------------------------------------------------------------------------------- CLI
def _parse_spec(raw: str) -> StudySpec:
    sid, _, ver = raw.partition("@")
    return StudySpec(study_id=sid, version=ver or "last")


def next_poll(now: pd.Timestamp, cfg: TvPullSettings) -> pd.Timestamp:
    """The next pass time: the next multiple of poll_interval_s after `now`, plus poll_offset_s."""
    step = pd.Timedelta(seconds=cfg.poll_interval_s)
    base = _utc(now).floor(step)
    t = base + pd.Timedelta(seconds=cfg.poll_offset_s)
    return t if t > now else t + step


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py tv-pull", description="TradingView direct pull (read-only): saved "
                                 "layouts' indicators and bars into tv_indicators.")
    ap.add_argument("action", choices=("sync", "follow", "status"))
    ap.add_argument("--study", action="append", default=[], metavar="PINE_ID[@VERSION]",
                    help="also pull this script (e.g. 'PUB;abc@last'), beside the ones on the saved layouts")
    ap.add_argument("--tf", action="append", default=[], help="timeframe(s) instead of settings tv_pull.timeframes")
    ap.add_argument("--state", default="state", help="state directory (tv.db)")
    ap.add_argument("--data-root", default=None, help="store root (default: settings data_root)")
    a = ap.parse_args(argv)
    from goldbot.config import load_settings
    settings = load_settings()
    cfg = settings.tv_pull
    plog = PullLog(Path(a.state) / "tv.db")
    if a.action == "status":
        last = plog.last()
        print(f"tv_auth={plog.auth_state() or 'never run'}; studies seen: {len(plog.metas())}")
        print(last.text() if last else "no pass yet")
        return 0
    if not cfg.enabled:
        print("tv-pull: tv_pull.enabled is false (set it in config/settings.local.yaml)", file=sys.stderr)
        return 2
    from goldbot.data.tv_wire import LiveNetwork
    from goldbot.ops.accounts import get_secret
    store = Store(a.data_root or settings.data_root)
    extra = [_parse_spec(s) for s in a.study]
    network = LiveNetwork(cfg.timeout_s)

    def once(state: FollowState | None) -> PassReport:
        sid, sign = get_secret(cfg.sessionid_key), get_secret(cfg.sessionid_sign_key)
        cookie = SecretStr(f"sessionid={sid}" + (f"; sessionid_sign={sign}" if sign else "")) if sid else None
        return run_pass(cfg, store=store, log=plog, network=network, cookie=cookie,
                        state=state, extra=extra, timeframes=a.tf or None)

    if a.action == "sync":
        rep = once(None)
        print(rep.text())
        return 0 if rep.ok else 1
    state = FollowState()
    try:
        while True:
            rep = once(state)
            print(rep.text(), flush=True)
            wait = (next_poll(pd.Timestamp.now(tz="UTC"), cfg) - pd.Timestamp.now(tz="UTC")).total_seconds()
            time.sleep(max(wait, 1.0))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
