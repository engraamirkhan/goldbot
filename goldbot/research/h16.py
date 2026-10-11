"""H-16's evaluation path (E-4 in docs/research/preregistration-2027Q2-draft.md): the opportunity score against the
HAR-only range forecast, read by its pre-registered rule and recorded as exactly ONE registry trial.

Design rule (opportunity-days.md 3.1): "Predicting how much gold will move today is well supported. Predicting which
way it will move is not." H-16 tests range, not returns: no trades, no DSR, no event floor ("counted for honesty
though it tests range, not returns").

Inputs, in this order (nothing is built before the cut):
1. 15m mid bars from the first bar of 2010, cut at `research.holdout_from` (`cut_before_holdout`), with the macro rows
   (GVZ) and the US calendar rows cut at the same instant. "The bars are cut at research.holdout_from (2025-10-01)
   before E-4 builds any feature, so no holdout bar is loaded";
2. `day_type.daily_inputs` on the cut bars: one row per stub-free opportunity day D, its target y_D = ln(W_D / A_D)
   and the HAR regressors through D-1 (`day_type.TARGET_REGRESSORS`);
3. the full score's inputs, each as known at 06:30 UTC on D (DECISION_UTC; "all visible at 06:30 UTC on D"):
   ln(GVZ / YZ20 annualised) with GVZ joined as of `available_utc` (`day_type.gvz_columns`; `gvz_stale` when the latest
   GVZ value_date is over 5 US business days old), the distinct tier-1 releases scheduled on D as known then
   (`day_type.tier1_columns`), ln(prior stub-free daily range / A_D), the NR4 / NR7 / inside-day flags of D-1, and
   |reopen gap| / A_D at the last reopen before 06:30 UTC (`day_type.reopen_gap`).

Models (`oos_log_forecast`): the HAR baseline (intercept + 3 regressors) and the full model (intercept + 10 regressors)
are both fitted by `day_type.fit_har` (its list-of-columns API) at the monthly refit instants
(`day_type.monthly_refits`), on every earlier row whose target day closed at least 1 day before the instant
(expanding, >= MIN_FIT_ROWS rows). The forecast for day D uses the latest fit at or before the close of the previous
stub-free day, exactly as `day_type.har_forecast` emits it (so the baseline here is the published
`opp_har_range_hat`, shifted to its target day).

Evaluation set: stub-free days from the frozen first out-of-sample month ("the first calendar month M whose baseline
fit at M's refit instant has >= 500 usable rows", `first_oos_month`) to the cut, with a finite target and both
forecasts, after dropping the `gvz_stale` days from both models. Each drop is counted; the days without a full
forecast also by the input that was missing (`dropped_no_full_by_reason`, a day can miss several). Fewer than
MIN_EVAL_SHARE (90%) of the frozen evaluable-day count is refused before any statistic is computed; a NaN statistic is
refused too (nothing recorded either way).

Reading rule (hypotheses.md A2, unchanged; READING_RULE): continue if, pooled out of sample, Spearman
rho(full, y) - rho(HAR, y) >= RHO_GAIN (0.05) AND the mean W/A of the full score's top quintile is >= QUINTILE_RATIO
(1.5) x its bottom quintile's; otherwise fail. Reported, not in the rule: the 20-day block-bootstrap 95% interval of
the rho difference and each input's drop-one contribution. Not built here: the London+60 / NY+60 refreshes with the
noise ratio (diagnostics only).

H-16 runs once, against its pre-registration (quant review of E-4, H1/M1/M2). Under the registry lock and before
anything is read, `run_trial` refuses when
* a trial row of FAMILY with the pre-registered config hash already exists (a second look, whatever its link);
* there is not exactly one unused (not run, not withdrawn) pre-registration of FAMILY;
* its config is not `h16_config(first_oos_month, expected_eval_days)` with the run's minimum fit rows (the frozen month
  and the frozen evaluable-day count are in the hashed config, and the run reads them from it);
* its feature version is not the one worked out now. H-16 pins what it reads, `feature_version(["day_type"])` (the
  GVZ series is named in the config), worked out at the freeze and checked at run time: a version change is a refusal,
  never a read followed by a recorded deviation. tests/test_day_type_golden.py ties day_type's outputs to
  `day_type.VERSION`;
* the quarter's budget is spent (`check_budget_reserved`).
It then evaluates and writes ONE `TrialRegistry.record` row (family FAMILY, status "evaluated", results.kind
"range_test") linked to that pre-registration.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Collection, Literal, overload

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features import day_type as dt
from goldbot.research.registry import PREREGISTERED, TrialRegistry, config_hash, is_trial

FAMILY = "h16_opportunity_score"
AGENT_ID = "h16-eval"
HYPOTHESIS = "H-16"
TIMEFRAME = "15m"
DECISION_UTC = pd.Timedelta(hours=6, minutes=30)     # every full-score input is as known at 06:30 UTC on D
RHO_GAIN = 0.05                                      # pre-registered: rho(full) - rho(HAR) >= 0.05
QUINTILE_RATIO = 1.5                                 # pre-registered: top-quintile mean W/A >= 1.5 x bottom's
RULE_TOL = 1e-12                                     # float representation only (0.35 - 0.30 < 0.05 in binary)
BOOT_BLOCK_DAYS = 20                                 # reported, not in the rule
BOOT_RESAMPLES = 1000
BOOT_SEED = 0
MIN_EVAL_SHARE = 0.9                                 # n below 90% of the frozen evaluable-day count: refused
GVZ_SERIES = "GVZCLS"                                # FRED's CBOE gold volatility index (macro series gold_vix)
FEATURE_NAMES = ("day_type",)                        # what H-16 reads; their version is H-16's feature version
KIND = "range_test"
EXTRA_REGRESSORS = ("ln_gvz_rv", "tier1_count", "ln_prev_range_atr", "nr4", "nr7", "inside_day", "gap_atr")
HAR_REGRESSORS = dt.TARGET_REGRESSORS
FULL_REGRESSORS = (*HAR_REGRESSORS, *EXTRA_REGRESSORS)
READING_RULE = ("continue if, pooled out of sample, Spearman rho(full, y) - rho(HAR, y) >= 0.05 AND the mean W/A of "
                "the full score's top quintile >= 1.5 x its bottom quintile's; otherwise fail (the day factor uses HAR "
                "alone; GVZ, events and compression are retired as range inputs)")
Verdict = Literal["continue", "fail"]


def h16_config(first_oos_month: str, expected_eval_days: int, min_rows: int = dt.MIN_FIT_ROWS) -> dict[str, Any]:
    """The configuration H-16's pre-registration and result rows share (same config hash). The frozen first OOS month
    ("YYYY-MM") and the frozen evaluable-day count, both counted from the calendar and data gaps before the freeze, are
    part of it, so they are pre-registered with the rule."""
    return {"hypothesis": HYPOTHESIS, "timeframe": TIMEFRAME, "target": "ln(W_D / A_D), 08:00 London - 16:30 New York",
            "baseline": list(HAR_REGRESSORS), "full": list(FULL_REGRESSORS), "decision_utc": "06:30",
            "refit": dt.REFIT_SCHEDULE, "min_fit_rows": int(min_rows), "purge_days": int(dt.FIT_PURGE.days),
            "rule": {"rho_gain": RHO_GAIN, "quintile_ratio": QUINTILE_RATIO, "min_eval_share": MIN_EVAL_SHARE},
            "first_oos_month": str(pd.Period(first_oos_month, "M")), "expected_eval_days": int(expected_eval_days),
            "feature_names": list(FEATURE_NAMES), "gvz_series": GVZ_SERIES, "day_type_version": dt.VERSION}


def h16_feature_version() -> str:
    """H-16's feature version: `feature_version(["day_type"])`, what it reads (registers the opt-in day_type)."""
    from goldbot.features.registry import feature_version
    dt.enable()
    return feature_version(list(FEATURE_NAMES))


