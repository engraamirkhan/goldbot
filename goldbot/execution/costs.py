"""Measured execution costs per terminal (design: "Costs are measured, not assumed").

One nightly job per terminal fits
* a spread table by session from that terminal's own ticks (median and p90 of ask - bid, $/oz), and
* a slippage table from the engine's own fills (filled minus requested price, signed so that positive is worse
  for us, $/oz) by session and order type, seeded with a conservative prior until `min_fills` fills exist,
plus the broker's commission and, when the terminal reports them, its swap rates (USD per lot per night, triple day).
Swap and commission are measured by the engine on the terminal (`BrokerTerms`, written by
`mt5_adapter.MT5Broker.broker_terms` to state/broker_terms_<account>.json); without fresh terms the table keeps the
settings' commission and no swap (research then charges the settings' swap prior).
The engine converts the round trip for the current session into ATR units for
the RiskGate and the probability threshold; without a table it keeps its configured fallback.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.labels.triple_barrier import SwapSpec

if TYPE_CHECKING:
    from goldbot.config import Settings

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


class BrokerTerms(FrozenRecord):
    """The broker's charges as the terminal reports them (engine-side, read nightly by the cost job): swap per lot
    (100 oz) per night in USD, broker sign (negative = paid), and the commission paid per lot round trip on recent
    closed positions. None = not measured (unsupported swap mode, no closed positions yet); `notes` says why."""
    account_id: str
    measured_utc: UtcTimestamp
    swap_long_usd_per_lot: float | None = None
    swap_short_usd_per_lot: float | None = None
    swap_triple_weekday: int | None = Field(None, ge=0, le=4)    # 0 = Monday
    swap_mode: int | None = None                                 # the terminal's SYMBOL_SWAP_MODE, for the record
    commission_per_lot_round_trip_usd: float | None = None       # commission + fees, both sides, per lot
    commission_lots: float = 0.0                                 # lots of closed positions it was measured on
    notes: list[str] = Field(default_factory=list)

    def swap_spec(self, server_tz: str, default_triple_weekday: int = 2) -> SwapSpec | None:
        if self.swap_long_usd_per_lot is None or self.swap_short_usd_per_lot is None:
            return None
        triple = default_triple_weekday if self.swap_triple_weekday is None else self.swap_triple_weekday
        return SwapSpec(long_usd_per_lot=self.swap_long_usd_per_lot, short_usd_per_lot=self.swap_short_usd_per_lot,
                        triple_weekday=triple, server_tz=server_tz)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=1))
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "BrokerTerms | None":
        p = Path(path)
        return cls.model_validate(json.loads(p.read_text())) if p.exists() else None


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
    # the broker's swap per lot (100 oz) per night, broker sign (negative = paid); None until the terminal reports it
    swap_long_usd_per_lot: float | None = None
    swap_short_usd_per_lot: float | None = None
    swap_triple_weekday: int | None = Field(None, ge=0, le=4)
    commission_measured: bool = False          # commission from the terminal's deals (else the settings value)
    commission_lots: float = 0.0               # lots of closed positions the measured commission rests on

    def extra_cost_usd(self) -> float:
        """Round-trip cost per oz beyond the quoted spread: entry and exit slippage (mean of the market-order cells,
        measured or the prior) plus commission both sides. What research labels pay on top of the bar spread."""
        slips = [max(s.mean, 0.0) for k, s in self.slippage.items() if k.endswith(":market")] or [self.slippage_prior_usd]
        return float(2 * (sum(slips) / len(slips)) + 2 * self.commission_per_lot_side_usd / CONTRACT_OZ)

    def swap_spec(self, server_tz: str, default_triple_weekday: int = 2) -> SwapSpec | None:
        """The broker's measured swap as a SwapSpec, or None when the table has no swap rates (keep the prior)."""
        if self.swap_long_usd_per_lot is None or self.swap_short_usd_per_lot is None:
            return None
        triple = default_triple_weekday if self.swap_triple_weekday is None else self.swap_triple_weekday
        return SwapSpec(long_usd_per_lot=self.swap_long_usd_per_lot, short_usd_per_lot=self.swap_short_usd_per_lot,
                        triple_weekday=triple, server_tz=server_tz)

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

    def round_trip_ex_spread_atr(self, session: str, atr_usd: float, order_type: str = "market") -> float | None:
        """Entry and exit slippage plus commission both sides, in ATR (the spread excluded: labels and the model's p
        already pay it). None when no spread was measured (no table to speak of) or ATR is unusable."""
        full = self.round_trip_usd_per_oz(session, order_type)
        if full is None or not np.isfinite(atr_usd) or atr_usd <= 0:
            return None
        sp = self.spread.get(session)
        spread = sp.median if sp is not None else max(s.median for s in self.spread.values())
        return (full - spread) / atr_usd

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
                     slippage_prior_usd: float, min_fills: int = 50, now: UtcTimestamp | None = None,
                     swap: SwapSpec | None = None, commission_measured: bool = False,
                     notes: list[str] | None = None) -> CostTable:
    sp = spread_table(ticks)
    notes = list(notes or []) + ([] if sp else ["no in-session ticks: no round-trip cost; the engine keeps its configured fallback"])
    return CostTable(account_id=account_id, built_utc=now or pd.Timestamp.now("UTC"), spread=sp,
                     slippage=slippage_table(fills, prior_usd=slippage_prior_usd, min_fills=min_fills),
                     commission_per_lot_side_usd=commission_per_lot_side_usd, slippage_prior_usd=slippage_prior_usd,
                     n_ticks=len(ticks), n_fills=len(fills), notes=notes,
                     swap_long_usd_per_lot=None if swap is None else swap.long_usd_per_lot,
                     swap_short_usd_per_lot=None if swap is None else swap.short_usd_per_lot,
                     swap_triple_weekday=None if swap is None else swap.triple_weekday,
                     commission_measured=commission_measured)


