"""TradingView direct pull: the owner's saved layouts, their indicators and bars, read from TradingView's chart
websocket with the owner's session cookie (owner decision 2026-10-18, ADR 0030).

    python -m goldbot.ops.run tv-pull sync      # one pass: discover, backfill what is missing, add the new closed bars
    python -m goldbot.ops.run tv-pull follow    # a pass after every 15m bar close (poll_interval_s + poll_offset_s)
    python -m goldbot.ops.run tv-pull status    # last pass, cookie state, studies seen; no network

The only owner step is storing the two browser cookies (`python -m goldbot.ops.accounts set tradingview-sessionid`
and `... set tradingview-sessionid-sign`, hidden prompts) and switching `tv_pull.enabled` on in settings.local.yaml.
Everything else is automatic:

* Discovery. The saved layouts (`/my-charts/`) are listed and each layout's chart page read: its `initData.content`
  holds every study with the owner's input values, the script id in the metaInfo string (`Script$PUB;<id>@...[v.2.0]`);
  `/chart-token/` + charts-storage is a fallback only (it answered 403 to the probe). Re-read every
  `discover_every_s`, cached in the pull log between.
* Metadata. The script's metaInfo (pine-facade) names each plot (`plots[].id`, the style title, the plot type and a
  colourer's target). It is kept in `tv_study_meta` with the catalog's view of it (`metainfo`) and the catalog's
  `meta_hash` (goldbot/data/tv_catalog.py: study, version, input values), so the feature id
  `tv_{study}_{meta_hash}_{tf}_{plot_id}` needs no dashboard naming. The script's compiled source (`ilTemplate`, the
  author's work) is sent to TradingView to compute the study and never stored.
* Rows. "Output lands in the same store the plots reader uses": `tv_indicators`, the `inbox.long_rows` schema plus
  `origin` and `meta_hash`; bars as indicator `chart` (version = the chart symbol, plots open/high/low/close/volume,
  `meta_hash` ""), each study as indicator = its short name, version = the script version, plot = the plot id
  (TradingView's na, 1e100, is NaN; a colourer's value is a packed colour int). Closed bars only: a bar still forming
  at receipt is dropped, a bar opening after now fails the pass (wrong clock), nothing written for that timeframe;
  the 1D series' leading yearly bars (before 1921) are dropped. Study values over a loaded window are recomputed by
  TradingView (`StudyMeta.history_recomputed`): only `pull_live` rows were seen as they were.
* "Every join to bars via asof_join on available_utc". `pull_live` (received within `live_max_lag_s` of the close):
  `available_utc` = the receipt time. `pull_history` (backfill): bar close + the p95 latency of the live bars over
  the last 30 days (`history_latency_s`), `history_latency_default_s` (960 s) until `min_live_samples` (20) exist.
  The default is conservative: a backfilled row becomes available about 900 s later than a live pull of the same bar
  will be (one poll interval plus the offset), so research on history never sees a value earlier than live would. First receipt
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
from goldbot.db.migrations import migrate
from goldbot.ops.tv_pull_health import DB_FILE as TV_AUTH_DB
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
STRATEGY_TYPE = "StrategyScript@tv-scripting-101!"
SERIES = "sds_1"
SPARSE_START_S = 14 * 86400          # bars before the last gap this long are dropped (1D: yearly/monthly early bars)
STORAGE = "https://charts-storage.tradingview.com/charts-storage/get/layout"
PINE = "https://pine-facade.tradingview.com/pine-facade/translate"
LATENCY_WINDOW = pd.Timedelta(days=30)
_ERRORS = {"critical_error", "protocol_error", "symbol_error", "series_error", "study_error"}
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
    """What the study is, from its metaInfo and the layout's saved inputs. `metainfo` is the catalog's view of it
    (goldbot/data/tv_catalog.py `StudyMeta.from_metainfo`), and `meta_hash8` is the catalog's `meta_hash` of it, so the
    rows, this record and the feature id `tv_{study}_{meta_hash}_{tf}_{plot_id}` agree."""

    study_id: str                         # pine id, e.g. PUB;abc or STD;RSI
    name: str
    version: str                          # the script's own version
    inputs: dict[str, Any]                # every declared input but the source and ids: the layout's value, else default
    input_types: dict[str, str]
    input_fake: dict[str, bool] = Field(default_factory=dict)
    colour_index: dict[str, int] = Field(default_factory=dict)   # colour inputs are sent as their input index
    plots: list[PlotMeta]
    colourers: dict[str, str]             # colourer plot id -> the plot it colours (its values are packed colour ints)
    is_strategy: bool = False
    # every value of a loaded window is recomputed by TradingView over the history (repaint risk): only a bar first
    # received live (origin pull_live) was seen as it was
    history_recomputed: bool = True
    metainfo: dict[str, Any]
    meta_hash8: str

    @classmethod
    def build(cls, *, study_id: str, name: str, version: str, inputs: dict[str, Any], input_types: dict[str, str],
              plots: list[PlotMeta], input_fake: dict[str, bool] | None = None,
              colour_index: dict[str, int] | None = None, is_strategy: bool = False) -> StudyMeta:
        from goldbot.data.tv_catalog import StudyMeta as CatalogMeta
        metainfo = {"id": study_id, "shortDescription": name, "pine": {"version": version},
                    "plots": [{k: v for k, v in p.model_dump().items() if k != "title" and v is not None} for p in plots],
                    "styles": {p.id: {"title": p.title} for p in plots},
                    "inputs": [{"id": k, "value": v, "type": input_types.get(k, "")} for k, v in inputs.items()]}
        return cls(study_id=study_id, name=name, version=version, inputs=inputs, input_types=input_types,
                   input_fake=input_fake or {}, colour_index=colour_index or {}, plots=plots,
                   colourers={p.id: p.target for p in plots if "colorer" in p.type and p.target},
                   is_strategy=is_strategy, metainfo=metainfo,
                   meta_hash8=CatalogMeta.from_metainfo(metainfo, tf="15").meta_hash)


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


def cookie_fingerprint(cookie: str) -> str:
    """A short one-way fingerprint of the cookie, to notice that a new one was stored (the cookie itself is never kept
    beyond the pass, not even in a repr)."""
    return hashlib.sha256(cookie.encode()).hexdigest()[:12] if cookie else ""


class FollowState(Record):
    """In-memory state of a `follow` loop: compiled study sources (never written anywhere) and the fingerprint of the
    cookie that failed sign-in ("" when none was stored), so a newly stored cookie is tried at once instead of after
    the expiry backoff."""

    compiled: dict[str, tuple[StudyMeta, str, float]] = Field(default_factory=dict)
    failed_cookie: str | None = None              # cookie_fingerprint, never the cookie


# ------------------------------------------------------------------------------------------------- the pull log
class PullLog:
    """The pull's durable record in state/tv.db (SQLite WAL, beside the alert inbox; no JSON state files). Its tables
    are tv.db's versioned schema (goldbot/db/migrations.py TV), brought up to date on open."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        try:
            migrate(conn, "tv")
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        return connect(self.path, "tv")

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
        path = self.path.parent / TV_AUTH_DB
        if not path.exists():
            return None
        conn = connect(path, "tv_auth")              # read only: goldbot/ops/tv_pull_health.py migrates it
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
_SCRIPT_ID = re.compile(r"^\w+\$([A-Z]+;[^@]+)@[\w-]+(?:\[v\.([\d.]+)\])?")
_SKIP_INPUTS = ("text", "pineId", "pineVersion")


