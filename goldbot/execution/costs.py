"""Measured execution costs per terminal (design: "Costs are measured, not assumed").

One nightly job per terminal fits
* a spread table by session from that terminal's own ticks (median and p90 of ask - bid, $/oz), and
* a slippage table from the engine's own fills (filled minus requested price, signed so that positive is worse
  for us, $/oz) by session and order type, seeded with a conservative prior until `min_fills` fills exist,
plus the broker's commission. The engine converts the round trip for the current session into ATR units for
the RiskGate and the probability threshold; without a table it keeps its configured fallback.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable

Session = Literal["asia", "london", "newyork"]
SESSIONS: tuple[Session, ...] = ("asia", "london", "newyork")
CONTRACT_OZ = 100.0


class SpreadStat(FrozenRecord):
    median: float
    p90: float
    n: int


class SlippageStat(FrozenRecord):
    mean: float          # $/oz, positive = worse than requested
    n: int
    from_prior: bool


class CostTable(FrozenRecord):
    account_id: str
    built_utc: UtcTimestamp
    spread: dict[str, SpreadStat]
    slippage: dict[str, SlippageStat]          # key "<session>:<order_type>"
    commission_per_lot_side_usd: float
    slippage_prior_usd: float
    n_ticks: int = 0
    n_fills: int = 0
    notes: list[str] = Field(default_factory=list)

    def round_trip_usd_per_oz(self, session: str, order_type: str = "market") -> float | None:
        """Spread + entry and exit slippage + commission both sides, in $/oz; None when no spread was measured.
        A session without ticks borrows the widest measured session (conservative)."""
        if not self.spread:
            return None
        sp = self.spread.get(session)
        spread = sp.median if sp is not None else max(s.median for s in self.spread.values())
        slip = self.slippage.get(f"{session}:{order_type}")
        slip_usd = slip.mean if slip is not None else self.slippage_prior_usd
        commission = 2 * self.commission_per_lot_side_usd / CONTRACT_OZ
        return float(spread + 2 * max(slip_usd, 0.0) + commission)

    def round_trip_atr(self, session: str, atr_usd: float, order_type: str = "market") -> float | None:
        usd = self.round_trip_usd_per_oz(session, order_type)
        if usd is None or not np.isfinite(atr_usd) or atr_usd <= 0:
            return None
        return usd / atr_usd

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "CostTable | None":
        p = Path(path)
        if not p.exists():
            return None
        return cls.model_validate(json.loads(p.read_text()))


def spread_table(ticks: pd.DataFrame, sessions: SessionTable = DEFAULT_SESSIONS) -> dict[str, SpreadStat]:
    """ticks: ts_utc, bid, ask. Only in-session ticks with ask >= bid count."""
    if ticks.empty:
        return {}
    t = ticks[(ticks["ask"] >= ticks["bid"]) & (ticks["bid"] > 0)]
    idx = pd.DatetimeIndex(pd.to_datetime(t["ts_utc"], utc=True))
    t = t[sessions.is_open(idx)]
    idx = pd.DatetimeIndex(pd.to_datetime(t["ts_utc"], utc=True))
    label = sessions.session_label(idx)
    spread = (t["ask"] - t["bid"]).to_numpy(dtype=float)
    out: dict[str, SpreadStat] = {}
    for s in SESSIONS:
        v = spread[label == s]
        if len(v):
            out[s] = SpreadStat(median=float(np.median(v)), p90=float(np.percentile(v, 90)), n=int(len(v)))
    return out


def slippage_table(fills: pd.DataFrame, *, prior_usd: float, min_fills: int = 50,
                   sessions: SessionTable = DEFAULT_SESSIONS) -> dict[str, SlippageStat]:
    """fills: ts_utc, side (+1/-1), requested, filled, order_type. Slippage = side * (filled - requested).

    A cell keeps the prior until it has `min_fills` fills of its own; the prior is never undercut by a lucky
    small sample (design: seeded with a conservative $0.15 until 50 fills exist)."""
    out: dict[str, SlippageStat] = {}
    f = fills.dropna(subset=["requested", "filled"]) if not fills.empty else fills
    labels = (sessions.session_label(pd.DatetimeIndex(pd.to_datetime(f["ts_utc"], utc=True)))
              if not f.empty else np.array([], dtype=object))
    otypes = f["order_type"].astype(str).to_numpy() if not f.empty else np.array([], dtype=object)
    slip = (f["side"].to_numpy(dtype=float) * (f["filled"].to_numpy(dtype=float) - f["requested"].to_numpy(dtype=float))
            if not f.empty else np.array([]))
    for s in SESSIONS:
        for ot in sorted(set(otypes) | {"market"}):
            v = slip[(labels == s) & (otypes == ot)]
            if len(v) >= min_fills:
                out[f"{s}:{ot}"] = SlippageStat(mean=float(np.mean(v)), n=int(len(v)), from_prior=False)
            else:
                out[f"{s}:{ot}"] = SlippageStat(mean=prior_usd, n=int(len(v)), from_prior=True)
    return out


def build_cost_table(account_id: str, ticks: pd.DataFrame, fills: pd.DataFrame, *, commission_per_lot_side_usd: float,
                     slippage_prior_usd: float, min_fills: int = 50, now: UtcTimestamp | None = None) -> CostTable:
    sp = spread_table(ticks)
    notes = [] if sp else ["no in-session ticks: no round-trip cost; the engine keeps its configured fallback"]
    return CostTable(account_id=account_id, built_utc=now or pd.Timestamp.now("UTC"), spread=sp,
                     slippage=slippage_table(fills, prior_usd=slippage_prior_usd, min_fills=min_fills),
                     commission_per_lot_side_usd=commission_per_lot_side_usd, slippage_prior_usd=slippage_prior_usd,
                     n_ticks=len(ticks), n_fills=len(fills), notes=notes)