def prior_extra_cost_usd(slippage_prior_usd: float, commission_per_lot_side_usd: float) -> float:
    """Round-trip cost per oz beyond the quoted spread before any fills are measured: entry and exit slippage at the
    conservative prior plus commission both sides. Research labels already pay the spread (ask in, bid out)."""
    return float(2 * max(slippage_prior_usd, 0.0) + 2 * commission_per_lot_side_usd / CONTRACT_OZ)


def settings_extra_cost_usd(settings: "Settings") -> float:
    """prior_extra_cost_usd for the canonical-cost broker's configured commission (the research pass and the VPS
    jobs before the first cost table exists)."""
    canonical = [b for b, cfg in settings.brokers.items() if cfg.canonical_costs]
    commission = max((settings.costs.commission_per_lot_side_usd.get(b, 0.0) for b in canonical), default=0.0)
    return prior_extra_cost_usd(settings.costs.slippage_prior_usd, commission)


def settings_swap(settings: "Settings") -> SwapSpec:
    """The swap prior from settings (`costs.swap_*`) on the canonical-cost broker's server clock: what research and
    the VPS jobs charge before a cost table reports the broker's own rates."""
    canonical = [cfg.server_tz for cfg in settings.brokers.values() if cfg.canonical_costs]
    c = settings.costs
    return SwapSpec(long_usd_per_lot=c.swap_long_usd_per_lot, short_usd_per_lot=c.swap_short_usd_per_lot,
                    triple_weekday=c.swap_triple_weekday, server_tz=canonical[0] if canonical else "Europe/Athens",
                    contract_oz=CONTRACT_OZ)


def research_costs(settings: "Settings", table: CostTable | None) -> tuple[float, SwapSpec, str]:
    """(extra cost per oz beyond the spread, swap, a line naming the source) for research: the canonical broker's
    measured cost table when given (the VPS publishes it on release costs-v1; research.yml downloads it), each part
    falling back to the settings prior when the table has not measured it. The swap runs on the canonical broker's
    server clock."""
    prior = settings_swap(settings)
    if table is None:
        return settings_extra_cost_usd(settings), prior, (
            f"PRIORS ONLY: no measured cost table (release {COSTS_TAG} has no {COSTS_ASSET}); slippage prior "
            f"{settings.costs.slippage_prior_usd:.2f} $/oz, commission from settings, swap prior")
    swap = table.swap_spec(prior.server_tz, prior.triple_weekday)
    parts = [f"cost table {table.account_id} built {table.built_utc:%Y-%m-%d}",
             "commission measured" if table.commission_measured else "commission from settings",
             "swap measured" if swap is not None else "swap prior (table has no measured swap)",
             f"slippage from {sum(not s.from_prior for s in table.slippage.values())} measured cells"]
    return table.extra_cost_usd(), swap or prior, ", ".join(parts)


