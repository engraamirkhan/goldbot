"""Catalogue of a TradingView study's plots: what each plot is, which way it points, and the feature id it feeds.

The owner names nothing (plan docs/plans/tradingview-strategies.md, lane A): plot names and kinds come from the study
metadata the direct pull receives with the study (TradingView's `metaInfo`: `plots` with an id and a type, `styles`
with each plot's title, `inputs` with their values), checked against a sample of the plot's own values.

* Kind. `shapes`, `chars` and `arrows` plots draw a mark on the bars where something happened: `event` (a value on
  the bar = it fired, no value = it did not). A `*colorer` plot colours a bar or the background by an index: `state`.
  A `line` within 5 % of the close (median over the sample) is a price `level` (an EMA, a band); any other line is an
  `oscillator` (MFI, RSI). A line without sample values is refused rather than guessed. Every other plot type
  (`data_offset`, `alertcondition`, OHLC parts) carries no readable value and is not catalogued.
* Side. Only an event can point: `buy`/`long`/`bull(ish)` as a word of its title is +1, `sell`/`short`/`bear(ish)` is -1. A title
  with neither, or with both, has no side: that plot is a feature only and can never be a trigger ("TradingView
  indicators: filter features only" until proven, DESIGN promotion table). A line has no side whatever its title
  ("Short EMA" names a length).
* Feature id `tv_{study}_{meta_hash}_{tf}_{plot_id}`. `meta_hash` is 8 hex characters of SHA-256 over the study, its
  version and its input values (sorted by input id), so an input or version change is a new feature id: a model trained
  on EMA 50 is never fed EMA 55. Rows of `tv_indicators` written by the direct pull carry `meta_hash`.

Origins of `tv_indicators` rows: the webhook alert (no `origin`, or "alert") and `pull_live` are recorded at the bar
close and are live; `csv` (goldbot/data/tv_csv.py) and `pull_history` are history drawn afterwards, which may repaint,
so they train only once the repaint check (goldbot/research/tv_repaint.py) has flagged them `repaint_validated`.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord

PlotKind = Literal["level", "oscillator", "event", "state"]
LEVEL_BAND = 0.05                    # a line within 5 % of the close is a price level
EVENT_TYPES = frozenset({"shapes", "chars", "arrows"})
LIVE_ORIGINS = frozenset({"", "alert", "pull_live"})        # recorded at the bar close (a missing origin = alert)
HISTORY_ORIGINS = frozenset({"csv", "pull_history"})        # drawn afterwards: train only when repaint-validated
_LONG = re.compile(r"(buy|buys|long|longs|bull|bulls|bullish)")        # whole words only: "Bullet" is no side
_SHORT = re.compile(r"(sell|sells|short|shorts|bear|bears|bearish)")


class StudyPlot(FrozenRecord):
    id: str
    type: str
    title: str = ""
    target: str | None = None        # a colourer's coloured plot


class StudyMeta(FrozenRecord):
    study: str
    version: str
    tf: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    plots: list[StudyPlot] = Field(default_factory=list)

    @classmethod
    def from_metainfo(cls, raw: Mapping[str, Any], *, tf: str) -> "StudyMeta":
        """Read TradingView's `metaInfo` object (as the chart websocket sends it) for timeframe `tf`."""
        pine = raw.get("pine") or {}
        version = str(pine.get("version") or raw.get("version") or "")
        styles: Mapping[str, Any] = raw.get("styles") or {}
        plots = [StudyPlot(id=str(p["id"]), type=str(p.get("type", "")), title=str((styles.get(p["id"]) or {}).get("title", "")),
                           target=p.get("target")) for p in raw.get("plots") or []]
        inputs = {str(i["id"]): i.get("value", i.get("defval")) for i in raw.get("inputs") or []}
        study = str(raw.get("shortDescription") or raw.get("description") or raw.get("id") or "")
        if not study:
            raise ValueError("study metadata has no name (shortDescription, description or id)")
        return cls(study=study, version=version, tf=str(tf), inputs=inputs, plots=plots)

    @property
    def meta_hash(self) -> str:
        key = json.dumps({"study": self.study, "version": self.version, "inputs": self.inputs}, sort_keys=True,
                         default=str, separators=(",", ":"))
        return hashlib.sha256(key.encode()).hexdigest()[:8]


class PlotSpec(FrozenRecord):
    feature_id: str
    study: str
    version: str
    meta_hash: str
    tf: str
    plot_id: str
    title: str
    plot_type: str
    kind: PlotKind
    side: Literal[1, -1] | None = None

    @property
    def can_trigger(self) -> bool:
        """Only an event with a known side may ever be offered as a trigger (and then only by the promotion rule)."""
        return self.kind == "event" and self.side is not None


def slug(x: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", x.lower()).strip("_")


def feature_id(meta: StudyMeta, plot_id: str) -> str:
    return f"tv_{slug(meta.study)}_{meta.meta_hash}_{slug(meta.tf)}_{slug(plot_id)}"


def side_from_title(title: str) -> Literal[1, -1] | None:
    words = re.findall(r"[a-z]+", title.lower())
    up = any(_LONG.fullmatch(w) for w in words)
    down = any(_SHORT.fullmatch(w) for w in words)
    if up == down:
        return None
    return 1 if up else -1


def _kind(plot: StudyPlot, sample: pd.DataFrame | None) -> PlotKind | None:
    t = plot.type.lower()
    if t in EVENT_TYPES:
        return "event"
    if t.endswith(("colorer", "colourer")):
        return "state"
    if t != "line":
        return None
    if sample is None or plot.id not in sample.columns or "close" not in sample.columns:
        raise ValueError(f"line plot {plot.id!r} needs sample values (and the close) to tell a level from an "
                         f"oscillator")
    v = pd.to_numeric(sample[plot.id], errors="coerce").to_numpy(dtype=float)
    c = pd.to_numeric(sample["close"], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(v) & np.isfinite(c) & (c != 0)
    if not ok.any():
        return "oscillator"          # nothing to compare: not treated as a price
    return "level" if float(np.median(np.abs(v[ok] / c[ok] - 1.0))) <= LEVEL_BAND else "oscillator"


def catalog(meta: StudyMeta, sample: pd.DataFrame | None = None) -> list[PlotSpec]:
    """Every readable plot of the study as a PlotSpec, in the study's order. `sample`: recent bars with `close` and
    one column per line plot id (needed only when the study has a line)."""
    out: list[PlotSpec] = []
    for p in meta.plots:
        kind = _kind(p, sample)
        if kind is None:
            continue
        out.append(PlotSpec(feature_id=feature_id(meta, p.id), study=meta.study, version=meta.version,
                            meta_hash=meta.meta_hash, tf=meta.tf, plot_id=p.id, title=p.title, plot_type=p.type,
                            kind=kind, side=side_from_title(p.title) if kind == "event" else None))
    return out


def is_live(origin: pd.Series) -> pd.Series:
    """Rows recorded at the bar close (alert or pull_live); a missing origin is a webhook alert."""
    return origin.fillna("").astype(str).isin(LIVE_ORIGINS)


def is_history(origin: pd.Series) -> pd.Series:
    return origin.fillna("").astype(str).isin(HISTORY_ORIGINS)
