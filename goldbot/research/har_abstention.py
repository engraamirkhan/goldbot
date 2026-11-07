"""N-3 HAR low-range-day abstention: the nightly forecast the engine's `refusals_novel` reads
(docs/research/prereg-2026-11-07-n3-har.md; card N-3 of docs/research/novel-techniques-2026-11-07.md).

Design rules, as built here:

* "Learning and control entries refused on HAR bottom-quintile days (trailing 250-day quintiles, computed at 22:00 UTC
  from closed daily bars via `asof_join`)". The cut-off is the latest opportunity-day close (22:00 UTC) at or before
  the job's slot (`cut_off`); only bars visible by it are read (`read_bars`, `mid_1h`), so the row is what the
  engine could have known at 22:00. The daily inputs, regressors and forecast are H-16's (`features.day_type`).
* One frozen OLS fit (`frozen_fit`) on the rows whose target day closed in [fit_from, fit_to), fit_to at most the
  holdout start (checked at load): the holdout trains nothing; recent days enter only as regressors.
* Low day: hat <= the `quantile` (0.2) of the `trailing_days` (250) forecasts emitted before it by the same fit.
* "A missing or stale HAR row gives har_unavailable plus an alert (fails closed)": a stale input (the last stub-free
  day is not the trading day before the forecast day), a short fit, a short trailing window or a hole in it (two
  consecutive input days more than `max_gap_days` apart, across the trailing forecasts and their HAR(22) lookback)
  raise `HarUnavailable`; the job then fails (the scheduler's job-failure health alert) and writes nothing, and the
  engine refuses learning entries that day.
* Point in time in the table: one row per (day, status), the first kept (`INSERT OR IGNORE`); `available_ns` is when
  it was written, and the engine reads only rows available at the signal's decision time.
* `enabled: false` writes an `off` row for the next trading day: from its availability the engine refuses nothing
  (the kill path; the prereg's kill rule).

Bars: the release's 1h bars (`bars_1h`, source dukascopy) before the hour of the canonical broker's first live minute,
that broker's own minutes (`account_bars_1m`: bars_1m_live, then its terminal M1, quarantine respected) from it on,
both as mid prices: one feed per span, one cut-over (engine/history.py).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.config import HarAbstentionSettings
from goldbot.data.timeutil import ROLLOVER_UTC_H, epoch_ns, opportunity_day, utc_index
from goldbot.db import Database
from goldbot.features import day_type as dt

log = logging.getLogger(__name__)

RELEASE_SOURCE = "dukascopy"            # engine/history.py RELEASE_SOURCE: the release bars research trained on
TABLE = "har_days"                      # aux migration 26
HOUR = pd.Timedelta(hours=1)


class HarUnavailable(RuntimeError):
    """No forecast can be written for the next trading day (stale or short input): the job fails, writes nothing."""


class HarRow(FrozenRecord):
    """One night's forecast for `for_day` (`ok`), or the job's `off` mark; `available_utc` is when it was written."""
    for_day: date
    status: Literal["ok", "off"]
    available_utc: UtcTimestamp
    inputs_close_utc: UtcTimestamp
    hat: float | None = None
    q20: float | None = None
    low: bool = False
    n_trailing: int = 0
    fit_rows: int = 0
    fit_to: date | None = None
    last_input_day: date | None = None


def cut_off(slot: pd.Timestamp) -> pd.Timestamp:
    """The latest opportunity-day close (ROLLOVER_UTC_H, 22:00 UTC) at or before `slot`."""
    t = pd.Timestamp(slot).tz_convert("UTC")
    close = t.normalize() + pd.Timedelta(hours=ROLLOVER_UTC_H)
    return close if close <= t else close - pd.Timedelta(days=1)


def next_trading_day(cut: pd.Timestamp) -> date:
    """The first weekday after the opportunity day that closed at `cut` (Friday, Saturday and Sunday -> Monday)."""
    d = pd.Timestamp(cut).tz_convert("UTC").tz_localize(None).normalize() + pd.Timedelta(days=1)
    while d.dayofweek >= 5:
        d += pd.Timedelta(days=1)
    return d.date()


def _previous_trading_day(day: date) -> date:
    d = pd.Timestamp(day) - pd.Timedelta(days=1)
    while d.dayofweek >= 5:
        d -= pd.Timedelta(days=1)
    return d.date()


def mid_1h(bars: pd.DataFrame) -> pd.DataFrame:
    """ts_utc, visible_at and mid OHLC of bid/ask bars (hourly or finer), aggregated to UTC hours."""
    cols = ["ts_utc", "visible_at", "open", "high", "low", "close"]
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=cols)
    ts = utc_index(bars["ts_utc"])
    vis = (utc_index(bars["visible_at"]) if "visible_at" in bars.columns else ts + pd.Timedelta(minutes=1))
    m = pd.DataFrame({"hour": ts.floor("h"), "ns": epoch_ns(ts), "vis": epoch_ns(vis)})
    for c in ("open", "high", "low", "close"):
        m[c] = (bars[f"bid_{c}"].to_numpy(dtype=float) + bars[f"ask_{c}"].to_numpy(dtype=float)) / 2.0
    m = m.sort_values("ns", kind="stable")
    out = m.groupby("hour", sort=True).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                           close=("close", "last"), vis=("vis", "max")).reset_index()
    return pd.DataFrame({"ts_utc": pd.DatetimeIndex(out["hour"]), "visible_at": pd.to_datetime(out["vis"], utc=True),
                         "open": out["open"], "high": out["high"], "low": out["low"], "close": out["close"]})


def read_bars(store: Any, *, broker: str, account_id: str, symbol: str, cut: pd.Timestamp,
              start: pd.Timestamp | None = None) -> pd.DataFrame:
    """Mid 1h bars visible by `cut`: the release before the hour of the broker's first live minute, the broker's own
    minutes from that hour on (module docstring)."""
    from goldbot.data.account_bars import account_bars_1m
    live = account_bars_1m(store, account_id=account_id, broker=broker, symbol=symbol, start=start, end=cut, as_of=cut)
    release = store.read("bars_1h", source=RELEASE_SOURCE, symbol="XAUUSD", start=start, end=cut)
    parts = []
    first = utc_index(live["ts_utc"])[0].ceil("h") if len(live) else None
    if release is not None and len(release):
        r = mid_1h(release.reset_index(drop=True))
        keep = r["visible_at"] <= cut
        if first is not None:
            keep &= r["ts_utc"] + HOUR <= first
        parts.append(r[keep])
    if first is not None:
        lv = live[utc_index(live["ts_utc"]) >= first].reset_index(drop=True)
        parts.append(mid_1h(lv))
    if not parts:
        return mid_1h(pd.DataFrame())
    out = pd.concat(parts, ignore_index=True)
    return out[out["visible_at"] <= cut].sort_values("ts_utc", kind="stable").reset_index(drop=True)


def inputs_of(bars: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    """H-16's daily inputs of the stub-free days closed by `cut`, from bars visible by it."""
    if len(bars) == 0:
        return pd.DataFrame()
    b = bars[np.asarray((utc_index(bars["visible_at"]) <= cut) & (utc_index(bars["ts_utc"]) < cut))]
    inp = dt.daily_inputs(dt.daily_bars(b.reset_index(drop=True)))
    if len(inp):
        inp = inp[utc_index(inp["close_utc"]) <= cut].reset_index(drop=True)
    return inp


