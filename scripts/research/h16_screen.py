"""H-16 screen on 2010-2017 (amendment A3; frozen rule in docs/research/prereg-2026-10-30-h16-screen.md).

Runs H-16's pre-registered evaluation (`goldbot.research.h16.evaluate_h16`, unchanged) with every input cut at
2018-01-01, so the HAR and full-score fits are walk-forward inside 2010-2017 and no 2018+ bar, GVZ row or calendar row
is loaded. Records nothing in any registry. Writes a JSON of statistics (ratios and correlations only, no prices) to
the path given; the output must not be committed.

    .venv/bin/python scripts/research/h16_screen.py --bars raw/ --macro ~/tmp/ts/macro \
        --us-calendar ~/tmp/ts-data/cal --out ~/tmp/h16-screen/result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.release import read_bar_files, read_macro_files, read_us_calendar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.research import h16  # noqa: E402

SCREEN_FROM_YEAR = 2010
SCREEN_TO_YEAR = 2017
SCREEN_CUT = "2018-01-01"           # A3: the screen reads 2010-01-01 .. 2017-12-31 only
MIN_SCREEN_DAYS = 1200              # frozen floor: refused (no statistic) below this many evaluation days
COSTS_USD_PER_OZ = (0.25, 0.85)     # planning round trip, prereg-cost-measurement-2026-10.md line 105 (reported only)


def _quintile(x: np.ndarray) -> np.ndarray:
    return np.asarray(pd.qcut(pd.Series(x).rank(method="first"), 5, labels=False).to_numpy(), dtype=int)


def diagnostics(ev: pd.DataFrame) -> dict[str, Any]:
    """Reported, not in the rule (the frozen list in the screen pre-registration)."""
    use = ev["in_eval"].to_numpy(dtype=bool)
    y = ev["y"].to_numpy(dtype=float)[use]
    full = ev["full_log"].to_numpy(dtype=float)[use]
    har = ev["har_log"].to_numpy(dtype=float)[use]
    a_d = ev["atr"].shift(1).to_numpy(dtype=float)[use]      # A_D: ATR(1d) through D-1, price units
    w = np.exp(y)
    w_usd = w * a_d
    out: dict[str, Any] = {}
    h_top, h_bot = h16.quintile_means(har, w)
    out["har_quintile_ratio"] = h_top / h_bot
    years = pd.DatetimeIndex(ev["day"].to_numpy()[use]).year.to_numpy()
    out["rho_gain_by_year"] = {int(yr): h16.spearman(full[years == yr], y[years == yr])
                               - h16.spearman(har[years == yr], y[years == yr]) for yr in np.unique(years)}
    rq = _quintile(w)                         # realised: 4 = best days, 0 = poorest days
    for name, s in (("full", full), ("har", har)):
        sq = _quintile(s)
        out[f"{name}_best_days_in_top2_score_quintiles"] = float(np.mean(sq[rq == 4] >= 3))
        out[f"{name}_poor_days_in_bottom2_score_quintiles"] = float(np.mean(sq[rq == 0] <= 1))
        out[f"{name}_top_score_quintile_share_best_days"] = float(np.mean(rq[sq == 4] == 4))
        for c in COSTS_USD_PER_OZ:
            out[f"{name}_median_W_over_cost_{c}_by_score_quintile"] = [
                float(np.nanmedian(w_usd[sq == q] / c)) for q in range(5)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="H-16 screen on 2010-2017 (A3)")
    ap.add_argument("--bars", required=True)
    ap.add_argument("--macro", required=True)
    ap.add_argument("--us-calendar", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    b1 = read_bar_files(Path(a.bars), SCREEN_FROM_YEAR, SCREEN_TO_YEAR)
    b1 = h16.cut_before_holdout(b1, SCREEN_CUT)
    bars = mid(resample_bars(b1, h16.TIMEFRAME))
    macro = read_macro_files(Path(a.macro))
    cal = read_us_calendar_files(Path(a.us_calendar))
    # evaluate_h16 cuts bars, macro (available_utc) and calendar (available_utc, event_utc) at SCREEN_CUT itself and
    # refuses a NaN statistic; the floor is enforced before any statistic through expected_eval_days (90% share).
    res, ev = h16.evaluate_h16(bars, macro, cal, holdout_from=SCREEN_CUT,
                               expected_eval_days=int(np.ceil(MIN_SCREEN_DAYS / h16.MIN_EVAL_SHARE)))
    out = {"result": res.model_dump(), "code_hash_h16": h16.code_hash(), "diagnostics": diagnostics(ev)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