# --------------------------------------------------------------------------- holdout cut (before any feature)
def _holdout_ts(holdout_from: date | datetime | pd.Timestamp | str) -> pd.Timestamp:
    t = pd.Timestamp(holdout_from)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


@overload
def cut_before_holdout(frame: pd.DataFrame, holdout_from: date | datetime | pd.Timestamp | str,
                       columns: tuple[str, ...] = ...) -> pd.DataFrame: ...
@overload
def cut_before_holdout(frame: None, holdout_from: date | datetime | pd.Timestamp | str,
                       columns: tuple[str, ...] = ...) -> None: ...
def cut_before_holdout(frame: pd.DataFrame | None, holdout_from: date | datetime | pd.Timestamp | str,
                       columns: tuple[str, ...] = ("ts_utc",)) -> pd.DataFrame | None:
    """The rows whose every column in `columns` (that the frame has) is before `holdout_from` (UTC midnight of a date).
    Bars cut on ts_utc; macro and calendar rows on available_utc (and event_utc): nothing at or after the holdout's
    first instant reaches a feature."""
    if frame is None:
        return None
    at = _holdout_ts(holdout_from)
    keep = np.ones(len(frame), dtype=bool)
    for c in columns:
        if c in frame:
            keep &= np.asarray(utc_index(frame[c]) < at)
    return frame.loc[keep].reset_index(drop=True)


