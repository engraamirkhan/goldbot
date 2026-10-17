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
  beyond it. The trials held for the pre-registered queue (research.reserved_trials_quarter, director.reserved_trials)
  are spent only by a run matching a pending `preregistered` row (family and config hash), which it is linked to;
  any other run (ad-hoc variant, screen, holdout scoring) must fit in budget - used - reserved
  (TrialRegistry.check_budget_reserved). The holdout window (research.holdout_from/to) is excluded unless
  `--score-holdout`, which scores a configuration on it exactly once;
* BACKLOG 50: before any look, the data-v1 sha256 digests a pending queued row of the run's family and agent pins in
  its plan are verified against the research-window files in --bars (a refresh or re-pull since the row was written
  is refused, naming the file and both digests), and a run with a pending queued row of its family and agent but
  another config hash is refused instead of falling back to the open budget. `--discover` cuts the 1m bars at
  research.holdout_from before anything is built, as `--h16` does.

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

Positioning (opt-in): `--positioning DIR` (the release positioning-v1, from the data-positioning workflow) adds the
point-in-time COT and GLD columns (goldbot/features/positioning.py: managed-money net % of OI, its 52-week z-score and
4-week change, GLD tonnes 5/20-day % change) to the candidate frame and the leakage check, under the same rules as
macro (the feature version includes them). Not available with --discover.

US calendar (opt-in): `--us-calendar DIR` (the release calendar-v1, from the data-us-calendar workflow) loads the
point-in-time tier-1/tier-2 schedule into ctx["us_calendar"] (what day_type's tier-1 columns read). The calendar's own
feature families (goldbot/features/us_calendar.py `tier1_events`/`tier2_events`: minutes to the next and since the
last event of each tier, capped at 7 days, and the tier's event count on the New York day) are added only with
`--us-calendar-features`, so a run can use the calendar through day_type alone (H-17: "HAR column only"). Not
available with --discover or --cpcv. The report line says how many rows are rule-derived (ISM);
`--us-calendar-exclude-rule` leaves them out.

Opportunity-day features (opt-in, BACKLOG 30a): `--day-type` adds the `day_type` family (goldbot/features/day_type.py:
H-16's HAR session-window range forecast with its monthly expanding refit and band cuts, GVZ over realised volatility
with a staleness flag (with --macro), prior-day range, NR4/NR7, inside day, reopen gap, the DST-aware noise ratio and
the tier-1 columns) to the candidate frame and the leakage check. It requires --us-calendar (so the tier-1 columns
are never silently NaN), --from-year 2010 or earlier (the daily inputs warm up from the start of the data), and is
refused with --discover or --cpcv. Its inputs (GVZ present, calendar present, refit schedule, min fit rows) and family
version are recorded on the trial row (`day_type`).

H-16 (E-4, goldbot/research/h16.py): `--h16` runs H-16's pre-registered range test (the opportunity score against the
HAR-only forecast) once and records it as ONE trial (family h16_opportunity_score). It runs only against the one unused
H-16 pre-registration, reading the frozen first out-of-sample month and evaluable-day count from its config, and is
refused, with nothing recorded, when H-16 already ran, when the pre-registered feature version is not day_type's
version now, or when the rule on the data gives another month. The 1m bars are cut at research.holdout_from before
anything is resampled or built; it needs --us-calendar, --macro with GVZ and --from-year 2010 or earlier, and is refused
with --discover, --cpcv, --score-holdout, --pooled, --day-type (it builds day_type itself), --positioning,
--us-calendar-features or --variants.

Combinatorial purged CV (M16, goldbot/research/cpcv.py): `--cpcv <trial#> [<trial#> ...]` re-evaluates registered
walk-forward trials on 6 time groups of the research window (15 purged splits, 5 backtest paths) and, for several
trials, the probability of backtest overfitting across them. It is not a trial: no budget slot, no registry row; the
result is attached to each trial as evidence (`<registry>.evidence.jsonl`). CPCV and PBO can only veto a config that
passed the gates (docs/decisions/0003-cpcv-and-pbo-can-only-veto.md), so a trial that failed them is refused unless
`--diagnostic`: the report is then labelled "diagnostic, not evidence" and nothing is attached to the failed trials.

  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report report.md \
      [--specialist session_open | --pooled 15m] [--from-year 2010] [--to-year 2026] [--rationale "..."] \
      [--variants '[{}]'] [--skip-screen] [--score-holdout] [--cost-table costs_measured.json] [--macro macro/] [--positioning positioning/]
      [--us-calendar calendar/ [--us-calendar-features]] [--day-type]
  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report cpcv.md --cpcv 12 [13 14] [--diagnostic]
  python scripts/research_pass.py --bars raw/ --registry registry.jsonl --report h16.md --h16 \
      --macro macro/ --us-calendar calendar/
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
from goldbot.data.release import (  # noqa: E402
    bar_digest_refusal,
    read_bar_files,
    read_macro_files,
    read_positioning_files,
    read_us_calendar_files,
)
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.execution.costs import SwapSpec, load_research_cost_table, research_costs  # noqa: E402
from goldbot.features import day_type as day_type_features  # noqa: E402
from goldbot.features import positioning as positioning_features  # noqa: E402
from goldbot.features import us_calendar as us_calendar_features  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.features.registry import feature_version  # noqa: E402
from goldbot.research import h16  # noqa: E402
from goldbot.research.director import cited_rule, queued_rows_for  # noqa: E402
from goldbot.research.gates import DSR_BAR  # noqa: E402
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DaySummedDsr  # noqa: E402
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
    research_feature_names,
    run_pool,
)
from goldbot.research.registry import (  # noqa: E402
    TrialBudgetExceeded,
    TrialRegistry,
    gate_pass_counts,
    quarter_of,
)
from goldbot.research.screen import (  # noqa: E402
    is_inconclusive,
    rule_only_split,
    rule_only_split_lines,
    screen,
    screen_floors,
    screen_lines,
    signal_timeframe,
)
from goldbot.specialists import SPECIALISTS, Specialist  # noqa: E402


