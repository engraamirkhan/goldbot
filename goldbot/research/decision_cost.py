"""cost(x): the cost of record known at decision time, in R (ADR 0098 amendment R-I: "cost(x) is the ub95 cost of
record known at decision time, never the realised cost"; ADR 0102 point 3 as amended by the quant review of PR #461).

The replay's realised leg r_mid − r = k·RT + k·OV·1[stop] − swap/risk (k = 1e-4 · entry / risk_usd) carries the exit
outcome (the overshoot is charged on stop exits only, a gapped stop included) and the nights actually held. Fed to a
keep rule it leaks the outcome. cost(x) replaces both outcome terms with their expectation at decision:

    cost(x) = k · (RT_BP + OVERSHOOT_BP · p_stop[arm]) + E[nights | decision, max_hold] · k · 1e4 · |swap rate(side)|

- p_stop[arm]: the arm's stop share on learn rows labelled before the judged block (`CostPriors`, per arm, never
  pooled; an arm unseen on learn takes the pooled share and is counted in `CostPriors.unseen`).
- E[nights | decision, max_hold] = rollover nights of the full hold [decision, decision + max_hold_s] (decision-time
  known) x the arm's learn ratio realised nights / full-hold nights.
- |swap rate(side)|: the research swap spec (`SwapSpec.research_shipped`, a rate on the trade's own notional) per
  night as a fraction of price, absolute (a received swap is never credited).
- RT_BP / OVERSHOOT_BP are the W2 cost-of-record values the replay labels charge (pinned equal to history_replay's by a
  test). They are the cost of record's point values; a measured ub95 per broker x session replaces them when the
  card names one.

k comes from the label's persisted `entry` and `risk_usd` (history_replay.label, from 2026-11-25). Caches labelled
before that carry only r and r_mid: `legacy_k` backs k out of the realised leg exactly, given the stop flag (r_mid <=
−1 at mid, gapped stops included) and the nights held. k is a decision-time quantity, so the back-out is legitimate;
only its route reads the label.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights

RT_BP = 0.575            # = history_replay.RT_BP (W2 bps round trip)
OVERSHOOT_BP = 1.75      # = history_replay.OVERSHOOT_BP (stop-exit overshoot)
COST_RECORD_PREFIX = "srv3-cost-"   # every frozen D-3 cost of record (srv3_cost_record); w2-point is a floor, never one


class CostNotOfRecord(PermissionError):
    """A gate read whose rows are not all charged at one frozen D-3 cost of record."""


def require_cost_of_record(versions: Any, what: str) -> str:
    """The one cost version every row of a gate read was charged at; refuses None (no costs), an empty read, any row
    whose `cost_version` is not a recorded cost of record ("srv3-cost-..."; w2-point is the floor of an unpriced
    label, never a gate read: quant on #489) and a read mixing versions."""
    if versions is None:
        raise CostNotOfRecord(f"{what}: no costs: a gate read is charged at the D-3 cost of record")
    v = [str(x) for x in np.asarray(versions, dtype=object).ravel()]
    if not v:
        raise CostNotOfRecord(f"{what}: no rows")
    bad = sorted({x for x in v if not x.startswith(COST_RECORD_PREFIX)})
    if bad:
        raise CostNotOfRecord(f"{what}: rows at {bad[:3]} are not charged at a cost of record ({COST_RECORD_PREFIX}*)")
    if len(set(v)) > 1:
        raise CostNotOfRecord(f"{what}: one read, one cost: {sorted(set(v))[:3]}")
    return v[0]
STOP_EPS = 1e-9


def swap_spec() -> SwapSpec:
    """The swap the history replay charges (`run_arm_backtest`: `SwapSpec.research_shipped()`)."""
    return SwapSpec.research_shipped()


def is_stop(labels: pd.DataFrame) -> np.ndarray:
    """Stop exits: `barrier_hit == "stop"` when the label carries it, else r_mid <= −1 at mid (a gapped stop is below
    −1; legacy caches)."""
    if "barrier_hit" in labels.columns:
        return (labels["barrier_hit"].astype(str) == "stop").to_numpy()
    return labels["r_mid"].to_numpy(dtype=float) <= -1.0 + STOP_EPS


def _dec(labels: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(labels["decision_utc"], utc=True))


def nights_full_hold(labels: pd.DataFrame, swap: SwapSpec | None = None) -> np.ndarray:
    """Rollover nights over the whole possible hold [decision, decision + max_hold_s]: known at decision."""
    sw = swap or swap_spec()
    d = _dec(labels)
    end = d + pd.to_timedelta(labels["max_hold_s"].to_numpy(dtype=float), unit="s")
    return rollover_nights(d, end, sw.server_tz, sw.triple_weekday, sw.seven_night).astype(float)


def nights_held(labels: pd.DataFrame, swap: SwapSpec | None = None) -> np.ndarray:
    """Rollover nights actually held [decision, label exit] (an outcome: priors and the legacy back-out only)."""
    sw = swap or swap_spec()
    return rollover_nights(_dec(labels), pd.DatetimeIndex(pd.to_datetime(labels["label_exit_utc"], utc=True)),
                           sw.server_tz, sw.triple_weekday, sw.seven_night).astype(float)


def _rate_signed(side: np.ndarray, swap: SwapSpec) -> np.ndarray:
    """Swap per night as a fraction of the entry price, broker sign (negative = paid)."""
    if swap.ref_price_usd is None:
        raise ValueError("decision_cost: the swap spec needs a reference price (a rate on notional)")
    return swap.usd_per_oz(np.asarray(side, dtype=float)) / swap.ref_price_usd


def price_risk_k(labels: pd.DataFrame) -> np.ndarray:
    """k = 1e-4 · entry / risk_usd: R per bp of entry price, from the label's persisted columns."""
    k = 1e-4 * labels["entry"].to_numpy(dtype=float) / labels["risk_usd"].to_numpy(dtype=float)
    if not np.isfinite(k).all() or (k <= 0).any():
        raise ValueError("decision_cost: entry / risk_usd must be finite and positive")
    return k


def legacy_k(labels: pd.DataFrame, swap: SwapSpec | None = None) -> np.ndarray:
    """k backed out of r_mid − r = k (RT + OV·1[stop] − 1e4 · nights held · signed swap rate), for caches labelled
    before entry / risk_usd were persisted."""
    sw = swap or swap_spec()
    realised = (labels["r_mid"] - labels["r"]).to_numpy(dtype=float)
    per_k = (RT_BP + OVERSHOOT_BP * is_stop(labels)
             - 1e4 * nights_held(labels, sw) * _rate_signed(labels["side"].to_numpy(dtype=float), sw))
    if not (per_k > 0).all():  # a future short-swap credit must not flip k's sign unnoticed (quant, PR #461)
        raise ValueError("legacy_k: non-positive cost divisor; check the swap specification")
    return realised / per_k


def row_k(labels: pd.DataFrame, swap: SwapSpec | None = None) -> np.ndarray:
    """k from entry / risk_usd when the label carries them, else the legacy back-out."""
    return price_risk_k(labels) if {"entry", "risk_usd"} <= set(labels.columns) else legacy_k(labels, swap)


class CostPriors(FrozenRecord):
    """Per-arm expectations fitted on learn rows labelled before the judged block."""
    p_stop: dict[str, float]
    night_share: dict[str, float]           # realised nights / full-hold nights (1.0 where no full-hold night)
    p_stop_pooled: float
    night_share_pooled: float
    n_rows: int
    labelled_before: str


def priors(learn: pd.DataFrame, *, before: pd.Timestamp, swap: SwapSpec | None = None) -> CostPriors:
    """`CostPriors` from the learn rows whose label closed before `before` (others are refused, not dropped)."""
    if (pd.to_datetime(learn["label_exit_utc"], utc=True) >= before).any():
        raise ValueError("decision_cost.priors: a learn label closes at or after the judged block's start")
    if len(learn) == 0:
        raise ValueError("decision_cost.priors: no learn rows")
    stop = is_stop(learn).astype(float)
    held, full = nights_held(learn, swap), nights_full_hold(learn, swap)
    arm = learn["arm"].astype(str).to_numpy()
    p, ns = {}, {}
    for a in np.unique(arm):
        m = arm == a
        p[str(a)] = float(stop[m].mean())
        ns[str(a)] = float(held[m].sum() / full[m].sum()) if full[m].sum() > 0 else 1.0
    return CostPriors(p_stop=p, night_share=ns, p_stop_pooled=float(stop.mean()),
                      night_share_pooled=float(held.sum() / full.sum()) if full.sum() > 0 else 1.0,
                      n_rows=len(learn), labelled_before=before.isoformat())


def _per_arm(arm: np.ndarray, table: Mapping[str, float], pooled: float) -> np.ndarray:
    return np.array([table.get(str(a), pooled) for a in arm], dtype=float)


def cost_x(rows: pd.DataFrame, pri: CostPriors, *, k: np.ndarray | None = None,
           swap: SwapSpec | None = None) -> np.ndarray:
    """cost(x) in R per row, from decision-time quantities only: arm, side, decision_utc, max_hold_s and k."""
    sw = swap or swap_spec()
    kk = row_k(rows, sw) if k is None else np.asarray(k, dtype=float)
    arm = rows["arm"].astype(str).to_numpy()
    p = _per_arm(arm, pri.p_stop, pri.p_stop_pooled)
    e_nights = nights_full_hold(rows, sw) * _per_arm(arm, pri.night_share, pri.night_share_pooled)
    rate = np.abs(_rate_signed(rows["side"].to_numpy(dtype=float), sw))
    return kk * (RT_BP + OVERSHOOT_BP * p) + e_nights * kk * 1e4 * rate