# --------------------------------------------------------------------------- the evaluation frame
def _ln(x: np.ndarray) -> np.ndarray:
    v = np.asarray(x, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(v > 0, np.log(np.where(v > 0, v, 1.0)), np.nan)


def _gap_before(bars: pd.DataFrame, days: pd.DatetimeIndex, decision: pd.DatetimeIndex) -> np.ndarray:
    """Per day: |reopen gap| (price units) at the last reopen before that day's decision instant, NaN if none."""
    ts = utc_index(bars["ts_utc"])
    f = pd.DataFrame({"day": dt.opportunity_day(ts), "ns": epoch_ns(ts), "g": dt.reopen_gap(bars)})
    f = f[np.isfinite(f["g"].to_numpy())]
    dec = pd.Series(epoch_ns(decision).astype(float), index=days)
    f = f[f["ns"].to_numpy(dtype=float) < dec.reindex(pd.DatetimeIndex(f["day"])).to_numpy()]
    return f.groupby("day")["g"].last().reindex(days).to_numpy(dtype=float)


def evaluation_inputs(bars: pd.DataFrame, macro: pd.DataFrame | None, us_calendar: pd.DataFrame | None) -> pd.DataFrame:
    """`day_type.daily_inputs` of `bars` (mid; columns ts_utc, open, high, low, close) plus the full score's inputs for
    each stub-free day D as known at 06:30 UTC on D (module docstring), `decision_utc` and `gvz_stale`."""
    inp = dt.daily_inputs(dt.daily_bars(bars))
    if len(inp) == 0:
        return inp.assign(**{c: pd.Series(dtype=float) for c in (*EXTRA_REGRESSORS, "gvz_stale")})
    days = pd.DatetimeIndex(inp["day"])
    decision = days.tz_localize("UTC") + DECISION_UTC
    a_d = inp["atr"].shift(1).to_numpy(dtype=float)              # A_D: through D-1, visible at D's open
    probe = pd.DataFrame({"ts_utc": decision})
    ratio, stale = dt.gvz_columns(probe, macro, inp["yz_ann"].shift(1).to_numpy(dtype=float))
    out = inp.copy()
    out["decision_utc"] = decision
    out["ln_gvz_rv"] = _ln(ratio)
    out["gvz_stale"] = stale
    out["tier1_count"] = dt.tier1_columns(probe, us_calendar)["opp_tier1_today"].to_numpy(dtype=float)
    out["ln_prev_range_atr"] = _ln(inp["prev_range_atr"].shift(1).to_numpy(dtype=float))
    for c in ("nr4", "nr7", "inside_day"):
        out[c] = inp[c].shift(1).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["gap_atr"] = np.where(a_d > 0, _gap_before(bars, days, decision) / np.where(a_d > 0, a_d, 1.0), np.nan)
    return out


def refit_months(refits: pd.DatetimeIndex) -> pd.PeriodIndex:
    """The month each refit instant opens (the refit is the close of the last weekday before that month)."""
    d = pd.DatetimeIndex(refits).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=dt.ROLLOVER_UTC_H)
    return d.normalize().to_period("M").shift(1)