def load_bars(folder: Path, from_year: int, to_year: int) -> pd.DataFrame:
    """Concatenate the yearly 1m files; bars with an error-level data-quality flag are dropped (`read_bar_files`)."""
    try:
        return read_bar_files(folder, from_year, to_year)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from None


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


def load_positioning(path: str) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """The positioning release for ctx["positioning"], or None with the reason (recorded in the trial and reported)."""
    if not path:
        return None, {"used": False, "detail": "off (no --positioning)"}
    df = read_positioning_files(Path(path))
    if df.empty:
        return None, {"used": False, "detail": f"off: no positioning release at {path} (run the data-positioning "
                                                f"workflow; `gh release download positioning-v1 -D {path}`)"}
    series = sorted(df["series"].unique())
    return df, {"used": True, "series": series,
                "detail": f"on: {', '.join(series)}; value dates {df['value_date'].min():%Y-%m-%d} .. "
                          f"{df['value_date'].max():%Y-%m-%d}, joined as of available_utc"}


def _positioning_line(info: dict[str, Any] | None) -> str:
    return f"- positioning features: {info['detail']}" if info else "- positioning features: off"


def load_us_calendar(path: str, exclude_rule: bool = False) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """The US calendar release for ctx["us_calendar"], or None with the reason (recorded in the trial and reported).
    The detail says how many rows are rule-derived (ISM, source `rule`); `exclude_rule` leaves them out."""
    if not path:
        return None, {"used": False, "detail": "off (no --us-calendar)"}
    df = read_us_calendar_files(Path(path))
    if df.empty:
        return None, {"used": False, "detail": f"off: no calendar release at {path} (run the data-us-calendar "
                                                f"workflow; `gh release download calendar-v1 -D {path}`)"}
    n_rule = int((df["source"] == "rule").sum())
    if exclude_rule:
        df = df[df["source"] != "rule"].reset_index(drop=True)
    ev = pd.DatetimeIndex(df["event_utc"])
    rule = "rule rows excluded" if exclude_rule else f"{n_rule:,} rule-derived (ISM) rows"
    return df, {"used": True, "event_types": sorted(df["event_type"].unique()), "rule_rows_excluded": exclude_rule,
                "detail": f"on: {df['event_type'].nunique()} event types ({rule}), events {ev.min():%Y-%m-%d} .. "
                          f"{ev.max():%Y-%m-%d}, joined as of available_utc"}


def _us_calendar_line(info: dict[str, Any] | None) -> str:
    return f"- US calendar features: {info['detail']}" if info else "- US calendar features: off"


DAY_TYPE_MAX_FROM_YEAR = 2010     # day_type's daily inputs warm up from the first data year (H-16: OOS from ~2012-04)


def day_type_info(on: bool, macro: pd.DataFrame | None = None,
                  us_calendar: pd.DataFrame | None = None) -> dict[str, Any]:
    """What the trial row records about the opt-in day_type family: its own version and its inputs (review M4)."""
    if not on:
        return {"used": False, "detail": "off (no --day-type)"}
    gvz = macro is not None and len(macro) > 0 and bool((macro["series"] == "gold_vix").any())
    cal = us_calendar is not None and len(us_calendar) > 0
    return {"used": True, "family": day_type_features.FAMILY, "version": day_type_features.VERSION,
            "gvz": gvz, "us_calendar": cal, "refit_schedule": day_type_features.REFIT_SCHEDULE,
            "min_fit_rows": day_type_features.MIN_FIT_ROWS,
            "detail": f"on: family {day_type_features.FAMILY} v{day_type_features.VERSION} "
                      f"({len(day_type_features.COLUMNS)} columns; GVZ {'on' if gvz else 'off'}, calendar "
                      f"{'on' if cal else 'off'}; HAR refit {day_type_features.REFIT_SCHEDULE}, "
                      f">= {day_type_features.MIN_FIT_ROWS} rows)"}


