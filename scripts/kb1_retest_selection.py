"""D-KB1 retest selection (docs/research/prereg-kb-2026-10-18.md section 3, KB-1): is a limit entry at the broken box
edge adversely selected? Diagnostic, 0 trials; no stop, target or P&L is computed.

Candidates: the H-37 `box_break` bare-break rung L6 breaks (`box_course.BREAK_RUNGS["L6"]`: pairs P1 1h boxes and P2 4h
boxes, a 1h close beyond the far edge by 0.2 box heights with the body, no money-flow, golden-zone or daily filter),
before any pullback entry. Retested = price returns to the broken box edge within 3 1h bars after the break close
(long: a low at or below the top edge; short: a high at or above the bottom edge). Report, for retested vs
never-retested, the forward move of the 15m close 16 and 32 bars after the break close in ATR14 (15m), signed with the
break, and the share of the 95th-percentile moves (16 bars) that come from never-retested breaks.

Reading rule (KB-1 as written): share >= 70% -> H-25's limit entry is structurally adverse-selected (lower its prior);
the groups differing by < 0.02 ATR (bootstrap 95% CI of the difference includes 0) -> "retest neutral".

  python scripts/fetch_data_release.py --download-only
  python scripts/kb1_retest_selection.py --bars raw [--out-json kb1.json]

Bars are cut at `research.holdout_from` before resampling: the holdout is never loaded.
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
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.research import h16  # noqa: E402
from goldbot.specialists import box_course as bx  # noqa: E402

RETEST_BARS = 3                  # 1h bars after the break close
HORIZONS = (16, 32)              # 15m bars after the break close
SHARE_ADVERSE = 0.70
NEUTRAL_ATR = 0.02
BOOTSTRAP, SEED = 2000, 20261018


def breaks(c: bx.Course) -> pd.DataFrame:
    """Every L6 break on the 1h trigger bars: time (the break bar's close, ns), side, edge (the broken edge), pair,
    retested (price returned to the edge within RETEST_BARS 1h bars)."""
    trig = c.b["1h"]
    rows = []
    for pair in bx.BREAK_RUNGS["L6"]["pairs"]:
        mp = c.b[bx.PAIRS[pair]["map"]]
        for s in range(1, trig.n):
            close_s = int(trig.close_ns[s])
            jm = c.row(bx.PAIRS[pair]["map"], close_s)
            tops, bots = mp.box_set(jm)
            if not len(tops):
                continue
            heff = np.maximum(tops - bots, 0.1 * mp.atr[jm])
            side, k = bx._breaks(trig.c[s - 1], trig.c[s], trig.o[s], tops, bots, heff, bx.BREAK_DEPTH)
            if side == 0 or s + RETEST_BARS >= trig.n:
                continue
            edge = float(tops[k]) if side > 0 else float(bots[k])
            after = slice(s + 1, s + RETEST_BARS + 1)
            back = bool((trig.l[after] <= edge).any()) if side > 0 else bool((trig.h[after] >= edge).any())
            rows.append((close_s, side, edge, pair, back))
    return pd.DataFrame(rows, columns=["close_ns", "side", "edge", "pair", "retested"])


def forward_moves(c: bx.Course, br: pd.DataFrame) -> pd.DataFrame:
    """`br` plus, per horizon, the signed move of the 15m close that many bars after the break close, in ATR14 (15m)."""
    q = c.b[c.dec_tf]
    j = np.array([c.row(c.dec_tf, int(t)) for t in br["close_ns"]], dtype=int)
    out = br.copy()
    for hz in HORIZONS:
        ok = (j >= 0) & (j + hz < q.n)
        mv = np.full(len(br), np.nan)
        mv[ok] = br["side"].to_numpy()[ok] * (q.c[j[ok] + hz] - q.c[j[ok]]) / q.atr[j[ok]]
        out[f"fwd{hz}"] = mv
    return out


def _ci(a: np.ndarray, b: np.ndarray, rng: np.random.Generator) -> list[float]:
    """Bootstrap 95% interval of mean(a) - mean(b)."""
    if not len(a) or not len(b):
        return [float("nan"), float("nan")]
    d = (rng.choice(a, (BOOTSTRAP, len(a))).mean(axis=1) - rng.choice(b, (BOOTSTRAP, len(b))).mean(axis=1))
    return [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))]


def summarise(moves: pd.DataFrame) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    re, nev = moves[moves["retested"]], moves[~moves["retested"]]
    out: dict[str, Any] = {"breaks": len(moves), "retested": len(re), "never_retested": len(nev), "horizons": {}}
    for hz in HORIZONS:
        a, b = re[f"fwd{hz}"].dropna().to_numpy(), nev[f"fwd{hz}"].dropna().to_numpy()
        diff = float(a.mean() - b.mean()) if len(a) and len(b) else float("nan")
        out["horizons"][hz] = {"retested_mean": float(a.mean()) if len(a) else float("nan"),
                               "never_mean": float(b.mean()) if len(b) else float("nan"),
                               "difference": diff, "ci95": _ci(a, b, rng)}
    col = f"fwd{HORIZONS[0]}"
    allv = moves[col].dropna()
    cut = float(np.quantile(allv, 0.95)) if len(allv) else float("nan")
    top = moves[moves[col] >= cut] if len(allv) else moves.iloc[0:0]
    out["p95_move_16"], out["p95_share_never_retested"] = cut, float((~top["retested"]).mean()) if len(top) else float("nan")
    d = out["horizons"][HORIZONS[0]]
    out["reading"] = ("adverse-selected" if out["p95_share_never_retested"] >= SHARE_ADVERSE
                      else "retest neutral" if abs(d["difference"]) < NEUTRAL_ATR and d["ci95"][0] <= 0 <= d["ci95"][1]
                      else "no pre-registered conclusion")
    return out


def diagnose(b1: pd.DataFrame) -> dict[str, Any]:
    dec = resample_bars(b1, "15m").reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs("15m")}
    c = bx.Course(mid(dec), "15m", context, ["1h", "4h"])
    return summarise(forward_moves(c, breaks(c)))


def markdown(r: dict[str, Any]) -> str:
    lines = [f"L6 breaks {r['breaks']:,}: retested {r['retested']:,}, never retested {r['never_retested']:,}. "
             "Forward moves in ATR14 (15m), signed with the break. No P&L.", "",
             "| Horizon (15m bars) | Retested | Never | Difference | 95% CI |", "|---:|---:|---:|---:|---|"]
    for hz, h in r["horizons"].items():
        lines.append(f"| {hz} | {h['retested_mean']:+.3f} | {h['never_mean']:+.3f} | {h['difference']:+.3f} | "
                     f"[{h['ci95'][0]:+.3f}, {h['ci95'][1]:+.3f}] |")
    lines += ["", f"Share of the 95th-percentile 16-bar moves from never-retested breaks: "
              f"{r['p95_share_never_retested']:.1%} (adverse at >= {SHARE_ADVERSE:.0%}). Reading: {r['reading']}."]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bars", type=Path, required=True, help="folder of xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()
    holdout_from = load_settings().research.holdout_from
    if holdout_from is None:
        raise SystemExit("research.holdout_from is not set: refusing to run without a holdout cut")
    b1 = h16.cut_before_holdout(read_bar_files(args.bars, 2010, pd.Timestamp(holdout_from).year), holdout_from)
    r = diagnose(b1)
    if args.out_json:
        args.out_json.write_text(json.dumps(r, indent=2, default=str))
    print(markdown(r))


if __name__ == "__main__":
    main()