def first_oos_month(inputs: pd.DataFrame, refits: pd.DatetimeIndex, *, min_rows: int = dt.MIN_FIT_ROWS,
                    purge: pd.Timedelta = dt.FIT_PURGE) -> pd.Period | None:
    """The frozen rule: the first calendar month M whose (baseline) fit at M's refit instant has >= `min_rows` usable
    rows. Counts rows only (calendar and data gaps), never a target value or an outcome."""
    for at, month in zip(refits, refit_months(refits), strict=True):
        if dt.fit_har(inputs, at, regressors=HAR_REGRESSORS, purge=purge, min_rows=min_rows).coef is not None:
            return month
    return None


def oos_log_forecast(inputs: pd.DataFrame, regressors: tuple[str, ...], refits: pd.DatetimeIndex, *,
                     min_rows: int = dt.MIN_FIT_ROWS, purge: pd.Timedelta = dt.FIT_PURGE) -> np.ndarray:
    """Per row D: the log forecast of y_D from the latest fit (`day_type.fit_har(regressors=...)`) at or before the
    close of the previous stub-free row, on D's own regressors (all known by 06:30 UTC on D). NaN before the first
    usable fit or where a regressor is missing."""
    n = len(inputs)
    pred = np.full(n, np.nan)
    if n < 2 or len(refits) == 0:
        return pred
    close = epoch_ns(utc_index(inputs["close_utc"]))
    which = np.full(n, -1)
    which[1:] = np.searchsorted(epoch_ns(refits), close[:-1], side="right") - 1
    X = np.column_stack([np.ones(n), inputs[list(regressors)].to_numpy(dtype=float)])
    for j in np.unique(which[which >= 0]):
        fit = dt.fit_har(inputs, refits[j], regressors=regressors, purge=purge, min_rows=min_rows)
        if fit.coef is not None:
            rows = which == j
            pred[rows] = X[rows] @ np.asarray(fit.coef)
    return pred


def evaluation_frame(bars: pd.DataFrame, macro: pd.DataFrame | None, us_calendar: pd.DataFrame | None, *,
                     min_rows: int = dt.MIN_FIT_ROWS, purge: pd.Timedelta = dt.FIT_PURGE) -> pd.DataFrame:
    """`evaluation_inputs` plus `har_log` and `full_log` (the two models' out-of-sample log forecasts). Bars must
    already be cut at the holdout (`evaluate_h16` does it)."""
    inp = evaluation_inputs(bars, macro, us_calendar)
    return _with_forecasts(inp, dt.monthly_refits(inp), min_rows=min_rows, purge=purge)


def _with_forecasts(inp: pd.DataFrame, refits: pd.DatetimeIndex, *, min_rows: int,
                    purge: pd.Timedelta = dt.FIT_PURGE) -> pd.DataFrame:
    inp["har_log"] = oos_log_forecast(inp, HAR_REGRESSORS, refits, min_rows=min_rows, purge=purge)
    inp["full_log"] = oos_log_forecast(inp, FULL_REGRESSORS, refits, min_rows=min_rows, purge=purge)
    return inp


