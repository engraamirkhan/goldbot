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

The broker spread profile (`SpreadProfile`, docs/research/costs-binding-2026-10-18.md section 1.4, K3/K4): the
recorded broker ticks (`goldbot.data.ticks`, the terminal's complete `copy_ticks` history) give the spread's median
and p90 by UTC hour and by session over the cost window, with no fill needed. It is an aggregate only (no tick, no
timestamp of one), reported on release costs-v1 next to the engine's session table; it is not charged (labels keep
the Dukascopy bar spread, K5) and it does not change what the engine charges the RiskGate.
Publishing (`publishable`), K3: "Publish once spread and swap are measured, with slippage marked
`fields.slippage.source = "prior"`". Every component carries its label (`FieldSource`: measured, prior, or mixed when
some slippage cells are measured and others still the prior) and `PublishedCostTable` refuses a label that disagrees
with its values, so a prior is never mixed into a measurement silently.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp, load_record
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.labels.triple_barrier import SwapSpec

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.engine.cost_probe import ProbeRecord

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


class ProbeSlippage(FrozenRecord):
    """One session's scheduled-probe entry slippage ($/oz, positive = worse): the raw measurement beside what is charged."""
    n: int
    mean: float          # raw, reported even when favourable
    ci_low: float        # day-block bootstrap 95% interval
    ci_high: float
    charged: float       # max(prior, mean): never below the prior
    raised: bool         # mean above the prior


PROBE_RETCODE_DONE = 10009           # TRADE_RETCODE_DONE (mt5_adapter.RETCODE_DONE; not imported: the adapter imports this module)
PROBE_BOOTSTRAP_RESAMPLES = 2000     # prereg-cost-measurement-2026-10 s5
PROBE_BOOTSTRAP_SEED = 20261020
PROBE_MIN_FILLS = 50                 # per session, prereg s5 (the architect's sketch said 30: that is the per-bucket decision read)

SPREAD_DECIMALS = 6      # spreads are counted at 1e-6 $/oz (float noise of ask - bid); quotes have 2-3 decimals


class SpreadProfile(FrozenRecord):
    """The broker's spread from its recorded ticks over the cost window: median and p90 of ask - bid ($/oz) by UTC
    hour ("00".."23", every valid tick, the rollover and the daily break included) and by session (in-session ticks,
    as `spread_table`). An aggregate only: counts and quantiles, never a tick or the time of one (section 1.4: "Raw
    ticks never leave the brain")."""
    source: Literal["broker_ticks"] = "broker_ticks"
    first_day: date                            # server dates of the oldest and newest day counted
    last_day: date
    days: int = Field(ge=1)                    # recorded days with at least one valid tick
    days_skipped: int = Field(0, ge=0)         # recorded days not read (unreadable, or the sha256 did not match)
    n_ticks: int = Field(ge=0)
    by_session: dict[str, SpreadStat]
    by_hour: dict[str, SpreadStat]


def _quantiles(counts: pd.Series) -> SpreadStat:
    """Median and p90 of the values `counts` indexes (value -> count), exactly as np.percentile's linear
    interpolation over the expanded sample, without expanding it (a 30-day window is millions of ticks)."""
    c = counts.sort_index()
    values, cum = c.index.to_numpy(dtype=float), np.cumsum(c.to_numpy(dtype=np.int64))
    n = int(cum[-1])

    def at(q: float) -> float:
        h = (n - 1) * q
        lo, hi = int(np.floor(h)), int(np.ceil(h))
        x_lo, x_hi = (float(values[np.searchsorted(cum, k, side="right")]) for k in (lo, hi))
        return x_lo + (h - lo) * (x_hi - x_lo)
    return SpreadStat(median=at(0.5), p90=at(0.9), n=n)