def _day_type_line(info: dict[str, Any] | None) -> str:
    return f"- day_type features: {info['detail']}" if info else "- day_type features: off"


def day_type_refusal(args: argparse.Namespace, us_calendar: pd.DataFrame | None) -> str | None:
    """Why `--day-type` cannot run with these arguments (None = it can)."""
    if not getattr(args, "day_type", False):
        return None
    if getattr(args, "discover", False) or getattr(args, "cpcv", None):
        return "--day-type is not wired into --discover or --cpcv; run it without them"
    if us_calendar is None or len(us_calendar) == 0:
        return "--day-type needs --us-calendar (its tier-1 columns would otherwise be NaN on every row)"
    if int(args.from_year) > DAY_TYPE_MAX_FROM_YEAR:
        return (f"--day-type needs --from-year {DAY_TYPE_MAX_FROM_YEAR} or earlier: the HAR fit and the daily inputs "
                "are computed from the start of the bars, so a later start moves the first out-of-sample month")
    return None


def h16_refusal(args: argparse.Namespace, macro: pd.DataFrame | None, us_calendar: pd.DataFrame | None) -> str | None:
    """Why `--h16` cannot run with these arguments and inputs (None = it can)."""
    if not getattr(args, "h16", False):
        return None
    for flag, on in (("--discover", getattr(args, "discover", False)), ("--cpcv", getattr(args, "cpcv", None)),
                     ("--score-holdout", getattr(args, "score_holdout", False)), ("--pooled", getattr(args, "pooled", "")),
                     ("--day-type", getattr(args, "day_type", False)), ("--positioning", getattr(args, "positioning", "")),
                     ("--us-calendar-features", getattr(args, "us_calendar_features", False)),
                     ("--variants", json.loads(getattr(args, "variants", "[{}]")) not in ([{}], {}))):
        if on:
            return f"--h16 runs H-16's one pre-registered configuration; {flag} does not apply to it"
    if us_calendar is None or len(us_calendar) == 0:
        return "--h16 needs --us-calendar (the full score's tier-1 count is pre-registered)"
    if macro is None or len(macro) == 0 or not bool((macro["series"] == "gold_vix").any()):
        return "--h16 needs --macro with GVZ (gold_vix): the full score's GVZ / realised-volatility ratio is pre-registered"
    if int(args.from_year) > DAY_TYPE_MAX_FROM_YEAR:
        return (f"--h16 needs --from-year {DAY_TYPE_MAX_FROM_YEAR} or earlier: the daily inputs warm up from the first "
                "bar, so a later start moves the first out-of-sample month")
    return None


