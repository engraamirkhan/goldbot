"""Exploration arms on the historical 1m bars (E10, ARM-SPEC 2026-10-25): the arm loader with config hashes, the hard
holdout guard, the frozen cost model and the per-arm prior. Pure functions; `goldbot.research.arm_rules` generates each
rule's candidates, `scripts/run_arm_backtest.py` runs one arm on one block, the workflow `research-arms-matrix.yml` fans
arm x 2-year block out and `scripts/aggregate_arm_priors.py` builds the priors.

The hard holdout (nothing after 2025-09-30 is read) is independent of `research.holdout_from` on purpose: the loader
cuts the bars, and the guard asserts nothing at or after it survived, so a loosened setting cannot widen this run."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import yaml
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord
from goldbot.data.calendar import SessionWindow
from goldbot.data.timeutil import utc_index

HOLDOUT_FROM = pd.Timestamp("2025-10-01", tz="UTC")     # first instant that is never read
LAST_DAY = pd.Timestamp("2025-09-30", tz="UTC")
OOS_FROM = pd.Timestamp("2019-01-01", tz="UTC")         # priors use 2019-2025 trades only
PRIOR_MEAN_FACTOR = 0.5
PRIOR_WEIGHT_CAP = 50.0
PRIOR_WEIGHT_PER_TRADE = 0.01                           # weight = min(50, n / 100)
# wave 2 lane W2d (goldbot.research.arm_rules_clock_box): WFRI, KA15/XC15, the H-80 box family
CLOCK_BOX_RULES = ("friday_long", "clock_anchor", "clock_anchor_control", "box_next")
# H-81 scalp-volume families Z1-Z10 and their controls (goldbot.research.arm_rules_scalp)
SCALP_RULES = ("tick_surge", "run_fade", "round_break", "round_reject", "extreme_break", "nr4_break",
               "halfhour_persist", "absorb_fade", "htf_reclaim", "three_horizon", "scalp_control")
Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
RULES = ("random_bracket", "session_vwap_fade", "opening_range_break", "sweep_absorb", "box_break_close",
         "ema_pullback", "tsmom", "specialist", *CLOCK_BOX_RULES, "blocked",
         # wave 2 (ARM-SPEC section 8)
         "squeeze_release", "donchian_break", "low30_touch", "low30_control", "pdc_break", "ob_retest",
         # G-1 (prereg-2026-10-26-time-structure.md section 3, G1A/G1C): frozen in the spec with status blocked until
         # its generator lands in arm_rules.RULE_FUNCS (engine lane E2); a blocked arm never runs
         "level_break",
         # wave 2 lane W2a (H-70 F1 / F-2)
         "hurst_band_break", "hurst_band_break_control", "htf_stretch_break", "htf_stretch_break_control",
         *SCALP_RULES)


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


# ---------------------------------------------------------------- the frozen cost model
class ArmCosts(FrozenRecord):
    """cost-model-1m-2026-10-25.md, conservative case: p90 spread both legs + slippage upper CI + commission = $0.23 per
    oz round trip (the bars are priced at mid, so the spread is inside it, never charged twice). The stop-exit overshoot
    ($0.70 mean, n = 14, unverified: S0R measures it) is reported both ways."""
    round_trip_usd: float = Field(ge=0)
    overshoot_scenarios_usd: tuple[float, ...]
    # the spread part of `round_trip_usd` (the cost model's p90 spread, both legs; arm_costs.yaml: "p90 spread both legs
    # 0.12"): a read that charges a measured spread instead (KA15 stratum T, arms 8.13 condition 2) adds what the
    # measured spread exceeds it by, so it never charges less than the frozen round trip
    spread_both_legs_usd: float = Field(0.12, ge=0)


def load_costs(path: Path) -> ArmCosts:
    raw = yaml.safe_load(Path(path).read_text())
    extra = {"spread_both_legs_usd": float(raw["spread_both_legs_usd"])} if "spread_both_legs_usd" in raw else {}
    return ArmCosts(round_trip_usd=float(raw["round_trip_usd"]),
                    overshoot_scenarios_usd=tuple(float(x) for x in raw["overshoot_scenarios_usd"]), **extra)


def net_r(move_usd: np.ndarray, risk_usd: np.ndarray, stop_exit: np.ndarray, costs: ArmCosts,
          overshoot_usd: float, extra_usd: np.ndarray | float = 0.0) -> np.ndarray:
    """Net result in R: the signed move per oz (swap included) less the round trip, less `overshoot_usd` on a stop exit,
    less `extra_usd` (a measured spread above the frozen one), over the candidate's own stop distance."""
    pnl = np.asarray(move_usd, dtype=float) - costs.round_trip_usd - overshoot_usd * np.asarray(stop_exit, dtype=float)
    return (pnl - np.asarray(extra_usd, dtype=float)) / np.asarray(risk_usd, dtype=float)


