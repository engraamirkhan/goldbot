"""TV_GRID_V1: the pre-registered grid of TradingView event trials (plan docs/plans/tradingview-strategies.md, lane C,
"Separate grid"; DESIGN "Bounded research loop": "bounded feature and parameter search, not discovery").

A point is a `tv_event` configuration less its plot (study, version, input set, plot, side: `PLOT_KEYS`), so the grid
is fixed in code although the owner's studies are not: every catalogued event plot with a known side
(`PlotSpec.can_trigger`) on a searchable timeframe gets the same points. Per timeframe: the defaults, then one exit
setting at a time (`target_atr`, `stop_atr`, `max_bars`) at 75 % and 125 % of the timeframe's default (as the loop's
GRID_V1), never combined. `grid_points` computes them; TV_GRID_V1 freezes them. Editing a default or a step changes
the computed set, fails tests/test_tv_event.py and needs a new dated TV_GRID_V2, never an edit here.

* Timeframes. Searchable only on 4h and 1d, pre-holdout (the window ends at research.holdout_from, `window_to`):
  their TradingView history reaches back before the holdout. 15m and 1h history sits inside the holdout, so those
  configurations are shadow-forward only (recorded live in the shadow book, judged later), never searched here.
* Counting. Each configuration is one registry trial: a manual, pre-registered run (rule_ref TV_RULE_REF) under the
  quarter's trial budget, so it is in `quarter_trials` and in the deflated Sharpe's K (`n_trials_effective`), unlike
  a loop trial, which skips the quarterly count. The loop never runs a TradingView family (`generator.loop_refusal`).
* Refusal (`tv_refusal`): membership is checked on the effective configuration (the timeframe's defaults filled in,
  as the registry hashes it), so a job is judged by what it runs, not by how its overrides are spelled.
* Bar alignment (safety review 2, quant c). The signal is the TradingView bar whose `bar_time` is our decision bar's
  open. Our 4h bars floor on the UTC clock and our 1d is the 13:30 New York feature-day (data/resample.py); TradingView's
  gold bars start at its session open (17:00 New York), so its 4h bars sit 1-2 h off ours and its daily bar closes
  3.5 h after our feature-day. A tolerance mapping (TradingView close near our close) is not clean: the bars hold
  different hours, so a "match" would read another bar's mark. So nothing is mapped: with the data at hand
  (`tv_long`, `bars_1m`), a trial is refused unless at least `tv.min_bar_match` of our decision bars inside the
  TradingView history have a TradingView bar of the same open (`bar_match`), before anything is charged.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.data.resample import resample_bars
from goldbot.data.timeutil import utc_index
from goldbot.data.tv_catalog import PlotSpec
from goldbot.features.tv_plots import eligible_rows, plot_name
from goldbot.research.generator import EXIT_KEYS, FROM_YEAR, GRID_STEPS
from goldbot.research.registry import config_hash, is_trial
from goldbot.specialists.base import TIMEFRAME_KEY
from goldbot.specialists.tv_event import FAMILY, PLOT_KEYS, TV_TF, TvEventSpecialist

SEARCH_TFS = ("4h", "1d")                 # TradingView history before the holdout: searched pre-holdout
SHADOW_TFS = ("15m", "1h")                # history inside the holdout: shadow-forward only
TV_RULE_REF = "tv_grid_v1"                # rule_ref of a TV_GRID_V1 trial (charged to the quarter's budget)
_PLACEHOLDER: dict[str, Any] = {"study": "grid", "version": "0", "meta_hash": "00000000", "plot_id": "plot", "side": 1}

# TV_GRID_V1, frozen 2026-10-18: (timeframe, hash of the effective configuration without its plot)
TV_GRID_V1: frozenset[tuple[str, str]] = frozenset({
    ("1d", "08a1bb502218"),
    ("1d", "30b017fd8d09"),
    ("1d", "3f4a5a2a485f"),
    ("1d", "40e89d72d1a8"),
    ("1d", "5aaa098d695f"),
    ("1d", "8a0b6b701696"),
    ("1d", "a80a73c60994"),
    ("4h", "1aee7ab5926c"),
    ("4h", "446582e40aca"),
    ("4h", "6e1fe3c47a34"),
    ("4h", "a27b73b056b7"),
    ("4h", "a3c6b87ea5ae"),
    ("4h", "c1be33932631"),
    ("4h", "d66166e46675"),
})


class TvCandidate(FrozenRecord):
    family: str
    timeframe: str
    feature_id: str                 # the plot (tv_catalog feature id)
    overrides: dict[str, Any]       # research_pass --variants entry (plot included); the registry hashes the effective config
    config_hash: str                # of the effective config, as recorded in the registry
    window_to: str                  # ISO date the search window ends (exclusive): the holdout's first day
    rationale: str


def point_of(config: dict[str, Any]) -> str:
    """The grid point of an effective `tv_event` configuration: its hash without the plot."""
    return config_hash({k: v for k, v in config.items() if k not in PLOT_KEYS})


def _overrides(tf: str) -> list[dict[str, Any]]:
    """The overrides of one timeframe (plot excluded): the defaults, then one exit setting at a time."""
    own: dict[str, Any] = {} if tf == TvEventSpecialist.timeframe else {TIMEFRAME_KEY: tf}
    base = TvEventSpecialist(**own, **_PLACEHOLDER).config
    out = [dict(own)]
    for key in EXIT_KEYS:
        b = base[key]
        for step in GRID_STEPS:
            v: float = b * step
            v = max(1, round(v)) if isinstance(b, int) else round(v, 4)
            if v != b:
                out.append({**own, key: v})
    return out


def grid_points() -> set[tuple[str, str]]:
    """(timeframe, point) of every configuration the grid declares: what TV_GRID_V1 freezes."""
    return {(tf, point_of(TvEventSpecialist(**ov, **_PLACEHOLDER).config)) for tf in SEARCH_TFS for ov in _overrides(tf)}


def _window_to(settings: Settings) -> str:
    window = settings.research.holdout_window()
    return window[0].date().isoformat() if window else f"{FROM_YEAR + 100}-01-01"


def tv_grid(catalog: list[PlotSpec], settings: Settings) -> list[TvCandidate]:
    """Every TV_GRID_V1 configuration of the catalogue's trigger plots, on the searchable timeframe each was drawn on
    (a plot drawn on 15m or 1h is shadow-forward only and gets none). Tried or not, in catalogue then grid order."""
    window_to = _window_to(settings)
    by_tv = {v: k for k, v in TV_TF.items()}
    out: list[TvCandidate] = []
    for spec in catalog:
        tf = by_tv.get(spec.tf)
        if not spec.can_trigger or tf not in SEARCH_TFS:
            continue
        for ov in _overrides(tf):
            s = TvEventSpecialist.from_plot(spec, **ov)
            out.append(TvCandidate(family=FAMILY, timeframe=tf, feature_id=spec.feature_id,
                                   overrides={**ov, **{k: s.config[k] for k in PLOT_KEYS}},
                                   config_hash=config_hash(s.config), window_to=window_to,
                                   rationale=f"TV_GRID_V1: {spec.feature_id} ({spec.title or spec.plot_id}) {tf} "
                                             f"{ov or 'defaults'}"))
    return out


def bar_match(long: pd.DataFrame | None, spec: TvEventSpecialist, dec: pd.DataFrame) -> float | None:
    """The share of our decision bars (`dec`, as resample_bars builds them) inside the span of the plot's usable
    TradingView rows that have a TradingView bar of the same open; None when the plot has no usable row."""
    if long is None or len(long) == 0 or len(dec) == 0:
        return None
    plot = spec.plot
    d = eligible_rows(long, plot)
    d = d[d["plot"] == plot_name(plot.plot_id)]
    if d.empty:
        return None
    tv = utc_index(d["bar_time"])
    ours = utc_index(dec["ts_utc"])
    inside = ours[(ours >= tv.min()) & (ours <= tv.max())]
    return float(inside.isin(tv).mean()) if len(inside) else 0.0


def tv_refusal(rows: list[dict[str, Any]], jobs: list[tuple[str, dict[str, Any]]], settings: Settings, *,
               tv_long: pd.DataFrame | None = None, bars_1m: pd.DataFrame | None = None) -> str | None:
    """Why a run of TradingView trials is refused, or None: every job is a `tv_event` configuration (overrides or
    effective config; judged on the effective one) on a searchable timeframe whose point is in TV_GRID_V1, and not
    already a trial in the registry or earlier in `jobs` (no double dispatch). With the research bars (`bars_1m`), also
    the bar alignment (module docstring): at least `tv.min_bar_match` of our decision bars line up with a TradingView
    bar of `tv_long`.
    Called before any budget check, so a refused job is never charged. Budget and pre-registration are the quarter's
    usual checks."""
    tried = {(r.get("family"), r.get("config_hash")) for r in rows if is_trial(r)}
    listed: set[tuple[str, str]] = set()       # this run's jobs so far: one grid point twice is a double dispatch
    dec_by_tf: dict[str, pd.DataFrame] = {}
    for family, config in jobs:
        if family != FAMILY:
            return f"TV_GRID_V1: {family} is not a TradingView family"
        try:
            spec = TvEventSpecialist(**config)
        except ValueError as exc:
            return f"TV_GRID_V1: {exc}"
        if spec.timeframe in SHADOW_TFS:
            return (f"TV_GRID_V1: {spec.timeframe} is shadow-forward only (its TradingView history sits inside the "
                    "holdout); it is recorded live, never searched")
        point = point_of(spec.config)
        if (spec.timeframe, point) not in TV_GRID_V1:
            return f"TV_GRID_V1: {family} {spec.timeframe} point {point} is not a point of TV_GRID_V1"
        h = config_hash(spec.config)
        if (family, h) in tried:
            return f"TV_GRID_V1: {family} config {h} is already in the registry (no double dispatch of one trial)"
        if (family, h) in listed:
            return f"TV_GRID_V1: {family} config {h} is listed twice in this run (no double dispatch of one trial)"
        listed.add((family, h))
        if bars_1m is not None:
            if spec.timeframe not in dec_by_tf:
                dec_by_tf[spec.timeframe] = resample_bars(bars_1m, spec.timeframe)
            share = bar_match(tv_long, spec, dec_by_tf[spec.timeframe])
            floor = settings.tv.min_bar_match
            if share is None or share < floor:
                got = "no usable TradingView rows" if share is None else f"{share:.1%}"
                return (f"TV_GRID_V1: {spec.column} does not line up with our {spec.timeframe} bars ({got} of our "
                        f"decision bars have a TradingView bar of the same open; at least {floor:.0%} needed)")
    return None
