"""Count the TVR events before dispatch (docs/research/prereg-tv-replica-2026-10-18.md, B2 "Count before dispatch" and
B4a): per preset of `smc_structure`, the candidate bars on our Dukascopy 15m bars from the pinned 2010 series start to
`research.holdout_from` (cut before resampling: no holdout row is loaded), per year and in total, and the TVR-1/TVR-2
overlap (the share of TVR-2 events within 4 bars of a TVR-1 event).

Counts only: no label, return or outcome is computed or imported. The candidates come from
`SmcStructureSpecialist.candidates_in_context`, the call research makes, so the series-start pin is enforced.

  python scripts/fetch_data_release.py --download-only        # raw/xauusd_1m_dukascopy_<year>.parquet
  python scripts/smc_counts.py --bars raw [--out-json smc_counts.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.timeutil import utc_index  # noqa: E402
from goldbot.research import h16  # noqa: E402
from goldbot.specialists.smc_structure import SERIES_START, SmcStructureSpecialist  # noqa: E402

OVERLAP_BARS = 4


def overlap_share(a: np.ndarray, b: np.ndarray, within: int = OVERLAP_BARS) -> float:
    """Share of the bars in `b` with a bar of `a` at most `within` bars away (NaN when `b` is empty)."""
    if len(b) == 0:
        return float("nan")
    a = np.sort(np.asarray(a, dtype=int))
    if len(a) == 0:
        return 0.0
    pos = np.clip(np.searchsorted(a, b), 1, len(a)) - 1
    near = np.minimum(np.abs(b - a[pos]), np.abs(b - a[np.minimum(pos + 1, len(a) - 1)]))
    return float(np.mean(near <= within))


def count(b1: pd.DataFrame, holdout_from: Any) -> dict[str, Any]:
    """The counts from 1m bars (cut at `holdout_from` here, before anything is built)."""
    b1 = h16.cut_before_holdout(b1, holdout_from)
    dec = resample_bars(b1, "15m").reset_index(drop=True)
    m = mid(dec)
    years = utc_index(m["ts_utc"]).year
    out: dict[str, Any] = {"bars_15m": len(m), "first_bar": str(utc_index(m["ts_utc"])[0]),
                           "last_bar": str(utc_index(m["ts_utc"])[-1]), "holdout_from": str(holdout_from),
                           "series_start": str(SERIES_START), "rules": {}}
    idx: dict[str, np.ndarray] = {}
    for preset in ("tvr1", "tvr2"):
        spec = SmcStructureSpecialist(**SmcStructureSpecialist.presets[preset])
        c = spec.candidates_in_context(m, pd.DataFrame(index=m.index), None)
        idx[preset] = c["idx"].to_numpy(dtype=int)
        per_year = pd.Series(np.asarray(years)[idx[preset]]).value_counts().sort_index()
        out["rules"][preset] = {"agent_id": spec.agent_id, "events": len(c), "long": int((c["side"] == 1).sum()),
                                "short": int((c["side"] == -1).sum()),
                                "per_year": {int(str(y)): int(str(k)) for y, k in per_year.items()}}
    out["tvr2_within_4_bars_of_tvr1"] = overlap_share(idx["tvr1"], idx["tvr2"])
    return out


def markdown(r: dict[str, Any]) -> str:
    t1, t2 = r["rules"]["tvr1"], r["rules"]["tvr2"]
    years = sorted(set(t1["per_year"]) | set(t2["per_year"]))
    lines = [f"Dukascopy 15m mid bars {r['first_bar'][:10]} .. {r['last_bar'][:10]} ({r['bars_15m']:,} bars; "
             f"holdout from {r['holdout_from']} not loaded). Counts only, no outcome.", "",
             "| Row | Events | Long | Short |", "|---|---:|---:|---:|",
             f"| TVR-1 | {t1['events']:,} | {t1['long']:,} | {t1['short']:,} |",
             f"| TVR-2 | {t2['events']:,} | {t2['long']:,} | {t2['short']:,} |", "",
             f"TVR-2 events within {OVERLAP_BARS} bars of a TVR-1 event: {r['tvr2_within_4_bars_of_tvr1']:.1%}.", "",
             "| Year | " + " | ".join(str(y) for y in years) + " |", "|---|" + "---:|" * len(years),
             "| TVR-1 | " + " | ".join(str(t1["per_year"].get(y, 0)) for y in years) + " |",
             "| TVR-2 | " + " | ".join(str(t2["per_year"].get(y, 0)) for y in years) + " |"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bars", type=Path, required=True, help="folder of xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()
    holdout_from = load_settings().research.holdout_from
    if holdout_from is None:
        raise SystemExit("research.holdout_from is not set: refusing to count without a holdout cut")
    b1 = read_bar_files(args.bars, SERIES_START.year, pd.Timestamp(holdout_from).year)
    r = count(b1, holdout_from)
    if args.out_json:
        args.out_json.write_text(json.dumps(r, indent=2))
    print(markdown(r))


if __name__ == "__main__":
    main()
