"""Research pass on real bars: published 1m Parquet -> 15m/1h/1d -> specialist candidates -> triple-barrier
labels -> purged walk-forward -> calibrated OOF metrics, a leakage check, a per-year table, and one row in the
trial registry. Runs as the `research` workflow (the sandboxes cannot download release assets); the report is
posted to an issue and the registry is kept as a release asset so the deflated Sharpe sees every trial ever run.

Research discipline (docs/proposals/2026-10-design-improvements.md, P1/P2):
* selection is cross-fitted (no calibrator or threshold sees the fold it selects from); costs are the bar spread
  (in the labels) plus slippage and commission (`--extra-cost-usd`, default: the settings' prior and commission);
* every report states the design's gates (1,500 candidates, 60 per test fold, three positive years incl. 2021-22)
  and the rule's own gross and net expectancy; the deflated Sharpe is shown only from 200 trades;
* each run is charged to the quarter's pre-registered trial budget (research.trial_budget_quarter) and refused
  beyond it; the holdout window (research.holdout_from/to) is excluded unless `--score-holdout`, which scores a
  configuration on it exactly once.

  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report report.md \
      [--specialist session_open] [--from-year 2010] [--to-year 2026] [--rationale "..."] [--score-holdout]
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
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.resample import BAR_COLUMNS, resample_bars  # noqa: E402
from goldbot.execution.costs import settings_extra_cost_usd  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.research.metrics import MIN_TRADES_FOR_DSR  # noqa: E402
from goldbot.research.pipeline import ResearchResult, lookahead_check, run_specialist  # noqa: E402
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry  # noqa: E402
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
    """Out-of-fold trades per calendar year: every candidate vs the model-filtered (cross-fitted) subset."""
    if oof.empty or "p_raw" not in oof:
        return pd.DataFrame()                    # no candidates or no fold: nothing was scored out of fold
    scored = oof.dropna(subset=["p_raw"]).copy()
    if scored.empty:
        return pd.DataFrame()
    scored["year"] = pd.DatetimeIndex(pd.to_datetime(scored["ts_utc"], utc=True)).year
    rows = []
    for y in sorted(scored["year"].unique()):
        g = scored[scored["year"] == y]
        taken = g[g["taken"].astype(bool)] if threshold is not None and "taken" in g else g.iloc[0:0]
        rows.append({"year": int(y), "n_all": len(g), "hit_all": g["target_hit"].mean(), "ret_all": g["ret"].mean(),
                     "n_model": len(taken), "hit_model": taken["target_hit"].mean() if len(taken) else np.nan,
                     "ret_model": taken["ret"].mean() if len(taken) else np.nan})
    return pd.DataFrame(rows)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
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
             f"- costs: bar spread in every label plus {m.get('extra_cost_usd', 0.0):.2f} $/oz round trip (slippage and commission)",
             _holdout_line(m.get("holdout")),
             f"- 1m bars with zero tick volume: {meta.get('zero_volume', float('nan')):.1%}"
             + (" (**volume features carry no information; re-pull the bars**)" if meta.get("zero_volume", 0) > 0.5 else ""),
             f"- runtime {meta['seconds']:.0f}s", ""]
    lines += _gates_lines(m.get("gates")) + _rule_only_lines(m.get("rule_only"))
    if "all_candidates" in m:
        lines += ["### Out of fold", "",
                  "| set | n | hit rate | mean ret | profit factor | Sharpe (ann.) | max DD | deflated SR |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for name in ("all_candidates", "model_filtered"):
            s = m[name]
            if s.get("n", 0) == 0:
                lines.append(f"| {name} | 0 | | | | | | |")
                continue
            dsr = f"{s['dsr']:.3f}" if s.get("dsr") is not None else f"n < {MIN_TRADES_FOR_DSR}"
            lines.append(f"| {name} | {s['n']} | {s['hit_rate']:.3f} | {s['mean_ret']:.5f} | {s['profit_factor']:.2f} | "
                         f"{s['sharpe_ann']:.2f} | {s['max_dd']:.3f} | {dsr} |")
        lines += ["", f"model_filtered = cross-fitted: each fold's calibrator is fitted on earlier folds only "
                      f"({m.get('n_calibrated', 0)} of {m['all_candidates'].get('n', 0)} scored candidates calibrated); "
                      f"take when p > break-even + 0.02 after costs (median threshold {m['threshold']:.3f})", ""]
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


def _holdout_line(h: dict[str, Any] | None) -> str:
    if not h:
        return "- holdout: none configured"
    if h.get("scored"):
        return f"- **holdout scoring**: metrics below are the held-out window {h['from'][:10]} .. {h['to'][:10]} only (final for this configuration)"
    return f"- holdout {h['from'][:10]} .. {h['to'][:10]} excluded (never seen by research)"


def _gates_lines(g: dict[str, Any] | None) -> list[str]:
    if not g:
        return []
    out = [f"### Design gates: {'**PASS**' if g['passed'] else '**FAIL**'}", ""]
    out += [f"- {'pass' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}" for c in g["checks"]]
    return out + [""]


def _rule_only_lines(r: dict[str, Any] | None) -> list[str]:
    if not r:
        return []
    out = ["### The rule alone (every candidate, no model)", "",
           "| costs | n | hit rate | mean ret | mean R | t-stat (R) |", "|---|---:|---:|---:|---:|---:|"]
    for name, label in (("gross", "gross (mid prices, no costs)"), ("net", "net (spread + slippage + commission)")):
        s = r.get(name) or {}
        if not s.get("n"):
            out.append(f"| {label} | 0 | | | | |")
            continue
        out.append(f"| {label} | {s['n']} | {s['hit_rate']:.3f} | {s['mean_ret']:.5f} | {s['mean_r']:.3f} | {s['t_stat']:.2f} |")
    return out + ["", "Meta-labelling can only filter an edge the rule already has: a gross t-stat near zero means "
                      "there is nothing to filter.", ""]


def parse_variants(family: str, raw: str) -> list[dict[str, Any]]:
    """`--variants` JSON: a list of config overrides, one trial each ([{}] = the family's defaults). Keys must be
    settings of the family (or timeframe / feature_seed); a typo must not silently run the defaults."""
    from goldbot.specialists.base import FEATURE_SEED_KEY, TIMEFRAME_KEY
    variants = json.loads(raw)
    if isinstance(variants, dict):
        variants = [variants]
    if not isinstance(variants, list) or not variants or not all(isinstance(v, dict) for v in variants):
        raise SystemExit("--variants must be a JSON list of objects, e.g. '[{}, {\"band_z\": 1.5}]'")
    allowed = set(SPECIALISTS[family].default_config) | {TIMEFRAME_KEY, FEATURE_SEED_KEY}
    for v in variants:
        bad = sorted(set(v) - allowed)
        if bad:
            raise SystemExit(f"unknown {family} settings {bad}; allowed: {sorted(allowed)}")
        try:
            SPECIALISTS[family](**v)         # rejects e.g. a timeframe the family cannot run on
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    return variants


def summary_table(rows: list[dict[str, Any]]) -> str:
    out = ["| trial | overrides | candidates | OOF AUC | gates | model n | model hit | model PF | DSR |",
           "|---:|---|---:|---:|---|---:|---:|---:|---:|"]
    for r in rows:
        mf = r["metrics"].get("model_filtered") or {}
        out.append(f"| {r['trial']} | `{json.dumps(r['overrides']) if r['overrides'] else 'defaults'}` | {r['n']:,} | "
                   f"{_fmt(r['metrics'].get('oof_auc'))} | {'pass' if (r['metrics'].get('gates') or {}).get('passed') else 'fail'} | {mf.get('n', 0)} | {_fmt(mf.get('hit_rate', float('nan')))} | "
                   f"{_fmt(mf.get('profit_factor', float('nan')))} | {_fmt(mf.get('dsr'))} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="raw")
    ap.add_argument("--registry", default="registry.jsonl")
    ap.add_argument("--report", default="research-report.md")
    ap.add_argument("--specialist", default="session_open")
    ap.add_argument("--from-year", type=int, default=2010)
    ap.add_argument("--to-year", type=int, default=2100)
    ap.add_argument("--rationale", default="baseline walk-forward on Dukascopy 1m bars")
    ap.add_argument("--variants", default="[{}]", help="JSON list of config overrides; each is one recorded trial")
    ap.add_argument("--extra-cost-usd", type=float, default=None,
                    help="round-trip slippage + commission per oz beyond the spread (default: settings prior + commission)")
    ap.add_argument("--score-holdout", action="store_true",
                    help="score the configurations on the held-out window (once per configuration, ever)")
    args = ap.parse_args()
    variants = parse_variants(args.specialist, args.variants)
    settings = load_settings()
    extra_cost = settings_extra_cost_usd(settings) if args.extra_cost_usd is None else float(args.extra_cost_usd)
    holdout = settings.research.holdout_window()
    t0 = time.time()
    b1 = load_bars(Path(args.bars), args.from_year, args.to_year)      # fails fast, before any registry file exists
    reg = TrialRegistry(args.registry)
    quarter: str | None = None
    if args.score_holdout:
        if holdout is None:
            raise SystemExit("--score-holdout: no holdout window configured (research.holdout_from/holdout_to)")
        for v in variants:
            cfg = SPECIALISTS[args.specialist](**v).config
            if reg.holdout_scored(args.specialist, cfg):
                raise SystemExit(f"{args.specialist} {json.dumps(v) or 'defaults'} was already scored on the holdout; "
                                 "that result is final for this configuration")
    else:
        try:
            quarter = reg.check_budget(len(variants), settings.research.trial_budget_quarter)
        except TrialBudgetExceeded as exc:
            raise SystemExit(str(exc)) from None
    years_span = (b1["ts_utc"].iloc[-1] - b1["ts_utc"].iloc[0]).days / 365.25
    zero_volume = float((b1["tick_count"].astype(float) <= 0).mean())
    print(f"1m bars: {len(b1):,} ({b1['ts_utc'].iloc[0]:%Y-%m-%d} -> {b1['ts_utc'].iloc[-1]:%Y-%m-%d}), "
          f"zero tick volume in {zero_volume:.1%}", flush=True)
    frames: dict[str, tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any]]] = {}
    sections, rows = [], []
    for overrides in variants:
        spec = SPECIALISTS[args.specialist](**overrides)
        tf = spec.timeframe
        if tf not in frames:                       # bars, context and the lookahead check once per timeframe
            b_dec = resample_bars(b1, tf)
            context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
            leak = lookahead_check(b_dec, context)
            frames[tf] = (b_dec, context, leak)
            sizes = "  ".join(f"{k} {len(v):,}" for k, v in context.items())
            print(f"{tf} {len(b_dec):,}  {sizes}; lookahead check: {len(leak['lookahead_columns'])} of "
                  f"{leak['columns_checked']} columns differ [{time.time() - t0:.0f}s]", flush=True)
        b_dec, context, leak = frames[tf]
        n_trials = reg.n_trials + 1
        res = run_specialist(spec, b_dec, context=context, n_trials=n_trials, extra_cost_usd=extra_cost,
                             holdout=holdout, score_holdout=args.score_holdout)
        trades_per_year = res.n_candidates / years_span if years_span > 0 else 0.0
        print(f"{json.dumps(overrides) or 'defaults'}: candidates {res.n_candidates}  folds {res.n_folds}  "
              f"[{time.time() - t0:.0f}s]", flush=True)
        results = {**res.metrics, "lookahead": leak, "trades_per_year": trades_per_year,
                   "bars_from": str(b1["ts_utc"].iloc[0]), "bars_to": str(b1["ts_utc"].iloc[-1])}
        rationale = args.rationale + (f" | overrides {json.dumps(overrides, sort_keys=True)}" if overrides else "")
        row = reg.record(agent_id=res.agent_id, family=spec.family, config=spec.config, feature_version=res.feature_version,
                         rationale=rationale, results=results, status="holdout" if args.score_holdout else "evaluated",
                         budget_quarter=quarter)
        years = per_year(res.oof, res.metrics.get("threshold"))
        meta = {"specialist": args.specialist, "from_year": int(b1["ts_utc"].iloc[0].year),
                "to_year": int(b1["ts_utc"].iloc[-1].year), "n_1m": len(b1), "n_dec": len(b_dec), "tf": tf,
                "zero_volume": zero_volume, "trial": row["trial"], "seconds": time.time() - t0}
        text = render_report(res, years, leak, meta)
        if overrides:
            text = text.replace("\n\n", f"\n\n- config overrides: `{json.dumps(overrides, sort_keys=True)}`\n", 1)
        sections.append(text)
        rows.append({"trial": row["trial"], "overrides": overrides, "n": res.n_candidates, "metrics": res.metrics})
        print(json.dumps({"trial": row["trial"], "agent_id": res.agent_id}), flush=True)
    head = [] if len(rows) == 1 else [f"## {args.specialist}: {len(rows)} variants", "", summary_table(rows), "",
                                      "Each variant is one recorded trial; the deflated SR of every later trial counts "
                                      "them all.", ""]
    report = "\n".join(head) + "\n\n".join(sections)
    Path(args.report).write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