# --------------------------------------------------------------------------- statistics and the reading rule
def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation (average ranks on ties); NaN with fewer than 3 rows or a constant input."""
    if len(a) < 3:
        return float("nan")
    ra = pd.Series(np.asarray(a, dtype=float)).rank().to_numpy()
    rb = pd.Series(np.asarray(b, dtype=float)).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def quintile_means(score: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    """(mean W/A of the score's top quintile, of its bottom quintile); quintiles by rank (ties in row order)."""
    if len(score) < 5:
        return float("nan"), float("nan")
    q = pd.qcut(pd.Series(np.asarray(score, dtype=float)).rank(method="first"), 5, labels=False).to_numpy()
    w = np.asarray(w, dtype=float)
    return float(w[q == 4].mean()), float(w[q == 0].mean())


def read_rule(rho_full: float, rho_har: float, top_mean: float, bottom_mean: float) -> Verdict:
    """H-16's pre-registered reading: continue iff rho_full - rho_har >= RHO_GAIN AND top_mean >= QUINTILE_RATIO x
    bottom_mean (both inclusive; RULE_TOL absorbs float representation only); anything NaN reads fail."""
    gain = rho_full - rho_har
    rho_ok = bool(np.isfinite(gain) and gain >= RHO_GAIN - RULE_TOL)
    q_ok = bool(np.isfinite(top_mean) and np.isfinite(bottom_mean) and bottom_mean > 0
                and top_mean >= QUINTILE_RATIO * bottom_mean - RULE_TOL)
    return "continue" if rho_ok and q_ok else "fail"


def block_bootstrap_gain(full: np.ndarray, har: np.ndarray, y: np.ndarray, *, block: int = BOOT_BLOCK_DAYS,
                         n_boot: int = BOOT_RESAMPLES, seed: int = BOOT_SEED) -> tuple[float, float] | None:
    """The moving-block bootstrap 95% interval of rho(full, y) - rho(HAR, y) (blocks of `block` consecutive evaluation
    days). Reported only; None with fewer than two blocks."""
    n = len(y)
    if n < 2 * block or n_boot <= 0:
        return None
    rng = np.random.default_rng(seed)
    k = int(np.ceil(n / block))
    gains = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=k)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        gains[i] = spearman(full[idx], y[idx]) - spearman(har[idx], y[idx])
    lo, hi = np.nanpercentile(gains, [2.5, 97.5])
    return float(lo), float(hi)


class H16Result(FrozenRecord):
    """H-16's out-of-sample reading and its counts (what the trial row records under `results`)."""
    kind: str = KIND                 # a range test, not a trade-producing trial (no DSR, no gates)
    hypothesis: str = HYPOTHESIS
    verdict: Verdict
    reading_rule: str = READING_RULE
    rho_full: float
    rho_har: float
    rho_gain: float
    top_quintile_mean_w: float
    bottom_quintile_mean_w: float
    quintile_ratio: float
    n: int
    first_oos_month: str
    n_days_oos: int                  # stub-free days from the first OOS month with a finite target
    dropped_gvz_stale: int           # dropped from both models: GVZ value_date over 5 US business days old
    dropped_no_har: int              # no baseline forecast (missing HAR regressors or fit)
    dropped_no_full: int             # baseline present, full forecast missing (an input unknown, e.g. no GVZ yet)
    dropped_no_full_by_reason: dict[str, int] = {}   # per missing input ("no_fit": inputs present, no full fit yet)
    expected_eval_days: int | None = None            # the frozen count n was checked against
    bootstrap_gain_ci95: tuple[float, float] | None = None
    drop_one: dict[str, float] = {}  # rho(full) - rho(full without the input), same evaluation rows
    bars_from: str
    bars_to: str
    holdout_from: str
    min_fit_rows: int
    feature_version: str


