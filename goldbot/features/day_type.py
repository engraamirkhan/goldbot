"""Opportunity-day features (opt-in family `day_type`; BACKLOG 30a, docs/research/opportunity-days.md section 3.1).

Design rule (opportunity-days.md 3.1): "Predicting how much gold will move today is well supported. Predicting which
way it will move is not." Every column here is a range or event input, never a direction: no column reads `side`, a
signed return or anything after the bar. They feed H-16 (range forecast), H-20 (compression), H-21 (noise ratio) and,
once H-16 passes, the day-score allocator input (BACKLOG 30b).

Opportunity day. Day D runs from D-1 22:00 UTC to D 22:00 UTC (ROLLOVER_UTC_H): the rollover falls inside gold's daily
break in both US DST regimes (17:00-18:00 New York is 21:00-22:00 UTC in summer, 22:00-23:00 UTC in winter), so the
Sunday reopen belongs to Monday and there is no Friday stub. A day whose bars span under MIN_DAY_SPAN_H hours (a
holiday or a data gap) is a stub and is left out of the daily inputs, as `time_series_momentum.drop_stub_days`; its
move is not lost (the next day's true range runs from the last stub-free close). A daily value is visible at its
day's nominal close (D 22:00 UTC) and reaches the bars only through `store.asof_join` on that `available_utc`.

Daily inputs on stub-free days k (all from completed days):
* `atr`: A_k, the mean of the last ATR_DAYS true ranges through k (H-16's A_D);
* `w`: range_k / A_{k-1}, the day's range in the ATR(1d) visible at its open; y_k = ln w_k (the HAR target);
* HAR regressors (Corsi 2009): ln of the mean of w over the last 1, 5 and 22 days.

Columns (per bar, unsigned):
* `opp_har_range_hat`: the HAR forecast of the next day's range in ATR(1d) units, exp(b0 + b1 ln w1 + b5 ln w5 + b22
  ln w22). Fitting (H-16, quant ruling Q-2): OLS refitted at each calendar month's first rollover (22:00 UTC on the
  last day of the previous month) on every earlier row whose target day closed at least FIT_PURGE (1 day) before
  the refit instant (expanding window, at least MIN_FIT_ROWS rows, else NaN). A forecast emitted at day k's close
  uses the latest fit whose refit instant is at or before that close. Fold-fit hook: `har_forecast(refit_at=...)`
  takes any schedule (e.g. walk-forward fold ends), and the feature reads ctx["day_type_refit_at"] (a list of UTC
  instants) and ctx["day_type_min_fit_rows"] when given. The coefficients are never a precomputed constant;
* `opp_gvz_rv`: GVZ (macro_gvz, joined on its available_utc: at the London open of D it is D-2's close) / the
  annualised Yang-Zhang volatility of the last YZ_DAYS stub-free days, in % (x sqrt(252) x 100). NaN when the latest
  GVZ was published more than GVZ_STALE ago (H-16 drops such days);
* `opp_prev_range_atr`: the last completed stub-free day's range / its A_k;
* `opp_nr4`, `opp_nr7`: 1.0 when that day's range is the narrowest of the last 4 / 7 stub-free days (ties count),
  else 0.0; `opp_inside_day`: 1.0 when its high <= the previous day's high and its low >= the previous day's low;
* `opp_gap_atr`: |first open of the day - last close before it| / A (the Sunday reopen on Mondays, else the daily
  reopen), from the day's first bar onward;
* `opp_noise_ratio` (H-21): on a bar inside the London (08:00-16:30 Europe/London) or New York (08:30-16:00
  America/New_York) local session (`calendar.LOCAL_SESSIONS`, so DST follows each zone), the high-low range from the
  session's first bar of the local date through this bar, divided by the mean of that range at the same session and
  minutes since the open over the previous NOISE_DAYS days that had that bar (fewer: NaN). The New York session takes
  over from its open (the London range has run for hours by then); outside both sessions NaN;
* `opp_tier1_today`, `opp_min_to_first_tier1`: tier-1 releases scheduled on the bar's opportunity day as known at the
  bar, and minutes from the bar's start to the first of them (negative once it has passed; NO_EVENT_MIN when none).
  Input: ctx["us_calendar"], the point-in-time US calendar rows (event_id, tier, event_utc, status, available_utc;
  release calendar-v1, goldbot/data/us_calendar.py on the BACKLOG 18 branch). Each event counts in the state of its
  latest row with available_utc <= the bar (a late addition is unseen before its stamp; a cancellation from its
  stamp). Both columns are NaN without the calendar and before its first row.

Opt-in: this module is NOT imported by goldbot.features and registers nothing on import. `enable()` registers the
`day_type` feature; scripts/research_pass.py calls it only with `--day-type`. So DEFAULT_FEATURE_NAMES, the frozen
Q1 H-02 universe and their feature versions do not change. Intended for intraday decision bars (15m, 1h, 4h).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import LOCAL_SESSIONS
from goldbot.data.macro import driver_frames
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.registry import FEATURES, FeatureCtx, feature
from goldbot.features.survey import yang_zhang

NAME = "day_type"
FAMILY = "day_type"
VERSION = "1"
COLUMNS = ("opp_har_range_hat", "opp_gvz_rv", "opp_prev_range_atr", "opp_nr4", "opp_nr7", "opp_inside_day",
           "opp_gap_atr", "opp_noise_ratio", "opp_tier1_today", "opp_min_to_first_tier1")
TIER1_COLUMNS = ("opp_tier1_today", "opp_min_to_first_tier1")

ROLLOVER_UTC_H = 22
MIN_DAY_SPAN_H = 12.0
ATR_DAYS = 20
HAR_LAGS = (1, 5, 22)
EMIT_REGRESSORS = ("x1", "x5", "x22")          # visible at day k's close: forecast for the next day
TARGET_REGRESSORS = ("x1_t", "x5_t", "x22_t")  # the same, one day earlier: the regressors of target y_k
MIN_FIT_ROWS = 500
FIT_PURGE = pd.Timedelta(days=1)
YZ_DAYS = 20
TRADING_DAYS = 252
GVZ_STALE = pd.Timedelta(days=7)
NOISE_DAYS = 14
NO_EVENT_MIN = 1440.0
LONDON, NEWYORK = 1, 2
_SESSION_CODES = ((NEWYORK, LOCAL_SESSIONS["newyork"]), (LONDON, LOCAL_SESSIONS["london"]))   # NY takes over


# --------------------------------------------------------------------------- opportunity days
def opportunity_day(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Label (naive date) of the opportunity day holding each UTC instant: [D-1 22:00, D 22:00) UTC is day D."""
    idx = pd.DatetimeIndex(ts).tz_convert("UTC")
    return pd.DatetimeIndex((idx + pd.Timedelta(hours=24 - ROLLOVER_UTC_H)).normalize().tz_localize(None))


