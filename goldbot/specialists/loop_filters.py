"""Pre-declared candidate filters of the continuous research loop (docs/plans/continuous-research-loop.md, "Loop grid v1").

Two optional settings of any specialist, absent = off, so a family's default configuration, its labels and its agent id
are unchanged:

* `session_filter`: asia | london | newyork. Keep a candidate only when its decision bar is in that session
  (`session_id` of the decision bar's own features: 0 asia, 1 london, 2 newyork, per `features.session`).
* `regime_filter`: a point-in-time volatility or trend regime of the decision bar. low_vol (`vol_tercile` == 0),
  not_high_vol (`vol_tercile` <= 1), trending (`adx14` >= 25), ranging (`adx14` < 20).

Both read only the decision bar's own feature row, which is trailing (as of the bar's close): nothing after the
decision is read and no join is added (design "Every join to bars via asof_join on available_utc" is untouched). A
feature column that is missing keeps no candidate (fail closed, as `mean_reversion` does). They are applied in
`Specialist.candidates` and `candidates_in_context` of every subclass, so research, the shadow book and the live engine
see the same candidates."""
from __future__ import annotations

from typing import Any

import pandas as pd

SESSION_FILTER = "session_filter"
REGIME_FILTER = "regime_filter"
SESSIONS = ("asia", "london", "newyork")                          # `session_id` codes 0, 1, 2
REGIMES = ("low_vol", "not_high_vol", "trending", "ranging")
ADX_TRENDING = 25.0                                               # adx14 buckets (features.technical): 25 starts "trend"
ADX_RANGING = 20.0
FILTER_KEYS = (SESSION_FILTER, REGIME_FILTER)


def validate(config: dict[str, Any]) -> None:
    """ValueError for a filter value that is not declared."""
    s, r = config.get(SESSION_FILTER), config.get(REGIME_FILTER)
    if s is not None and s not in SESSIONS:
        raise ValueError(f"{SESSION_FILTER} {s!r} is not one of {list(SESSIONS)}")
    if r is not None and r not in REGIMES:
        raise ValueError(f"{REGIME_FILTER} {r!r} is not one of {list(REGIMES)}")


def apply_filters(config: dict[str, Any], cands: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """`cands` (columns idx, side, ...; idx positional into `features`) kept where every filter in `config` passes."""
    s, r = config.get(SESSION_FILTER), config.get(REGIME_FILTER)
    if (s is None and r is None) or cands.empty:
        return cands
    keep = pd.Series(True, index=range(len(features)))
    if s is not None:
        keep &= _column(features, "session_id") == float(SESSIONS.index(s))
    if r is not None:
        if r in ("low_vol", "not_high_vol"):
            tercile = _column(features, "vol_tercile")
            keep &= (tercile == 0.0) if r == "low_vol" else (tercile <= 1.0)
        else:
            adx = _column(features, "adx14")
            keep &= (adx >= ADX_TRENDING) if r == "trending" else (adx < ADX_RANGING)
    idx = cands["idx"].to_numpy(dtype=int)
    return cands[keep.to_numpy(dtype=bool)[idx]].reset_index(drop=True)


def _column(features: pd.DataFrame, name: str) -> pd.Series:
    """The column by position (NaN where it is missing, which compares False: fail closed)."""
    if name not in features.columns:
        return pd.Series(float("nan"), index=range(len(features)))
    return pd.Series(features[name].astype(float).to_numpy(), index=range(len(features)))