def _auth(net: ReadOnlyNetwork, pacer: Pacer, hint: str) -> tuple[str, str | None, str]:
    """(websocket auth token, account id, plan: "" free, else e.g. "pro_premium") from the signed-in home page."""
    pacer.wait()
    page = net.get(SITE + "/")
    m = re.search(r'"auth_token"\s*:\s*"([^"]+)"', page)
    if m is None:
        raise TvAuthError(f"TradingView did not accept the session cookie (expired or signed out): store fresh "
                          f"ones with {hint}")
    net.add_secret(m.group(1))
    uid = re.search(r'"user"\s*:\s*\{[^{}]*?"id"\s*:\s*(\d+)', page) or \
        re.search(r'"id"\s*:\s*(\d+)\s*,\s*"username"', page) or re.search(r'"id"\s*:\s*(\d+)', page)
    if uid:
        net.add_secret(uid.group(1), min_len=1)            # the account id is an identifier: never in a report
    plan = re.search(r'"pro_plan"\s*:\s*"([^"]*)"', page)
    return m.group(1), uid.group(1) if uid else None, plan.group(1) if plan else ""


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    elif isinstance(obj, str) and obj[:1] in "{[" and "pineId" in obj:
        try:
            yield from _walk(json.loads(obj))
        except ValueError:
            pass