def evaluate_h16(bars: pd.DataFrame, macro: pd.DataFrame | None, us_calendar: pd.DataFrame | None, *,
                 holdout_from: date | datetime | pd.Timestamp | str, frozen_first_oos_month: str | None = None,
                 expected_eval_days: int | None = None, min_rows: int = dt.MIN_FIT_ROWS,
                 feature_version: str | None = None,
                 n_boot: int = BOOT_RESAMPLES) -> tuple[H16Result, pd.DataFrame]:
    """Cut every input at `holdout_from`, build the evaluation frame, apply the frozen first-OOS-month rule (ValueError
    when `frozen_first_oos_month` "YYYY-MM" is given and the rule says otherwise, before any forecast is read), refuse
    an evaluation set under MIN_EVAL_SHARE of `expected_eval_days` before any statistic, read H-16's rule and refuse a
    NaN statistic. Returns the result and the evaluation frame (column `in_eval` marks the evaluation set)."""
    bars = cut_before_holdout(bars, holdout_from)
    macro = cut_before_holdout(macro, holdout_from, ("available_utc",))
    us_calendar = cut_before_holdout(us_calendar, holdout_from, ("available_utc", "event_utc"))
    if len(bars) == 0:
        raise ValueError(f"no bars before the holdout ({_holdout_ts(holdout_from):%Y-%m-%d})")
    inp = evaluation_inputs(bars, macro, us_calendar)
    refits = dt.monthly_refits(inp)
    month = first_oos_month(inp, refits, min_rows=min_rows)
    if month is None:
        raise ValueError(f"no month has a fit with >= {min_rows} usable rows before the holdout: no out-of-sample set")
    if frozen_first_oos_month is not None and str(month) != str(pd.Period(frozen_first_oos_month, "M")):
        raise ValueError(f"the frozen first out-of-sample month is {frozen_first_oos_month} but the rule on these bars "
                         f"gives {month} (data changed?); nothing was read or recorded")
    inp = _with_forecasts(inp, refits, min_rows=min_rows)
    oos = np.asarray(pd.DatetimeIndex(inp["day"]).to_period("M") >= month) & np.isfinite(inp["y"].to_numpy(dtype=float))
    stale = oos & (inp["gvz_stale"].to_numpy(dtype=float) == 1.0)
    no_har = oos & ~stale & ~np.isfinite(inp["har_log"].to_numpy())
    no_full = oos & ~stale & ~no_har & ~np.isfinite(inp["full_log"].to_numpy())
    use = oos & ~stale & ~no_har & ~no_full
    inp["in_eval"] = use
    n = int(use.sum())
    if expected_eval_days is not None and n < MIN_EVAL_SHARE * expected_eval_days:
        raise ValueError(f"{n:,} evaluation days, under {MIN_EVAL_SHARE:.0%} of the frozen {expected_eval_days:,} "
                         f"(dropped: {int(stale.sum())} GVZ-stale, {int(no_har.sum())} without HAR, "
                         f"{int(no_full.sum())} without the full forecast); no statistic was computed or recorded")
    missing = {c: int((no_full & ~np.isfinite(inp[c].to_numpy(dtype=float))).sum()) for c in EXTRA_REGRESSORS}
    inputs_ok = np.isfinite(inp[list(FULL_REGRESSORS)].to_numpy(dtype=float)).all(axis=1)
    missing["no_fit"] = int((no_full & inputs_ok).sum())
    y = inp.loc[use, "y"].to_numpy(dtype=float)
    full, har = inp.loc[use, "full_log"].to_numpy(), inp.loc[use, "har_log"].to_numpy()
    rho_full, rho_har = spearman(full, y), spearman(har, y)
    top, bottom = quintile_means(full, np.exp(y))
    stats = {"rho_full": rho_full, "rho_har": rho_har, "top_quintile_mean_w": top, "bottom_quintile_mean_w": bottom}
    if not all(np.isfinite(v) for v in stats.values()):
        raise ValueError(f"a NaN statistic on {n:,} evaluation days ({stats}): no reading; nothing was recorded")
    drop_one = {}
    for c in EXTRA_REGRESSORS:
        regs = tuple(r for r in FULL_REGRESSORS if r != c)
        pred = oos_log_forecast(inp, regs, refits, min_rows=min_rows)[use]
        drop_one[c] = float(rho_full - spearman(pred, y))
    ts = utc_index(bars["ts_utc"])
    result = H16Result(
        verdict=read_rule(rho_full, rho_har, top, bottom), rho_full=rho_full, rho_har=rho_har,
        rho_gain=rho_full - rho_har, top_quintile_mean_w=top, bottom_quintile_mean_w=bottom,
        quintile_ratio=top / bottom if bottom > 0 else float("nan"), n=n, first_oos_month=str(month),
        n_days_oos=int(oos.sum()), dropped_gvz_stale=int(stale.sum()), dropped_no_har=int(no_har.sum()),
        dropped_no_full=int(no_full.sum()), dropped_no_full_by_reason=missing, expected_eval_days=expected_eval_days,
        bootstrap_gain_ci95=block_bootstrap_gain(full, har, y, n_boot=n_boot),
        drop_one=drop_one, bars_from=ts[0].isoformat(), bars_to=ts[-1].isoformat(),
        holdout_from=_holdout_ts(holdout_from).isoformat(), min_fit_rows=int(min_rows),
        feature_version=feature_version or h16_feature_version())
    return result, inp