def spread_profile(days: Iterable[pd.DataFrame], *, first_day: date, last_day: date, days_skipped: int = 0,
                   sessions: SessionTable = DEFAULT_SESSIONS) -> SpreadProfile | None:
    """The SpreadProfile of recorded tick days (each ts_utc, bid, ask, as `ticks.load_day` returns), or None when no
    day has a valid tick (finite, ask >= bid > 0). Each day is reduced to value counts before the next is read, so memory
    stays at the number of distinct spreads, not ticks."""
    acc: dict[str, pd.Series] = {}
    n_days = n_ticks = 0
    for t in days:
        t = t[(t["ask"] >= t["bid"]) & (t["bid"] > 0) & np.isfinite(t["ask"] - t["bid"])] if not t.empty else t
        if t.empty:
            continue
        n_days += 1
        n_ticks += len(t)
        idx = pd.DatetimeIndex(pd.to_datetime(t["ts_utc"], utc=True))
        spread = pd.Series((t["ask"] - t["bid"]).to_numpy(dtype=float).round(SPREAD_DECIMALS))
        hours = [f"h{h:02d}" for h in idx.hour]
        sess = np.where(sessions.is_open(idx), sessions.session_label(idx), "")
        for cells in (pd.Series(hours), pd.Series([f"s{x}" for x in sess])):
            for key, part in spread.groupby(cells.to_numpy()):
                cell = str(key)
                if cell == "s":                    # out of session: in the hour profile only
                    continue
                vc = part.value_counts()
                acc[cell] = vc if cell not in acc else acc[cell].add(vc, fill_value=0)
    if not n_days:
        return None
    return SpreadProfile(first_day=first_day, last_day=last_day, days=n_days, days_skipped=days_skipped,
                         n_ticks=n_ticks,
                         by_session={k[1:]: _quantiles(v) for k, v in sorted(acc.items()) if k[0] == "s"},
                         by_hour={k[1:]: _quantiles(v) for k, v in sorted(acc.items()) if k[0] == "h"})


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
        """None when either rate is unmeasured or not a finite number (the cost table then keeps no swap and readers
        charge the prior; SwapSpec refuses a non-finite rate, so one bad reading never fails the nightly job)."""
        long, short = self.swap_long_usd_per_lot, self.swap_short_usd_per_lot
        if long is None or short is None or not (np.isfinite(long) and np.isfinite(short)):
            return None
        triple = default_triple_weekday if self.swap_triple_weekday is None else self.swap_triple_weekday
        return SwapSpec(long_usd_per_lot=long, short_usd_per_lot=short, triple_weekday=triple, server_tz=server_tz)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=1))
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "BrokerTerms | None":
        p = Path(path)
        return load_record(BrokerTerms, json.loads(p.read_text()), source="broker_terms") if p.exists() else None


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
    # the broker's swap per lot (100 oz) per night, broker sign (negative = paid); None until the terminal reports it.
    # Finite only (BACKLOG 36 review MEDIUM): a NaN table fails to load, and every reader falls back to the prior
    swap_long_usd_per_lot: float | None = Field(None, allow_inf_nan=False)
    swap_short_usd_per_lot: float | None = Field(None, allow_inf_nan=False)
    swap_triple_weekday: int | None = Field(None, ge=0, le=4)
    commission_measured: bool = False          # commission from the terminal's deals (else the settings value)
    commission_lots: float = 0.0               # lots of closed positions the measured commission rests on
    spread_profile: SpreadProfile | None = None   # the recorded broker ticks by hour and session (reported only)
    probe_slippage: dict[str, ProbeSlippage] = Field(default_factory=dict)   # by session; reported beside `slippage`
    n_probe_fills: int = 0                     # scheduled-probe entry fills seen (never part of n_fills)

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
        return load_record(CostTable, json.loads(p.read_text()), source="cost_table")


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


def probe_entry_fills(rows: Iterable["ProbeRecord"]) -> list["ProbeRecord"]:
    """The rows that count: scheduled (`cost_measurement`) entry legs the broker filled (done retcode, both prices)."""
    return [r for r in rows if r.purpose == "cost_measurement" and r.retcode == PROBE_RETCODE_DONE
            and r.requested_price is not None and r.fill_price is not None]