def _studies_in(obj: Any) -> list[StudySpec]:
    """Pine studies in a saved layout. The chart page's `initData.content` keeps each study's script id in its
    metaInfo STRING (`Script$PUB;<id>@tv-scripting-101[v.2.0]`) with `state.inputs.pineId` empty, and the inputs there
    are the owner's settings; charts-storage keeps it in `inputs.pineId` (verified by the local probe, 2026-10-18)."""
    out: list[StudySpec] = []
    for d in _walk(obj):
        st = d.get("state")
        state: dict[str, Any] = st if isinstance(st, dict) else d
        raw_inputs = state.get("inputs")
        inputs: dict[str, Any] = raw_inputs if isinstance(raw_inputs, dict) else {}
        mi_raw = d.get("metaInfo")
        mi: dict[str, Any] = mi_raw if isinstance(mi_raw, dict) else {}
        if isinstance(mi_raw, str):
            ms = _SCRIPT_ID.match(mi_raw)
            if ms:
                mi = {"scriptIdPart": ms.group(1), "pine": {"version": ms.group(2) or "last"}}
        pid = inputs.get("pineId") or mi.get("scriptIdPart") or d.get("pineId")
        if not isinstance(pid, str) or ";" not in pid:
            continue
        ver = str(inputs.get("pineVersion") or (mi.get("pine") or {}).get("version") or "last")
        out.append(StudySpec(study_id=pid, version=ver,
                             inputs={k: v for k, v in inputs.items() if k not in _SKIP_INPUTS},
                             name=str(mi.get("shortDescription") or mi.get("description") or "") or None))
    return out