# ---------------------------------------------------------------- arms
class Arm(FrozenRecord):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    rule: str
    timeframe: Timeframe
    params: dict[str, Any] = Field(default_factory=dict)
    max_open: int = Field(1, ge=1)
    status: Literal["run", "blocked"] = "run"
    note: str = ""
    # ADR 0043 broker amendment (2026-10-26): "an arm on a second broker is a new hypothesis: a distinct arm id". A
    # broker twin copies `twin_of`'s rule body (rule, timeframe, params, max_open; checked at load) under its own id
    # and hash, and has no history run of its own (`runnable_ids`): its broker starts from the flat prior
    twin_of: str | None = None
    # a forward-only arm has no history run (`runnable_ids`), only live fills from a flat prior: the time-structure
    # arms ("they are not run on history (no H-60 look; flat priors)", arms-2026-10-25.md 7a), KA "forward-only: no
    # confirmation credit" (8.8) and H-80's box family, whose 2018+ half is its evaluator's single confirmation read.
    # A run policy, like status: not in the hash
    forward_only: bool = False
    config_hash: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Arm:
        if self.rule not in RULES:
            raise ValueError(f"{self.id}: unknown rule {self.rule!r}; known: {RULES}")
        if self.status == "blocked" and not self.note:
            raise ValueError(f"{self.id}: a blocked arm needs a note saying what unblocks it")
        if "window_utc" in self.params and "window_local" in self.params:
            raise ValueError(f"{self.id}: an arm has one decision window, window_utc or window_local, not both")
        try:
            local_window(self.params)
            if self.rule == "specialist":
                # arms expansion 2026-10-26: an unknown or blocked family, preset, timeframe or config is refused at load
                from goldbot.research.arm_rules_specialist import build_specialist
                build_specialist(self)
            if self.rule in ("squeeze_release", "donchian_break", "low30_touch", "low30_control"):
                # ARM-SPEC 8 (wave 2): a timeframe the spec does not declare or a hold other than the frozen one
                from goldbot.research.arm_rules_channels import check_arm
                check_arm(self)
            if self.rule in CLOCK_BOX_RULES:
                from goldbot.research.arm_rules_clock_box import check
                check(self)
            if self.rule in SCALP_RULES:
                from goldbot.research.arm_rules_scalp import check as check_scalp
                check_scalp(self)
        except ValueError as exc:
            raise ValueError(f"{self.id}: {exc}") from exc
        return self


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def local_window(params: dict[str, Any]) -> SessionWindow | None:
    """The arm's `window_local` as a session window on city clocks, None without one. Spec form:
    `{"from": [zone, "HH:MM"], "to": [zone, "HH:MM"]}`, e.g. London open to the New York cash close
    `{"from": ["Europe/London", "08:00"], "to": ["America/New_York", "16:00"]}`. ARM-SPEC: "Clock times are UTC unless a
    local session clock is named"; a named clock is converted per date (`SessionWindow`, playbook D-1), so the window
    follows each city's own DST switch, the UK/US gap weeks included. A window that is not a positive span shorter
    than a day on every date of a year (both DST regimes) is refused at load."""
    spec = params.get("window_local")
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) != {"from", "to"}:
        raise ValueError(f"window_local needs exactly from and to, each [zone, HH:MM]; got {spec!r}")
    ends: list[tuple[str, time]] = []
    for key in ("from", "to"):
        end = spec[key]
        if not isinstance(end, list | tuple) or len(end) != 2:
            raise ValueError(f"window_local {key}: {end!r} is not [zone, HH:MM]")
        zone, hhmm = end
        try:
            ZoneInfo(str(zone))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"window_local {key}: unknown time zone {zone!r}") from exc
        if not isinstance(hhmm, str) or not _HHMM.match(hhmm):
            raise ValueError(f"window_local {key}: {hhmm!r} is not HH:MM")
        ends.append((str(zone), time(int(hhmm[:2]), int(hhmm[3:]))))
    w = SessionWindow(open_tz=ends[0][0], open=ends[0][1], close_tz=ends[1][0], close=ends[1][1])
    w.bounds_ns(np.arange(20454, 20454 + 366, dtype=np.int64))      # every date of 2026 (both regimes, 4 switches)
    return w