def probe_slippage(rows: Iterable["ProbeRecord"], prior_usd: float, min_fills: int = PROBE_MIN_FILLS,
                   sessions: SessionTable = DEFAULT_SESSIONS) -> dict[str, ProbeSlippage]:
    """Scheduled-probe entry slippage by session (prereg-cost-measurement-2026-10 s0.3/s5): "Demo slippage only raises
    costs ... Research slippage is max(0.15, measured)". Only `cost_measurement` entry legs with a done retcode and both
    prices count. A session with fewer than `min_fills` is absent (the prior stands; sessions are never pooled). The
    charge is max(prior, mean); favourable slippage is reported in `mean` (with the 95% day-block bootstrap interval)
    and never credited."""
    t: list[pd.Timestamp] = []
    v: list[float] = []
    for r in probe_entry_fills(rows):
        assert r.requested_price is not None and r.fill_price is not None
        t.append(pd.Timestamp(r.sent_utc))
        v.append(r.side * (r.fill_price - r.requested_price))
    if not t:
        return {}
    idx = pd.DatetimeIndex(pd.to_datetime(t, utc=True))
    labels = np.asarray(sessions.session_label(idx))
    slip = np.asarray(v, dtype=float)
    days = np.asarray(idx.strftime("%Y-%m-%d"))
    out: dict[str, ProbeSlippage] = {}
    rng = np.random.default_rng(PROBE_BOOTSTRAP_SEED)
    for s in SESSIONS:
        m = labels == s
        if int(m.sum()) < min_fills:
            continue
        x, d = slip[m], days[m]
        _, inv = np.unique(d, return_inverse=True)
        sums = np.bincount(inv, weights=x)
        cnts = np.bincount(inv).astype(float)
        pick = rng.integers(0, len(sums), size=(PROBE_BOOTSTRAP_RESAMPLES, len(sums)))
        means = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
        lo, hi = (float(q) for q in np.percentile(means, [2.5, 97.5]))
        mean = float(x.mean())
        raised = mean > prior_usd
        out[s] = ProbeSlippage(n=int(m.sum()), mean=mean, ci_low=lo, ci_high=hi,
                               charged=max(prior_usd, mean), raised=raised)
    return out


def apply_probe_slippage(table: CostTable, probe: dict[str, ProbeSlippage], n_probe_fills: int) -> CostTable:
    """Raise a session's market cell to the probe charge where it exceeds the prior; a cell is never lowered, and a
    real-fill cell already above the probe charge stays (the larger of the two is charged)."""
    slip = dict(table.slippage)
    for s, p in probe.items():
        cur = slip.get(f"{s}:market")
        if p.raised and (cur is None or p.charged > cur.mean):
            slip[f"{s}:market"] = SlippageStat(mean=p.charged, n=p.n, from_prior=False)
    return table.model_copy(update={"slippage": slip, "probe_slippage": probe, "n_probe_fills": n_probe_fills})