def _layout_content(html: str) -> Any:
    """The saved layout the chart page embeds: `initData.content = {...};` (JSON)."""
    m = re.search(r"initData\.content\s*=\s*", html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError:
        return None
    return obj


def _spec_key(s: StudySpec) -> str:
    return json.dumps([s.study_id, s.version, s.inputs], sort_keys=True, default=str)


def discover(net: ReadOnlyNetwork, pacer: Pacer, user_id: str | None, max_studies: int) -> list[StudySpec]:
    """Every Pine study on the owner's saved layouts with the layout's inputs (read-only GETs): the layout list, then
    each layout's chart page; `/chart-token/` + charts-storage only when a page shows none (it answered 403 in the
    probe, so it is a fallback)."""
    pacer.wait()
    listing = json.loads(net.get(SITE + "/my-charts/", xhr=True))
    layouts = listing if isinstance(listing, list) else (listing.get("results") or listing.get("charts") or [])
    found: dict[str, StudySpec] = {}
    for lay in layouts:
        lid = str(lay.get("image_url") or lay.get("short_url") or lay.get("id") or "") if isinstance(lay, dict) else ""
        if not lid:
            continue
        net.add_secret(lid, min_len=1)                      # a layout id names the owner's chart: never in a report
        pacer.wait()
        content = _layout_content(net.get(f"{SITE}/chart/{quote(lid)}/"))
        specs = _studies_in(content) if content is not None else []
        if not specs and user_id is not None:
            pacer.wait()
            token = json.loads(net.get(f"{SITE}/chart-token/?image_url={quote(lid)}&user_id={user_id}")).get("token")
            if token:
                net.add_secret(str(token))
                pacer.wait()
                specs = _studies_in(json.loads(net.get(f"{STORAGE}/{quote(lid)}/sources?chart_id=_shared&jwt={token}")))
        for spec in specs:
            found.setdefault(_spec_key(spec), spec)
        if len(found) >= max_studies:
            break
    return list(found.values())[:max_studies]


def _unwrap(v: Any) -> Any:
    return v["v"] if isinstance(v, dict) and "v" in v else v           # layout state sometimes stores {v: ...}


def fetch_meta(net: ReadOnlyNetwork, pacer: Pacer, spec: StudySpec) -> tuple[StudyMeta, str]:
    """(metadata, compiled source) of one study. The compiled source is the author's work: memory only."""
    pacer.wait()
    body = json.loads(net.get(f"{PINE}/{quote(spec.study_id, safe='')}/{quote(spec.version, safe='')}"))
    res = body.get("result") if isinstance(body, dict) else None
    if not isinstance(res, dict) or not body.get("success", True) or not isinstance(res.get("ilTemplate"), str) \
            or not res["ilTemplate"]:
        raise TvPullError(f"no compiled source for {spec.study_id} (access revoked or the script was removed)")
    il: str = res["ilTemplate"]
    meta = res.get("metaInfo") or {}
    raw = [i for i in meta.get("inputs") or [] if isinstance(i, dict) and i.get("id")]
    declared = [(n, i) for n, i in enumerate(raw) if str(i["id"]) not in _SKIP_INPUTS]
    inputs = {str(i["id"]): _unwrap(spec.inputs.get(str(i["id"]), i.get("defval"))) for _, i in declared}
    styles = meta.get("styles") or {}
    plots = [PlotMeta(id=str(p["id"]), title=str((styles.get(p["id"]) or {}).get("title") or p["id"]),
                      type=str(p.get("type") or ""), target=p.get("target"))
             for p in meta.get("plots") or [] if isinstance(p, dict) and p.get("id")]
    return StudyMeta.build(
        study_id=str(meta.get("scriptIdPart") or spec.study_id),
        name=str(meta.get("shortDescription") or meta.get("description") or spec.name or spec.study_id),
        version=str((meta.get("pine") or {}).get("version") or spec.version), inputs=inputs,
        input_types={str(i["id"]): str(i.get("type") or "") for _, i in declared},
        input_fake={str(i["id"]): bool(i.get("isFake")) for _, i in declared},
        colour_index={str(i["id"]): n for n, i in declared if i.get("type") == "color"},
        plots=plots, is_strategy=bool(meta.get("isTVScriptStrategy")) or "strategy" in str(meta.get("id", "")).lower(),
    ), il


# ------------------------------------------------------------------------------------------------- the chart
class _Chart:
    """One chart session on the socket: series `sds_1` on symbol `sds_sym_1`, its history pages and its studies
    `st<k>` (the ids and sequence the probe verified against TradingView, 2026-10-18)."""

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
                if sid == SERIES:
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
            self.done.add(SERIES)
        elif m == "study_completed" and len(p) > 1:
            self.done.add(str(p[1]))

    def load(self, symbol: str, tf: str, n: int, stop_at: int | None, max_pages: int) -> int:
        """The newest `n` bars, then history pages until the store's newest bar is reached (`stop_at`), history
        ends, or `max_pages`. Returns the pages requested."""
        self._send("chart_create_session", [self.cs, ""])
        self._send("resolve_symbol", [self.cs, "sds_sym_1",
                                      "=" + json.dumps({"symbol": symbol, "adjustment": "splits", "session": "regular"})])
        self.pacer.wait()
        self._send("create_series", [self.cs, SERIES, "s1", "sds_sym_1", tf, n, ""])
        self._pump(SERIES, f"create_series {symbol} {tf}")
        pages = 0
        while pages < max_pages and self.bars:
            if stop_at is not None and min(self.bars) <= stop_at:
                break
            before = len(self.bars)
            self.done.discard(SERIES)
            self.pacer.wait()
            self._send("request_more_data", [self.cs, SERIES, n])
            self._pump(SERIES, f"request_more_data {symbol} {tf}")
            pages += 1
            if len(self.bars) == before:
                break                                       # the start of the history this plan serves
        return pages

    def study(self, meta: StudyMeta, il: str, idx: int) -> dict[int, list[float]]:
        sid = f"st{idx}"
        self.studies[sid] = {}
        inputs: dict[str, Any] = {"text": il, "pineId": meta.study_id, "pineVersion": meta.version}
        for k, v in meta.inputs.items():
            value = meta.colour_index[k] if k in meta.colour_index else v
            inputs[k] = {"v": value, "f": meta.input_fake.get(k, False), "t": meta.input_types.get(k, "")}
        self.pacer.wait()
        kind = STRATEGY_TYPE if meta.is_strategy else STUDY_TYPE
        self._send("create_study", [self.cs, sid, "st1", SERIES, kind, inputs])
        self._pump(sid, f"create_study {meta.name}")
        self._send("remove_study", [self.cs, sid])
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


def _pulled(df: pd.DataFrame, tf: str, meta_hash: str = "") -> pd.DataFrame:
    """The pulled rows of one timeframe and input set (`meta_hash`; "" for the bars)."""
    if df.empty or "origin" not in df.columns:
        return df.iloc[0:0]
    hashes = df["meta_hash"].fillna("").astype(str) if "meta_hash" in df.columns else pd.Series("", index=df.index)
    return df[(df["tf"].astype(str) == tf) & df["origin"].isin(ORIGINS) & hashes.eq(meta_hash)]


def _drop_sparse_start(times: list[int], tf: str) -> list[int]:
    """Keep only the bars after the last gap longer than SPARSE_START_S. TradingView's 1D gold series starts with
    yearly bars before 1921, monthly ones to 1970 and a patchy 1997-98 (real pass, 2026-10-18): daily bars are kept
    from 1998-10-14.
    Gold has no two-week gap in its real bars at any timeframe, so the cost is only history before an outage that
    long, never a wrong bar. 1D only: a weekly or monthly series has gaps this long by nature, and the intraday
    series showed no such start (quant/data review, 2026-10-18)."""
    if tf != "1D":
        return times
    gaps = [i for i in range(len(times) - 1) if times[i + 1] - times[i] > SPARSE_START_S]
    return times[gaps[-1] + 1:] if gaps else times


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


def _write(store: Store, *, indicator: str, version: str, meta_hash: str, symbol: str, tf: str,
           values: dict[int, list[float]],
           names: Sequence[str], closes: dict[int, float], received: pd.Timestamp, cfg: TvPullSettings,
           latency_s: float) -> _Written:
    """Append the bars of `values` not yet stored (first receipt wins) and count the stored ones that changed."""
    if not values:
        return _Written(new=[], live=0, mismatched=0, rows=0)
    source = source_of(indicator, version)
    times = sorted(values)
    start = pd.Timestamp(times[0], unit="s", tz="UTC")
    held = _pulled(_read(store, source, symbol, start), tf, meta_hash)
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
        "origin": np.where(live, ORIGIN_LIVE, ORIGIN_HISTORY), "meta_hash": meta_hash})
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
              mono: float) -> tuple[StudyMeta, str]:
    key = _spec_key(spec)
    if state is not None and key in state.compiled and mono - state.compiled[key][2] < cfg.discover_every_s:
        meta, il, _ = state.compiled[key]
        return meta, il
    meta, il = fetch_meta(net, pacer, spec)
    if state is not None:
        state.compiled = {**state.compiled, key: (meta, il, mono)}
    return meta, il


