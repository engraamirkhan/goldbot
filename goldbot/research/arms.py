"""Exploration arms on the historical 1m bars (E10, ARM-SPEC 2026-10-25): the arm loader with config hashes, the hard
holdout guard, the frozen cost model and the per-arm prior. Pure functions; `goldbot.research.arm_rules` generates each
rule's candidates, `scripts/run_arm_backtest.py` runs one arm on one block, the workflow `research-arms-matrix.yml` fans
arm x 2-year block out and `scripts/aggregate_arm_priors.py` builds the priors.

The hard holdout (nothing after 2025-09-30 is read) is independent of `research.holdout_from` on purpose: the loader
cuts the bars, and the guard asserts nothing at or after it survived, so a loosened setting cannot widen this run."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import yaml
from pydantic import Field, ValidationError, model_validator

from goldbot.base import FrozenRecord, load_record
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionWindow
from goldbot.data.timeutil import utc_index

log = logging.getLogger(__name__)

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
SPEC_RULE = "spec_rule"
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
         *SCALP_RULES,
         # ADR 0058 phase 4: a discovered StrategySpec run by the one reviewed interpreter (`arm_rules_spec`); its rows
         # live in config/spec_arms.yaml and load on demo accounts only (`load_arms(live_class=True)` refuses them)
         SPEC_RULE,
         # growth sprint 2026-11-01 (goldbot.research.arm_rules_growth): GS1 and its random-entry control GS1C
         "session_range_break", "session_range_control")


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
    # the measured cost of record by broker x session (config/cost_of_record.yaml); None = the frozen model only
    of_record: CostOfRecord | None = None

    def _cell(self, broker: str, session: str) -> CostOfRecordCell | None:
        if self.of_record is None:
            return None
        cells = self.of_record.cells.get(broker)
        if cells is None:              # ADR 0043 broker amendment: another broker's measurements are never used
            raise KeyError(f"no cost of record for broker {broker!r} (has {sorted(self.of_record.cells)})")
        if session != ALL_SESSIONS and session not in SESSION_NAMES:   # quant review #238: never a silent pooled read
            raise ValueError(f"unknown session {session!r}: one of {(*SESSION_NAMES, ALL_SESSIONS)}")
        return cells.get(session, cells[ALL_SESSIONS])

    def require_version(self, version: str) -> CostOfRecord:
        """The cost of record a pre-registration pinned (ADR: cost-of-record version pinning): the loaded table must be
        exactly that version, else the screen stops before it prices anything."""
        if self.of_record is None:
            raise ValueError(f"no cost of record loaded; the pre-registration pins version {version!r}")
        if self.of_record.version != version:
            raise ValueError(f"cost of record version {self.of_record.version!r} is not the pinned {version!r}")
        return self.of_record

    def overshoot_charge_usd(self, broker: str, session: str) -> float:
        """The stop-exit overshoot a NEW pre-registration charges (quant ruling on #238, binding): a trusted cell's
        clustered upper 90% bound, floored at OVERSHOOT_FLOOR_USD; an untrusted cell (Vantage demo fills stops at the
        stop price) or no table charges the OVERSHOOT_STRESS_USD stress until its exit legs are recorded (ADR 0043)."""
        cell = self._cell(broker, session)
        if cell is None or not cell.overshoot_trusted:
            return OVERSHOOT_STRESS_USD
        return max(OVERSHOOT_FLOOR_USD, cell.overshoot_ci90[1])

    def screen_charges(self, broker: str, sessions: np.ndarray, version: str) -> tuple[np.ndarray, np.ndarray]:
        """Per trade (its entry's session label; "" = outside every session, charged the broker's dearest cell): the
        round trip and the stop-exit overshoot a new pre-registration's screen charges, after pinning `version`."""
        self.require_version(version)
        cells = list(SESSION_NAMES)
        rt = {s: self.charged_round_trip_usd(broker, s) for s in cells}
        os_ = {s: self.overshoot_charge_usd(broker, s) for s in cells}
        rt[""], os_[""] = max(rt.values()), max(os_.values())
        labels = [str(x) for x in np.asarray(sessions)]
        return (np.array([rt[x] if x in rt else self.charged_round_trip_usd(broker, x) for x in labels], dtype=float),
                np.array([os_[x] if x in os_ else self.overshoot_charge_usd(broker, x) for x in labels], dtype=float))

    def charged_round_trip_usd(self, broker: str, session: str) -> float:
        """The round trip a screen charges per oz on `broker` in `session` (a session the table does not split reads
        the broker's pooled cell): the measured round trip's upper 90% bound, never below the frozen `round_trip_usd`
        (the frozen model is itself the conservative case, so like is charged for like). The frozen figure alone
        without a table."""
        cell = self._cell(broker, session)
        return self.round_trip_usd if cell is None else max(self.round_trip_usd, cell.round_trip_ci90[1])

    def overshoot_of_record_usd(self, broker: str, session: str) -> float | None:
        """The measured stop-exit overshoot's upper 90% bound, charged on stop exits only; None without a table or
        when the cell's overshoot is not trusted (Vantage demo fills stops at the stop price): the caller keeps the
        frozen `overshoot_scenarios_usd`."""
        cell = self._cell(broker, session)
        if cell is None or not cell.overshoot_trusted:
            return None
        return max(0.0, cell.overshoot_ci90[1])


ALL_SESSIONS = "all"
SESSION_NAMES: tuple[str, ...] = tuple(DEFAULT_SESSIONS.sessions_local)
OVERSHOOT_FLOOR_USD = 0.12        # quant ruling on #238: the least a new ICM pre-registration charges per stop exit
OVERSHOOT_STRESS_USD = 0.70       # the 10-25 scenario: a reported stress line, and Vantage's charge until exit legs exist


class ScreenPin(FrozenRecord):
    """What a new pre-registration fixes for its screen's costs: the broker it is priced for and the cost of record
    version it was written against (registry rows keep the frozen 0.23 and [0, 0.70])."""
    broker: str = Field(min_length=1)
    cost_version: str = Field(min_length=1)


def screen_net_r(move_usd: np.ndarray, risk_usd: np.ndarray, stop_exit: np.ndarray, costs: ArmCosts, *, broker: str,
                 sessions: np.ndarray, version: str, extra_usd: np.ndarray | float = 0.0,
                 overshoot_usd: float | None = None) -> np.ndarray:
    """Net R at the pinned cost of record: the move less the session's charged round trip, less the ruling's overshoot
    on a stop exit (`overshoot_usd` replaces it for the stress line), less `extra_usd`, over the stop distance."""
    rt, os_ = costs.screen_charges(broker, sessions, version)
    if overshoot_usd is not None:
        os_ = np.full(len(rt), float(overshoot_usd))
    pnl = np.asarray(move_usd, dtype=float) - rt - os_ * np.asarray(stop_exit, dtype=float)
    return (pnl - np.asarray(extra_usd, dtype=float)) / np.asarray(risk_usd, dtype=float)
_ROUND_TRIP_TOL = 0.003           # the table's figures are rounded to 1e-3: four parts, each half a unit at most


class CostOfRecordCell(FrozenRecord):
    """One broker x session cell of the cost of record, USD per oz (= per 0.01 lot), adverse positive; ci90 = the
    5th-95th percentile of the bootstrapped mean. round_trip = spread + max(0, entry slip) + max(0, exit slip) +
    commission (pooled means, floored after pooling); overshoot = past the server stop on stop exits only."""
    n_fills: int = Field(gt=0)
    n_minutes: int = Field(0, ge=0)            # distinct send minutes: the bootstrap's clusters (0 = an i.i.d. table)
    spread_usd: float = Field(ge=0)
    spread_p90_usd: float = Field(ge=0)
    spread_ci90: tuple[float, float]
    entry_slip_usd: float
    entry_slip_ci90: tuple[float, float]
    n_exit_legs: int = Field(ge=0)
    exit_slip_usd: float
    exit_slip_ci90: tuple[float, float]
    exit_slip_source: Literal["strategy_exit_legs", "scheduled_probes", "none"]
    commission_usd: float = Field(ge=0)
    round_trip_usd: float = Field(ge=0)
    round_trip_ci90: tuple[float, float]
    n_stop_exits: int = Field(ge=0)
    n_moved_stop_exits: int = Field(0, ge=0)   # stop exits at a moved (break-even, trailed) stop: not in the overshoot
    overshoot_usd: float
    overshoot_ci90: tuple[float, float]
    overshoot_trusted: bool

    @model_validator(mode="after")
    def _consistent(self) -> "CostOfRecordCell":
        for name in ("spread_ci90", "entry_slip_ci90", "exit_slip_ci90", "round_trip_ci90", "overshoot_ci90"):
            lo, hi = getattr(self, name)
            if lo > hi:
                raise ValueError(f"{name} is inverted: [{lo}, {hi}]")
        parts = self.spread_usd + max(0.0, self.entry_slip_usd) + max(0.0, self.exit_slip_usd) + self.commission_usd
        if abs(parts - self.round_trip_usd) > _ROUND_TRIP_TOL:
            raise ValueError(f"round_trip_usd {self.round_trip_usd} is not its parts ({parts:.4f})")
        return self


class CostOfRecord(FrozenRecord):
    """The measured cost table every research screen reads (docs/research/cost-of-record-2026-10-31.md): broker ->
    session (asia, london, newyork and the pooled `all`) -> cell, with the version a qualification cites."""
    version: str = Field(min_length=1)
    window_utc: tuple[str, str]
    source: str
    interval: str = "iid bootstrap"
    cells: dict[str, dict[str, CostOfRecordCell]]

    @model_validator(mode="after")
    def _pooled_cell(self) -> "CostOfRecord":
        for broker, cells in self.cells.items():
            if ALL_SESSIONS not in cells:
                raise ValueError(f"broker {broker!r} has no pooled {ALL_SESSIONS!r} cell")
        return self


ArmCosts.model_rebuild()          # `of_record` names CostOfRecord, defined after it


def load_costs(path: Path) -> ArmCosts:
    raw = yaml.safe_load(Path(path).read_text())
    extra: dict[str, Any] = {"spread_both_legs_usd": float(raw["spread_both_legs_usd"])} \
        if "spread_both_legs_usd" in raw else {}
    if raw.get("cost_of_record"):     # a path relative to the cost model's own file
        table = yaml.safe_load((Path(path).parent / str(raw["cost_of_record"])).read_text())
        try:
            extra["of_record"] = load_record(CostOfRecord, table, source="cost_of_record")
        except ValidationError as e:
            raise ValueError(f"cost of record {raw['cost_of_record']}: {e}") from e
    return ArmCosts(round_trip_usd=float(raw["round_trip_usd"]),
                    overshoot_scenarios_usd=tuple(float(x) for x in raw["overshoot_scenarios_usd"]), **extra)


def net_r(move_usd: np.ndarray, risk_usd: np.ndarray, stop_exit: np.ndarray, costs: ArmCosts,
          overshoot_usd: float, extra_usd: np.ndarray | float = 0.0) -> np.ndarray:
    """Net result in R: the signed move per oz (swap included) less the round trip, less `overshoot_usd` on a stop exit,
    less `extra_usd` (a measured spread above the frozen one), over the candidate's own stop distance."""
    pnl = np.asarray(move_usd, dtype=float) - costs.round_trip_usd - overshoot_usd * np.asarray(stop_exit, dtype=float)
    return (pnl - np.asarray(extra_usd, dtype=float)) / np.asarray(risk_usd, dtype=float)


# ---------------------------------------------------------------- qualification before demo
# Owner rule 2026-10-30 (ADR 0061): "every strategy should have been back tested and qualified before it is tested and
# learn from live trades that are taken on demo accounts". A strategy arm enters on demo only with a passing
# `qualification` on its own row; the random controls and the named cost arms are exempt by their explicit `role`.
_REFUSAL_LOGGED: set[tuple[str, str]] = set()      # (arm id, reason) already reported at INFO by this process
QUALIFY_RULE = "unqualified (owner rule 2026-10-30)"
COST_READ_DONE = "cost read complete, qualify first"
REPO_ROOT = Path(__file__).resolve().parents[2]           # a doc reference resolves inside this checkout only
# the trial registry (settings research.registry): a `registry:<config_hash>` reference must name one of its rows
REGISTRY_PATH = REPO_ROOT / "state" / "research_registry.jsonl"
EXEMPT_ROLES = ("control", "cost")
# ADR 0061 amendment 2026-11-01 (owner instruction 2026-11-01, growth sprint section 3: "`role: owner_experiment`, for
# GS1 only, with the expiry 2026-11-27 in code, journalled"): the (account, arm) pairs the owner exempts from the
# qualification rule, each with the last UTC day it may enter. Closed: the role on any other id is refused, the arm
# registered on any other account is refused at load (`ops.accounts.Account._owner_experiment_account`), and after the
# day the arm is refused like any unqualified strategy (re-read once a minute: `exploration.refresh_owner_experiments`)
OWNER_EXPERIMENTS: dict[tuple[str, str], str] = {("icm-demo", "GS1"): "2026-11-27"}
OWNER_EXPERIMENT_RULE = "owner experiment (owner instruction 2026-11-01, ADR 0061 amendment)"
# trading-safety M2 + quant ruling 2026-10-30: the cost role is closed to these arms, each naming its prereg cost read
# (prereg-2026-10-26-time-structure.md 2.2 / 3.2: "Signal-time cost read ... at >= 200 admissible F1A fills", ">= 100
# admissible G1A fills"); the exemption ends when that many admissible fills are counted (`engine.exploration`)
COST_ARMS: dict[str, tuple[str, int]] = {"F1A": ("O-11", 200), "G1A": ("O-12", 100)}
# each cost arm's paired control: no purpose once its arm stops ("F1C stops with F1A, G1C with G1A", prereg 2.2/3.2)
COST_CONTROLS: dict[str, str] = {"F1A": "F1C", "G1A": "G1C"}
# a measured slippage cell rests on this many fills at least (prereg 2026-10-26 Costs: "per (session x exit type) cell
# with at least 30 fills")
MIN_COST_CELL_FILLS = 30
REGISTRY_PREFIX = "registry:"
_REGISTRY_REF = re.compile(r"^registry:([A-Za-z0-9][A-Za-z0-9:_.#/-]{0,127})(?:@([0-9a-f]{40}))?$")
_WINDOW = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")
_PATH_MAX = 1024                                          # a longer doc reference is no repo path


class CostCell(FrozenRecord):
    """One slippage cell a qualification's costs rest on (`execution.costs.SlippageStat`, key `<session>:<order_type>`):
    its fill count, the measured mean, the prior and what the read charged, all USD per oz."""
    n: int = Field(ge=0)
    mean_usd: float
    prior_usd: float
    charged_usd: float
    from_prior: bool


class Qualification(FrozenRecord):
    """An arm's history qualification (ADR 0061): the screen read (2010-2017) and the confirm read (2018-2025-09), each
    a repo doc path (an `#anchor` allowed) or a trial-registry row (`registry:<config_hash>[@<evaluator commit>]`), the
    evaluator's verdict and the cost basis of the reads: the cost table version cited and every slippage cell used.
    Both reads are required: a screen alone is selection, not qualification."""
    screen: str = Field(min_length=1)
    screen_window: str
    confirm: str = Field(min_length=1)
    confirm_window: str
    verdict: str
    cost_basis: str
    cost_table: str = Field(min_length=1)
    cost_cells: dict[str, CostCell]


def _window(text: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    m = _WINDOW.match(text)
    if m is None:
        return None
    try:
        start, end = pd.Timestamp(m.group(1), tz="UTC"), pd.Timestamp(m.group(2), tz="UTC")
    except ValueError:
        return None
    return (start, end) if start <= end else None


def _registry_hashes(path: Path) -> frozenset[str] | None:
    """The config hashes of the registry's rows, None when the file is not there or cannot be read (a bad line is
    skipped: one row never hides the others)."""
    try:
        if not path.is_file():
            return None
        lines = path.read_text().splitlines()
    except (OSError, ValueError):
        return None
    out = set()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("config_hash"), str):
            out.add(row["config_hash"])
    return frozenset(out)


def _ref_exists(ref: str, root: Path, registry: frozenset[str] | None) -> bool:
    """A doc reference: a file of the repo at `root` (the part before `#`), never a path that leaves it. A registry
    reference (trading-safety L3): a row of the registry file when it is there, else it must carry the evaluator's
    commit (`@<40 hex>`). Never raises: a NUL byte or an over-long path (ValueError, OSError) is a missing file."""
    try:
        m = _REGISTRY_REF.match(ref)
        if ref.startswith(REGISTRY_PREFIX):
            if m is None:
                return False
            return m.group(1) in registry if registry is not None else m.group(2) is not None
        rel = ref.split("#", 1)[0]
        if not rel or len(rel) > _PATH_MAX or "\x00" in rel or Path(rel).is_absolute():
            return False
        base = root.resolve()
        path = (base / rel).resolve()
        return path.is_relative_to(base) and path.is_file()
    except (OSError, ValueError):
        return False


def _cost_refusal(q: Qualification) -> str | None:
    """Quant ruling 2026-10-30: `cost_basis: measured` cites the cost table version, and every slippage cell it uses is
    measured (not at the prior) on at least MIN_COST_CELL_FILLS fills and priced at the worse of measured and prior.
    Until such cells exist no arm qualifies."""
    if q.cost_basis != "measured":
        return f"cost_basis {q.cost_basis!r} is not measured"
    if not q.cost_cells:
        return "no measured cost cell cited"
    for key, c in sorted(q.cost_cells.items()):
        if c.from_prior:
            return f"cost cell {key} is at the prior, not measured"
        if c.n < MIN_COST_CELL_FILLS:
            return f"cost cell {key} rests on {c.n} fills (< {MIN_COST_CELL_FILLS})"
        if c.charged_usd < max(c.mean_usd, c.prior_usd) - 1e-12:
            return f"cost cell {key} charged {c.charged_usd} below the worse of measured and prior"
    return None


def owner_experiment_until(arm_id: str) -> str | None:
    """The last day of `arm_id`'s owner experiment (any account), None when it is none."""
    return next((day for (_, a), day in OWNER_EXPERIMENTS.items() if a == arm_id), None)


def owner_experiment_accounts(arm_id: str) -> frozenset[str]:
    """The accounts `arm_id` may run on as an owner experiment (empty: it is none)."""
    return frozenset(acc for acc, a in OWNER_EXPERIMENTS if a == arm_id)


def owner_experiment_refusal(arm: Arm, now: pd.Timestamp | None = None) -> str | None:
    """Why an `owner_experiment` arm may not enter at `now` (default: the clock), None while its exemption holds: the
    id must be one of OWNER_EXPERIMENTS and `now` on or before its last day (UTC)."""
    last = owner_experiment_until(arm.id)
    if last is None:
        return f"{QUALIFY_RULE}: the owner_experiment role is only for {sorted(a for _, a in OWNER_EXPERIMENTS)}"
    at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    if at.normalize() > pd.Timestamp(last, tz="UTC"):
        return f"{QUALIFY_RULE}: the owner experiment {arm.id} expired after {last}"
    return None


def qualification_refusal(arm: Arm, root: Path = REPO_ROOT, registry: Path = REGISTRY_PATH,
                          now: pd.Timestamp | None = None) -> str | None:
    """Why `arm` may send no demo entry under the owner rule 2026-10-30, or None when it may. Exempt: `role` control
    (a random control: a random_bracket rule or a body naming its `control`), or `role` cost on a COST_ARMS id naming
    its own cost read (`cost_read`). Any other arm needs a `qualification` that validates, with verdict `pass`,
    measured costs (`_cost_refusal`), a screen window that ends before the confirm window starts, a confirm window that
    ends before the holdout (2025-10-01), and two distinct references that exist. Never raises: a malformed record is
    a refusal (one bad row never stops the engine)."""
    if arm.role == "control" and not (arm.rule == "random_bracket" or "control" in arm.params):
        return f"{QUALIFY_RULE}: role control on a body that is not a control (no random_bracket rule, no params.control)"
    if arm.role == "cost":
        read = COST_ARMS.get(arm.id)
        if read is None:
            return f"{QUALIFY_RULE}: the cost role is only for {sorted(COST_ARMS)}"
        if arm.cost_read != read[0]:
            return f"{QUALIFY_RULE}: cost arm {arm.id} names cost read {arm.cost_read!r}, not {read[0]!r}"
        return None
    if arm.role in EXEMPT_ROLES:
        return None
    if arm.role == "owner_experiment":
        return owner_experiment_refusal(arm, now)
    if arm.qualification is None:
        return f"{QUALIFY_RULE}: no qualification record (a screen and a confirm, both pass)"
    try:
        q = Qualification.model_validate(arm.qualification)
    except ValidationError as exc:
        return f"{QUALIFY_RULE}: qualification record invalid ({exc.error_count()} errors)"
    if q.verdict != "pass":
        return f"{QUALIFY_RULE}: verdict {q.verdict!r} is not pass"
    if (why := _cost_refusal(q)) is not None:
        return f"{QUALIFY_RULE}: {why}"
    screen, confirm = _window(q.screen_window), _window(q.confirm_window)
    if screen is None or confirm is None:
        return f"{QUALIFY_RULE}: a window is not YYYY-MM-DD..YYYY-MM-DD"
    if confirm[1] >= HOLDOUT_FROM:
        return f"{QUALIFY_RULE}: the confirm window reaches the holdout (from {HOLDOUT_FROM.date()})"
    if screen[1] >= confirm[0]:
        return f"{QUALIFY_RULE}: the screen window must end before the confirm window starts"
    if q.screen == q.confirm:
        return f"{QUALIFY_RULE}: screen and confirm are the same reference (two reads are required)"
    hashes = _registry_hashes(registry) if REGISTRY_PREFIX in q.screen + q.confirm else None
    for name, ref in (("screen", q.screen), ("confirm", q.confirm)):
        if not _ref_exists(ref, root, hashes):
            return f"{QUALIFY_RULE}: the {name} reference {ref[:80]!r} does not exist"
    return None


def stamp_qualification(arm: Arm, root: Path = REPO_ROOT, registry: Path = REGISTRY_PATH) -> Arm:
    """`arm` with `entry_refused` set from `qualification_refusal` (a value written on the row is overwritten)."""
    return arm.model_copy(update={"entry_refused": qualification_refusal(arm, root, registry) or ""})


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
    # A retirement from demo trading recorded on the arm's own row, as the daily review records a stop in aux.db
    # `exploration_arm_status` (day, rule): "A retired unit keeps its K, its registry row and its arm id (never
    # reused)" (prereg-amendment-2026-10-30-kill-rules.md A2). The engine sends no new entry of it on any account and
    # keeps it configured, so an open position keeps every exit (`goldbot.engine.exploration.spec_retired`). Run
    # policy, like status: not in the hash, so the frozen body and its id stay as they were
    retired_day: str | None = None
    retired_rule: str = ""
    # Owner rule 2026-10-30 (ADR 0061): `role` control (a random control) or cost (a cost arm) is exempt; a strategy
    # needs `qualification` (`Qualification`, read leniently by `qualification_refusal`: a malformed record refuses the
    # arm, never the load). `entry_refused` is the loader's verdict (`load_arms` stamps it, overwriting the row): an
    # arm with one stays configured, so an open position keeps every exit, and sends no new entry. Run policy, like
    # a retirement: not in the hash
    # `owner_experiment`: exempt until its day in OWNER_EXPERIMENTS (ADR 0061 amendment 2026-11-01), that list only
    role: Literal["strategy", "control", "cost", "owner_experiment"] = "strategy"
    qualification: Any = None
    # a cost arm's prereg cost read (COST_ARMS: F1A O-11, G1A O-12). A row field, not `params`: params are hashed, and
    # naming the read is run policy on a frozen body (trading-safety M2 asked for params.cost_read; the hash forbids it)
    cost_read: str = ""
    entry_refused: str = ""
    config_hash: str | None = None

    @property
    def retired(self) -> bool:
        return self.retired_day is not None

    @property
    def family(self) -> str:
        """The arm's family: its rule, or for a `specialist` arm the specialist it runs (box_break, session_open, ...),
        the name its exploration rows carry and every view reads it under."""
        return family_of(self.rule, self.params)

    @model_validator(mode="after")
    def _consistent(self) -> Arm:
        if self.rule not in RULES:
            raise ValueError(f"{self.id}: unknown rule {self.rule!r}; known: {RULES}")
        if self.retired_day is not None and not _DAY.match(self.retired_day):
            raise ValueError(f"{self.id}: retired_day {self.retired_day!r} is not YYYY-MM-DD")
        if (self.retired_day is None) != (not self.retired_rule):
            raise ValueError(f"{self.id}: a retirement names both its retired_day and its retired_rule (the rule that "
                             "retired it), or neither")
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
            if self.rule in ("session_range_break", "session_range_control"):
                from goldbot.research.arm_rules_growth import check as check_growth
                check_growth(self)
            if self.rule == SPEC_RULE:
                # ADR 0058 phase 4: a spec the engine cannot run exactly as its research read is refused at load
                from goldbot.research.arm_rules_spec import check as check_spec
                check_spec(self)
        except ValueError as exc:
            raise ValueError(f"{self.id}: {exc}") from exc
        return self


def family_of(rule: str, params: dict[str, Any]) -> str:
    """An arm's family from its rule and params: the rule, or for a `specialist` arm the specialist it runs."""
    spec = params.get("specialist") if rule == "specialist" else None
    return spec if isinstance(spec, str) and spec else rule


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    """The arm's frozen configuration: sha256 over id, rule, timeframe, params, max_open (status, note,
    forward_only, a retirement, the role and the qualification are prose and run policy)."""
    body = {"id": arm.id, "rule": arm.rule, "timeframe": arm.timeframe, "params": arm.params, "max_open": arm.max_open}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_arms(path: Path, *, check_hash: bool = True, live_class: bool = False, root: Path = REPO_ROOT,
              registry: Path = REGISTRY_PATH) -> list[Arm]:
    """The arms of `path`. With `check_hash` every arm must carry a `config_hash` equal to its own: an arm edited after
    it was frozen is refused (ARM-SPEC: an edit needs a written amendment and a new arm id). With `live_class` (the
    arms are loaded for an account that is not demo) a `spec_rule` arm is refused: ADR 0058 "Demo arms are data rows
    run by one reviewed interpreter (`spec_rule`), loaded only on demo accounts".

    Owner rule 2026-10-30 (ADR 0061), first layer: every arm is stamped `entry_refused` from `qualification_refusal`
    (doc references resolved under `root`, registry references against `registry`) and each refusal is logged (INFO once per process, DEBUG after). The
    row is kept, never dropped: an arm no longer configured has its open positions closed at once, and the history
    matrix must still run an arm to qualify it."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    out = [stamp_qualification(Arm.model_validate(a), root, registry) for a in raw.get("arms", [])]
    if live_class and (spec := sorted(a.id for a in out if a.rule == SPEC_RULE)):
        raise ValueError(f"spec_rule arms {spec} load on demo accounts only (ADR 0058): refused for a live-class account")
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
    for a in out:
        if a.entry_refused and a.status == "run" and not a.retired:
            first = (a.id, a.entry_refused) not in _REFUSAL_LOGGED
            _REFUSAL_LOGGED.add((a.id, a.entry_refused))
            log.log(logging.INFO if first else logging.DEBUG, "arm %s refuses new demo entries: %s", a.id,
                    a.entry_refused)
    return out


def runnable_ids(path: Path) -> list[str]:
    """The arms the historical matrix runs (H-60): every runnable arm but a broker twin, whose history is its
    original's (same rule body on the same bars) and whose broker takes no H-60 prior (ADR 0043 broker amendment),
    nor a forward-only arm (`Arm.forward_only`)."""
    return [a.id for a in load_arms(path) if a.status == "run" and a.twin_of is None and not a.forward_only]


# ---------------------------------------------------------------- send timing
SEND_DELAY_TFS = ("1m", "5m")
QUARTER_S = 900
SEND_DELAY_MIN_S, SEND_DELAY_STEP_S, SEND_DELAY_STEPS = 3.0, 0.5, 9      # 3.0, 3.5, ..., 7.0 s


def send_delay_s(arm_id: str, timeframe: str, close: pd.Timestamp) -> float:
    """Seconds after its decision close a 1m/5m arm's entry order is sent (ARM-SPEC section 0, dated note
    2026-10-27): on a quarter-hour close (:00/:15/:30/:45), which carries the 15m+ close work and sends, 3.0-7.0 s in
    0.5 s steps fixed by the arm id (a hash: no state, restart-safe, identical in history and live); otherwise and for
    15m+ arms 0. The decision stays on its bar; every delay is inside the 1m stale window (10 s) and the first minute
    after the close, so the history entry (the open of the minute holding close + delay) is still that minute's."""
    if timeframe not in SEND_DELAY_TFS or int(pd.Timestamp(close).timestamp()) % QUARTER_S:
        return 0.0
    h = int.from_bytes(hashlib.sha256(f"goldbot-send-delay|{arm_id}".encode()).digest()[:8], "big")
    return SEND_DELAY_MIN_S + SEND_DELAY_STEP_S * (h % SEND_DELAY_STEPS)


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
