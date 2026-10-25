"""Exploration arms on the historical 1m bars (E10): the arm loader, the hard holdout guard, per-era cost pricing and
the per-arm prior. Pure functions; `scripts/run_arm_backtest.py` runs one arm on one block, the workflow
`research-arms-matrix.yml` fans arm x 2-year block out and `scripts/aggregate_arm_priors.py` builds the priors.

The hard holdout (nothing after 2025-09-30 is read) is independent of `research.holdout_from` on purpose: the loader
cuts the bars, and the guard asserts nothing at or after it survived, so a loosened setting cannot widen this run."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import utc_index

HOLDOUT_FROM = pd.Timestamp("2025-10-01", tz="UTC")     # first instant that is never read
LAST_DAY = pd.Timestamp("2025-09-30", tz="UTC")
FIRST_DAY = pd.Timestamp("2015-01-01", tz="UTC")
OOS_FROM = pd.Timestamp("2019-01-01", tz="UTC")         # priors use 2019-2025 trades only
PRIOR_OOS_FACTOR = 0.5
PRIOR_WEIGHT_CAP = 50.0
CONTRACT_OZ = 100.0
TIMEFRAMES = ("1m", "15m", "1h", "4h")


# ---------------------------------------------------------------- holdout guard
def assert_block_allowed(start: pd.Timestamp, end: pd.Timestamp) -> None:
    if end >= HOLDOUT_FROM or start >= HOLDOUT_FROM:
        raise ValueError(f"block {start.date()}..{end.date()} reaches the holdout (from {HOLDOUT_FROM.date()})")
    if start > end:
        raise ValueError("block starts after it ends")


def cut_at_holdout(frame: pd.DataFrame, col: str = "ts_utc") -> pd.DataFrame:
    """Rows strictly before the holdout, unit-safe (pandas s/ms/us/ns)."""
    return frame.loc[np.asarray(utc_index(frame[col]) < HOLDOUT_FROM)].reset_index(drop=True)


def assert_no_holdout_rows(frame: pd.DataFrame, col: str = "ts_utc") -> None:
    if len(frame) and utc_index(frame[col]).max() >= HOLDOUT_FROM:
        raise AssertionError(f"holdout rows present in {col} (>= {HOLDOUT_FROM.date()})")


def blocks() -> list[tuple[str, str]]:
    """Two-year blocks 2015-01-01 .. 2025-09-30 (the last is the 9 months left before the holdout)."""
    out = []
    for y in range(2015, 2026, 2):
        end = min(pd.Timestamp(f"{y + 1}-12-31", tz="UTC"), LAST_DAY)
        out.append((f"{y}-01-01", str(end.date())))
    return out


def parse_block(text: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        a, b = text.split(":")
        start, end = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
    except ValueError as e:
        raise ValueError(f"block must be <start>:<end> (YYYY-MM-DD), got {text!r}") from e
    assert_block_allowed(start, end)
    return start, end


# ---------------------------------------------------------------- era costs
class EraCost(FrozenRecord):
    from_: pd.Timestamp = Field(alias="from")
    spread_floor_usd: float = Field(ge=0)       # the quoted spread a retail broker would not go under, per oz
    commission_per_lot_side_usd: float = Field(ge=0)
    slippage_usd: float = Field(ge=0)           # per oz per side

    def fixed_usd(self) -> float:
        """Round trip per oz beyond the spread: two slippages and both commissions."""
        return 2 * self.slippage_usd + 2 * self.commission_per_lot_side_usd / CONTRACT_OZ


class EraCosts(FrozenRecord):
    eras: tuple[EraCost, ...]

    @model_validator(mode="after")
    def _ordered(self) -> EraCosts:
        starts = [e.from_ for e in self.eras]
        if not starts or starts[0] > FIRST_DAY:
            raise ValueError("era costs must start on or before 2015-01-01")
        if starts != sorted(set(starts)):
            raise ValueError("era costs must be in strictly increasing order of `from`")
        return self

    def at(self, ts: pd.Timestamp) -> EraCost:
        cur = self.eras[0]
        for e in self.eras:
            if e.from_ <= ts:
                cur = e
        return cur


def load_era_costs(path: Path) -> EraCosts:
    raw = yaml.safe_load(Path(path).read_text())
    eras = [{**e, "from": pd.Timestamp(str(e["from"]), tz="UTC")} for e in raw["eras"]]
    return EraCosts.model_validate({"eras": eras})


def extra_cost_usd(entry_ts: pd.DatetimeIndex, bar_spread: np.ndarray, table: EraCosts) -> np.ndarray:
    """Round-trip cost per oz beyond what the labels already pay (they fill ask in, bid out, so the bar's own spread is
    paid): the era's slippage and commission, plus the top-up when the bar's spread is under the era's floor
    (Dukascopy's quote in the early years was tighter than a retail account could trade)."""
    out = np.empty(len(entry_ts))
    for i, (t, s) in enumerate(zip(entry_ts, np.asarray(bar_spread, dtype=float), strict=True)):
        era = table.at(t)
        top_up = max(era.spread_floor_usd - (s if np.isfinite(s) else 0.0), 0.0)
        out[i] = era.fixed_usd() + top_up
    return out


# ---------------------------------------------------------------- arms
class Arm(FrozenRecord):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    kind: Literal["specialist", "random"]
    timeframe: Literal["1m", "15m", "1h", "4h"]
    family: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    # random arms: probability a decision bar opens a trade, and the barriers in ATR (same shape for both controls)
    p_entry: float = Field(0.02, gt=0, le=1)
    target_atr: float = Field(1.5, gt=0)
    stop_atr: float = Field(1.0, gt=0)
    max_bars: int = Field(16, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> Arm:
        if self.kind == "random" and self.seed is None:
            raise ValueError(f"{self.id}: a random arm needs a seed")
        if self.kind == "specialist":
            from goldbot.specialists import SPECIALISTS
            if self.family not in SPECIALISTS:
                raise ValueError(f"{self.id}: unknown specialist family {self.family!r}")
            if self.timeframe == "1m":
                raise ValueError(f"{self.id}: specialist arms run on 15m, 1h or 4h")
        return self


def load_arms(path: Path) -> list[Arm]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    out = [Arm.model_validate(a) for a in raw.get("arms", [])]
    ids = [a.id for a in out]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate arm ids: {dup}")
    return out


def random_candidates(arm: Arm, n_bars: int) -> pd.DataFrame:
    """Seeded random control: each bar enters with probability `p_entry`, side a fair coin. Same barriers as a
    specialist's, so the control prices the cost and barrier geometry with no edge."""
    rng = np.random.default_rng(arm.seed)
    take = rng.random(n_bars) < arm.p_entry
    side = np.where(rng.random(n_bars) < 0.5, 1, -1)
    idx = np.flatnonzero(take)
    return pd.DataFrame({"idx": idx, "side": side[idx]})


# ---------------------------------------------------------------- priors
def build_prior(trades: pd.DataFrame) -> dict[str, float]:
    """Prior for one arm from its net-R trades: OOS (2019-2025) trades only, their weight halved and capped at 50
    trades. `trades`: ts_utc (entry), net_r."""
    ts = utc_index(trades["ts_utc"])
    oos = trades.loc[np.asarray(ts >= OOS_FROM), "net_r"].astype(float)
    n = int(len(oos))
    if n == 0:
        return {"n_oos": 0, "mean_r": 0.0, "weight": 0.0}
    return {"n_oos": n, "mean_r": float(oos.mean()), "weight": float(min(PRIOR_OOS_FACTOR * n, PRIOR_WEIGHT_CAP))}