def day_close_utc(day: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The nominal close of opportunity day `day` (D 22:00 UTC): when its daily values become visible."""
    return pd.DatetimeIndex(day).tz_localize("UTC") + pd.Timedelta(hours=ROLLOVER_UTC_H)


def daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    """One row per opportunity day with bars: day, open/high/low/close, first_ts, close_utc, stub (spans under
    MIN_DAY_SPAN_H hours from the first bar's start to the last bar's end)."""
    ts = utc_index(df["ts_utc"])
    if len(ts) == 0:
        return pd.DataFrame(columns=["day", "open", "high", "low", "close", "first_ts", "close_utc", "stub", "ts_utc"])
    ns = epoch_ns(ts)
    steps = np.diff(ns)
    bar_ns = int(steps[steps > 0].min()) if (steps > 0).any() else 0
    g = pd.DataFrame({"day": opportunity_day(ts), "open": df["open"].to_numpy(dtype=float),
                      "high": df["high"].to_numpy(dtype=float), "low": df["low"].to_numpy(dtype=float),
                      "close": df["close"].to_numpy(dtype=float), "ns": ns}).groupby("day", sort=True)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                first_ns=("ns", "min"), last_ns=("ns", "max")).reset_index()
    span_h = (out["last_ns"] - out["first_ns"] + bar_ns) / 3.6e12
    out["first_ts"] = pd.to_datetime(out["first_ns"].to_numpy(), unit="ns", utc=True)
    out["ts_utc"] = out["first_ts"]
    out["close_utc"] = day_close_utc(pd.DatetimeIndex(out["day"]))
    out["stub"] = span_h.to_numpy() < MIN_DAY_SPAN_H
    return out.drop(columns=["first_ns", "last_ns"])


def _ln(x: pd.Series) -> pd.Series:
    v = x.to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return pd.Series(np.where(v > 0, np.log(np.where(v > 0, v, 1.0)), np.nan), index=x.index)


def daily_inputs(daily: pd.DataFrame) -> pd.DataFrame:
    """The stub-free days with their daily inputs (module docstring), each known at its `close_utc`."""
    s = daily[~daily["stub"].astype(bool)].reset_index(drop=True)
    h, lo, c = s["high"].astype(float), s["low"].astype(float), s["close"].astype(float)
    pc = c.shift(1)
    tr = pd.Series(np.fmax(np.fmax(h - lo, (h - pc).abs()), (lo - pc).abs()), index=s.index)
    rng = h - lo
    out = s.assign(range=rng, tr=tr)
    out["atr"] = tr.rolling(ATR_DAYS, min_periods=ATR_DAYS).mean()
    out["w"] = rng / out["atr"].shift(1)
    out["y"] = _ln(out["w"])
    for lag, col in zip(HAR_LAGS, EMIT_REGRESSORS, strict=True):
        out[col] = _ln(out["w"].rolling(lag, min_periods=lag).mean())
    for e, t in zip(EMIT_REGRESSORS, TARGET_REGRESSORS, strict=True):
        out[t] = out[e].shift(1)
    out["prev_range_atr"] = rng / out["atr"]
    for n in (4, 7):
        narrowest = rng.rolling(n, min_periods=n).min()
        out[f"nr{n}"] = np.where(narrowest.notna(), (rng <= narrowest).astype(float), np.nan)
    inside = (h <= h.shift(1)) & (lo >= lo.shift(1))
    out["inside_day"] = np.where(h.shift(1).notna(), inside.astype(float), np.nan)
    out["yz_ann"] = (yang_zhang(s, YZ_DAYS) * np.sqrt(TRADING_DAYS) * 100.0) if len(s) else np.array([], dtype=float)
    return out


# --------------------------------------------------------------------------- HAR fit (fold-fit hook)
class HarFit(FrozenRecord):
    """One OLS fit of the HAR range model: coefficients (intercept, b1, b5, b22) or None with too few rows."""
    refit_at: UtcTimestamp
    coef: tuple[float, ...] | None
    n_rows: int
    last_target_close: UtcTimestamp | None = None


def fit_har(inputs: pd.DataFrame, refit_at: pd.Timestamp, *, purge: pd.Timedelta = FIT_PURGE,
            min_rows: int = MIN_FIT_ROWS) -> HarFit:
    """OLS of y on the target regressors over the rows whose target day closed at or before `refit_at` - `purge`
    (nothing later is read, whatever the frame holds)."""
    at = pd.Timestamp(refit_at)
    close = utc_index(inputs["close_utc"]) if len(inputs) else pd.DatetimeIndex([], tz="UTC")
    cols = ["y", *TARGET_REGRESSORS]
    rows = inputs.loc[np.asarray(close <= at - purge), cols]
    rows = rows[np.isfinite(rows.to_numpy(dtype=float)).all(axis=1)]
    if len(rows) < max(min_rows, len(cols)):
        return HarFit(refit_at=at, coef=None, n_rows=len(rows))
    X = np.column_stack([np.ones(len(rows)), rows[list(TARGET_REGRESSORS)].to_numpy(dtype=float)])
    coef, *_ = np.linalg.lstsq(X, rows["y"].to_numpy(dtype=float), rcond=None)
    return HarFit(refit_at=at, coef=tuple(float(b) for b in coef), n_rows=len(rows),
                  last_target_close=pd.Timestamp(inputs.loc[rows.index[-1], "close_utc"]))


def monthly_refits(inputs: pd.DataFrame) -> pd.DatetimeIndex:
    """Each calendar month's first rollover (22:00 UTC on the previous month's last day) up to the last close."""
    if len(inputs) == 0:
        return pd.DatetimeIndex([], tz="UTC")
    close = utc_index(inputs["close_utc"])
    first = (close.min() + pd.Timedelta(hours=24 - ROLLOVER_UTC_H)).tz_localize(None).to_period("M").to_timestamp()
    months = pd.date_range(first, close.max().tz_localize(None) + pd.Timedelta(days=1), freq="MS")
    return pd.DatetimeIndex(months.tz_localize("UTC") - pd.Timedelta(hours=24 - ROLLOVER_UTC_H))


def har_forecast(inputs: pd.DataFrame, refit_at: pd.DatetimeIndex | list | None = None, *,
                 purge: pd.Timedelta = FIT_PURGE, min_rows: int = MIN_FIT_ROWS) -> np.ndarray:
    """The forecast emitted at each row's close (range of the next day in ATR(1d) units), from the latest fit in
    `refit_at` (default: monthly_refits) at or before that close; NaN before the first usable fit."""
    out = np.full(len(inputs), np.nan)
    if len(inputs) == 0:
        return out
    refits = monthly_refits(inputs) if refit_at is None else pd.DatetimeIndex(pd.to_datetime(list(refit_at), utc=True))
    refits = refits.sort_values()
    if len(refits) == 0:
        return out
    which = np.searchsorted(epoch_ns(refits), epoch_ns(utc_index(inputs["close_utc"])), side="right") - 1
    X = np.column_stack([np.ones(len(inputs)), inputs[list(EMIT_REGRESSORS)].to_numpy(dtype=float)])
    for j in np.unique(which[which >= 0]):
        fit = fit_har(inputs, refits[j], purge=purge, min_rows=min_rows)
        if fit.coef is None:
            continue
        rows = which == j
        out[rows] = np.exp(X[rows] @ np.asarray(fit.coef))
    return out


# --------------------------------------------------------------------------- noise ratio
def noise_components(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per bar: (range since the active local session's first bar of the local date, NaN outside the sessions; the
    session code LONDON/NEWYORK, 0 outside; minutes since the session open, NaN outside)."""
    ts = utc_index(df["ts_utc"])
    n = len(ts)
    session, since, day = np.zeros(n, dtype=np.int64), np.full(n, np.nan), np.zeros(n, dtype=np.int64)
    for code, sess in reversed(_SESSION_CODES):          # London first, New York overrides where both are open
        m, d = sess.clock(ts)
        inside = ~np.isnan(m)
        session[inside], since[inside], day[inside] = code, m[inside], d[inside]
    rng = np.full(n, np.nan)
    on = session > 0
    if on.any():
        key = session[on] * 100_000_000 + day[on]          # one run per (session, local date)
        g = pd.DataFrame({"k": key, "h": df["high"].to_numpy(dtype=float)[on],
                          "l": df["low"].to_numpy(dtype=float)[on]}).groupby("k", sort=False)
        rng[on] = (g["h"].cummax() - g["l"].cummin()).to_numpy()
    return rng, session, since