def frozen_fit(inputs: pd.DataFrame, cfg: HarAbstentionSettings) -> dt.HarFit:
    """The one OLS fit on rows whose target day closed in [fit_from, fit_to) (no purge needed: fit_to is fixed)."""
    lo = pd.Timestamp(cfg.fit_from, tz="UTC")
    hi = pd.Timestamp(cfg.fit_to, tz="UTC")
    close = utc_index(inputs["close_utc"]) if len(inputs) else pd.DatetimeIndex([], tz="UTC")
    rows = inputs[np.asarray((close >= lo) & (close < hi))].reset_index(drop=True)
    return dt.fit_har(rows, hi, purge=pd.Timedelta(0), min_rows=cfg.min_fit_rows)


def _check_gaps(inputs: pd.DataFrame, finite: np.ndarray, cfg: HarAbstentionSettings) -> None:
    """HarUnavailable when two consecutive input days of the window are more than `max_gap_days` calendar days apart.
    The window runs from the first of the trailing forecasts, less the HAR(22) lookback, to the last input day: a hole
    there (missing release months, a dead feed) would make w22 and the trailing quantile mix distant regimes."""
    first = int(finite[-cfg.trailing_days])
    days = pd.DatetimeIndex(inputs["day"].iloc[max(0, first - max(dt.HAR_LAGS)):])
    if len(days) < 2:
        return
    gaps = np.diff(days.to_numpy()).astype("timedelta64[D]").astype(int)
    worst = int(np.argmax(gaps))
    if gaps[worst] > cfg.max_gap_days:
        raise HarUnavailable(f"input gap of {gaps[worst]} days ({days[worst].date()} -> {days[worst + 1].date()}) "
                             f"in the trailing window (> {cfg.max_gap_days})")