# --------------------------------------------------------------------------- one trial in the registry
def h16_preregistration(rows: list[dict[str, Any]], min_rows: int = dt.MIN_FIT_ROWS) -> dict[str, Any]:
    """The one pre-registration H-16 may run against, or ValueError (module docstring: never run twice, exactly one
    unused pre-registration, H-16's configuration with `min_rows`)."""
    from goldbot.research.director import is_withdrawn, preregistration_run
    unused = [r for r in rows if r.get("status") == PREREGISTERED and r.get("family") == FAMILY
              and isinstance(r.get("config"), dict) and not is_withdrawn(rows, r)
              and preregistration_run(rows, r) is None]
    hashes = {r.get("config_hash") for r in unused}
    ran = [r for r in rows if is_trial(r) and r.get("family") == FAMILY and r.get("config_hash") in hashes]
    if ran:
        raise ValueError(f"H-16 config {ran[0].get('config_hash')} was already run (trial #{ran[0].get('trial')}): "
                         "H-16 runs once; its result stands")
    if len(unused) != 1:
        raise ValueError(f"H-16 needs exactly one unused pre-registration of family {FAMILY}, found {len(unused)}"
                         + (" (no unused pre-registration: write it with h16_config(...) first)" if not unused else ""))
    pre = unused[0]
    cfg = pre["config"]
    try:
        want = h16_config(str(cfg["first_oos_month"]), int(cfg["expected_eval_days"]), min_rows)
    except (KeyError, TypeError, ValueError):
        want = None
    if cfg != want:
        raise ValueError(f"pre-registration #{pre.get('trial')} holds {cfg}, which is not H-16's configuration "
                         f"(h16_config(first_oos_month, expected_eval_days, min_rows={min_rows})); withdraw it")
    return pre