# ------------------------------------------------------------------ the published table (release costs-v1, research)
COSTS_TAG = "costs-v1"
COSTS_ASSET = "costs_measured.json"


class FieldSource(FrozenRecord):
    """Where one cost component comes from: the broker's measurement or the settings prior, and the count it rests on
    (ticks for the spread, fills for slippage, lots for commission; None for swap, the terminal's quoted rate)."""
    source: Literal["measured", "prior"]
    n: float | None = None


class PublishedCostTable(FrozenRecord):
    """The canonical broker's measured costs as research reads them (asset `costs_measured.json` on release
    `costs-v1`, published by the VPS). Costs only: the broker's short name, never an account id, login, server,
    balance or equity, and no free-text notes (they could quote one). `fields` says per component whether the value is
    measured or a prior, so a prior is never mistaken for a measurement."""
    schema_version: Literal[1] = 1
    broker: str
    measured_at: UtcTimestamp
    spread: dict[str, SpreadStat]
    slippage: dict[str, SlippageStat]          # key "<session>:<order_type>"
    slippage_prior_usd: float
    commission_per_lot_side_usd: float
    swap_long_usd_per_lot: float | None
    swap_short_usd_per_lot: float | None
    swap_triple_weekday: int | None = Field(None, ge=0, le=4)
    n_ticks: int
    n_fills: int
    fields: dict[str, FieldSource]             # spread, slippage, commission, swap

    @classmethod
    def from_table(cls, table: CostTable, broker: str) -> "PublishedCostTable":
        swap_ok = table.swap_long_usd_per_lot is not None and table.swap_short_usd_per_lot is not None
        slip_ok = any(not s.from_prior for s in table.slippage.values())
        return cls(broker=broker, measured_at=table.built_utc, spread=table.spread, slippage=table.slippage,
                   slippage_prior_usd=table.slippage_prior_usd,
                   commission_per_lot_side_usd=table.commission_per_lot_side_usd,
                   swap_long_usd_per_lot=table.swap_long_usd_per_lot, swap_short_usd_per_lot=table.swap_short_usd_per_lot,
                   swap_triple_weekday=table.swap_triple_weekday, n_ticks=table.n_ticks, n_fills=table.n_fills,
                   fields={"spread": FieldSource(source="measured" if table.spread else "prior", n=table.n_ticks),
                           "slippage": FieldSource(source="measured" if slip_ok else "prior", n=table.n_fills),
                           "commission": FieldSource(source="measured" if table.commission_measured else "prior",
                                                     n=table.commission_lots),
                           "swap": FieldSource(source="measured" if swap_ok else "prior")})

    def to_cost_table(self) -> CostTable:
        """The research view: a CostTable labelled with the broker (research_costs names it in the report)."""
        commission = self.fields.get("commission")
        return CostTable(account_id=f"{self.broker} (published)", built_utc=self.measured_at, spread=self.spread,
                         slippage=self.slippage, commission_per_lot_side_usd=self.commission_per_lot_side_usd,
                         slippage_prior_usd=self.slippage_prior_usd, n_ticks=self.n_ticks, n_fills=self.n_fills,
                         swap_long_usd_per_lot=self.swap_long_usd_per_lot,
                         swap_short_usd_per_lot=self.swap_short_usd_per_lot, swap_triple_weekday=self.swap_triple_weekday,
                         commission_measured=commission is not None and commission.source == "measured",
                         commission_lots=(commission.n or 0.0) if commission is not None else 0.0)


def publishable(table: CostTable, broker: str, *, min_fills: int) -> tuple[PublishedCostTable | None, str]:
    """The table to publish, or None and why not. Refused while swap is unmeasured or fewer than `min_fills` fills
    exist, so a prior is never published as a measurement (research then keeps the settings priors and says so)."""
    if table.swap_long_usd_per_lot is None or table.swap_short_usd_per_lot is None:
        return None, "swap not measured by the terminal yet"
    if table.n_fills < min_fills:
        return None, f"{table.n_fills} fills, fewer than {min_fills}"
    return PublishedCostTable.from_table(table, broker), "ok"


def load_research_cost_table(path: str | Path) -> CostTable | None:
    """A cost table for research from `path`: the published format (release costs-v1) or a local CostTable file.
    None when the file does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    if isinstance(raw, dict) and "broker" in raw and "schema_version" in raw:
        return PublishedCostTable.model_validate(raw).to_cost_table()
    return CostTable.model_validate(raw)