def noise_ratio(df: pd.DataFrame) -> np.ndarray:
    rng, session, since = noise_components(df)
    out = np.full(len(df), np.nan)
    on = session > 0
    if not on.any():
        return out
    f = pd.DataFrame({"r": rng[on], "k": session[on] * 10_000 + since[on].astype(np.int64)})
    base = f.groupby("k", sort=False)["r"].transform(
        lambda x: x.shift(1).rolling(NOISE_DAYS, min_periods=NOISE_DAYS).mean())
    b = base.to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[on] = np.where(b > 0, f["r"].to_numpy() / np.where(b > 0, b, 1.0), np.nan)
    return out


# --------------------------------------------------------------------------- tier-1 calendar (optional input)
def tier1_columns(df: pd.DataFrame, cal: pd.DataFrame | None) -> pd.DataFrame:
    """`opp_tier1_today` and `opp_min_to_first_tier1` from a point-in-time calendar frame (module docstring); NaN
    columns when `cal` is None or empty."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in TIER1_COLUMNS}, index=df.index)
    if cal is None or len(cal) == 0 or len(df) == 0:
        return out
    ts = utc_index(df["ts_utc"])
    t = epoch_ns(ts)
    avail_all = pd.DatetimeIndex(pd.to_datetime(cal["available_utc"], utc=True))
    covered = t >= epoch_ns(avail_all).min()
    count = np.where(covered, 0.0, np.nan)
    minutes = np.where(covered, NO_EVENT_MIN, np.nan)
    c1 = cal[cal["tier"].astype(int) == 1]
    if len(c1):
        ev = pd.DatetimeIndex(pd.to_datetime(c1["event_utc"], utc=True))
        c1 = c1.assign(_ev=epoch_ns(ev), _av=epoch_ns(pd.DatetimeIndex(pd.to_datetime(c1["available_utc"], utc=True))),
                       _day=opportunity_day(ev), _sched=(c1["status"].astype(str) == "scheduled").to_numpy())
        bar_day = opportunity_day(ts)
        for d, rows in c1.groupby("_day", sort=False):
            idx = np.flatnonzero(np.asarray(bar_day == d) & covered)
            if len(idx) == 0:
                continue
            tb = t[idx]
            lo_ns = epoch_ns(day_close_utc(pd.DatetimeIndex([d])) - pd.Timedelta(days=1))[0]
            first = np.full(len(idx), np.inf)
            n = np.zeros(len(idx))
            ids = c1[c1["event_id"].isin(rows["event_id"].unique())]
            for _, hist in ids.sort_values("_av", kind="stable").groupby("event_id", sort=False):
                pos = np.searchsorted(hist["_av"].to_numpy(), tb, side="right") - 1      # latest known row
                known = pos >= 0
                p = np.where(known, pos, 0)
                e = hist["_ev"].to_numpy()[p]
                live = known & hist["_sched"].to_numpy()[p] & (e >= lo_ns) & (e < lo_ns + 86_400 * 10**9)
                n += live
                first = np.where(live, np.minimum(first, e), first)
            count[idx] = n
            minutes[idx] = np.where(np.isfinite(first), (first - tb) / 6e10, NO_EVENT_MIN)
    out["opp_tier1_today"], out["opp_min_to_first_tier1"] = count, minutes
    return out


# --------------------------------------------------------------------------- the feature
def _join(df: pd.DataFrame, frame: pd.DataFrame, cols: list[str], tolerance: pd.Timedelta | None = None) -> pd.DataFrame:
    bars = pd.DataFrame({"ts_utc": utc_index(df["ts_utc"]).as_unit("ns"), "_row": np.arange(len(df))})
    j = asof_join(bars, frame[["available_utc", *cols]], tolerance=tolerance).sort_values("_row")
    return j[cols].reset_index(drop=True)


def f_day_type(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """The ten day_type columns as known at each bar (module docstring); NaN where an input or history is missing."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in COLUMNS}, index=df.index)
    if len(df) == 0:
        return out
    daily = daily_bars(df)
    inp = daily_inputs(daily)
    if len(inp):
        refit_at = ctx.get("day_type_refit_at")
        hat = har_forecast(inp, refit_at, min_rows=int(ctx.get("day_type_min_fit_rows", MIN_FIT_ROWS)))
        emit = pd.DataFrame({"available_utc": utc_index(inp["close_utc"]), "har": hat, "atr": inp["atr"],
                             "prev": inp["prev_range_atr"], "nr4": inp["nr4"], "nr7": inp["nr7"],
                             "inside": inp["inside_day"], "yz": inp["yz_ann"]})
        j = _join(df, emit, ["har", "atr", "prev", "nr4", "nr7", "inside", "yz"])
        out["opp_har_range_hat"] = j["har"].to_numpy()
        out["opp_prev_range_atr"] = j["prev"].to_numpy()
        out["opp_nr4"], out["opp_nr7"] = j["nr4"].to_numpy(), j["nr7"].to_numpy()
        out["opp_inside_day"] = j["inside"].to_numpy()
        atr_now = j["atr"].to_numpy(dtype=float)
        gap = pd.DataFrame({"available_utc": utc_index(daily["first_ts"]),
                            "gap": (daily["open"].astype(float) - daily["close"].astype(float).shift(1)).abs()})
        g = _join(df, gap, ["gap"])["gap"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            out["opp_gap_atr"] = np.where(atr_now > 0, g / np.where(atr_now > 0, atr_now, 1.0), np.nan)
        macro = ctx.get("macro")
        if macro is not None and len(macro):
            gvz = driver_frames(macro)["macro_gvz"]
            if len(gvz):
                gv = _join(df, gvz, ["macro_gvz"], tolerance=GVZ_STALE)["macro_gvz"].to_numpy(dtype=float)
                yz = j["yz"].to_numpy(dtype=float)
                with np.errstate(divide="ignore", invalid="ignore"):
                    out["opp_gvz_rv"] = np.where(yz > 0, gv / np.where(yz > 0, yz, 1.0), np.nan)
    out["opp_noise_ratio"] = noise_ratio(df)
    tier = tier1_columns(df, ctx.get("us_calendar"))
    for c in TIER1_COLUMNS:
        out[c] = tier[c].to_numpy()
    return out


def enable() -> str:
    """Register the `day_type` feature (idempotent) and return its name, for an explicit feature list."""
    if NAME not in FEATURES:
        feature(NAME, FAMILY, version=VERSION)(f_day_type)
    return NAME
