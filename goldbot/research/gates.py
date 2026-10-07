"""The design's research gates (Modelling: Validation), checked on every walk-forward result.

A specialist configuration passes only when
* it has at least MIN_CANDIDATES labelled candidates overall,
* every walk-forward test fold holds at least MIN_TEST_FOLD candidates,
* its model-filtered (cross-fitted, out-of-fold) trades have positive mean net return in at least MIN_POSITIVE_YEARS
  separate calendar years, one of them in the 2021-2022 chop. A year counts only with MIN_TRADES_PER_YEAR trades, so
  one lucky trade is not a "positive year".

The deflated Sharpe is a separate gate (metrics.MIN_TRADES_FOR_DSR, the registry's trial count). The population's
shadow -> live promotion requires a passed research trial for the agent's configuration (TrialRegistry.passed_gates).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

MIN_CANDIDATES = 1500
MIN_TEST_FOLD = 60
MIN_POSITIVE_YEARS = 3
MIN_TRADES_PER_YEAR = 10
CHOP_YEARS = (2021, 2022)


def research_gates(n_candidates: int, fold_test_sizes: list[int], taken: pd.DataFrame) -> dict[str, Any]:
    """taken: the model-filtered out-of-fold trades (ts_utc, ret). Returns {"passed": bool, "checks": [...]}."""
    checks: list[dict[str, Any]] = [
        {"name": "candidates", "passed": n_candidates >= MIN_CANDIDATES,
         "detail": f"{n_candidates:,} labelled candidates (need {MIN_CANDIDATES:,})"},
    ]
    smallest = min(fold_test_sizes) if fold_test_sizes else 0
    checks.append({"name": "per_fold", "passed": bool(fold_test_sizes) and smallest >= MIN_TEST_FOLD,
                   "detail": f"{len(fold_test_sizes)} test folds, smallest {smallest} candidates (need {MIN_TEST_FOLD} in every fold)"})
    positive: list[int] = []
    if not taken.empty:
        year = pd.DatetimeIndex(pd.to_datetime(taken["ts_utc"], utc=True)).year
        for y, g in taken.groupby(year):
            if len(g) >= MIN_TRADES_PER_YEAR and float(g["ret"].mean()) > 0:
                positive.append(int(str(y)))
    chop = any(y in CHOP_YEARS for y in positive)
    checks.append({"name": "positive_years", "passed": len(positive) >= MIN_POSITIVE_YEARS and chop,
                   "detail": f"positive model-filtered years (>= {MIN_TRADES_PER_YEAR} trades each): "
                             f"{positive or 'none'}; need {MIN_POSITIVE_YEARS} incl. one of {list(CHOP_YEARS)}"})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}
