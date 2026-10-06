"""Research pass on real bars: published 1m Parquet -> 15m/1h/1d -> specialist candidates -> triple-barrier
labels -> purged walk-forward -> calibrated OOF metrics, a leakage check, a per-year table, and one row in the
trial registry. Runs as the `research` workflow (the sandboxes cannot download release assets); the report is
posted to an issue and the registry is kept as a release asset so the deflated Sharpe sees every trial ever run.

  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report report.md \
      [--specialist session_open] [--from-year 2010] [--to-year 2026] [--rationale "..."]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.resample import BAR_COLUMNS, resample_bars  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.research.metrics import summarize  # noqa: E402
from goldbot.research.pipeline import ResearchResult, lookahead_check, run_specialist  # noqa: E402
from goldbot.research.registry import TrialRegistry  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402


def load_bars(folder: Path, from_year: int, to_year: int) -> pd.DataFrame:
    """Concatenate the yearly 1m files; bars with an error-level data-quality flag are dropped."""
    frames = []
    for f in sorted(folder.glob("xauusd_1m_dukascopy_*.parquet")):
        year = int(f.stem.rsplit("_", 1)[1])
        if from_year <= year <= to_year:
            frames.append(pd.read_parquet(f))
    if not frames:
        raise SystemExit(f"no xauusd_1m_dukascopy_<year>.parquet files for {from_year}-{to_year} in {folder}")
    b = pd.concat(frames, ignore_index=True)
    b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
    b["visible_at"] = pd.to_datetime(b["visible_at"], utc=True)
    flags = b["dq_flag"].fillna("").astype(str) if "dq_flag" in b else pd.Series("", index=b.index)
    b = b[~flags.str.startswith("error")]
    return b.drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)[BAR_COLUMNS]


def per_year(oof: pd.DataFrame, threshold: float | None) -> pd.DataFrame:
    """Out-of-fold trades per calendar year: every candidate vs the model-filtered subset."""
    if oof.empty or "p_raw" not in oof:
        return pd.DataFrame()                    # no candidates or no fold: nothing was scored out of fold
    scored = oof.dropna(subset=["p_raw"]).copy()
    if scored.empty:
        return pd.DataFrame()
    scored["year"] = pd.DatetimeIndex(pd.to_datetime(scored["ts_utc"], utc=True)).year
    rows = []
    for y in sorted(scored["year"].unique()):
        g = scored[scored["year"] == y]
        taken = g[g["p"] > threshold] if threshold is not None and "p" in g else g.iloc[0:0]
        rows.append({"year": int(y), "n_all": len(g), "hit_all": g["target_hit"].mean(), "ret_all": g["ret"].mean(),
                     "n_model": len(taken), "hit_model": taken["target_hit"].mean() if len(taken) else np.nan,
                     "ret_model": taken["ret"].mean() if len(taken) else np.nan})
    return pd.DataFrame(rows)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return "—" if np.isnan(v) else f"{v:.4f}"
    return str(v)


def render_report(res: ResearchResult, years: pd.DataFrame, leak: dict[str, Any] | None, meta: dict[str, Any]) -> str:
    m = res.metrics
    lines = [f"## {meta['specialist']} walk-forward on real bars ({meta['from_year']}-{meta['to_year']})", "",
             f"- bars: {meta['n_1m']:,} 1m -> {meta['n_dec']:,} {meta['tf']} decision bars, feature version `{res.feature_version}`",
             f"- candidates {res.n_candidates:,}, folds {res.n_folds}, registry trial #{meta['trial']} (the deflated SR counts all {meta['trial']} trials)",
             f"- lookahead check (features rebuilt on history cut at {leak['cut_utc'][:10]}): "
             + ("clean" if not leak["lookahead_columns"] else f"**{len(leak['lookahead_columns'])} columns use future data: "
                f"{', '.join(leak['lookahead_columns'][:10])}**") if leak else "- lookahead check: n/a",
             f"- out-of-fold AUC of the model (0.5 = no skill): {_fmt(res.metrics.get('oof_auc'))}",
             f"- 1m bars with zero tick volume: {meta.get('zero_volume', float('nan')):.1%}"
             + (" (**volume features carry no information; re-pull the bars**)" if meta.get("zero_volume", 0) > 0.5 else ""),
             f"- runtime {meta['seconds']:.0f}s", ""]
    if "all_candidates" in m:
        lines += ["| set | n | hit rate | mean ret | profit factor | Sharpe (ann.) | max DD | deflated SR |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for name in ("all_candidates", "model_filtered"):
            s = m[name]
            if s.get("n", 0) == 0:
                lines.append(f"| {name} | 0 | | | | | | |")
                continue
            lines.append(f"| {name} | {s['n']} | {s['hit_rate']:.3f} | {s['mean_ret']:.5f} | {s['profit_factor']:.2f} | "
                         f"{s['sharpe_ann']:.2f} | {s['max_dd']:.3f} | {s['dsr']:.3f} |")
        lines += ["", f"Model threshold (breakeven + 0.02 after costs): p > {m['threshold']:.3f}", ""]
    else:
        lines += ["Too few out-of-fold predictions to calibrate; no metrics.", ""]
    if not years.empty:
        lines += ["### Per year (out-of-fold)", "", "| year | n all | hit all | ret all | n model | hit model | ret model |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for r in years.itertuples(index=False):
            lines.append(f"| {r.year} | {r.n_all} | {_fmt(r.hit_all)} | {_fmt(r.ret_all)} | {r.n_model} | {_fmt(r.hit_model)} | {_fmt(r.ret_model)} |")
        lines.append("")
    if res.importance is not None:
        lines += ["### Top features (gain, last fold)", "", "```", res.importance.head(15).to_string(), "```", ""]
    lines += ["Promotion still needs the design's gates (deflated SR, per-regime stability, shadow period); this is one trial."]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="raw")
    ap.add_argument("--registry", default="registry.jsonl")
    ap.add_argument("--report", default="research-report.md")
    ap.add_argument("--specialist", default="session_open")
    ap.add_argument("--from-year", type=int, default=2010)
    ap.add_argument("--to-year", type=int, default=2100)
    ap.add_argument("--rationale", default="baseline walk-forward on Dukascopy 1m bars")
    args = ap.parse_args()
    t0 = time.time()
    b1 = load_bars(Path(args.bars), args.from_year, args.to_year)
    years_span = (b1["ts_utc"].iloc[-1] - b1["ts_utc"].iloc[0]).days / 365.25
    zero_volume = float((b1["tick_count"].astype(float) <= 0).mean())
    print(f"1m bars: {len(b1):,} ({b1['ts_utc'].iloc[0]:%Y-%m-%d} -> {b1['ts_utc'].iloc[-1]:%Y-%m-%d}), "
          f"zero tick volume in {zero_volume:.1%}", flush=True)
    spec = SPECIALISTS[args.specialist]()
    tf = spec.timeframe
    b_dec = resample_bars(b1, tf)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    sizes = "  ".join(f"{k} {len(v):,}" for k, v in context.items())
    print(f"{tf} {len(b_dec):,}  {sizes}  [{time.time() - t0:.0f}s]", flush=True)

    reg = TrialRegistry(args.registry)
    n_trials = reg.n_trials + 1
    res = run_specialist(spec, b_dec, context=context, n_trials=n_trials)
    trades_per_year = res.n_candidates / years_span if years_span > 0 else 0.0
    if "threshold" in res.metrics and trades_per_year > 0:
        # annualise by the candidate rate actually observed, not the pipeline's default guess
        scored = res.oof.dropna(subset=["p_raw"])
        taken = scored[scored["p"] > res.metrics["threshold"]]
        res.metrics["all_candidates"] = summarize(scored, trades_per_year, n_trials)
        res.metrics["model_filtered"] = summarize(taken, trades_per_year * len(taken) / max(len(scored), 1), n_trials)
    print(f"candidates {res.n_candidates}  folds {res.n_folds}  [{time.time() - t0:.0f}s]", flush=True)

    leak = lookahead_check(b_dec, context)
    print(f"lookahead check: {len(leak['lookahead_columns'])} of {leak['columns_checked']} columns differ "
          f"[{time.time() - t0:.0f}s]", flush=True)
    results = {**res.metrics, "lookahead": leak, "trades_per_year": trades_per_year,
               "bars_from": str(b1["ts_utc"].iloc[0]), "bars_to": str(b1["ts_utc"].iloc[-1])}
    row = reg.record(agent_id=res.agent_id, family=spec.family, config=spec.config, feature_version=res.feature_version,
                     rationale=args.rationale, results=results, status="evaluated")
    years = per_year(res.oof, res.metrics.get("threshold"))
    meta = {"specialist": args.specialist, "from_year": int(b1["ts_utc"].iloc[0].year), "to_year": int(b1["ts_utc"].iloc[-1].year),
            "n_1m": len(b1), "n_dec": len(b_dec), "tf": tf, "zero_volume": zero_volume, "trial": row["trial"], "seconds": time.time() - t0}
    report = render_report(res, years, leak, meta)
    Path(args.report).write_text(report)
    print(report)
    print(json.dumps({"trial": row["trial"], "agent_id": res.agent_id}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
