"""Time-of-day periodicity (opt-in family `tod_periodicity`; BACKLOG 89, N1 in docs/research/candidates/next-2026-10-18.md,
signed amendment under O-4 in docs/research/preregistration-2027Q1.md).

Design rule (amendment): "Slots are 30-minute UTC bins; a 15m bar maps to the slot containing its open (both 15m bars
of a slot map to it). Each column uses only the same slot on earlier days, so `available_utc` = the prior day's slot
close; joined by `asof_join`; `lookahead_check` must report 0 columns." And: "`tod_periodicity` is an opt-in family,
never in `DEFAULT_FEATURE_NAMES` ... because `features.registry.feature_version` hashes the family list."

Slot. The UTC day is cut into 48 bins of SLOT_MIN minutes. A bar belongs to the bin containing its open, so on 15m bars
both bars of a bin map to it. Bins are UTC, not local, so a DST switch does not move a bin; it moves what the bin means
in New York or London time, which the model can read from the session columns. A slot observation on day d is
`log(close of the slot's last bar / open of its first bar)`. It exists only for a complete slot (bar count = SLOT_MIN
/ bar minutes; a slot missing a bar is left out of the history rather than averaged on half its bars) and only for bars
no longer than a slot (longer bars give NaN columns).

Point in time. A slot observation is known at its slot's close, `available_utc` = slot start + SLOT_MIN. Per time of
day the observations form one table (rows in day order, the statistics computed through that row, so each row uses
that day and earlier days only), and the bars of that time of day read it through `store.asof_join` on `available_utc`.
Every bar of a slot opens before the slot closes, so it sees at most the previous day's observation: a slot never
uses its own day's return. A Monday slot's latest observation is Friday's; weekends carry no bars and no rows. A
bar looks back at most MAX_STALE (5 days, a long weekend plus a holiday) for the latest observation, else NaN.

Columns (SIGNED: a prior positive return favours a long, so a side-aligned model sees side x (value - neutral)):
* `slot_mean_ret_5d`, `slot_mean_ret_10d`, `slot_mean_ret_20d`: mean of the last 5/10/20 observations of the slot
  (NaN with fewer; neutral 0);
* `slot_hit_rate_20d`: share of the last 20 observations above zero (neutral 0.5);
* `slot_t_20d`: mean / (sample sd / sqrt(20)) over the last 20 (NaN when the sd is 0; neutral 0);
* `slot_ret_over_atr`: the latest observation divided by ATR(14)/close at the slot's last bar (a return in ATR units;
  neutral 0). The ATR is the bar-level Wilder ATR of the same frame, known at that bar's close.
No feature-to-label statistic is computed anywhere in this module.

Opt-in: this module is NOT imported by goldbot.features and registers nothing on import; `enable()` registers the
feature, and `pipeline.OPT_IN_FEATURES` keeps it out of `DEFAULT_FEATURE_NAMES` once enabled, so the default feature
version `f-079deacc61` and every hashed row are unchanged. Its own family makes it its own group under O-4's `family`
group map (not inside `activity`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns
from goldbot.features.columns import Columns
from goldbot.features.registry import FEATURES, FeatureCtx, feature
from goldbot.features.technical import atr

NAME = FAMILY = "tod_periodicity"
VERSION = "1"       # the open choices are pinned in preregistration-2027Q1.md (O-4, N1 amendment, "Pinned 2026-10-18"); change one, bump this
SLOT_MIN = 30
WINDOWS = (5, 10, 20)
LONG = 20                                   # window of the hit rate and the t statistic
ATR_N = 14
MAX_STALE = pd.Timedelta(days=5)
COLUMNS = (*(f"slot_mean_ret_{n}d" for n in WINDOWS), "slot_hit_rate_20d", "slot_t_20d", "slot_ret_over_atr")
SIGNED = {r"slot_mean_ret_(5|10|20)d": 0.0, "slot_hit_rate_20d": 0.5, "slot_t_20d": 0.0, "slot_ret_over_atr": 0.0}
_SLOT_NS = SLOT_MIN * 60 * 10**9
_DAY_NS = 86_400 * 10**9


def slot_start(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The UTC start of the SLOT_MIN-minute slot containing each timestamp."""
    ns = epoch_ns(pd.DatetimeIndex(ts).tz_convert("UTC"))
    return pd.DatetimeIndex(pd.to_datetime(ns // _SLOT_NS * _SLOT_NS, unit="ns", utc=True))


def slot_of_day(ts: pd.DatetimeIndex) -> np.ndarray:
    """The slot number within the UTC day (0 .. 47 for 30-minute slots) of each timestamp."""
    ns = epoch_ns(pd.DatetimeIndex(ts).tz_convert("UTC"))
    return (ns % _DAY_NS) // _SLOT_NS


def _observations(df: pd.DataFrame, idx: pd.DatetimeIndex, per_slot: int) -> pd.DataFrame:
    """One row per complete slot: slot_ns (start), ret (log open-to-close), atr_pct (ATR(14)/close at the last bar)."""
    slot = epoch_ns(idx) // _SLOT_NS
    first = np.flatnonzero(np.r_[True, slot[1:] != slot[:-1]])
    last = np.r_[first[1:], len(slot)] - 1
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.log(c[last] / o[first])
        atr_pct = atr(df).to_numpy(float)[last] / c[last]
    obs = pd.DataFrame({"slot_ns": slot[first] * _SLOT_NS, "ret": ret, "atr_pct": atr_pct})
    keep = (last - first + 1 == per_slot) & np.isfinite(ret)
    return obs[keep].reset_index(drop=True)


def _table(g: pd.DataFrame) -> pd.DataFrame:
    """The statistics of one time of day's observations, each row through its own day, known at its slot close."""
    r = g["ret"]
    t = pd.DataFrame({"available_utc": pd.to_datetime(g["slot_ns"] + _SLOT_NS, unit="ns", utc=True)})
    for n in WINDOWS:
        t[f"slot_mean_ret_{n}d"] = r.rolling(n).mean()
    t["slot_hit_rate_20d"] = (r > 0).astype(float).where(r.notna()).rolling(LONG).mean()
    sd = r.rolling(LONG).std()
    t["slot_t_20d"] = (r.rolling(LONG).mean() / (sd / np.sqrt(LONG))).where(sd > 0)
    t["slot_ret_over_atr"] = (r / g["atr_pct"]).where(g["atr_pct"] > 0)
    return t


def f_tod_periodicity(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """The six slot-periodicity columns (module docstring). `df`: mid bars in time order, columns ts_utc, open, high,
    low, close."""
    out = {c: np.full(len(df), np.nan) for c in COLUMNS}
    if len(df) < 3:
        return Columns(df.index, out).frame()
    idx = pd.DatetimeIndex(df["ts_utc"]).tz_convert("UTC")
    step = float(np.median(np.diff(epoch_ns(idx)))) / 6e10
    per_slot = SLOT_MIN / step if step > 0 else 0.0
    if per_slot < 1 or abs(per_slot - round(per_slot)) > 1e-9:          # bars longer than a slot, or not nesting in one
        return Columns(df.index, out).frame()
    obs = _observations(df, idx, int(round(per_slot)))
    tod_obs = (obs["slot_ns"].to_numpy() % _DAY_NS) // _SLOT_NS
    tod_bar = slot_of_day(idx)
    for t in np.unique(tod_obs):
        pos = np.flatnonzero(tod_bar == t)
        if not len(pos):
            continue
        table = _table(obs[tod_obs == t].reset_index(drop=True))
        joined = asof_join(pd.DataFrame({"ts_utc": idx[pos]}), table, tolerance=MAX_STALE)
        for c in COLUMNS:
            out[c][pos] = joined[c].to_numpy(float)
    return Columns(df.index, out).frame()


def enable() -> str:
    """Register the `tod_periodicity` feature (idempotent) and return its name, for an explicit feature list."""
    if NAME not in FEATURES:
        feature(NAME, FAMILY, version=VERSION, signed=SIGNED)(f_tod_periodicity)
    return NAME