def build_cost_table(account_id: str, ticks: pd.DataFrame, fills: pd.DataFrame, *, commission_per_lot_side_usd: float,
                     slippage_prior_usd: float, min_fills: int = 50, now: UtcTimestamp | None = None,
                     swap: SwapSpec | None = None, commission_measured: bool = False,
                     notes: list[str] | None = None, spread_profile: SpreadProfile | None = None) -> CostTable:
    sp = spread_table(ticks)
    notes = list(notes or []) + ([] if sp else ["no in-session ticks: no round-trip cost; the engine keeps its configured fallback"])
    return CostTable(account_id=account_id, built_utc=now or pd.Timestamp.now("UTC"), spread=sp,
                     slippage=slippage_table(fills, prior_usd=slippage_prior_usd, min_fills=min_fills),
                     commission_per_lot_side_usd=commission_per_lot_side_usd, slippage_prior_usd=slippage_prior_usd,
                     n_ticks=len(ticks), n_fills=len(fills), notes=notes,
                     swap_long_usd_per_lot=None if swap is None else swap.long_usd_per_lot,
                     swap_short_usd_per_lot=None if swap is None else swap.short_usd_per_lot,
                     swap_triple_weekday=None if swap is None else swap.triple_weekday,
                     commission_measured=commission_measured, spread_profile=spread_profile)


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
    n_slip = sum(not s.from_prior for s in table.slippage.values())
    parts = [f"cost table {table.account_id} built {table.built_utc:%Y-%m-%d}",
             "commission measured" if table.commission_measured else "commission from settings",
             "swap measured" if swap is not None else "swap prior (table has no measured swap)",
             f"slippage from {n_slip} measured cells" if n_slip else "slippage prior (no measured cell)"]
    if table.spread_profile is not None:
        parts.append(f"broker spread profile {table.spread_profile.days} days (reported, not charged)")
    return table.extra_cost_usd(), swap or prior, ", ".join(parts)


# ------------------------------------------------------------------ the published table (release costs-v1, research)
COSTS_TAG = "costs-v1"
COSTS_ASSET = "costs_measured.json"


class FieldSource(FrozenRecord):
    """Where one cost component comes from: the broker's measurement, the settings prior, or (slippage only) a mix of
    measured cells and cells still at the prior, each cell then saying which (`SlippageStat.from_prior`); and the
    count it rests on (ticks for the spread, fills for slippage, lots for commission, days for the spread profile;
    None for swap, the terminal's quoted rate)."""
    source: Literal["measured", "prior", "mixed"]
    n: float | None = None


REQUIRED_FIELDS = ("spread", "slippage", "commission", "swap")