def _h16(args: argparse.Namespace, b1: pd.DataFrame, reg: TrialRegistry, settings: Any, macro: pd.DataFrame | None,
         us_calendar: pd.DataFrame | None, macro_info: dict[str, Any], us_calendar_info: dict[str, Any]) -> int:
    """H-16 (E-4): cut the 1m bars at research.holdout_from, resample to 15m mid, run and record ONE trial
    (`h16.run_trial`, which takes the registry lock and checks the budget) and write the report."""
    holdout_from = settings.research.holdout_from
    if holdout_from is None:
        raise SystemExit("--h16: no research.holdout_from configured; H-16 is cut at the holdout before any feature")
    b1 = h16.cut_before_holdout(b1, holdout_from)
    bars = mid(resample_bars(b1, h16.TIMEFRAME))
    info = {"macro": macro_info, "us_calendar": us_calendar_info,
            "day_type": day_type_info(True, macro, us_calendar)}
    try:
        row, res = h16.run_trial(reg, bars, macro, us_calendar, holdout_from=holdout_from,
                                 cap=settings.research.trial_budget_quarter,
                                 reserved=settings.research.reserved_for(quarter_of()),
                                 retired={e.family for e in settings.research.retired_families},
                                 rationale=args.rationale, min_rows=day_type_features.MIN_FIT_ROWS, input_info=info)
    except (TrialBudgetExceeded, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    text = "\n".join(h16.report_lines(res, int(row["trial"])))
    Path(args.report).write_text(text)
    print(text)
    print(json.dumps({"trial": row["trial"], "agent_id": h16.AGENT_ID, "status": row["status"],
                      "verdict": res.verdict}), flush=True)
    return 0


def queued_digest_refusal(rows: list[dict[str, Any]], quarter: str, targets: list[tuple[str, str | None]],
                          bars: str, from_year: int, to_year: int, holdout_from: Any) -> str | None:
    """Why this run may not go on, or None: a pending queued row of `quarter` that the run could match (each target's
    family and agent, None: the family alone; `director.queued_rows_for`) pins data-v1 sha256 digests (BACKLOG 50) and
    the research-window files the run reads from `bars` (years `from_year`..min(`to_year`, holdout year)) are not
    exactly those (a refresh or re-pull after the write, another --from-year). A row pins the files from the year of
    its bars_from (`plan.data_years`), so its run reads from that year. Checked before any look. A matching row that
    pins no digests is refused too (quant review 2026-10-16: fail closed): the run could not show it reads the
    pre-registered data."""
    last = min(int(to_year), holdout_from.year) if holdout_from is not None else int(to_year)
    for family, agent in targets:
        for row in queued_rows_for(rows, quarter, family, agent):
            pinned = (row.get("plan") or {}).get("data_digests")
            spec_id = (row.get("plan") or {}).get("spec_id")
            if not pinned:
                return (f"{family}: the pending queued row #{row.get('trial')}{f' ({spec_id})' if spec_id else ''} "
                        f"pins no data-v1 digests (plan.data_digests), so this run cannot show it reads the "
                        f"pre-registered data; withdraw the row and re-write it with the preregister-quarter workflow")
            why = bar_digest_refusal(pinned, Path(bars), int(from_year), last)
            if why:
                years = (row.get("plan") or {}).get("data_years")
                span = f"; the row pins years {years[0]}-{years[1]} (--from-year {years[0]})" if years else ""
                return (f"{family}: the pending queued row #{row.get('trial')}{f' ({spec_id})' if spec_id else ''} "
                        f"pins data-v1 sha256 digests that the bars in {bars} do not match: {why}{span}. The run would "
                        f"not be the pre-registered one; restore the pinned files, or withdraw the row first")
    return None


def feature_ctx(macro: pd.DataFrame | None, positioning: pd.DataFrame | None,
                us_calendar: pd.DataFrame | None = None, *, calendar_features: bool = False,
                day_type: bool = False) -> tuple[dict | None, list[str] | None]:
    """ctx for the features and the explicit feature list: None (the pipeline's default, with macro when passed)
    unless an opt-in family is on, which registers and appends it. A calendar always goes into ctx["us_calendar"]
    (day_type reads it); the calendar's own families join only with `calendar_features`."""
    ctx = {k: v for k, v in (("macro", macro), ("positioning", positioning), ("us_calendar", us_calendar))
           if v is not None}
    cal_on = calendar_features and us_calendar is not None
    if positioning is None and not cal_on and not day_type:
        return ctx or None, None
    extra = [positioning_features.enable()] if positioning is not None else []
    if cal_on:
        extra += us_calendar_features.enable()
    if day_type:
        extra.append(day_type_features.enable())
    return ctx, [*research_feature_names(ctx), *extra]


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
             _positioning_line(m.get("positioning")),
             _us_calendar_line(m.get("us_calendar")),
             _day_type_line(m.get("day_type")),
             _holdout_line(m.get("holdout")),
             f"- 1m bars with zero tick volume: {meta.get('zero_volume', float('nan')):.1%}"
             + (" (**volume features carry no information; re-pull the bars**)" if meta.get("zero_volume", 0) > 0.5 else ""),
             f"- runtime {meta['seconds']:.0f}s", ""]
    lines += screen_lines(m.get("screen"), bool(m.get("screen_skipped")))
    lines += _gates_lines(m.get("gates")) + day_summed_lines(m.get("model_filtered"))
    lines += _rule_gates_lines(m.get("rule_only_gates")) + _holdout_lines(m.get("holdout_verdict")) + _rule_only_lines(m.get("rule_only"))
    lines += rule_only_split_lines(m.get("rule_only_split"))
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


