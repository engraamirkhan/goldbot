"""Positioning and flow features (opt-in): COT managed-money pressure and GLD holdings flow, point in time.

Inputs: ctx["positioning"], the long table of release `positioning-v1` (goldbot/data/positioning.py). Each value is
computed on its series' first releases (a later revision never changes what an earlier bar saw) and reaches the bars
only through `store.asof_join` on available_utc.

Columns:
* `pos_cot_mm_net_pct_oi`: managed-money net (long - short) as % of open interest, per weekly report;
* `pos_cot_mm_net_pct_oi_z52`: its 52-report z-score (at least 40 reports);
* `pos_cot_mm_net_pct_oi_chg4`: its 4-report change, in percentage points;
* `pos_gld_tonnes_chg5_pct`, `pos_gld_tonnes_chg20_pct`: GLD tonnes 5- and 20-observation change, in %.

Opt-in: this module is NOT imported by goldbot.features and registers nothing on import. `enable()` registers the
`positioning` feature; scripts/research_pass.py calls it only with `--positioning`. So the default feature set and its
feature version (what the live engine builds) do not change, whatever is installed or loaded. (goldbot/research's
OPT_IN_FEATURES list would be the more uniform home for this; it is outside this change's lane.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.macro import first_release
from goldbot.data.store import asof_join
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME = "positioning"
COT_COLUMNS = ("pos_cot_mm_net_pct_oi", "pos_cot_mm_net_pct_oi_z52", "pos_cot_mm_net_pct_oi_chg4")
GLD_COLUMNS = ("pos_gld_tonnes_chg5_pct", "pos_gld_tonnes_chg20_pct")
COLUMNS = (*COT_COLUMNS, *GLD_COLUMNS)
VERSION = "1"
# directional for a meta-label model: crowded managed-money longs or an ETF outflow mean opposite things for a long and
# a short. The level (% of OI) is a regime and stays unsigned.
SIGNED = {"pos_cot_mm_net_pct_oi_z52": 0.0, "pos_cot_mm_net_pct_oi_chg4": 0.0,
          "pos_gld_tonnes_chg5_pct": 0.0, "pos_gld_tonnes_chg20_pct": 0.0}


def _one_per_stamp(avail: pd.Series, col: str, x: pd.Series) -> pd.DataFrame:
    f = pd.DataFrame({"available_utc": avail.to_numpy(), col: x.to_numpy()})
    return f.drop_duplicates("available_utc", keep="last").dropna().reset_index(drop=True)


def positioning_frames(pos: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """column -> DataFrame(available_utc, column), each on its own availability timeline."""
    net, oi = first_release(pos, "cot_mm_net"), first_release(pos, "cot_open_interest")
    cot = net.merge(oi, on="value_date", suffixes=("_net", "_oi"))
    cot = cot.assign(available_utc=np.maximum(cot["available_utc_net"], cot["available_utc_oi"]))
    cot = cot[cot["value_oi"] > 0]
    pct = 100.0 * cot["value_net"].astype(float) / cot["value_oi"].astype(float)
    r = pct.rolling(52, min_periods=40)
    avail = cot["available_utc"].cummax()
    gld = first_release(pos, "gld_tonnes")
    t = gld["value"].astype(float)
    return {
        "pos_cot_mm_net_pct_oi": _one_per_stamp(avail, "pos_cot_mm_net_pct_oi", pct),
        "pos_cot_mm_net_pct_oi_z52": _one_per_stamp(avail, "pos_cot_mm_net_pct_oi_z52", (pct - r.mean()) / r.std()),
        "pos_cot_mm_net_pct_oi_chg4": _one_per_stamp(avail, "pos_cot_mm_net_pct_oi_chg4", pct.diff(4)),
        "pos_gld_tonnes_chg5_pct": _one_per_stamp(gld["available_utc"], "pos_gld_tonnes_chg5_pct",
                                                  100.0 * t.pct_change(5, fill_method=None)),
        "pos_gld_tonnes_chg20_pct": _one_per_stamp(gld["available_utc"], "pos_gld_tonnes_chg20_pct",
                                                   100.0 * t.pct_change(20, fill_method=None)),
    }


def f_positioning(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """The five positioning columns as known at each bar; NaN without positioning data (the column set is fixed)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in COLUMNS}, index=df.index)
    pos = ctx.get("positioning")
    if pos is None or len(pos) == 0:
        return out
    bars = pd.DataFrame({"ts_utc": df["ts_utc"].to_numpy(), "_row": np.arange(len(df))})
    for col, frame in positioning_frames(pos).items():
        if frame.empty:
            continue
        joined = asof_join(bars, frame).sort_values("_row")     # asof_join sorts by time; restore the bar order
        out[col] = joined[col].to_numpy()
    return out


def enable() -> str:
    """Register the `positioning` feature (idempotent) and return its name, for an explicit feature list."""
    if NAME not in FEATURES:
        feature(NAME, "positioning", version=VERSION, signed=SIGNED)(f_positioning)
    return NAME