def run_trial(reg: TrialRegistry, bars: pd.DataFrame, macro: pd.DataFrame | None, us_calendar: pd.DataFrame | None,
              *, holdout_from: date | datetime | pd.Timestamp | str, cap: int, reserved: int, retired: Collection[str],
              rationale: str, min_rows: int = dt.MIN_FIT_ROWS, input_info: dict[str, Any] | None = None,
              now: datetime | None = None, n_boot: int = BOOT_RESAMPLES) -> tuple[dict[str, Any], H16Result]:
    """Run H-16 once and record it as ONE trial (module docstring). Under the registry lock, before anything is read:
    the pre-registration (`h16_preregistration`), its feature version against the one worked out now, the budget
    (TrialBudgetExceeded). Then the evaluation with the frozen month and day count read from the pre-registered config
    (ValueError before any row is written), then one `record` row linked to the pre-registration."""
    from goldbot.research.registry_sync import read_rows
    with reg.locked():
        pre = h16_preregistration(read_rows(reg.path), min_rows)
        config = pre["config"]
        fv = h16_feature_version()
        if pre.get("feature_version") != fv:
            raise ValueError(f"the pre-registered feature version is {pre.get('feature_version')} but day_type now "
                             f"hashes to {fv}: H-16 is refused rather than read on other inputs (withdraw and "
                             "pre-register again before the run, or restore day_type)")
        quarter, preregs = reg.check_budget_reserved([(FAMILY, config)], cap, reserved, now, retired=retired)
        prereg = preregs[0] or pre
        result, _ = evaluate_h16(bars, macro, us_calendar, holdout_from=holdout_from,
                                 frozen_first_oos_month=config["first_oos_month"],
                                 expected_eval_days=config["expected_eval_days"], min_rows=min_rows,
                                 feature_version=fv, n_boot=n_boot)
        results = {**result.model_dump(mode="json"), "inputs": input_info or {}, "config_hash": config_hash(config)}
        row = reg.record(agent_id=AGENT_ID, family=FAMILY, config=config, feature_version=fv,
                         rationale=rationale, results=results, status="evaluated", budget_quarter=quarter,
                         preregistration=prereg)
    return row, result


def report_lines(result: H16Result, trial: int | None = None) -> list[str]:
    """The markdown report: the rule, both rho, the gain, the quintile ratio, n and the counts."""
    ci = result.bootstrap_gain_ci95
    lines = [f"## H-16 opportunity score vs HAR-only range forecast: **{result.verdict.upper()}**", "",
             f"- reading rule (pre-registered): {result.reading_rule}",
             f"- Spearman rho(full, y) {result.rho_full:.4f}, rho(HAR, y) {result.rho_har:.4f}, gain "
             f"{result.rho_gain:+.4f} (needs >= {RHO_GAIN})",
             f"- full score's top-quintile mean W/A {result.top_quintile_mean_w:.4f} vs bottom "
             f"{result.bottom_quintile_mean_w:.4f}: ratio {result.quintile_ratio:.3f} (needs >= {QUINTILE_RATIO})",
             f"- evaluation days n = {result.n:,} from {result.first_oos_month} (frozen first OOS month) to "
             f"{result.bars_to[:10]}; {result.n_days_oos:,} out-of-sample days, dropped: {result.dropped_gvz_stale:,} "
             f"GVZ-stale (from both models), {result.dropped_no_har:,} without a HAR forecast, "
             f"{result.dropped_no_full:,} without a full forecast ("
             + (", ".join(f"{k} {v:,}" for k, v in result.dropped_no_full_by_reason.items() if v) or "none")
             + "; a day can miss several inputs)"
             + (f"; frozen evaluable-day count {result.expected_eval_days:,} (needs >= {MIN_EVAL_SHARE:.0%})"
                if result.expected_eval_days else ""),
             f"- holdout from {result.holdout_from[:10]} cut before any feature was built; bars "
             f"{result.bars_from[:10]} .. {result.bars_to[:10]}; feature version `{result.feature_version}`",
             "", "Reported, not in the rule:",
             "- 20-day block-bootstrap 95% interval of the rho gain: "
             + (f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci else "n/a (too few days)"),
             "- drop-one contribution (rho(full) - rho(full without the input)): "
             + ", ".join(f"{k} {v:+.4f}" for k, v in result.drop_one.items()),
             "- not built: the London+60 / NY+60 refreshes with the noise ratio (H-21 diagnostics)", ""]
    if trial is not None:
        lines.insert(2, f"- registry trial #{trial} (one trial; no trades, so no DSR and no event floor)")
    return lines