def forecast(bars: pd.DataFrame, cut: pd.Timestamp, cfg: HarAbstentionSettings, *, now: pd.Timestamp) -> HarRow:
    """The `ok` row for the next trading day after `cut`, or HarUnavailable (module docstring)."""
    day = next_trading_day(cut)
    inputs = inputs_of(bars, cut)
    if len(inputs) == 0:
        raise HarUnavailable("no daily inputs visible at the cut-off")
    last = pd.Timestamp(inputs["day"].iloc[-1]).date()
    if last != _previous_trading_day(day):
        raise HarUnavailable(f"stale input: last stub-free day {last}, forecast day {day}")
    fit = frozen_fit(inputs, cfg)
    if fit.coef is None:
        raise HarUnavailable(f"fit has {fit.n_rows} rows (< {cfg.min_fit_rows})")
    X = np.column_stack([np.ones(len(inputs)), inputs[list(dt.EMIT_REGRESSORS)].to_numpy(dtype=float)])
    hats = np.exp(X @ np.asarray(fit.coef))
    hat = float(hats[-1])
    trailing = hats[:-1][np.isfinite(hats[:-1])][-cfg.trailing_days:]
    if not np.isfinite(hat):
        raise HarUnavailable("no forecast: a regressor of the last input day is missing")
    if len(trailing) < cfg.trailing_days:
        raise HarUnavailable(f"trailing forecasts {len(trailing)} < {cfg.trailing_days}")
    _check_gaps(inputs, np.flatnonzero(np.isfinite(hats[:-1])), cfg)
    q = float(np.quantile(trailing, cfg.quantile))
    return HarRow(for_day=day, status="ok", available_utc=now, inputs_close_utc=cut, hat=hat, q20=q, low=hat <= q,
                  n_trailing=len(trailing), fit_rows=fit.n_rows, fit_to=cfg.fit_to, last_input_day=last)


# ------------------------------------------------------------------------------------------------ the table
def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([pd.Timestamp(ts)]))[0])


def write_row(db: Database, row: HarRow) -> bool:
    """Insert `row` unless its (day, status) exists (the first row is kept); True when written."""
    with db.write() as conn:
        cur = conn.execute(f"INSERT OR IGNORE INTO {TABLE} (for_day, status, available_ns, body) VALUES (?, ?, ?, ?)",
                           (row.for_day.isoformat(), row.status, _ns(row.available_utc), row.model_dump_json()))
        return cur.rowcount == 1


def _one(db: Database, sql: str, args: tuple[Any, ...]) -> HarRow | None:
    with db.connection() as conn:
        got = conn.execute(sql, args).fetchone()
    return load_record_json(HarRow, got[0], source=TABLE) if got else None


def latest_row(db: Database, at: pd.Timestamp) -> HarRow | None:
    """The latest row of any status available at `at`; None before the job's first row."""
    return _one(db, f"SELECT body FROM {TABLE} WHERE available_ns <= ? ORDER BY available_ns DESC, status DESC LIMIT 1",
                (_ns(at),))


def row_for(db: Database, day: date, at: pd.Timestamp) -> HarRow | None:
    """The `ok` row of `day` available at `at`, None when there is none."""
    return _one(db, f"SELECT body FROM {TABLE} WHERE for_day = ? AND status = 'ok' AND available_ns <= ?",
                (day.isoformat(), _ns(at)))


def signal_day(ts: pd.Timestamp) -> date:
    """The opportunity day (rolling at 22:00 UTC) of a decision time."""
    return pd.Timestamp(opportunity_day(pd.DatetimeIndex([pd.Timestamp(ts)]))[0]).date()


# ------------------------------------------------------------------------------------------------ the job
def run_job(ctx: Any, slot: pd.Timestamp, now: pd.Timestamp | None = None) -> dict[str, Any]:
    """Scheduler job `har_days`: the row for the next trading day after the slot's cut-off (module docstring)."""
    cfg: HarAbstentionSettings = ctx.settings.research.har_abstention
    # available from the write, never from the slot: a late catch-up run is not back-dated
    clock = getattr(ctx, "clock", None)
    at = pd.Timestamp(now) if now is not None else (clock() if clock is not None else pd.Timestamp.now(tz="UTC"))
    at = at.tz_localize("UTC") if at.tzinfo is None else at.tz_convert("UTC")
    cut = cut_off(pd.Timestamp(slot))
    db = Database.open(str(ctx.state_dir), "aux")
    if not cfg.enabled:
        row = HarRow(for_day=next_trading_day(cut), status="off", available_utc=at, inputs_close_utc=cut)
        written = write_row(db, row)
        return {"status": "off", "for_day": row.for_day.isoformat(), "written": written}
    acc = next((a for a in ctx.accounts if a.broker == cfg.broker and a.enabled), None)
    if acc is None:
        raise HarUnavailable(f"no enabled {cfg.broker} account for the live gold feed")
    start = pd.Timestamp(cfg.fit_from, tz="UTC") - pd.Timedelta(days=60)      # ATR(20) and HAR(22) warm-up
    bars = read_bars(ctx.store, broker=acc.broker, account_id=acc.account_id, symbol=acc.symbol, cut=cut, start=start)
    row = forecast(bars, cut, cfg, now=at)
    written = write_row(db, row)
    log.info("har_days %s: hat %.4f q20 %.4f low %s (written %s)", row.for_day, row.hat, row.q20, row.low, written)
    return {"status": "ok", "for_day": row.for_day.isoformat(), "low": row.low, "hat": row.hat, "q20": row.q20,
            "n_trailing": row.n_trailing, "fit_rows": row.fit_rows, "written": written}
