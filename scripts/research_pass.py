"""Research pass on real bars: published 1m Parquet -> 15m/1h/1d -> specialist candidates -> triple-barrier
labels -> purged walk-forward -> calibrated OOF metrics, a leakage check, a per-year table, and one row in the
trial registry. Runs as the `research` workflow (the sandboxes cannot download release assets); the report is
posted to an issue and the registry is kept as a release asset so the deflated Sharpe sees every trial ever run.

Research discipline (docs/proposals/2026-10-design-improvements.md, P1/P2):
* selection is cross-fitted (no calibrator or threshold sees the fold it selects from); costs are the bar spread
  (in the labels) plus slippage and commission (`--extra-cost-usd`, default: the settings' prior and commission);
* overnight financing (swap, `costs.swap_*` in settings, a prior until the broker's cost table reports its own) is
  charged in the net labels for every server-day rollover a trade is held through, three times on the triple day;
* `--cost-table FILE` (the canonical broker's measured cost table, `costs_measured.json` on release costs-v1, which
  the VPS publishes; research.yml downloads it when present) replaces the priors: slippage and commission from the
  table (unless `--extra-cost-usd`), swap from the table when it has measured swap. Without it the report's cost
  source says PRIORS ONLY;
* every report states the design's gates (1,500 candidates, 60 per test fold, three positive years incl. 2021-22)
  and the rule's own gross and net expectancy; the deflated Sharpe is shown only from 200 trades;
* each run is charged to the quarter's pre-registered trial budget (research.trial_budget_quarter) and refused
  beyond it; the holdout window (research.holdout_from/to) is excluded unless `--score-holdout`, which scores a
  configuration on it exactly once.

Primary-signal screen (P4, goldbot/research/screen.py): before any model, the rule's own gross expectancy over the
research window must be positive with t >= 2 on >= 1,000 events. A configuration that fails is recorded with status
"screened" (it is a trial: a screen selects rules on data) and no model is fitted, unless `--skip-screen`.

Pooled meta-model (P5): `--pooled 15m|1h` fits ONE model over the union of every family deciding on that timeframe
(family indicators and `side` among its inputs), screened, gated and recorded as family "pooled_<tf>" (one trial).

Macro drivers: `--macro DIR` (the release macro-v1, from the data-macro workflow) adds the point-in-time macro
features (goldbot/features/macro.py: real-yield change and z-score, dollar change, GVZ level and change) to the
candidate frame and to the leakage check. A specialist's declared model_features are unchanged; the columns are there
for a trial that declares them, a seeded clone, or a specialist without a declaration. The feature version then
includes them, so such a model scores only frames built with macro. A missing or empty release is reported and the
pass runs without them.

Combinatorial purged CV (M16, goldbot/research/cpcv.py): `--cpcv <trial#> [<trial#> ...]` re-evaluates registered
walk-forward trials on 6 time groups of the research window (15 purged splits, 5 backtest paths) and, for several
trials, the probability of backtest overfitting across them. It is not a trial: no budget slot, no registry row; the
result is attached to each trial as evidence (`<registry>.evidence.jsonl`).

  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report report.md \
      [--specialist session_open | --pooled 15m] [--from-year 2010] [--to-year 2026] [--rationale "..."] \
      [--variants '[{}]'] [--skip-screen] [--score-holdout] [--cost-table costs_measured.json] [--macro macro/]
  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report cpcv.md --cpcv 12 [13 14]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_macro_files  # noqa: E402
from goldbot.data.resample import BAR_COLUMNS, resample_bars  # noqa: E402
from goldbot.execution.costs import SwapSpec, load_research_cost_table, research_costs  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.research.metrics import MIN_TRADES_FOR_DSR  # noqa: E402
from goldbot.research.pipeline import (  # noqa: E402
    POOLED_FEATURES,
    ResearchResult,
    build_decision_frame,
    evaluate,
    lookahead_check,
    pool_identity,
    pooled_family,
    pooled_members,
    prepare,
    run_pool,
)
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry  # noqa: E402
from goldbot.research.screen import (  # noqa: E402
    is_inconclusive,
    min_events_for,
    screen,
    screen_lines,
    signal_timeframe,
)
from goldbot.specialists import SPECIALISTS, Specialist  # noqa: E402


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


def load_macro(path: str) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """The macro release for ctx["macro"], or None with the reason (recorded in the trial and shown in the report)."""
    if not path:
        return None, {"used": False, "detail": "off (no --macro)"}
    df = read_macro_files(Path(path))
    if df.empty:
        return None, {"used": False, "detail": f"off: no macro release at {path} (run the data-macro workflow; "
                                                f"`gh release download macro-v1 -D {path}`); ran without macro features"}
    series = sorted(df["series"].unique())
    return df, {"used": True, "series": series,
                "detail": f"on: {', '.join(series)}; value dates {df['value_date'].min():%Y-%m-%d} .. "
                          f"{df['value_date'].max():%Y-%m-%d}, joined as of available_utc"}


def _macro_line(info: dict[str, Any] | None) -> str:
    return f"- macro features: {info['detail']}" if info else "- macro features: off"


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
             _swap_line(m.get("swap")),
             f"- cost source: {m.get('cost_source', 'settings priors')}",
             _macro_line(m.get("macro")),
             _holdout_line(m.get("holdout")),
             f"- 1m bars with zero tick volume: {meta.get('zero_volume', float('nan')):.1%}"
             + (" (**volume features carry no information; re-pull the bars**)" if meta.get("zero_volume", 0) > 0.5 else ""),
             f"- runtime {meta['seconds']:.0f}s", ""]
    lines += screen_lines(m.get("screen"), bool(m.get("screen_skipped")))
    lines += _gates_lines(m.get("gates")) + _rule_gates_lines(m.get("rule_only_gates")) + _holdout_lines(m.get("holdout_verdict")) + _rule_only_lines(m.get("rule_only"))
    lines += _by_family_lines(m.get("by_family"))
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


def _swap_line(sw: dict[str, Any] | None) -> str:
    if not sw:
        return "- swap: not charged"
    days = ("Mon", "Tue", "Wed", "Thu", "Fri")
    out = (f"- swap: long {sw['long_usd_per_lot']:+.2f} / short {sw['short_usd_per_lot']:+.2f} USD per lot per night, "
           f"x3 on {days[int(sw['triple_weekday'])]}, rollover at {sw['server_tz']} midnight")
    if "mean_nights" in sw:
        out += (f"; {sw['share_held_overnight']:.0%} of trades held overnight, {sw['mean_nights']:.2f} nights and "
                f"{sw['mean_r']:+.3f} R per trade")
    return out


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


def _rule_gates_lines(g: dict[str, Any] | None) -> list[str]:
    """The same gates on every candidate without the model (informational; promotion still requires the model path)."""
    if not g:
        return []
    out = [f"### Design gates, {g['label']}: {'pass' if g['passed'] else 'fail'}", ""]
    out += [f"- {'pass' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}" for c in g["checks"]]
    return out + [""]


def _holdout_lines(v: dict[str, Any] | None) -> list[str]:
    if not v:
        return []
    return [f"### Held-out year: {'**PASS**' if v['passed'] else '**FAIL**'}", "",
            f"- rule: {v['rule']}",
            f"- {v['n']} model-filtered trades, mean R {v['mean_r']:.3f}, t-stat {v['t_stat']:.2f}", ""]


def _by_family_lines(b: dict[str, Any] | None) -> list[str]:
    """Pooled trials: each member family under the shared model, and pooled vs the family's own model (P5's test)."""
    if not b:
        return []
    out = ["### Pooled model by family", "",
           "| family | candidates | gross mean R (t) | OOF AUC | model n | model mean R | log-loss pooled | log-loss local |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for fam, e in b.items():
        g = (e.get("rule_only") or {}).get("gross") or {}
        mf = e.get("model_filtered") or {}
        ll = e.get("logloss") or {}
        out.append(f"| {fam} | {e['n_candidates']:,} | "
                   + (f"{g['mean_r']:+.3f} ({g['t_stat']:.2f})" if g.get("n") else "—") + f" | {_fmt(e.get('oof_auc'))} | "
                   f"{mf.get('n', 0)} | " + (f"{mf['mean_r']:+.3f}" if mf.get("n") else "—") + " | "
                   f"{_fmt(ll.get('pooled'))} | {_fmt(ll.get('local'))} |")
    return out + ["", "Log-loss on the same out-of-fold rows; the local model is the family's own (its declared inputs) "
                      "trained on its rows of the same folds. Lower is better.", ""]


def _rule_only_lines(r: dict[str, Any] | None) -> list[str]:
    if not r:
        return []
    out = ["### The rule alone (every candidate, no model)", "",
           "| costs | n | hit rate | mean ret | mean R | t-stat (R) |", "|---|---:|---:|---:|---:|---:|"]
    for name, label in (("gross", "gross (mid prices, no costs)"), ("net", "net (spread + slippage + commission + swap)")):
        s = r.get(name) or {}
        if not s.get("n"):
            out.append(f"| {label} | 0 | | | | |")
            continue
        out.append(f"| {label} | {s['n']} | {s['hit_rate']:.3f} | {s['mean_ret']:.5f} | {s['mean_r']:.3f} | {s['t_stat']:.2f} |")
    return out + ["", "Meta-labelling can only filter an edge the rule already has: a gross t-stat near zero means "
                      "there is nothing to filter.", ""]


def parse_variants(family: str, raw: str) -> list[dict[str, Any]]:
    """`--variants` JSON: a list of config overrides, one trial each ([{}] = the family's defaults); a string names one
    of the family's presets (e.g. '["slow"]' for tsmom, H-01) and stands for its overrides. Keys must be settings of
    the family (its defaults or optional settings, or timeframe / feature_seed); a typo must not silently run the
    defaults."""
    from goldbot.specialists.base import FEATURE_SEED_KEY, TIMEFRAME_KEY
    cls = SPECIALISTS[family]
    variants = json.loads(raw)
    if isinstance(variants, (dict, str)):
        variants = [variants]
    if isinstance(variants, list):
        for i, v in enumerate(variants):
            if isinstance(v, str):
                if v not in cls.presets:
                    raise SystemExit(f"unknown {family} preset {v!r}; presets: {sorted(cls.presets)}")
                variants[i] = dict(cls.presets[v])
    if not isinstance(variants, list) or not variants or not all(isinstance(v, dict) for v in variants):
        raise SystemExit("--variants must be a JSON list of objects or preset names, e.g. '[{}, {\"band_z\": 1.5}]'")
    allowed = set(cls.default_config) | set(cls.optional_config) | {TIMEFRAME_KEY, FEATURE_SEED_KEY}
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
    out = ["| trial | overrides | candidates | screen | OOF AUC | gates | model n | model hit | model PF | DSR |",
           "|---:|---|---:|---|---:|---|---:|---:|---:|---:|"]
    for r in rows:
        mf = r["metrics"].get("model_filtered") or {}
        scr = r["metrics"].get("screen")
        verdict = "inconclusive (event floor)" if is_inconclusive(scr) else "fail"
        scr_txt = "—" if not scr else ("pass" if scr["passed"] else (f"{verdict} (skipped)" if r["metrics"].get("screen_skipped") else f"**{verdict}**"))
        out.append(f"| {r['trial']} | `{json.dumps(r['overrides']) if r['overrides'] else 'defaults'}` | {r['n']:,} | {scr_txt} | "
                   f"{_fmt(r['metrics'].get('oof_auc'))} | {'pass' if (r['metrics'].get('gates') or {}).get('passed') else 'fail'} | {mf.get('n', 0)} | {_fmt(mf.get('hit_rate', float('nan')))} | "
                   f"{_fmt(mf.get('profit_factor', float('nan')))} | {_fmt(mf.get('dsr'))} |")
    return "\n".join(out)


class Job(NamedTuple):
    """One recorded trial: a family configuration, or a pooled model over every family on a timeframe."""
    family: str                      # registry family ("pooled_<tf>" for a pooled trial)
    config: dict[str, Any]
    overrides: dict[str, Any]
    specs: list[Specialist]
    timeframe: str
    pooled: bool


def make_jobs(args: argparse.Namespace, variants: list[dict[str, Any]]) -> list[Job]:
    if args.pooled:
        tf = args.pooled
        specs = [SPECIALISTS[f]() for f in pooled_members(tf)]
        cfg = {"timeframe": tf, "families": {s.family: s.config for s in specs}}
        return [Job(pooled_family(tf), cfg, {}, specs, tf, True)]
    jobs = []
    for v in variants:
        spec = SPECIALISTS[args.specialist](**v)
        jobs.append(Job(args.specialist, spec.config, v, [spec], spec.timeframe, False))
    return jobs


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
    ap.add_argument("--cost-table", default="",
                    help="measured cost table JSON (release costs-v1 asset costs_measured.json); default: the settings priors")
    ap.add_argument("--score-holdout", action="store_true",
                    help="score the configurations on the held-out window (once per configuration, ever)")
    ap.add_argument("--skip-screen", action="store_true",
                    help="fit the model even when the rule fails the primary-signal screen (the screen is still recorded)")
    ap.add_argument("--pooled", default="", choices=["", *sorted(POOLED_FEATURES)],
                    help="one meta-model over every family deciding on this timeframe (one trial, family pooled_<tf>)")
    ap.add_argument("--macro", default="",
                    help="folder or Parquet of the macro-v1 release: adds the point-in-time macro features")
    ap.add_argument("--cpcv", type=int, nargs="+", default=None, metavar="TRIAL",
                    help="combinatorial purged CV of registered trials (evidence on them, not a trial); PBO across several")
    _discovery_args(ap)
    args = ap.parse_args()
    if args.pooled and json.loads(args.variants) not in ([{}], {}):
        raise SystemExit("--pooled runs every member family at its defaults; --variants does not apply")
    variants = [{}] if args.pooled else parse_variants(args.specialist, args.variants)
    settings = load_settings()
    table = None
    if args.cost_table:
        table = load_research_cost_table(args.cost_table)
        if table is None:
            raise SystemExit(f"--cost-table {args.cost_table}: file not found")
    extra_cost, swap, cost_source = research_costs(settings, table)
    if args.extra_cost_usd is not None:
        extra_cost, cost_source = float(args.extra_cost_usd), cost_source + f"; --extra-cost-usd {args.extra_cost_usd}"
    holdout = settings.research.holdout_window()
    t0 = time.time()
    b1 = load_bars(Path(args.bars), args.from_year, args.to_year)      # fails fast, before any registry file exists
    macro, macro_info = load_macro(args.macro)
    print(_macro_line(macro_info)[2:], flush=True)
    reg = TrialRegistry(args.registry)
    with reg.locked():                    # budget check, runs and records as one step
        if args.cpcv:
            return _cpcv(args, b1, reg, extra_cost, swap, holdout, cost_source, macro)
        if args.discover:
            return _discover(args, variants, b1, reg, settings, extra_cost, swap, holdout, cost_source, macro)
        return _run(args, make_jobs(args, variants), extra_cost, holdout, b1, reg, t0, settings, swap=swap,
                    cost_source=cost_source, macro=macro, macro_info=macro_info)


def _run(args: argparse.Namespace, jobs: list[Job], extra_cost: float, holdout: tuple[pd.Timestamp, pd.Timestamp] | None,
         b1: pd.DataFrame, reg: TrialRegistry, t0: float, settings: Any, *, swap: SwapSpec, cost_source: str,
         macro: pd.DataFrame | None = None, macro_info: dict[str, Any] | None = None) -> int:
    feat_ctx = {"macro": macro} if macro is not None else None
    if args.score_holdout:
        if holdout is None:
            raise SystemExit("--score-holdout: no holdout window configured (research.holdout_from/holdout_to)")
        for job in jobs:
            label = json.dumps(job.overrides) if job.overrides else "defaults"
            if reg.holdout_scored(job.family, job.config):
                raise SystemExit(f"{job.family} {label} was already scored on the holdout; "
                                 "that result is final for this configuration")
            if not reg.passed_gates(job.family, job.config):
                raise SystemExit(f"{job.family} {label} has no research trial that passed the design's gates; "
                                 "only a passing configuration is scored on the holdout")
    try:                                  # a holdout scoring or a screen is a look at the data too: charged like any trial
        quarter = reg.check_budget(len(jobs), settings.research.trial_budget_quarter)
    except TrialBudgetExceeded as exc:
        raise SystemExit(str(exc)) from None
    years_span = (b1["ts_utc"].iloc[-1] - b1["ts_utc"].iloc[0]).days / 365.25
    zero_volume = float((b1["tick_count"].astype(float) <= 0).mean())
    print(f"1m bars: {len(b1):,} ({b1['ts_utc'].iloc[0]:%Y-%m-%d} -> {b1['ts_utc'].iloc[-1]:%Y-%m-%d}), "
          f"zero tick volume in {zero_volume:.1%}", flush=True)
    frames: dict[str, tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any], tuple[pd.DataFrame, pd.DataFrame]]] = {}
    sections, rows = [], []
    title = jobs[0].family
    for job in jobs:
        tf = job.timeframe
        if tf not in frames:                       # bars, context, decision frame and lookahead check once per timeframe
            b_dec = resample_bars(b1, tf)
            context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
            leak = lookahead_check(b_dec, context, ctx=feat_ctx)
            frames[tf] = (b_dec, context, leak, build_decision_frame(b_dec.reset_index(drop=True), context, ctx=feat_ctx))
            sizes = "  ".join(f"{k} {len(v):,}" for k, v in context.items())
            print(f"{tf} {len(b_dec):,}  {sizes}; lookahead check: {len(leak['lookahead_columns'])} of "
                  f"{leak['columns_checked']} columns differ [{time.time() - t0:.0f}s]", flush=True)
        b_dec, context, leak, frame = frames[tf]
        # + every discovery's K_eff (survey 4b), charged to EVERY later trial, survivor or not: the conservative
        # default, recorded as an owner-acknowledged choice in docs/research/preregistration-2027Q1.md
        n_trials = reg.n_trials_effective + 1
        preps = [prepare(s, b_dec, context, extra_cost_usd=extra_cost, holdout=holdout, score_holdout=args.score_holdout,
                         frame=frame, swap=swap) for s in job.specs]
        # event floor: research.screen_min_events, or the daily-signal override (owner ruling A), set before the run
        floor = min_events_for(tf if job.pooled else signal_timeframe(job.specs[0]), settings.research)
        scr = None if args.score_holdout else screen(preps if job.pooled else preps[0], floor)
        skipped = bool(scr is not None and not scr["passed"] and args.skip_screen)
        agent_id = pool_identity(tf, preps).agent_id if job.pooled else job.specs[0].agent_id
        version = preps[0].feature_version
        meta = {"specialist": job.family, "from_year": int(b1["ts_utc"].iloc[0].year),
                "to_year": int(b1["ts_utc"].iloc[-1].year), "n_1m": len(b1), "n_dec": len(b_dec), "tf": tf,
                "zero_volume": zero_volume}
        common = {"lookahead": leak, "bars_from": str(b1["ts_utc"].iloc[0]), "bars_to": str(b1["ts_utc"].iloc[-1]),
                  "screen": scr, "screen_skipped": skipped, "cost_source": cost_source, "macro": macro_info}
        rationale = args.rationale + (f" | overrides {json.dumps(job.overrides, sort_keys=True)}" if job.overrides else "")
        if scr is not None and not scr["passed"] and not args.skip_screen:
            n = int(sum(len(p.labels) for p in preps))
            metrics: dict[str, Any] = {"n_candidates": n, "rule_only": scr["rule_only"], "swap": swap.model_dump(),
                                       "trades_per_year": n / years_span if years_span > 0 else 0.0, **common}
            row = reg.record(agent_id=agent_id, family=job.family, config=job.config, feature_version=version,
                             rationale=rationale, results=metrics, status="screened", budget_quarter=quarter)
            print(f"{json.dumps(job.overrides) or 'defaults'}: screen {scr['verdict']} ({n} events) [{time.time() - t0:.0f}s]", flush=True)
            text = render_screen_failed(scr, leak, {**meta, "trial": row["trial"], "seconds": time.time() - t0, "n": n,
                                                    "swap": swap.model_dump(), "cost_source": cost_source,
                                                    "macro": macro_info})
        else:
            if job.pooled:
                res = run_pool(preps, tf, n_trials=n_trials, extra_cost_usd=extra_cost, holdout=holdout,
                               score_holdout=args.score_holdout)
            else:
                res = evaluate(preps[0], n_trials=n_trials, extra_cost_usd=extra_cost, holdout=holdout,
                               score_holdout=args.score_holdout)
            n = res.n_candidates
            print(f"{json.dumps(job.overrides) or 'defaults'}: candidates {n}  folds {res.n_folds}  "
                  f"[{time.time() - t0:.0f}s]", flush=True)
            metrics = {**res.metrics, "trades_per_year": n / years_span if years_span > 0 else 0.0, **common}
            row = reg.record(agent_id=res.agent_id, family=job.family, config=job.config, feature_version=res.feature_version,
                             rationale=rationale, results=metrics, status="holdout" if args.score_holdout else "evaluated",
                             budget_quarter=quarter)
            res.metrics = {**res.metrics, "screen": scr, "screen_skipped": skipped, "cost_source": cost_source,
                           "macro": macro_info}
            years = per_year(res.oof, res.metrics.get("threshold"))
            text = render_report(res, years, leak, {**meta, "trial": row["trial"], "seconds": time.time() - t0})
        if job.overrides:
            text = text.replace("\n\n", f"\n\n- config overrides: `{json.dumps(job.overrides, sort_keys=True)}`\n", 1)
        sections.append(text)
        rows.append({"trial": row["trial"], "overrides": job.overrides, "n": n, "metrics": metrics})
        print(json.dumps({"trial": row["trial"], "agent_id": agent_id, "status": row["status"]}), flush=True)
    head = [] if len(rows) == 1 else [f"## {title}: {len(rows)} variants", "", summary_table(rows), "",
                                      "Each variant is one recorded trial (a configuration that fails the screen too); "
                                      "the deflated SR of every later trial counts them all.", ""]
    report = "\n".join(head) + "\n\n".join(sections)
    Path(args.report).write_text(report)
    print(report)
    return 0


# ---------------------------------------------------------------------------------------------- feature discovery
def _discovery_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--discover", action="store_true",
                    help="ONE pre-registered feature-discovery trial (goldbot/research/discovery.py) on --specialist")
    ap.add_argument("--families", nargs="?", const="family", default="column", choices=["column", "feature", "family"],
                    help="--discover: group features by registry family (bare flag), by producing feature, or not")
    ap.add_argument("--discover-config", default="{}", help="--discover: JSON overrides of DiscoveryConfig")


def _discover(args: argparse.Namespace, variants: list[dict[str, Any]], b1: pd.DataFrame, reg: TrialRegistry,
              settings: Any, extra_cost: float, swap: SwapSpec, holdout: tuple[pd.Timestamp, pd.Timestamp] | None,
              cost_source: str, macro: pd.DataFrame | None) -> int:
    from goldbot.research.discovery import DiscoveryConfig, discovery_pass
    if args.pooled or args.score_holdout or len(variants) != 1:
        raise SystemExit("--discover runs one specialist configuration (one --variants entry), no --pooled or --score-holdout")
    try:
        cfg = DiscoveryConfig(**json.loads(args.discover_config))
        row, text = discovery_pass(SPECIALISTS[args.specialist](**variants[0]), b1, reg,
                                   budget_cap=settings.research.trial_budget_quarter, cfg=cfg, group_mode=args.families,
                                   extra_cost_usd=extra_cost, swap=swap, holdout=holdout,
                                   ctx={"macro": macro} if macro is not None else None, rationale=args.rationale,
                                   cost_source=cost_source)
    except (TrialBudgetExceeded, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    Path(args.report).write_text(text)
    print(text)
    print(json.dumps({"trial": row["trial"], "agent_id": row["agent_id"], "status": row["status"]}), flush=True)
    return 0


# ---------------------------------------------------------------------------------------------- CPCV (M16)
def _cpcv(args: argparse.Namespace, b1: pd.DataFrame, reg: TrialRegistry, extra_cost: float, swap: SwapSpec,
          holdout: tuple[pd.Timestamp, pd.Timestamp] | None, cost_source: str, macro: pd.DataFrame | None) -> int:
    """Combinatorial purged CV of registered trials (goldbot/research/cpcv.py). Not a trial: no budget check, no
    registry row; the result is attached to each trial as evidence. Several trials: PBO across them."""
    from goldbot.research import cpcv
    rows = []
    for n in dict.fromkeys(args.cpcv):
        row = reg.row(n)
        if row is None:
            raise SystemExit(f"--cpcv: no trial #{n} in {reg.path}")
        why = cpcv.cpcv_eligible(row)
        if why:
            raise SystemExit(f"--cpcv: {why}")
        rows.append(row)
    tfs = {cpcv.trial_timeframe(r) for r in rows}
    if len(tfs) != 1:
        raise SystemExit(f"--cpcv: trials compared together must share a decision timeframe (got {sorted(tfs)})")
    tf = tfs.pop()
    feat_ctx = {"macro": macro} if macro is not None else None
    b_dec = resample_bars(b1, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    frame = build_decision_frame(b_dec, context, ctx=feat_ctx)
    out = cpcv.cpcv_trials(rows, b_dec, context, extra_cost_usd=extra_cost, holdout=holdout, swap=swap, frame=frame,
                           ctx=feat_ctx)
    version = frame[1].attrs["feature_version"]
    lines = [f"## Combinatorial purged CV: trials {', '.join(str(r['trial']) for r in rows)} ({tf})", "",
             f"- research window {out['edges'][0][:10]} .. {out['edges'][-1][:10]} (holdout excluded), "
             f"{cpcv.N_GROUPS} groups of equal duration",
             f"- costs: bar spread in every label plus {extra_cost:.2f} $/oz round trip; cost source: {cost_source}",
             "- evidence attached to each trial (not a trial: no budget slot, the deflated-Sharpe count is unchanged)"]
    stale = [str(r["trial"]) for r in rows if r.get("feature_version") and r["feature_version"] != version]
    if stale:
        lines.append(f"- **feature version differs from the trial's** for {', '.join(stale)} (data or --macro differ): "
                     "read the paths with care")
    lines += ["", *cpcv.report_lines(out["per_trial"], out["pbo"])]
    for r in rows:
        res = out["per_trial"][int(r["trial"])]
        reg.attach_evidence(int(r["trial"]), cpcv.EVIDENCE_KIND,
                            cpcv.evidence_payload(res, out["pbo"], f"research_pass --cpcv {' '.join(map(str, args.cpcv))}"))
    text = "\n".join(lines)
    Path(args.report).write_text(text)
    print(text)
    return 0


def render_screen_failed(scr: dict[str, Any], leak: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [f"## {meta['specialist']} primary-signal screen on real bars ({meta['from_year']}-{meta['to_year']})", "",
             f"- bars: {meta['n_1m']:,} 1m -> {meta['n_dec']:,} {meta['tf']} decision bars",
             f"- events {meta['n']:,} (one position at a time, holdout excluded), registry trial #{meta['trial']} "
             f"(a screen counts as a trial)",
             f"- lookahead check: {'clean' if not leak['lookahead_columns'] else str(len(leak['lookahead_columns'])) + ' columns use future data'}",
             _swap_line(meta.get("swap")),
             f"- cost source: {meta.get('cost_source', 'settings priors')}",
             _macro_line(meta.get("macro")),
             f"- runtime {meta['seconds']:.0f}s", ""]
    lines += screen_lines(scr) + _rule_only_lines(scr["rule_only"])
    if is_inconclusive(scr):
        lines += [f"Screen inconclusive (event floor): positive and significant on {scr['n']:,} events, fewer than the "
                  f"{scr['min_events']:,} the floor needs. No model was fitted. This recorded, charged trial cannot "
                  "retire the hypothesis; it is not evidence for promotion either."]
    else:
        lines += ["Screen failed: no model was fitted. The rule is retired from model research (it may still serve as a "
                  "feature); `--skip-screen` fits a model anyway and is recorded as such."]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