def day_summed_lines(mf: dict[str, Any] | None) -> list[str]:
    """E-6: the day-summed DSR the discovery survivors' reading rule needs (pre-registration 2027Q1 O-5/O-6: "the
    day-summed DSR is also >= 0.95"), beside the per-trade DSR and both effective ts, on this run's trial count. The
    evaluator reads it on today's count (`gates.day_summed_check`); for other trials it is information only."""
    if not mf:
        return []
    raw = mf.get("day_summed")
    head = "### Day-summed DSR (reading rule O-5/O-6, discovery survivors; E-6)"
    if raw is None:
        return [head, "", f"- n/a: fewer than {MIN_TRADES_FOR_DSR} model-filtered trades or trading days", ""]
    d = DaySummedDsr(**raw)
    fall = 1 - d.t_day_summed / d.t_per_trade if d.t_per_trade else 0.0
    return [head, "",
            f"- day-summed {d.dsr_day_summed:.3f} on {d.n_days} trading days, per-trade {d.dsr_per_trade:.3f} on "
            f"{d.n_trades} trades, K = {d.n_trials} at this run (need {DSR_BAR}, re-read on the registry's count today)",
            f"- effective t {d.t_day_summed:.2f} day-summed vs {d.t_per_trade:.2f} per trade ({fall:.1%} lower)", ""]


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
    defaults. One resolver with the pre-registration writer (`preregistration.resolve_variant`), so a queued row and
    the run hash the same config."""
    from goldbot.research.preregistration import resolve_variant
    variants = json.loads(raw)
    if isinstance(variants, (dict, str)):
        variants = [variants]
    if not isinstance(variants, list) or not variants or not all(isinstance(v, (dict, str)) for v in variants):
        raise SystemExit("--variants must be a JSON list of objects or preset names, e.g. '[{}, {\"band_z\": 1.5}]'")
    try:
        return [resolve_variant(family, v) for v in variants]
    except ValueError as exc:                # unknown preset or setting, or e.g. a timeframe the family cannot run on
        raise SystemExit(str(exc)) from None


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
    ap.add_argument("--rule-ref", default=None,
                    help="the pre-registered rule this run follows (e.g. preregistration-2027Q1.md#reserve); required "
                         "in a quarter under the E-5 cap unless the run matches a queued, slot or rule_ref row")
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
    ap.add_argument("--diagnostic", action="store_true",
                    help="with --cpcv: allow trials that failed the gates; output is diagnostic, not evidence (ADR 0003)")
    ap.add_argument("--positioning", default="",
                    help="folder or Parquet of the positioning-v1 release: adds the opt-in COT/GLD features")
    ap.add_argument("--us-calendar-exclude-rule", action="store_true",
                    help="with --us-calendar: leave out the rule-derived (ISM) rows")
    ap.add_argument("--us-calendar", default="",
                    help="folder or Parquet of the calendar-v1 release: the point-in-time schedule in ctx")
    ap.add_argument("--us-calendar-features", action="store_true",
                    help="with --us-calendar: adds the opt-in tier1_events/tier2_events families")
    ap.add_argument("--day-type", action="store_true",
                    help="adds the opt-in day_type opportunity features (BACKLOG 30a; needs --us-calendar)")
    ap.add_argument("--h16", action="store_true",
                    help="H-16's pre-registered range test, one trial (E-4; needs its pre-registration, --macro, --us-calendar)")
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
    positioning, positioning_info = load_positioning(args.positioning)
    print(_positioning_line(positioning_info)[2:], flush=True)
    if args.discover and positioning is not None:
        raise SystemExit("--positioning is not wired into --discover yet; run it without --discover")
    us_calendar, us_calendar_info = load_us_calendar(args.us_calendar, args.us_calendar_exclude_rule)
    print(_us_calendar_line(us_calendar_info)[2:], flush=True)
    if args.h16:
        refusal = h16_refusal(args, macro, us_calendar)
        if refusal:
            raise SystemExit(refusal)
        return _h16(args, b1, TrialRegistry(args.registry), settings, macro, us_calendar, macro_info, us_calendar_info)
    refusal = day_type_refusal(args, us_calendar)
    if refusal:
        raise SystemExit(refusal)
    if (args.discover or args.cpcv) and us_calendar is not None:
        raise SystemExit("--us-calendar is not wired into --discover or --cpcv yet; run it without them")
    if args.us_calendar_features and us_calendar is None:
        raise SystemExit("--us-calendar-features needs --us-calendar")
    reg = TrialRegistry(args.registry)
    with reg.locked():                    # budget check, runs and records as one step
        if args.cpcv:
            return _cpcv(args, b1, reg, extra_cost, swap, holdout, cost_source, macro)
        if args.discover:
            return _discover(args, variants, b1, reg, settings, extra_cost, swap, holdout, cost_source, macro)
        return _run(args, make_jobs(args, variants), extra_cost, holdout, b1, reg, t0, settings, swap=swap,
                    cost_source=cost_source, macro=macro, macro_info=macro_info, positioning=positioning,
                    positioning_info=positioning_info, us_calendar=us_calendar, us_calendar_info=us_calendar_info)


def _run(args: argparse.Namespace, jobs: list[Job], extra_cost: float, holdout: tuple[pd.Timestamp, pd.Timestamp] | None,
         b1: pd.DataFrame, reg: TrialRegistry, t0: float, settings: Any, *, swap: SwapSpec, cost_source: str,
         macro: pd.DataFrame | None = None, macro_info: dict[str, Any] | None = None,
         positioning: pd.DataFrame | None = None, positioning_info: dict[str, Any] | None = None,
         us_calendar: pd.DataFrame | None = None, us_calendar_info: dict[str, Any] | None = None) -> int:
    day_type = bool(getattr(args, "day_type", False))
    feat_ctx, feat_names = feature_ctx(macro, positioning, us_calendar,
                                       calendar_features=bool(getattr(args, "us_calendar_features", False)),
                                       day_type=day_type)
    dt_info = day_type_info(day_type, macro, us_calendar)
    if args.score_holdout:
        if holdout is None:
            raise SystemExit("--score-holdout: no holdout window configured (research.holdout_from/holdout_to)")
        fv = feature_version(feat_names or research_feature_names(feat_ctx))
        for job in jobs:
            label = json.dumps(job.overrides) if job.overrides else "defaults"
            if reg.holdout_scored(job.family, job.config):
                raise SystemExit(f"{job.family} {label} was already scored on the holdout; "
                                 "that result is final for this configuration")
            # the gate pass must be on the feature version this run builds (a version bump is a different model) and
            # record the cost treatment this run charges (swap charged, cost source stated)
            if not reg.passed_gates(job.family, job.config, feature_version=fv):
                raise SystemExit(f"{job.family} {label} has no research trial on feature version {fv} that passed "
                                 "the design's gates with swap charged and its cost source stated; only a passing "
                                 "configuration is scored on the holdout")
    # BACKLOG 50: the data-v1 digests a queued row of these jobs pins are verified before any look; a pooled job's
    # agent is not known before its members are prepared, so it is matched on its family alone
    agents = [None if j.pooled else j.specs[0].agent_id for j in jobs]
    refusal = queued_digest_refusal(reg._rows(), quarter_of(), [(j.family, a) for j, a in zip(jobs, agents)],
                                    args.bars, args.from_year, args.to_year, settings.research.holdout_from)
    if refusal:
        raise SystemExit(refusal)
    # a holdout scoring or a screen is a look at the data too: charged like any trial, and like any trial it spends the
    # pre-registered queue's reservation only when it matches a pending preregistered row (linked to it below); a job
    # whose family and agent have a pending queued row with another config hash is refused, never charged to the open
    # budget (QueuedConfigMismatch)
    try:
        quarter, preregs = reg.check_budget_reserved([(j.family, j.config) for j in jobs],
                                                     settings.research.trial_budget_quarter,
                                                     settings.research.reserved_for(quarter_of()),
                                                     retired={e.family for e in settings.research.retired_families},
                                                     agents=agents)
    except TrialBudgetExceeded as exc:
        raise SystemExit(str(exc)) from None
    # E-5 cap: a manual row cites its pre-registered rule (recorded as rule_ref; refused when a capped run cites none)
    try:
        rule_refs = [cited_rule(p, args.rule_ref, capped=settings.research.open_budget_capped(quarter)) for p in preregs]
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    years_span = (b1["ts_utc"].iloc[-1] - b1["ts_utc"].iloc[0]).days / 365.25
    zero_volume = float((b1["tick_count"].astype(float) <= 0).mean())
    print(f"1m bars: {len(b1):,} ({b1['ts_utc'].iloc[0]:%Y-%m-%d} -> {b1['ts_utc'].iloc[-1]:%Y-%m-%d}), "
          f"zero tick volume in {zero_volume:.1%}", flush=True)
    frames: dict[str, tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any], tuple[pd.DataFrame, pd.DataFrame]]] = {}
    sections, rows = [], []
    title = jobs[0].family
    for job, prereg, rule_ref in zip(jobs, preregs, rule_refs, strict=True):
        tf = job.timeframe
        if tf not in frames:                       # bars, context, decision frame and lookahead check once per timeframe
            b_dec = resample_bars(b1, tf)
            context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
            leak = lookahead_check(b_dec, context, feature_names=feat_names, ctx=feat_ctx)
            frames[tf] = (b_dec, context, leak, build_decision_frame(b_dec.reset_index(drop=True), context,
                                                                     feature_names=feat_names, ctx=feat_ctx))
            sizes = "  ".join(f"{k} {len(v):,}" for k, v in context.items())
            print(f"{tf} {len(b_dec):,}  {sizes}; lookahead check: {len(leak['lookahead_columns'])} of "
                  f"{leak['columns_checked']} columns differ [{time.time() - t0:.0f}s]", flush=True)
        b_dec, context, leak, frame = frames[tf]
        # + every discovery's K_eff (survey 4b), charged to EVERY later trial, survivor or not: the conservative
        # default, recorded as an owner-acknowledged choice in docs/research/preregistration-2027Q1.md
        n_trials = reg.n_trials_effective + 1
        # ctx: the point-in-time tables a candidate rule reads (H-06's macro gate); the frame is already built
        preps = [prepare(s, b_dec, context, ctx=feat_ctx, extra_cost_usd=extra_cost, holdout=holdout,
                         score_holdout=args.score_holdout, frame=frame, swap=swap) for s in job.specs]
        # event floors: research.screen_min_events, or the daily-signal override (owner ruling A), set before the run;
        # the daily override is retire-only, so a pass still needs research.screen_min_events
        floors = screen_floors(tf if job.pooled else signal_timeframe(job.specs[0]), settings.research)
        scr = None if args.score_holdout else screen(preps if job.pooled else preps[0], *floors)
        # a family under its pre-registered rule-only screen (Specialist.screening, e.g. asia_drift) never reaches the
        # model stage, pass or fail, with or without --skip-screen: the screen row records the look (quant review M2)
        screen_only = scr is not None and not job.pooled and job.specs[0].screening
        skipped = bool(scr is not None and not scr["passed"] and args.skip_screen and not screen_only)
        agent_id = pool_identity(tf, preps).agent_id if job.pooled else job.specs[0].agent_id
        version = preps[0].feature_version
        meta = {"specialist": job.family, "from_year": int(b1["ts_utc"].iloc[0].year),
                "to_year": int(b1["ts_utc"].iloc[-1].year), "n_1m": len(b1), "n_dec": len(b_dec), "tf": tf,
                "zero_volume": zero_volume}
        # rule-only net R by side and year over every candidate (the pre-registered reading rules read these)
        # holdout scorings report no split: it would mix research-window and held-out labels (quant review)
        split = None if args.score_holdout else rule_only_split(pd.concat([p.labels for p in preps], ignore_index=True))
        common = {"lookahead": leak, "bars_from": str(b1["ts_utc"].iloc[0]), "bars_to": str(b1["ts_utc"].iloc[-1]),
                  "screen": scr, "screen_skipped": skipped, "cost_source": cost_source, "macro": macro_info,
                  "positioning": positioning_info, "us_calendar": us_calendar_info, "day_type": dt_info,
                  "rule_only_split": split}
        rationale = args.rationale + (f" | overrides {json.dumps(job.overrides, sort_keys=True)}" if job.overrides else "")
        if scr is not None and (screen_only or (not scr["passed"] and not args.skip_screen)):
            n = int(sum(len(p.labels) for p in preps))
            metrics: dict[str, Any] = {"n_candidates": n, "rule_only": scr["rule_only"], "swap": swap.model_dump(),
                                       "trades_per_year": n / years_span if years_span > 0 else 0.0, **common}
            row = reg.record(agent_id=agent_id, family=job.family, config=job.config, feature_version=version,
                             rationale=rationale, results=metrics, status="screened", budget_quarter=quarter,
                             preregistration=prereg, rule_ref=rule_ref)
            print(f"{json.dumps(job.overrides) or 'defaults'}: screen {scr['verdict']} ({n} events) [{time.time() - t0:.0f}s]", flush=True)
            text = render_screen_failed(scr, leak, {**meta, "trial": row["trial"], "seconds": time.time() - t0, "n": n,
                                                    "swap": swap.model_dump(), "cost_source": cost_source,
                                                    "macro": macro_info, "rule_only_split": split})
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
                             budget_quarter=quarter, preregistration=prereg, rule_ref=rule_ref)
            res.metrics = {**res.metrics, "screen": scr, "screen_skipped": skipped, "cost_source": cost_source,
                           "macro": macro_info, "positioning": positioning_info, "us_calendar": us_calendar_info,
                           "day_type": dt_info,
                           "rule_only_split": split}
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
    ap.add_argument("--families", nargs="?", const="family", default="column", choices=["column", "feature", "family", "cluster"],
                    help="--discover: group features by registry family (bare flag), by producing feature, or not")
    ap.add_argument("--discover-config", default="{}", help="--discover: JSON overrides of DiscoveryConfig")


def _discover(args: argparse.Namespace, variants: list[dict[str, Any]], b1: pd.DataFrame, reg: TrialRegistry,
              settings: Any, extra_cost: float, swap: SwapSpec, holdout: tuple[pd.Timestamp, pd.Timestamp] | None,
              cost_source: str, macro: pd.DataFrame | None) -> int:
    from goldbot.research.discovery import DiscoveryConfig, discovery_family, discovery_pass
    if args.pooled or args.score_holdout or len(variants) != 1:
        raise SystemExit("--discover runs one specialist configuration (one --variants entry), no --pooled or --score-holdout")
    spec = SPECIALISTS[args.specialist](**variants[0])
    # BACKLOG 50: the data-v1 digests a queued discovery row of this specialist configuration pins, before any look;
    # discovery_pass then cuts the bars at research.holdout_from and refuses a config-hash mismatch against that row
    refusal = queued_digest_refusal(reg._rows(), quarter_of(), [(discovery_family(spec.family), spec.agent_id)],
                                    args.bars, args.from_year, args.to_year, settings.research.holdout_from)
    if refusal:
        raise SystemExit(refusal)
    try:
        cfg = DiscoveryConfig(**json.loads(args.discover_config))
        row, text = discovery_pass(spec, b1, reg,
                                   budget_cap=settings.research.trial_budget_quarter,
                                   reserved_setting=settings.research.reserved_for(quarter_of()), cfg=cfg,
                                   group_mode=args.families,
                                   extra_cost_usd=extra_cost, swap=swap, holdout=holdout,
                                   ctx={"macro": macro} if macro is not None else None, rationale=args.rationale,
                                   cost_source=cost_source, rule_ref=args.rule_ref,
                                   holdout_from=settings.research.holdout_from,
                                   capped=settings.research.open_budget_capped(quarter_of()))
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
    registry row; the result is attached to each gate-passing trial as evidence. Several trials: PBO across them.
    ADR 0003: CPCV can only veto a passed config, so a trial that failed the gates is refused unless --diagnostic."""
    from goldbot.research import cpcv
    from goldbot.research.registry_sync import read_rows
    rows = []
    for n in dict.fromkeys(args.cpcv):
        row = reg.row(n)
        if row is None:
            raise SystemExit(f"--cpcv: no trial #{n} in {reg.path}")
        why = cpcv.cpcv_eligible(row)
        if why:
            raise SystemExit(f"--cpcv: {why}")
        rows.append(row)
    failed = [int(r["trial"]) for r in rows if not gate_pass_counts(r)]     # a pre-swap pass is not a pass
    if failed and not getattr(args, "diagnostic", False):
        raise SystemExit(f"--cpcv: trial(s) {', '.join(map(str, failed))} failed the gates. CPCV and PBO can only veto a "
                         "config that passed them, never rescue one (docs/decisions/0003-cpcv-and-pbo-can-only-veto.md); "
                         "add --diagnostic to run anyway (diagnostic, not evidence)")
    tfs = {cpcv.trial_timeframe(r) for r in rows}
    if len(tfs) != 1:
        raise SystemExit(f"--cpcv: trials compared together must share a decision timeframe (got {sorted(tfs)})")
    tf = tfs.pop()
    selection = cpcv.selection_set(read_rows(reg.path), {str(r["family"]) for r in rows}, tf)
    feat_ctx = {"macro": macro} if macro is not None else None
    b_dec = resample_bars(b1, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    frame = build_decision_frame(b_dec, context, ctx=feat_ctx)
    out = cpcv.cpcv_trials(rows, b_dec, context, extra_cost_usd=extra_cost, holdout=holdout, swap=swap, frame=frame,
                           ctx=feat_ctx, selection=selection)
    version = frame[1].attrs["feature_version"]
    label = " (diagnostic, not evidence)" if failed else ""
    lines = [f"## Combinatorial purged CV: trials {', '.join(str(r['trial']) for r in rows)} ({tf}){label}", "",
             f"- research window {out['edges'][0][:10]} .. {out['edges'][-1][:10]} (holdout excluded), "
             f"{cpcv.N_GROUPS} groups of equal duration",
             f"- costs: bar spread in every label plus {extra_cost:.2f} $/oz round trip; cost source: {cost_source}",
             "- evidence attached to each gate-passing trial (not a trial: no budget slot, the deflated-Sharpe count is "
             "unchanged)"]
    if failed:
        lines.append(f"- **diagnostic, not evidence**: trial(s) {', '.join(map(str, failed))} failed the gates; CPCV "
                     "cannot rescue them (ADR 0002) and nothing is attached to them")
    stale = [str(r["trial"]) for r in rows if r.get("feature_version") and r["feature_version"] != version]
    if stale:
        lines.append(f"- **feature version differs from the trial's** for {', '.join(stale)} (data or --macro differ): "
                     "read the paths with care")
    lines += ["", *cpcv.report_lines(out["per_trial"], out["pbo"])]
    for r in rows:
        if int(r["trial"]) in failed:
            continue
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
    lines += screen_lines(scr) + _rule_only_lines(scr["rule_only"]) + rule_only_split_lines(meta.get("rule_only_split"))
    if scr["passed"]:
        lines += ["Screen passed; rule-only screen: no model was fitted. The family is under its pre-registered screen "
                  "(`Specialist.screening`); model research on it needs its own pre-registration."]
    elif is_inconclusive(scr):
        pass_n = int(scr.get("pass_min_events") or scr["min_events"])
        short = (f"fewer than the {pass_n:,} a pass needs (the {scr['min_events']:,}-event floor is retire-only)"
                 if scr["n"] >= scr["min_events"] else f"fewer than the {scr['min_events']:,} the floor needs")
        lines += [f"Screen inconclusive (event floor): {scr['n']:,} events, {short}. No model was fitted. This "
                  "recorded, charged trial cannot retire the hypothesis; it is not evidence for promotion either."]
    else:
        lines += ["Screen failed: no model was fitted. The rule is retired from model research (it may still serve as a "
                  "feature); `--skip-screen` fits a model anyway and is recorded as such."]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