def _pass(cfg: TvPullSettings, *, store: Store, plog: PullLog, net: ReadOnlyNetwork, pacer: Pacer,
          now: Callable[[], pd.Timestamp], monotonic: Callable[[], float], started: pd.Timestamp, hint: str,
          state: FollowState | None, extra: Sequence[StudySpec], timeframes: Sequence[str]) -> PassReport:
    token, uid, plan = _auth(net, pacer, hint)
    studies = []
    unavailable: list[str] = []
    for spec in _specs(cfg, net, pacer, plog, uid, started, extra):
        try:
            meta, il = _compiled(net, pacer, spec, cfg, state, monotonic())
        except (TvAuthError, ReadOnlyViolation):
            raise
        except (TvPullError, ValueError, KeyError) as exc:   # one revoked or removed script never stops the others
            unavailable.append(f"{spec.study_id}: {redact(str(exc), net.secrets)[:120]}")
            continue
        plog.save_meta(meta, _utc(now()))
        studies.append((meta, il))
    latency, measured = history_latency_s(store, cfg, now=started)
    per_tf: list[TfReport] = []
    sock = net.open_socket(pro=bool(plan))                  # a paid plan's depth is on prodata
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
                      studies=[f"{m.name}@{m.meta_hash8}" for m, _ in studies], unavailable=unavailable,
                      per_tf=per_tf,
                      history_latency_s=latency, history_latency_measured=measured)