def slippage_source(slippage: dict[str, SlippageStat]) -> Literal["measured", "prior", "mixed"]:
    """measured when every cell is, prior when none is, mixed otherwise."""
    measured = [not s.from_prior for s in slippage.values()]
    return "measured" if measured and all(measured) else "mixed" if any(measured) else "prior"


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
    fields: dict[str, FieldSource]             # spread, slippage, commission, swap (+ spread_profile, probe_slippage when present)
    spread_profile: SpreadProfile | None = None   # the recorded broker ticks by hour and session (reported only)
    probe_slippage: dict[str, ProbeSlippage] = Field(default_factory=dict)   # scheduled-probe slippage by session (demo venue)
    n_probe_fills: int = 0

    @model_validator(mode="after")
    def _labels_match_values(self) -> "PublishedCostTable":
        """Refuse a component without a label and a label that disagrees with the values it labels (a prior passed
        off as a measurement, or the reverse). Commission is a number either way: its label is the builder's."""
        f = self.fields
        missing = [k for k in REQUIRED_FIELDS if k not in f]
        if missing:
            raise ValueError(f"cost components without a label: {missing}")
        swap_ok = self.swap_long_usd_per_lot is not None and self.swap_short_usd_per_lot is not None
        want = {"spread": "measured" if self.spread else "prior", "slippage": slippage_source(self.slippage),
                "swap": "measured" if swap_ok else "prior"}
        wrong = {k: f[k].source for k, v in want.items() if f[k].source != v}
        if wrong:
            raise ValueError(f"labels disagree with the values: {wrong}, expected {want}")
        if f["commission"].source == "mixed":
            raise ValueError("commission is measured or the prior, never mixed")
        if (self.spread_profile is None) != ("spread_profile" not in f):
            raise ValueError("the spread profile and its label come together")
        if "spread_profile" in f and f["spread_profile"].source != "measured":
            raise ValueError("a spread profile is a measurement: its label must say so")
        if bool(self.probe_slippage) != ("probe_slippage" in f):
            raise ValueError("the probe slippage and its label come together")
        if "probe_slippage" in f and f["probe_slippage"].source != "measured":
            raise ValueError("probe slippage is a measurement: its label must say so")
        return self

    @classmethod
    def from_table(cls, table: CostTable, broker: str) -> "PublishedCostTable":
        swap_ok = table.swap_long_usd_per_lot is not None and table.swap_short_usd_per_lot is not None
        fields = {"spread": FieldSource(source="measured" if table.spread else "prior", n=table.n_ticks),
                  "slippage": FieldSource(source=slippage_source(table.slippage), n=table.n_fills),
                  "commission": FieldSource(source="measured" if table.commission_measured else "prior",
                                            n=table.commission_lots),
                  "swap": FieldSource(source="measured" if swap_ok else "prior")}
        if table.spread_profile is not None:
            fields["spread_profile"] = FieldSource(source="measured", n=table.spread_profile.days)
        if table.probe_slippage:
            fields["probe_slippage"] = FieldSource(source="measured", n=table.n_probe_fills)
        return cls(broker=broker, measured_at=table.built_utc, spread=table.spread, slippage=table.slippage,
                   probe_slippage=table.probe_slippage, n_probe_fills=table.n_probe_fills,
                   slippage_prior_usd=table.slippage_prior_usd,
                   commission_per_lot_side_usd=table.commission_per_lot_side_usd,
                   swap_long_usd_per_lot=table.swap_long_usd_per_lot, swap_short_usd_per_lot=table.swap_short_usd_per_lot,
                   swap_triple_weekday=table.swap_triple_weekday, n_ticks=table.n_ticks, n_fills=table.n_fills,
                   fields=fields, spread_profile=table.spread_profile)

    def to_cost_table(self) -> CostTable:
        """The research view: a CostTable labelled with the broker (research_costs names it in the report)."""
        commission = self.fields.get("commission")
        return CostTable(account_id=f"{self.broker} (published)", built_utc=self.measured_at, spread=self.spread,
                         slippage=self.slippage, commission_per_lot_side_usd=self.commission_per_lot_side_usd,
                         slippage_prior_usd=self.slippage_prior_usd, n_ticks=self.n_ticks, n_fills=self.n_fills,
                         swap_long_usd_per_lot=self.swap_long_usd_per_lot,
                         swap_short_usd_per_lot=self.swap_short_usd_per_lot, swap_triple_weekday=self.swap_triple_weekday,
                         commission_measured=commission is not None and commission.source == "measured",
                         commission_lots=(commission.n or 0.0) if commission is not None else 0.0,
                         spread_profile=self.spread_profile, probe_slippage=self.probe_slippage,
                         n_probe_fills=self.n_probe_fills)


def publishable(table: CostTable, broker: str) -> tuple[PublishedCostTable | None, str]:
    """The table to publish, or None and why not. K3 (costs-binding-2026-10-18.md): "Publish once spread and swap are
    measured, with slippage marked `fields.slippage.source = "prior"`": refused while swap is unmeasured or no spread
    is (neither the engine's session table nor the recorded-tick profile). Fills are not needed: a slippage cell below
    `research.min_fills_for_slippage` fills keeps the prior and is labelled so, as is a commission no deal has shown."""
    if table.swap_long_usd_per_lot is None or table.swap_short_usd_per_lot is None:
        return None, "swap not measured by the terminal yet"
    if not table.spread and table.spread_profile is None:
        return None, "no measured spread (no engine tick sample and no recorded broker-tick profile)"
    return PublishedCostTable.from_table(table, broker), "ok"


def load_research_cost_table(path: str | Path) -> CostTable | None:
    """A cost table for research from `path`: the published format (release costs-v1) or a local CostTable file.
    None when the file does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    if isinstance(raw, dict) and "broker" in raw and "schema_version" in raw:
        return load_record(PublishedCostTable, raw, source="costs-v1").to_cost_table()
    return load_record(CostTable, raw, source="cost_table")