def arm_hash(arm: Arm) -> str:
    """The arm's frozen configuration: sha256 over id, rule, timeframe, params, max_open (status, note and
    forward_only are prose and run policy)."""
    body = {"id": arm.id, "rule": arm.rule, "timeframe": arm.timeframe, "params": arm.params, "max_open": arm.max_open}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_arms(path: Path, *, check_hash: bool = True) -> list[Arm]:
    """The arms of `path`. With `check_hash` every arm must carry a `config_hash` equal to its own: an arm edited after
    it was frozen is refused (ARM-SPEC: an edit needs a written amendment and a new arm id)."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    out = [Arm.model_validate(a) for a in raw.get("arms", [])]
    ids = [a.id for a in out]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate arm ids: {dup}")
    by_id = {a.id: a for a in out}
    for a in out:
        if a.twin_of is None:
            continue
        twin = by_id.get(a.twin_of)
        if twin is None or twin.twin_of is not None:
            raise ValueError(f"{a.id}: twin_of {a.twin_of!r} names no arm of this spec (a twin copies an original)")
        if (a.rule, a.timeframe, a.params, a.max_open) != (twin.rule, twin.timeframe, twin.params, twin.max_open):
            raise ValueError(f"{a.id}: a broker twin keeps {twin.id}'s rule body (rule, timeframe, params, max_open)")
    if check_hash:
        for a in out:
            if a.config_hash != arm_hash(a):
                raise ValueError(f"{a.id}: config_hash {a.config_hash!r} does not match the arm (expected "
                                 f"{arm_hash(a)!r}); an arm is frozen, amend it under a new id")
    return out


def runnable_ids(path: Path) -> list[str]:
    """The arms the historical matrix runs (H-60): every runnable arm but a broker twin, whose history is its
    original's (same rule body on the same bars) and whose broker takes no H-60 prior (ADR 0043 broker amendment),
    nor a forward-only arm (`Arm.forward_only`)."""
    return [a.id for a in load_arms(path) if a.status == "run" and a.twin_of is None and not a.forward_only]


# ---------------------------------------------------------------- priors
def build_prior(trades: pd.DataFrame, column: str = "net_r") -> dict[str, float]:
    """Prior for one arm from its per-trade net R (`column`): OOS (2019-2025) trades only; mean x 0.5; weight
    min(50, n / 100) trades. `trades`: ts_utc (entry) and the R column."""
    ts = utc_index(trades["ts_utc"])
    oos = trades.loc[np.asarray(ts >= OOS_FROM), column].astype(float)
    n = int(len(oos))
    if n == 0:
        return {"n_oos": 0, "mean_r": 0.0, "weight": 0.0}
    return {"n_oos": n, "mean_r": float(PRIOR_MEAN_FACTOR * oos.mean()),
            "weight": float(min(PRIOR_WEIGHT_CAP, PRIOR_WEIGHT_PER_TRADE * n))}