def _pull_tf(cfg: TvPullSettings, *, store: Store, plog: PullLog, sock: GuardedSocket, pacer: Pacer,
             now: Callable[[], pd.Timestamp], monotonic: Callable[[], float], started: pd.Timestamp, chart_sym: str,
             symbol: str, tf: str, studies: list[tuple[StudyMeta, str]],
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
        values = [(m, chart.study(m, il, i + 1)) for i, (m, il) in enumerate(studies)]
    finally:
        chart.close()
    received = _utc(now())
    rs = int(_epoch_s(pd.DatetimeIndex([received]))[0])
    times = _drop_sparse_start(sorted(chart.bars), tf)
    if times and times[-1] > rs:
        raise TvPullError(f"future bar: the {chart_sym} {tf} bar opening "
                          f"{pd.Timestamp(times[-1], unit='s', tz='UTC').isoformat()} is after now "
                          f"({received.isoformat()}): a wrong clock or timezone; nothing written")
    closed = [t for t in times if t + width <= rs]
    closes = {t: chart.bars[t][3] for t in closed}

    def put(indicator: str, version: str, meta_hash: str, vals: dict[int, list[float]],
            names: Sequence[str]) -> _Written:
        return _write(store, indicator=indicator, version=version, meta_hash=meta_hash, symbol=symbol, tf=tf,
                      values=vals, names=names, closes=closes, received=received, cfg=cfg, latency_s=latency)

    bars = put(CHART, chart_sym, "", {t: chart.bars[t] for t in closed}, OHLCV)
    rows, mismatched = bars.rows, bars.mismatched
    cut = times[cfg.study_warmup_bars] if len(times) > cfg.study_warmup_bars else None
    for meta, vals in values:
        if cut is None:
            continue
        keep = {t: vals[t] for t in closed if t in vals and t >= cut}
        w = put(meta.name, meta.version, meta.meta_hash8, keep, [p.id for p in meta.plots])
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
                 and state.failed_cookie != cookie_fingerprint(secret))
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
            state.failed_cookie = cookie_fingerprint(secret)
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


def stored_secret(key: str) -> str | None:
    """A cookie from the secret store every goldbot service uses (goldbot.ops.accounts: the OS keyring, or on the
    headless brain, where the units force the `fail` keyring backend, the owner-only state/.secrets.json)."""
    from goldbot.ops.accounts import get_secret
    value = get_secret(key)
    return value.strip() if value else None


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


def follow(once: Callable[[FollowState], PassReport], cfg: TvPullSettings, *,
           now: Callable[[], pd.Timestamp] | None = None, sleep: Callable[[float], None] = time.sleep,
           max_passes: int | None = None) -> None:
    """The `follow` loop: a pass after every poll. A pass already fails soft; anything that still raises around it
    (the secret store, the pull log) is logged by type only (its text could quote a secret) and the loop waits
    `backoff_base_s`, doubling to `backoff_max_s`, instead of ending the service."""
    clock = now or (lambda: pd.Timestamp.now(tz="UTC"))
    state = FollowState()
    errors = 0
    passes = 0
    while max_passes is None or passes < max_passes:
        passes += 1
        try:
            print(once(state).text(), flush=True)
            errors = 0
            wait = (next_poll(clock(), cfg) - clock()).total_seconds()
        except Exception as exc:  # the loop must outlive any one pass (fail soft; the alerts path is independent)
            errors += 1
            wait = min(cfg.backoff_base_s * 2 ** min(errors - 1, 30), cfg.backoff_max_s)
            log.warning("tv pull follow: pass raised %s; next try in %.0f s", type(exc).__name__, wait)
        sleep(max(wait, 1.0))


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
    store = Store(a.data_root or settings.data_root)
    extra = [_parse_spec(s) for s in a.study]
    network = LiveNetwork(cfg.timeout_s)

    def once(state: FollowState | None) -> PassReport:
        sid, sign = stored_secret(cfg.sessionid_key), stored_secret(cfg.sessionid_sign_key)
        cookie = SecretStr(f"sessionid={sid}" + (f"; sessionid_sign={sign}" if sign else "")) if sid else None
        return run_pass(cfg, store=store, log=plog, network=network, cookie=cookie,
                        state=state, extra=extra, timeframes=a.tf or None)

    if a.action == "sync":
        rep = once(None)
        print(rep.text())
        return 0 if rep.ok else 1
    try:
        follow(once, cfg)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
