"""Roadmap phase gates and the stop rule, evaluated from evidence (design: Roadmap; rows P6, P7).

The design's roadmap has four gates (`accounts.GATES`), each "gated by evidence, not by calendar: a gate that is not
met extends the phase rather than being waived". Its thresholds, verbatim from the roadmap figure:

* foundation_to_backtest ("leakage audit"): shuffle test at chance; as-of audit clean; broker and Dukascopy bars agree.
* backtest_to_paper: DSR >= 1.0 on the true trial count; positive in 3 separate years; >= 500 backtest trades;
  live-tick spread model; CPCV/PBO evidence for the quarter, read only (ADR 0083 amendment, research/cpcv_gate.py).
* paper_to_tiny_live: >= 150 paper trades; expectancy within 50% of backtest; fills within 30% of target; chaos drill
  passed. ADR 0106 (owner 2026-11-26) adds the goals of record, "2-3 months of paper trading meeting these targets
  before real money" (the 182-day minimum stays, stricter): `as_if_real_money` (the paper record re-sized as one
  real-money account under 1 % risk / 2 % daily stop / 15 % drawdown / 1 % of daily volume breaches nothing, and at least
  `paper_min_trades` trades could be sized: below the minimum lot they are reported and not counted),
  `paper_sharpe` (as-if daily Sharpe above 1.0), `beats_buy_and_hold` ("Beats buy-and-hold of the same market over 6+
  months, after costs": the as-if return over the paper window above every traded instrument's buy-and-hold net of a
  round trip and the swap, research/benchmarks.py) and `paper_cost_share` ("Trading costs under one third of gross
  profit").
* tiny_live_to_full_size: >= 300 live trades; expectancy within 40% of paper; drawdown under 1.5x backtest; brokers
  within 15%.

Stop rule (P6): "The project stops if, after 18 months of paper plus live, the pooled trade count exceeds 500 and the
lower 90% confidence bound on expectancy is still below zero, or if any single incident produces a loss larger than
the weekly cap."

Numbers the design commits live in `config/settings.yaml` `gates:`; the ones it does not commit (the "at chance"
tolerance, how closely the feeds must agree, minimum phase durations, trades per broker) carry a proposed default
marked `PROPOSED: owner sign-off required`, since the design wants the thresholds "committed in writing before the
first paper trade". Interpretations the code makes are stated where they are made:

* "within X% of" means no more than X% below the reference (better than the reference always passes) and needs a
  positive reference; "brokers within 15%" compares the two brokers' live expectancy to the larger of the two.
* Expectancy and drawdown are on the per-trade net return `ret`, the unit the backtest reports (research/metrics.py),
  so paper, live and backtest compare like for like. A trade whose `ret` is not net (commission or swap unknown, or a
  price return) is kept, never dropped ("their causes can be outcome-linked, so dropping them is selection bias"), and
  charged a conservative imputed cost (ops/cost_imputation.py); every such item shows the gross count and the total
  charged, and a gross row that cannot be priced fails the item closed. The same charge applies to the stop rule's
  bound. `gates.max_gross_share` (owner decision, off by default) can also fail the paper and live gates on the share.
* "lower 90% confidence bound" is the one-sided 90% bound, mean - t(0.90, n-1) * sd / sqrt(n).
* A stop-rule "incident" is one closed trade whose net loss exceeds `risk.weekly_cap` of its account's equity before
  it; it counts at any time, paper or live.

What this module does and does not do:

* It READS evidence: state/closed_trades.jsonl (one ClosedTrade per line: the paper and live record), the trial
  registry (DSR, positive years, backtest trades), state/costs_<account>.json (spread from own ticks, measured fills),
  state/gate_evidence.json (leakage audit, feed agreement, chaos drill: recorded with `run.py gate-evidence`),
  state/shadow_book.json (informational) and state/phase_state.json (which gates are recorded and when); `run.py
  gates` also reads gold's Dukascopy 1h bars from the local store for the buy-and-hold benchmark (`store_buy_hold`).
* It WRITES only state/gate_report.json (the last report) and, through `record_evidence`, state/gate_evidence.json.
* It never records a gate, never edits phase_state.json or accounts.yaml, never unlocks a live account, never halts,
  closes or resizes anything. Recording stays `run.py record-gate` (accounts.record_gate) and live still needs
  `unlock_live` plus the typed phrase. A stop-rule breach is reported here and as a health FAIL (the Telegram alert
  path announces it once); stopping is the owner's decision (/halt), beyond what the RiskGate already enforces.

  python -m goldbot.ops.run gates [--json] [--no-write]
  python -m goldbot.ops.run gate-evidence <name> (--value X | --passed yes|no) [--detail TEXT]
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field
from scipy import stats

from goldbot.base import FrozenRecord, UtcTimestamp, fsync_dir, load_record_json, write_atomic
from goldbot.config import GateSettings, Settings
from goldbot.data.timeutil import opportunity_day
from goldbot.instruments import GOLD, point_of
from goldbot.ops.accounts import GATES
from goldbot.ops.cost_imputation import CostImputer, ImputationSummary
from goldbot.research import cpcv, cpcv_gate
from goldbot.research.as_if_sizing import (
    AsIfInput,
    AsIfLimits,
    AsIfReport,
    as_if_real_money,
)
from goldbot.research.benchmarks import SESSION_DAYS_PER_YEAR, BuyHoldRead, buy_and_hold
from goldbot.research.gates import current_dsr
from goldbot.research.metrics import max_drawdown
from goldbot.research.promotion import session_days
from goldbot.research.registry import (
    TrialRegistry,
    costs_recorded,
    discovery_origin,
    latest_evaluated,
    n_trials_effective,
    phase_gate_k,
    phase_gate_refusal,
    quarter_of,
    row_hash,
)
from goldbot.risk.limits_profile import LimitsTag, counts_as_default_evidence

CLOSED_TRADES_FILE = "closed_trades.jsonl"
EVIDENCE_FILE = "gate_evidence.json"
REPORT_FILE = "gate_report.json"
PHASE_FILE = "phase_state.json"
# Owner- or job-recorded evidence for the items the code cannot measure itself (foundation gate, chaos drill).
EVIDENCE_NAMES = ("shuffle_auc", "asof_violations", "feed_mismatch_share", "chaos_drill")
MIN_TRADES_FOR_BOUND = 2           # fewer pooled trades: no lower bound (and no interval) can be estimated
MAX_INCIDENTS = 5                  # stop-rule incidents listed (worst first); `incident_count` has them all
TRADES_UNREADABLE = "closed-trade record"   # prefix of the error an unreadable closed-trade line gives
# ADR 0106 cost share: the most of the as-if sized trades, by count and separately by as-if notional, whose cost
# cannot be measured (no side, fill prices or decision spread) before the item fails closed (quant P6)
COST_SHARE_MAX_UNMEASURED = 0.05
# trading-safety review of #490 (F3): the ADR 0106 minimum equity for gold (~GBP 315: 0.01 lot at a typical stop at
# 1 % risk); the as-if item also reports how many trades this equity would size (report only)
AS_IF_MIN_EQUITY_USD = 400.0
NEVER_UNLOCKS = ("This report never records a gate or unlocks live: record with `run.py record-gate`, "
                 "enable live with `accounts unlock-live` and the typed phrase.")


class ClosedTrade(FrozenRecord):
    """One closed paper (demo account) or live trade, net of every cost: the record the gates and the stop rule read.

    The engine writes one record per position when it is fully closed (engine/runner.py `_record_closed`). A scale-out
    is not a trade of its own: its lots, price and P&L are folded into the final record (`lots` is the size at entry,
    `exit_price` the lots-weighted average of every exit, `partial_lots` what the scale-out took), so one record is one
    labelled trade, as in the backtest. Fields after `equity_before` are descriptive and optional (older lines, and
    trades a broker reports without them, leave them empty)."""

    account_id: str
    broker: str
    mode: Literal["demo", "live"]
    exit_utc: UtcTimestamp
    ret: float                         # net return per trade, side * (exit - entry) / entry less costs (backtest unit)
    pnl: float                         # net P&L in account currency
    equity_before: float = Field(gt=0)
    position_id: int | None = None     # the broker's position: with account_id, the key a trade is recorded once by
    client_order_id: str | None = None
    agent_id: str | None = None
    side: int | None = None
    lots: float | None = None          # size at entry (before any scale-out)
    entry_utc: UtcTimestamp | None = None
    entry_price: float | None = None
    exit_price: float | None = None    # lots-weighted average of every exit of the position
    exit_reason: str | None = None     # stop, target, time_exit, hard_flat, trail_close, engine_stop_close, ...
    r: float | None = None             # pnl over the initial risk (entry to the initial stop, size at entry)
    commission: float | None = None    # cost in account currency (positive), entry and exit; None when not reported
    swap: float | None = None          # account currency, broker sign; None when not reported
    partial_lots: float = 0.0          # lots closed by a scale-out before the final close
    # the currency of `pnl` (BACKLOG 28): the account currency when the broker's P&L was used or the engine's own was
    # converted; "USD" for the unconverted own-fills fallback on a non-USD account (never summed with account money);
    # None only on legacy records from before the fix (the monthly report flags their R as understated)
    pnl_currency: str | None = None
    # MAE / MFE (BACKLOG 23, labels/excursion.py): the worst and best exit-side price while the engine held the
    # position (bid for a long, ask for a short; the exit fill included), in R of the initial risk from price (gross
    # of costs; MAE <= 0) and when each was first reached, never after `exit_utc`. None on older records, when
    # tracking failed and when nothing of the path was observed (no usable tick track and no stored bar inside); R also None when the initial stop is unknown (an adopted orphan without an order record). A
    # scaled position's figures cover the whole position until its final exit (the scale-out changes size, not path)
    mae_price: float | None = None
    mae_r: float | None = None
    mae_utc: UtcTimestamp | None = None
    mfe_price: float | None = None
    mfe_r: float | None = None
    mfe_utc: UtcTimestamp | None = None
    # True when part of the path was not seen by ticks (a restart, an adopted or never-tracked position): that part is
    # filled from the stored 1m bars wholly inside the trade, so an extreme in a gap the bars do not cover is missed;
    # also when ticks after the exit reached the track, when the exit time or price is not the broker's (a close
    # confirmed without deals), or when the position outlived a freeze at its broker stop or target
    excursion_approx: bool | None = None
    # backlog 161 (quant Q-2): base bars credited for closes missed while the engine was down (> 0: the time exit or
    # hard flat came at a restart close, not the label's deadline: kept out of label-vs-live exit reconciliation).
    # None: none credited, or a record from before it
    downtime_bars: int | None = None
    # finance audit (docs/finance/audit-framework.md). Every field below is None on records from before it existed,
    # so "unknown" is never read as a value. GAP-02: the decision price (the quote at the signal, Proposal.entry) and
    # spread, apart from the quote the order was priced from (in propose mode the quote at the owner's approval): side x
    # (requested_price - decision_price) is the cost of the approval wait. GAP-01 / GAP-13: what made the trade
    decision_price: float | None = None
    decision_spread_points: float | None = None
    decision_utc: UtcTimestamp | None = None     # host clock when the proposal was made (Proposal.created)
    requested_price: float | None = None
    timeframe: str | None = None
    family: str | None = None
    model_version: str | None = None             # the agent's champion registry version; None when none was loaded
    # GAP-03: the stop at entry (None for an orphan adopted without an order record, whose entry stop is unknown) and
    # the stop and target the engine held at the exit; the stop history between them is the journal's `trail`,
    # `weekend_tighten`, `orphan_stop` and `reinstate_stops` rows. Exit fills against their asked price: store
    # `exit_fills` (GAP-04)
    initial_sl: float | None = None
    final_sl: float | None = None
    final_tp: float | None = None
    # GAP-05: "broker" when pnl comes from the broker's deals, "engine" from the engine's own fills. "net" only when
    # both commission and swap are known and inside pnl; otherwise "gross" (never summed as net)
    money_source: Literal["broker", "engine"] | None = None
    pnl_basis: Literal["net", "gross"] | None = None
    # what `ret` was computed from (quant review 2026-10-12, finding B): "pnl" = pnl / notional in the account currency,
    # so it carries exactly the costs `pnl` carries (pnl_basis says whether that is net); "price" = the gross price
    # return side * (exit - entry) / entry, which carries no commission or swap whatever pnl_basis says (the broker's
    # deals on a non-USD account with no conversion). None on records from before the field (read by pnl_basis alone)
    ret_basis: Literal["pnl", "price"] | None = None
    # ADR 0032: the limits profile the entry was decided under ("default" or the owner's demo profile, e.g.
    # "demo-2026-10-18") and its limits. None on records from before the field (all of which traded the defaults) and
    # on adopted orphans; gate evidence must never mix profiles
    limits_profile: str | None = None
    limits: LimitsTag | None = None
    # ADR 0031 (A1): the entry was the minimum-lot exception (BACKLOG 25), so the adaptive controller counts it toward
    # the 60-trade exploration budget. None on records from before the field and on adopted orphans
    min_lot_exception: bool | None = None
    # ADR 0097 (quant F7): "unlimited" for an entry sent on a demo account without limits; None everywhere else
    limits_epoch: str | None = None
    # multi-instrument demo (plan 2026-11-02, lanes L3/L4): the instrument the trade was on (a config/instruments.yaml
    # key); None on gold's and on every record before it, so the gates' gold evidence reads exactly as before
    symbol: str | None = None


class EntryAudit(FrozenRecord):
    """The entry facts the closed record carries for the finance audit (GAP-01, GAP-02, GAP-13), fixed when the order
    is sent and kept with the pending order and the open trade, so a restart or a recovered fill keeps them. Field
    names are ClosedTrade's."""

    decision_price: float | None = None
    decision_spread_points: float | None = None
    decision_utc: UtcTimestamp | None = None
    requested_price: float | None = None
    timeframe: str | None = None
    family: str | None = None
    model_version: str | None = None
    # ADR 0032: the limits profile the entry was decided under ("default" or the owner's demo profile, e.g.
    # "demo-2026-10-18") and its limits. None on records from before the field (all of which traded the defaults) and
    # on adopted orphans; gate evidence must never mix profiles
    limits_profile: str | None = None
    limits: LimitsTag | None = None
    # ADR 0031 (A1): the entry was the minimum-lot exception (BACKLOG 25), so the adaptive controller counts it toward
    # the 60-trade exploration budget. None on records from before the field and on adopted orphans
    min_lot_exception: bool | None = None
    # ADR 0097 (quant F7): "unlimited" for an entry sent on a demo account without limits; None everywhere else
    limits_epoch: str | None = None


class GateProgress(FrozenRecord):
    """How far a count or duration item has come: "have `value` of `target` `unit`"."""
    value: float
    target: float
    unit: Literal["trades", "days"]


class GateItem(FrozenRecord):
    name: str
    met: bool
    evidence: str                      # what was measured; never the owner's typed note (`detail`)
    need: str
    required: bool = True              # False: shown for context, does not decide the gate
    # structured facts for the dashboard (docs/ux/progress-screens.md); None where they do not apply
    detail: str | None = None          # the note typed with `gate-evidence --detail`: CLI output only, never served
    threshold: str | None = None       # the GateSettings field that sets `need` (its PROPOSED state is the API's)
    progress: GateProgress | None = None
    earliest_utc: UtcTimestamp | None = None    # a day item: its clock start + the minimum days (a calendar fact)
    ci95: tuple[float, float] | None = None     # an expectancy item: the 95% interval of the phase's mean return


class GateStatus(FrozenRecord):
    gate: str
    met: bool
    recorded: bool
    items: list[GateItem]
    recorded_utc: UtcTimestamp | None = None    # from phase_state.json's gate_log


class StopIncident(FrozenRecord):
    """One closed trade whose loss (with a gross row's imputed cost) exceeded the weekly cap of its equity before it."""
    account: str
    mode: Literal["demo", "live"]
    closed_utc: UtcTimestamp
    loss_frac: float


class StopRuleStatus(FrozenRecord):
    breached: bool
    reasons: list[str]
    pooled_trades: int
    months: float
    lower_bound: float | None
    worst_loss_frac: float | None      # largest single-trade loss as a fraction of equity before it
    detail: str
    # structured, so a screen can word and localise them (the reasons above carry UTC times and the account)
    clock_start_utc: UtcTimestamp | None = None   # where the pooled trades and the months start
    clock_source: Literal["paper_gate", "first_trade"] | None = None
    breach: list[Literal["long_run", "single_loss", "unpriced"]] = Field(default_factory=list)
    incidents: list[StopIncident] = Field(default_factory=list)   # worst first, at most MAX_INCIDENTS
    incident_count: int = 0
    imputed: ImputationSummary = Field(default_factory=ImputationSummary)   # the pooled gross rows and their charge


class GateReport(FrozenRecord):
    ts: UtcTimestamp
    phase: int
    gates_passed: list[str]
    gates: list[GateStatus]
    stop_rule: StopRuleStatus
    errors: list[str] = Field(default_factory=list)
    note: str = NEVER_UNLOCKS


# ---------------------------------------------------------------------------------------------- evidence readers
def load_closed_trades(state_dir: Path) -> tuple[list[ClosedTrade], str | None]:
    """The paper and live record; (trades, error). An unreadable line (a torn write) is skipped and reported in the
    error, never silently dropped, and the lines after it still count, so the engine's set of recorded positions is
    never built from a truncated list. A trade is counted once: a later line with the same (account_id, position_id)
    is ignored (the engine writes once per position; this keeps the count right even if a line were ever repeated)."""
    files = closed_trade_files(Path(state_dir))
    if not files:
        return [], None
    out: list[ClosedTrade] = []
    seen: set[tuple[str, int]] = set()
    bad: list[str] = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                t = load_record_json(ClosedTrade, line, source="closed_trades")
            except ValueError as exc:
                bad.append(f"{f.name} line {i}: {str(exc).splitlines()[0][:120]}")
                continue
            if t.position_id is not None:
                key = (t.account_id, t.position_id)
                if key in seen:
                    continue
                seen.add(key)
            out.append(t)
    if bad:
        return out, f"{TRADES_UNREADABLE}: {len(bad)} unreadable line(s) skipped ({bad[0]})"
    return out, None


def pnl_basis(t: ClosedTrade) -> Literal["net", "gross"] | None:
    """"net" | "gross" from the record's `pnl_basis`; a legacy row without it is net only when it records both
    commission and swap (the broker's deals, net of every cost); anything else is not recorded (perf_report, quant
    review of 207c9ef item 1). Every report sums only "net" rows as net: "nothing gross is labelled net"."""
    if t.pnl_basis is not None:
        return t.pnl_basis
    if t.commission is not None and t.swap is not None:
        return "net"
    return None


def closed_trade_file(state_dir: Path, account_id: str) -> Path:
    """One record file per account: two engines never append to the same file (on Windows an O_APPEND write is a
    seek plus a write, not atomic, so concurrent closes could overwrite each other; trading-safety review)."""
    return Path(state_dir) / f"closed_trades_{account_id}.jsonl"


def closed_trade_files(state_dir: Path) -> list[Path]:
    """The legacy shared file (read only) plus every per-account file."""
    legacy = Path(state_dir) / CLOSED_TRADES_FILE
    return ([legacy] if legacy.exists() else []) + sorted(Path(state_dir).glob("closed_trades_*.jsonl"))


def append_closed_trade(state_dir: Path, trade: ClosedTrade) -> None:
    """Append one closed trade to the record (the writer the engine calls when a position closes), durably: the line
    goes out in one write on an O_APPEND descriptor and is fsynced (with the directory when the file is new) before
    this returns, so a power cut never loses a recorded trade. A short write is completed (os.write may write less
    than asked), and a file whose last line was torn (no trailing newline) gets a newline first, so the new record is
    never glued to the torn one."""
    f = closed_trade_file(Path(state_dir), trade.account_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    new = not f.exists()
    data = (trade.model_dump_json() + "\n").encode("utf-8")
    if not new and f.stat().st_size > 0:
        with open(f, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                data = b"\n" + data
    fd = os.open(f, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError(f"short write to {f.name}: {len(view)} bytes left")
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if new:
        fsync_dir(f.parent)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def phase_record(state_dir: Path) -> tuple[dict[str, Any], dict[str, pd.Timestamp], str | None]:
    """(phase_state, {gate: recorded at}, error). Read only."""
    f = Path(state_dir) / PHASE_FILE
    if not f.exists():
        return {"phase": 0, "gates_passed": []}, {}, None
    try:
        st = _read_json(f)
        when = {str(e["gate"]): pd.Timestamp(e["ts_utc"]) for e in st.get("gate_log", []) if "gate" in e and "ts_utc" in e}
        return st, when, None
    except (ValueError, OSError, AttributeError, TypeError, KeyError) as exc:
        return {"phase": 0, "gates_passed": []}, {}, f"{PHASE_FILE} unreadable: {exc}"


def load_evidence(state_dir: Path) -> dict[str, dict[str, Any]]:
    f = Path(state_dir) / EVIDENCE_FILE
    try:
        d = _read_json(f) if f.exists() else {}
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def record_evidence(state_dir: Path, name: str, *, value: float | None = None, passed: bool | None = None,
                    detail: str = "", now: pd.Timestamp | None = None) -> dict[str, Any]:
    """Record one evidence item (a measured value or a pass/fail with a note) in state/gate_evidence.json. Evidence
    only: it records no gate and changes no phase."""
    if name not in EVIDENCE_NAMES:
        raise ValueError(f"unknown evidence {name!r}; known: {', '.join(EVIDENCE_NAMES)}")
    if (value is None) == (passed is None):
        raise ValueError("give exactly one of a value or pass/fail")
    if value is not None and not math.isfinite(value):
        raise ValueError("value must be a finite number")
    ev = load_evidence(state_dir)
    ts = now if now is not None else pd.Timestamp.now("UTC")
    ev[name] = {"value": value, "passed": passed, "detail": detail, "ts_utc": ts.isoformat()}
    write_atomic(Path(state_dir) / EVIDENCE_FILE, json.dumps(ev, indent=1))
    return ev


def _trial_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _discovery_k(reg_path: Path) -> int | None:
    """The registry's `n_trials_discovery` (its K plus every discovery batch's k_contrib), None when unreadable."""
    try:
        return TrialRegistry(reg_path).n_trials_discovery if Path(reg_path).exists() else None
    except (OSError, ValueError):
        return None


def best_backtest_trial(rows: list[dict[str, Any]], *, k: int | None = None) -> dict[str, Any] | None:
    """The evaluated walk-forward trial (never a holdout scoring) with the highest model-filtered DSR recomputed on the
    registry's trial count today (`current_dsr`; a row that cannot be recomputed ranks last), among rows that record
    the research cost treatment (`registry.costs_recorded`: the swap charged and the cost source stated). Quant
    review, 2026-10-11: "A pass from before swap was charged is not a pass of the swap-charged configuration", so a
    pre-swap row is not backtest evidence for the backtest -> paper gate, whatever its DSR. Only the latest run of each
    configuration on its feature version competes (`registry.latest_evaluated`; quant review 2026-10-16 N1: "only the
    latest evaluated row ... decides"): a superseded run ranked above its own passing rerun was refused by
    `phase_gate_refusal` and left gate 1 unmet although the configuration's deciding run passes. `k`: the registry's
    `n_trials_effective(rows)` when the caller has it already (`evaluate_gates` counts once per evaluation: the count
    builds the registry ledger, the costliest step of the health pass; CPU attribution 2026-11-10, round 3)."""
    if k is None:
        k = n_trials_effective(rows)

    def dsr(r: dict[str, Any]) -> float:
        v = current_dsr(r, k)[0]
        return v if v is not None else -1.0
    cands = [r for r in rows if r.get("status") == "evaluated" and (r.get("results") or {}).get("model_filtered")
             and costs_recorded(r.get("results") or {})
             and latest_evaluated(rows, str(r.get("family")), row_hash(r), r.get("feature_version")) is r]
    return max(cands, key=dsr) if cands else None


def _cost_tables(state_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(Path(state_dir).glob("costs_*.json")):
        try:
            d = _read_json(f)
            if isinstance(d, dict):
                out.append(d)
        except (ValueError, OSError):
            continue
    return out


# ---------------------------------------------------------------------------------------------- statistics
def mean_ret(rets: list[float]) -> float | None:
    return float(np.mean(rets)) if rets else None


def _charged(note: ImputationSummary) -> str:
    """The evidence suffix: how many rows were gross and what they were charged (empty when all are net)."""
    return "" if not note.n_gross else f"; {note.text()}"


def lower_bound(rets: list[float], confidence: float) -> float | None:
    """One-sided lower confidence bound on the mean: mean - t(confidence, n-1) * sd / sqrt(n); None below
    MIN_TRADES_FOR_BOUND trades."""
    n = len(rets)
    if n < MIN_TRADES_FOR_BOUND:
        return None
    a = np.asarray(rets, dtype=float)
    sd = float(np.std(a, ddof=1))
    return float(np.mean(a) - stats.t.ppf(confidence, n - 1) * sd / math.sqrt(n))


def mean_ci95(rets: list[float]) -> tuple[float, float] | None:
    """Two-sided 95% interval of the mean per-trade return, mean +- t(0.975, n-1) * sd / sqrt(n), on the same iid
    standard error as `lower_bound` (the gates' convention; ADR 0022 leaves the gates unchanged). Shown beside an
    expectancy item; it decides nothing. None below MIN_TRADES_FOR_BOUND trades."""
    n = len(rets)
    if n < MIN_TRADES_FOR_BOUND:
        return None
    a = np.asarray(rets, dtype=float)
    half = float(stats.t.ppf(0.975, n - 1) * float(np.std(a, ddof=1)) / math.sqrt(n))
    return float(np.mean(a) - half), float(np.mean(a) + half)


def within(value: float | None, reference: float | None, frac: float) -> bool:
    """`value` is no more than `frac` below a positive `reference` (better always passes)."""
    return value is not None and reference is not None and reference > 0 and value >= (1 - frac) * reference


def _months(start: pd.Timestamp, end: pd.Timestamp) -> float:
    return max((end - start).total_seconds() / (365.25 / 12 * 86400), 0.0)


def _fmt(v: float | None, spec: str = ".5f") -> str:
    return "n/a" if v is None else format(v, spec)


# ---------------------------------------------------------------------------------------------- ADR 0106 paper goals
def as_if_inputs(trades: list[ClosedTrade], imputer: CostImputer) -> tuple[list[AsIfInput], list[ClosedTrade], int]:
    """The trades as the as-if replay reads them (research/as_if_sizing.py), with a gross row's R less its imputed
    cost (unknown when the charge has no R); the closed trades they came from, in the same order; and how many had no
    entry time or price (they cannot be replayed)."""
    out: list[AsIfInput] = []
    mapped: list[ClosedTrade] = []
    unmapped = 0
    for t in trades:
        if t.entry_utc is None or t.entry_price is None or not t.entry_price > 0:
            unmapped += 1
            continue
        c = imputer.charge(t)
        r = None if t.r is None or c.r is None else t.r - c.r
        out.append(AsIfInput(entry_utc=t.entry_utc, exit_utc=t.exit_utc, side=t.side, entry_price=t.entry_price,
                             initial_sl=t.initial_sl, r=r, symbol=t.symbol or GOLD,
                             spread_points=t.decision_spread_points, scoring_sha=t.model_version))
        mapped.append(t)
    return out, mapped, unmapped


LO_LAGS = 5                        # quant ruling on #490 (P5): Lo (2002) annualisation with lags 1-5


def daily_sharpe(daily: Mapping[date, float], start: date, end: date) -> float | None:
    """The paper Sharpe floor (quant ruling on #490, P5): "`paper_sharpe` = min(naive sqrt(260) . mean/sd, the Lo (2002)
    autocorrelation-adjusted annualisation using lags 1-5) on the as-if daily series, zero days kept. It must be above
    `paper_min_sharpe` (1.0). It is a floor. Over about 182 days the sampling SE of an annual Sharpe is about 1.2, so
    passing it is not evidence of an edge. The judged evidence is the paired read and the DSR gates."
    The series is every session day in [start, end] (a day without a close is 0). Lo: SR_daily x q / sqrt(q + 2 sum
    over k = 1..5 of (q - k) rho_k), q = SESSION_DAYS_PER_YEAR. None below LO_LAGS + 2 days, without dispersion, or
    when the Lo denominator is not positive (fail closed)."""
    grid = sorted({*session_days(start, end), *(d for d in daily if start <= d <= end)})
    x = np.array([float(daily.get(d, 0.0)) for d in grid])
    if len(x) < LO_LAGS + 2:
        return None
    sd = float(np.std(x, ddof=1))
    if not sd > 0:
        return None
    sr = float(np.mean(x)) / sd
    q = SESSION_DAYS_PER_YEAR
    dev = x - x.mean()
    var = float(dev @ dev)
    rho = [float(dev[k:] @ dev[:-k]) / var for k in range(1, LO_LAGS + 1)]
    inner = q + 2 * sum((q - k) * r for k, r in enumerate(rho, 1))
    if not inner > 0:
        return None
    return min(sr * math.sqrt(q), sr * q / math.sqrt(inner))


def trade_cost(t: ClosedTrade, imputer: CostImputer) -> tuple[float, float] | None:
    """(cost, net) of one trade as fractions of its notional, or None when unmeasured: cost is its fill-to-fill price
    return less its net return (commission, swap and any imputed charge) plus the decision quote's spread over the
    entry price (the round trip's spread from mid; counted once: gross = net + cost). Exit slippage is inside the
    fills. Unmeasured: no side, fill prices or decision spread, or an unknown instrument."""
    if (t.side not in (1, -1) or t.entry_price is None or not t.entry_price > 0 or t.exit_price is None
            or t.decision_spread_points is None):
        return None
    try:
        point = point_of(t.symbol or GOLD)
    except KeyError:
        return None
    net = imputer.net_ret(t)
    price_ret = t.side * (t.exit_price - t.entry_price) / t.entry_price
    return price_ret - net + t.decision_spread_points * point / t.entry_price, net


class CostShare(FrozenRecord):
    """Quant ruling on #490 (P6): "`paper_cost_share` = sum cost_usd / sum gross_usd over the as-if sized trades (cost
    and gross per trade x its as-if notional). Pass when <= 1/3 and sum gross_usd > 0. Fail closed when more than 5 %
    of trades (by count, and separately by as-if notional) are unmeasured." """
    share: float | None
    n_sized: int
    n_unmeasured: int
    notional_usd: float
    unmeasured_notional_usd: float

    @property
    def measurable(self) -> bool:
        return (self.n_unmeasured <= COST_SHARE_MAX_UNMEASURED * self.n_sized
                and self.unmeasured_notional_usd <= COST_SHARE_MAX_UNMEASURED * self.notional_usd)


def cost_share(mapped: list[ClosedTrade], as_if: AsIfReport, imputer: CostImputer) -> CostShare:
    """The P6 cost share on the as-if sized trades (`mapped[i]` is the closed trade of as-if input i)."""
    cost = gross = notional = blind = 0.0
    n = n_blind = 0
    for a in as_if.trades:
        if a.status != "sized":
            continue
        n += 1
        notional += a.notional_usd
        m = trade_cost(mapped[a.index], imputer)
        if m is None:
            n_blind += 1
            blind += a.notional_usd
            continue
        c, net = m
        cost += c * a.notional_usd
        gross += (net + c) * a.notional_usd
    return CostShare(share=cost / gross if gross > 0 else None, n_sized=n, n_unmeasured=n_blind,
                     notional_usd=notional, unmeasured_notional_usd=blind)


def _as_if_item(rep: AsIfReport, unmapped: int, cfg: GateSettings, small: AsIfReport,
                no_adv: list[str]) -> GateItem:
    """The replay on `as_if_equity_usd`; beside it, report only, how many trades the same replay sizes at
    AS_IF_MIN_EQUITY_USD, the ADR 0106 minimum equity for gold (trading-safety F3). Without a reference daily volume
    for a traded instrument the item says it is unmeetable (F4)."""
    met = rep.clean and rep.n_sized >= cfg.paper_min_trades
    lead = (f"unmeetable until the ADV reference is filled (no reference daily volume for {', '.join(no_adv)}); "
            if no_adv else "")
    return GateItem(name="as_if_real_money", met=met,
                    evidence=lead + rep.text() + (f"; {unmapped} without an entry time or price" if unmapped else "")
                    + f"; at ${small.equity_start:,.0f} (ADR 0106 minimum equity for gold) {small.n_sized} of "
                      f"{small.n_trades} would be sized",
                    need=f"re-sized on ${cfg.as_if_equity_usd:,.0f} as one real-money account under the stricter of "
                         f"ADR 0106 and the risk settings: no breach of {rep.limits.text()}, and >= "
                         f"{cfg.paper_min_trades} sized trades (ADR 0106 decision 2)",
                    threshold="as_if_equity_usd",
                    progress=GateProgress(value=rep.n_sized, target=cfg.paper_min_trades, unit="trades"))


def _buy_hold_item(rep: AsIfReport, symbols: list[str], buy_hold: Mapping[str, BuyHoldRead], p_days: float,
                   cfg: GateSettings) -> GateItem:
    ret = rep.equity_end / rep.equity_start - 1.0
    missing = [s for s in symbols if s not in buy_hold]
    reads = [buy_hold[s] for s in symbols if s in buy_hold]
    best = max((b.net_ret for b in reads), default=None)
    met = (p_days >= cfg.paper_min_days and rep.n_sized >= cfg.paper_min_trades and not missing and best is not None
           and ret > best)
    shown = "; ".join(b.text() for b in reads)
    if missing:
        shown = "; ".join(filter(None, [shown, f"no buy-and-hold read for {', '.join(missing)}"]))
    return GateItem(name="beats_buy_and_hold", met=met,
                    evidence=f"as-if paper return {ret:+.2%} over {p_days:.0f} days vs {shown or 'no paper trades'}",
                    need=f"above the buy-and-hold of every traded instrument after a round trip and the swap, over >= "
                         f"{cfg.paper_min_days} paper days (ADR 0106 goal)", threshold="paper_min_days")


def store_buy_hold(settings: Settings, start: pd.Timestamp | None, end: pd.Timestamp) -> dict[str, BuyHoldRead]:
    """Gold's buy-and-hold over [start, end] from the local store: the mid close of each session day's last
    Dukascopy 1h bar, one round trip at the settings' extra cost (slippage prior and commission; the spread is left out,
    so the benchmark's cost errs low and the item strict), the settings swap prior. {} before the paper clock starts,
    without a store, or without bars (the item is then not met). Read only: a missing store root is never created."""
    from goldbot.data.store import Store
    from goldbot.execution.costs import settings_extra_cost_usd, settings_swap
    root = Path(settings.data_root).expanduser()
    if start is None or not root.exists():
        return {}
    try:
        bars = Store(root).read("bars_1h", source="dukascopy", symbol=GOLD, start=start, end=end,
                                columns=["ts_utc", "bid_close", "ask_close"])
    except (OSError, ValueError, KeyError, TimeoutError):
        return {}
    if bars.empty:
        return {}
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)) + pd.Timedelta(hours=1)    # the bar's close
    mid = pd.Series(((bars["bid_close"] + bars["ask_close"]) / 2).to_numpy(float), index=ts)
    last = mid.groupby(opportunity_day(ts)).tail(1)
    bh = buy_and_hold(last, symbol=GOLD, round_trip_per_unit=settings_extra_cost_usd(settings),
                      swap=settings_swap(settings))
    return {GOLD: bh} if bh is not None else {}


# ---------------------------------------------------------------------------------------------- stop rule (P6)
def evaluate_stop_rule(trades: list[ClosedTrade], paper_start: pd.Timestamp | None, weekly_cap: float,
                       cfg: GateSettings, now: pd.Timestamp, *, imputer: CostImputer) -> StopRuleStatus:
    """P6. Pooled paper + live trades since the paper phase began (the backtest_to_paper record; the first trade when
    it is not recorded). Breached when (elapsed >= stop_after_months AND pooled > stop_min_pooled_trades AND the
    one-sided lower bound < 0) OR any single trade lost more than `weekly_cap` of its equity before it. Gross rows stay
    pooled and the bound is on their return less the imputed cost (quant review: "Keep gross rows in, but apply the
    same cost charge"). The incident check adds a gross row's imputed cost to its loss: the charge is in USD and is
    subtracted from `pnl` as if it were account currency (more than its value on a GBP or EUR deposit: conservative
    for the stop rule). Fail closed (quant review of R130): a pooled non-gold gross row the imputer cannot price (no
    own instrument terms; it is never charged gold's) would count at its gross return, so any such row breaches
    (`unpriced`) until it is priced. Gold's unpriced rows (no price or entry time) are reported as before: breaching
    on them would turn every legacy record without a price into a project stop."""
    start = paper_start if paper_start is not None else (min(t.exit_utc for t in trades) if trades else None)
    pooled = [t for t in trades if start is None or t.exit_utc >= start]
    months = _months(start, now) if start is not None else 0.0
    rets, note = imputer.net_rets(pooled)
    lb = lower_bound(rets, cfg.stop_confidence)
    reasons: list[str] = []
    breach: list[Literal["long_run", "single_loss", "unpriced"]] = []
    unpriced = sum(1 for t in pooled if t.symbol not in (None, GOLD) and not imputer.charge(t).priced)
    if unpriced:
        breach.append("unpriced")
        reasons.append(f"{unpriced} pooled non-gold gross row(s) could not be priced (no own instrument terms): their "
                       "cost is unknown, so the rule fails closed")
    if months >= cfg.stop_after_months and len(pooled) > cfg.stop_min_pooled_trades and lb is not None and lb < 0:
        breach.append("long_run")
        reasons.append(f"after {months:.1f} months and {len(pooled)} pooled trades the lower {cfg.stop_confidence:.0%} "
                       f"bound on expectancy is {lb:.5f} (< 0)")
    # a USD-flagged P&L (pnl_currency) is kept here as recorded, never dropped: on a GBP or EUR deposit it overstates
    # the loss fraction (conservative for the stop rule); it is kept out of money sums elsewhere
    losses = [(-(t.pnl - (imputer.charge(t).usd or 0.0)) / t.equity_before, t) for t in trades]
    worst = max(losses, key=lambda x: x[0]) if losses else None
    over = sorted((x for x in losses if x[0] > weekly_cap), key=lambda x: (-x[0], x[1].exit_utc))
    if over and worst is not None:
        breach.append("single_loss")
        frac, t = worst
        more = f" ({len(over)} such trades)" if len(over) > 1 else ""
        reasons.append(f"single loss of {frac:.2%} of equity on {t.account_id} ({t.mode}) closed {t.exit_utc:%Y-%m-%d %H:%M} "
                       f"UTC exceeds the weekly cap {weekly_cap:.1%}{more}")
    detail = (f"{len(pooled)} pooled trades (needs > {cfg.stop_min_pooled_trades}), {months:.1f} of "
              f"{cfg.stop_after_months} months, lower {cfg.stop_confidence:.0%} bound {_fmt(lb)}, "
              f"worst single loss {_fmt(worst[0] if worst else None, '.2%')} (weekly cap {weekly_cap:.1%})"
              + _charged(note))
    return StopRuleStatus(breached=bool(reasons), reasons=reasons, pooled_trades=len(pooled), months=round(months, 2),
                          lower_bound=lb, worst_loss_frac=worst[0] if worst else None, detail=detail,
                          clock_start_utc=start,
                          clock_source=None if start is None else "paper_gate" if paper_start is not None else "first_trade",
                          breach=breach, incident_count=len(over), imputed=note,
                          incidents=[StopIncident(account=t.account_id, mode=t.mode, closed_utc=t.exit_utc, loss_frac=f)
                                     for f, t in over[:MAX_INCIDENTS]])


# ---------------------------------------------------------------------------------------------- gates (P7)
def _evidence_item(ev: dict[str, dict[str, Any]], name: str, need: str, ok: Any,
                   threshold: str | None = None) -> GateItem:
    """A recorded evidence item: the value or pass/fail and the date as evidence; the owner's typed note apart in
    `detail` (it can hold a login or a host, so only the CLI prints it)."""
    e = ev.get(name)
    if not e:
        return GateItem(name=name, met=False, evidence="not recorded (run.py gate-evidence)", need=need,
                        threshold=threshold)
    shown = f"value {e['value']}" if e.get("value") is not None else ("passed" if e.get("passed") else "failed")
    return GateItem(name=name, met=bool(ok(e)), evidence=f"{shown} on {str(e.get('ts_utc', '?'))[:10]}", need=need,
                    detail=str(e["detail"]) if e.get("detail") else None, threshold=threshold)


def _previous(i: int, passed: list[str]) -> list[GateItem]:
    if i == 0:
        return []
    prev = GATES[i - 1]
    return [GateItem(name="previous_gate", met=prev in passed, evidence="recorded" if prev in passed else "not recorded",
                     need=f"{prev} recorded first")]


def _incident_item(stop: StopRuleStatus, weekly_cap: float) -> GateItem:
    bad = stop.worst_loss_frac is not None and stop.worst_loss_frac > weekly_cap
    return GateItem(name="no_incident", met=not bad,
                    evidence=f"worst single loss {_fmt(stop.worst_loss_frac, '.2%')}",
                    need=f"no single loss > weekly cap {weekly_cap:.1%} (stop rule)")


def _gross_share_item(name: str, note: ImputationSummary, limit: float | None) -> GateItem:
    """`gates.max_gross_share` (owner decision, recommended 5%): the share of the phase's trades whose return is not
    net. With the setting off it is shown for context and decides nothing."""
    share = note.n_gross / note.n if note.n else 0.0
    evidence = f"{note.n_gross} of {note.n} trades gross ({share:.1%}), each charged an imputed cost"
    if limit is None:
        return GateItem(name=name, met=True, required=False, evidence=evidence,
                        need="context only (gates.max_gross_share is off)")
    return GateItem(name=name, met=share <= limit, evidence=evidence, need=f"<= {limit:.0%} of trades gross")


def evaluate_gates(state_dir: Path, settings: Settings, now: pd.Timestamp, trials_path: Path | None = None, *,
                   buy_hold: Mapping[str, BuyHoldRead] | None = None,
                   adv_usd: Mapping[str, float] | None = None) -> GateReport:
    """Every gate's items against the evidence; never writes anything (see `write_report`). `buy_hold`: buy-and-hold
    over the paper window per instrument (`store_buy_hold`); `adv_usd`: reference daily volume per instrument, USD
    (ADR 0106 lane 1's reference). Either missing leaves its ADR 0106 item unmet (fail closed)."""
    state_dir = Path(state_dir)
    cfg, cap = settings.gates, settings.risk.weekly_cap
    errors: list[str] = []
    st, when, err = phase_record(state_dir)
    if err:
        errors.append(err)
    passed = [str(g) for g in st.get("gates_passed", [])]
    trades, err = load_closed_trades(state_dir)
    if err:
        errors.append(err)
    ev = load_evidence(state_dir)
    reg_path = trials_path if trials_path is not None else _registry_path(state_dir, settings)
    rows = _trial_rows(reg_path)
    k_now = n_trials_effective(rows)                 # once per evaluation: best_backtest_trial and gate 1 read the same K
    best = best_backtest_trial(rows, k=k_now)
    mf = ((best or {}).get("results") or {}).get("model_filtered") or {}
    # the backtest reference for paper expectancy and live drawdown is the best trial only when its research pass
    # counts for a promotion (registry.phase_gate_refusal: gates passed with costs recorded, the latest rerun of its
    # config, and a discovery-derived config's holdout passed): a trial that fails that is no reference, so the
    # comparator can never silently switch to an unrelated trial and pass (review of 978c667; quant review 2026-10-16)
    # quant-reviewer ruling 2026-11-18 Q1: a discovered spec's DSR is read at the registry's n_trials_discovery (a
    # tightening only; every other spec keeps n_trials_effective, tests/test_phase_gate_pin.py)
    k_disc = _discovery_k(reg_path) if best is not None and discovery_origin(best) else None
    refusal = phase_gate_refusal(best, rows, k_discovery=k_disc) if best is not None else "no evaluated trial"
    best_ok = refusal is None
    bt_mean = float(mf["mean_ret"]) if best_ok and mf.get("mean_ret") is not None else None
    bt_dd = float(mf["max_dd"]) if best_ok and mf.get("max_dd") is not None else None

    paper_start, live_start = when.get("backtest_to_paper"), when.get("paper_to_tiny_live")
    demo = [t for t in trades if t.mode == "demo" and (paper_start is None or t.exit_utc >= paper_start)]
    # ADR 0032: only trades decided under the default limits are paper evidence; demo-profile ones never count
    paper = [t for t in demo if counts_as_default_evidence(t, settings.risk)]
    excluded = len(demo) - len(paper)
    live = [t for t in trades if t.mode == "live" and (live_start is None or t.exit_utc >= live_start)]
    imputer = CostImputer.from_state(state_dir, settings)
    stop = evaluate_stop_rule(trades, paper_start, cap, cfg, now, imputer=imputer)
    gates: list[GateStatus] = []

    # 0 foundation -> backtest: the leakage audit
    tol = cfg.shuffle_auc_tolerance
    items = [
        _evidence_item(ev, "shuffle_auc", f"|AUC - 0.5| <= {tol} (at chance)",
                       lambda e: e.get("value") is not None and abs(float(e["value"]) - 0.5) <= tol + 1e-12,
                       "shuffle_auc_tolerance"),
        _evidence_item(ev, "asof_violations", "0 (as-of audit clean)",
                       lambda e: e.get("value") is not None and float(e["value"]) == 0),
        _evidence_item(ev, "feed_mismatch_share", f"<= {cfg.feed_max_mismatch_share} of bars (broker and Dukascopy agree)",
                       lambda e: e.get("value") is not None and float(e["value"]) <= cfg.feed_max_mismatch_share,
                       "feed_max_mismatch_share"),
    ]
    gates.append(_status(0, passed, items, when))

    # 1 backtest -> paper: from the trial registry and the measured spread table; the DSR recomputed on today's count
    k_gate = phase_gate_k(best, rows, k_disc) if best is not None else k_now
    k_name = f"n_trials_discovery {k_gate}" if best is not None and discovery_origin(best) \
        else f"the registry's {k_now} trials"
    dsr, dsr_why = current_dsr(best, k_gate) if k_gate is not None else (None, "n_trials_discovery unread")
    stored = mf.get("dsr")
    gchecks = {c.get("name"): c for c in (((best or {}).get("results") or {}).get("gates") or {}).get("checks", [])}
    years = gchecks.get("positive_years") or {}
    spread_tables = [c for c in _cost_tables(state_dir) if int(c.get("n_ticks") or 0) > 0 and c.get("spread")]
    trial = f"trial #{best.get('trial')} ({best.get('family')})" if best else "no evaluated trial"
    failed_checks = [str(n) for n, c in gchecks.items() if not c.get("passed")]
    research_ev = (f"{trial} passed the research gates" if best_ok else
                   f"{trial} did not pass the research gates"
                   + (f" (failed: {', '.join(failed_checks)})" if failed_checks else "")
                   + (f": {refusal}" if refusal and "research gates" not in refusal else "")
                   if best else trial)
    reference = f"{trial}" + ("" if best_ok or best is None else ", which did not pass the research gates: no reference")
    # ADR 0083 amendment (decided 2026-11-14): the best trial's CPCV/PBO evidence for this quarter, READ ONLY. The gate
    # never runs CPCV (the tournament or research_pass --cpcv produce it), so missing evidence leaves the item unmet.
    # The registry exists whenever there is a best trial, so TrialRegistry writes nothing here.
    # An unreadable evidence sidecar is no evidence (trading-safety review): the item is unmet, the report still runs.
    try:
        cpcv_why = (cpcv_gate.promotion_refusal(TrialRegistry(reg_path), best, rows, now=now.to_pydatetime())
                    if best is not None else "no evaluated trial")
    except (OSError, ValueError) as exc:        # JSONDecodeError is a ValueError
        cpcv_why = f"can't read CPCV evidence ({type(exc).__name__})"
    items = _previous(1, passed) + [
        GateItem(name="research_gates", met=best_ok, evidence=research_ev,
                 need="the trial passed the design's research gates with swap charged and its cost source stated"),
        GateItem(name="cpcv", met=cpcv_why is None,
                 evidence=f"{trial}: clean CPCV/PBO evidence for {quarter_of(now.to_pydatetime())}" if cpcv_why is None
                 else cpcv_why,
                 need=f"CPCV evidence this quarter with PBO <= {cpcv.PBO_FRAGILE:.0%} and at most half the paths "
                      "negative (ADR 0003, ADR 0083 amendment; read only, never run by the gate)"),
        GateItem(name="dsr", met=dsr is not None and dsr >= cfg.backtest_min_dsr,
                 evidence=(f"{_fmt(dsr, '.3f')} at {trial}, recomputed on {k_name}"
                           if dsr is not None else f"n/a at {trial}: {dsr_why}")
                 + (f" ({float(stored):.3f} stored at run time)" if stored is not None else ""),
                 need=f">= {cfg.backtest_min_dsr} on the true trial count", threshold="backtest_min_dsr"),
        GateItem(name="positive_years", met=bool(years.get("passed")), evidence=str(years.get("detail") or "n/a"),
                 need="positive in 3 separate years incl. 2021-22 (research gates)"),
        GateItem(name="backtest_trades", met=int(mf.get("n") or 0) >= cfg.backtest_min_trades,
                 evidence=f"{int(mf.get('n') or 0)} model-filtered trades", need=f">= {cfg.backtest_min_trades}",
                 threshold="backtest_min_trades",
                 progress=GateProgress(value=int(mf.get("n") or 0), target=cfg.backtest_min_trades, unit="trades")),
        GateItem(name="live_tick_spread_model", met=bool(spread_tables),
                 evidence=", ".join(f"{c.get('account_id')} {int(c.get('n_ticks') or 0):,} ticks" for c in spread_tables)
                 or "no cost table built from own ticks", need="a spread table measured from the terminal's own ticks"),
    ]
    gates.append(_status(1, passed, items, when))

    # 2 paper -> tiny live
    p_rets, p_note = imputer.net_rets(paper)
    p_mean = mean_ret(p_rets)
    p_days = (now - paper_start).total_seconds() / 86400 if paper_start is not None else 0.0
    slip = _fill_slippage(state_dir)
    items = _previous(2, passed) + [
        GateItem(name="paper_trades", met=len(paper) >= cfg.paper_min_trades,
                 evidence=f"{len(paper)} closed demo trades"
                 + (f" ({excluded} under raised demo limits not counted, ADR 0032)" if excluded else "")
                 + ("" if closed_trade_files(state_dir) else " (no closed-trade record yet)"),
                 need=f">= {cfg.paper_min_trades}", threshold="paper_min_trades",
                 progress=GateProgress(value=len(paper), target=cfg.paper_min_trades, unit="trades")),
        GateItem(name="paper_days", met=p_days >= cfg.paper_min_days, evidence=f"{p_days:.0f} days since backtest_to_paper",
                 need=f">= {cfg.paper_min_days} days", threshold="paper_min_days",
                 progress=GateProgress(value=round(p_days, 2), target=cfg.paper_min_days, unit="days"),
                 earliest_utc=_earliest(paper_start, cfg.paper_min_days)),
        GateItem(name="paper_expectancy",
                 met=not p_note.n_unpriced and within(p_mean, bt_mean, cfg.paper_expectancy_within),
                 evidence=f"paper {_fmt(p_mean)} vs backtest {_fmt(bt_mean)} per trade ({reference}){_charged(p_note)}",
                 need=f"no more than {cfg.paper_expectancy_within:.0%} below a positive backtest expectancy",
                 threshold="paper_expectancy_within", ci95=mean_ci95(p_rets)),
        GateItem(name="fills", met=slip is not None and slip[0] <= (1 + cfg.paper_fills_within) * slip[1],
                 evidence=(f"measured slippage ${slip[0]:.3f}/oz on {slip[2]} fills vs modelled ${slip[1]:.3f}"
                           if slip else "no measured fills in the cost tables"),
                 need=f"within {cfg.paper_fills_within:.0%} of the modelled slippage", threshold="paper_fills_within"),
        _evidence_item(ev, "chaos_drill", "passed", lambda e: e.get("passed") is True),
        _incident_item(stop, cap),
        _gross_share_item("paper_gross_share", p_note, cfg.max_gross_share),
    ]
    # ADR 0106: the goals of record on the paper record as if traded on real money
    inputs, mapped, unmapped = as_if_inputs(paper, imputer)
    limits = AsIfLimits.stricter(settings.risk)      # trading-safety F1: the stricter of ADR 0106 and the rails
    as_if = as_if_real_money(inputs, equity=cfg.as_if_equity_usd, adv_usd=adv_usd or {}, limits=limits)
    small = as_if_real_money(inputs, equity=AS_IF_MIN_EQUITY_USD, adv_usd=adv_usd or {}, limits=limits)
    no_adv = sorted({i.symbol for i in inputs} - set(adv_usd or {}))
    d0 = opportunity_day(pd.DatetimeIndex([paper_start]))[0].date() if paper_start is not None else None
    d1 = opportunity_day(pd.DatetimeIndex([now]))[0].date()
    sharpe = daily_sharpe(as_if.daily, d0, d1) if d0 is not None else None
    cs = cost_share(mapped, as_if, imputer)
    items += [
        _as_if_item(as_if, unmapped, cfg, small, no_adv),
        GateItem(name="paper_sharpe", met=sharpe is not None and sharpe > cfg.paper_min_sharpe,
                 evidence=f"as-if daily Sharpe {_fmt(sharpe, '.2f')} (the lower of naive and Lo 2002 lags 1-{LO_LAGS}) "
                          "over the session days since backtest_to_paper",
                 need=f"> {cfg.paper_min_sharpe}: a floor, not evidence of an edge (ADR 0106 goal)",
                 threshold="paper_min_sharpe"),
        _buy_hold_item(as_if, sorted({t.symbol or GOLD for t in paper}), buy_hold or {}, p_days, cfg),
        GateItem(name="paper_cost_share",
                 met=cs.measurable and cs.share is not None and cs.share <= cfg.paper_max_cost_share,
                 evidence=(f"costs {_fmt(cs.share, '.1%')} of gross in as-if USD on "
                           f"{cs.n_sized - cs.n_unmeasured} measured as-if sized trades"
                           + (f" ({cs.n_unmeasured} of {cs.n_sized} unmeasured, "
                              f"${cs.unmeasured_notional_usd:,.0f} of ${cs.notional_usd:,.0f} notional: no side, fill "
                              "prices or decision spread)" if cs.n_unmeasured else "")),
                 need=f"<= {cfg.paper_max_cost_share:.1%} of gross profit in as-if USD, at most "
                      f"{COST_SHARE_MAX_UNMEASURED:.0%} of as-if sized trades unmeasured by count and by notional "
                      "(ADR 0106 goal)",
                 threshold="paper_max_cost_share"),
        _shadow_item(state_dir),
    ]
    gates.append(_status(2, passed, items, when))

    # 3 tiny live -> full size
    live = sorted(live, key=lambda t: t.exit_utc)
    l_rets, l_note = imputer.net_rets(live)
    l_mean = mean_ret(l_rets)
    l_days = (now - live_start).total_seconds() / 86400 if live_start is not None else 0.0
    l_dd = max_drawdown(np.asarray(l_rets)) if live else None
    by_broker = {b: [t for t in live if t.broker == b] for b in sorted({t.broker for t in live})}
    means = {b: mean_ret(imputer.net_rets(ts)[0]) for b, ts in by_broker.items()}
    l_ok = not l_note.n_unpriced                 # a gross row that cannot be charged fails these items closed
    enough = [b for b, ts in by_broker.items() if len(ts) >= cfg.broker_min_trades]
    if len(enough) >= 2:
        vals = [float(means[b] or 0.0) for b in enough]
        spread, scale = max(vals) - min(vals), max(abs(v) for v in vals)
        brokers_ok = scale > 0 and spread <= cfg.brokers_within * scale
    else:
        brokers_ok = False
    items = _previous(3, passed) + [
        GateItem(name="live_trades", met=len(live) >= cfg.live_min_trades, evidence=f"{len(live)} closed live trades",
                 need=f">= {cfg.live_min_trades}", threshold="live_min_trades",
                 progress=GateProgress(value=len(live), target=cfg.live_min_trades, unit="trades")),
        GateItem(name="live_days", met=l_days >= cfg.live_min_days, evidence=f"{l_days:.0f} days since paper_to_tiny_live",
                 need=f">= {cfg.live_min_days} days", threshold="live_min_days",
                 progress=GateProgress(value=round(l_days, 2), target=cfg.live_min_days, unit="days"),
                 earliest_utc=_earliest(live_start, cfg.live_min_days)),
        GateItem(name="live_expectancy",
                 met=l_ok and not p_note.n_unpriced and within(l_mean, p_mean, cfg.live_expectancy_within),
                 evidence=f"live {_fmt(l_mean)} vs paper {_fmt(p_mean)} per trade{_charged(l_note)}",
                 need=f"no more than {cfg.live_expectancy_within:.0%} below a positive paper expectancy",
                 threshold="live_expectancy_within", ci95=mean_ci95(l_rets)),
        GateItem(name="live_drawdown",
                 met=l_ok and l_dd is not None and bt_dd is not None and l_dd < cfg.live_dd_mult * bt_dd,
                 evidence=f"live max drawdown {_fmt(l_dd, '.4f')} vs backtest {_fmt(bt_dd, '.4f')} ({reference})"
                 + _charged(l_note),
                 need=f"under {cfg.live_dd_mult}x backtest", threshold="live_dd_mult"),
        GateItem(name="brokers_agree", met=l_ok and brokers_ok,
                 evidence=(", ".join(f"{b} {len(ts)} trades {_fmt(means[b])}" for b, ts in by_broker.items())
                           or "no live trades") + _charged(l_note),
                 need=f"two brokers with >= {cfg.broker_min_trades} trades each, expectancy within {cfg.brokers_within:.0%}",
                 threshold="broker_min_trades"),
        _incident_item(stop, cap),
        _gross_share_item("live_gross_share", l_note, cfg.max_gross_share),
    ]
    gates.append(_status(3, passed, items, when))
    return GateReport(ts=now, phase=int(st.get("phase", 0) or 0), gates_passed=passed, gates=gates, stop_rule=stop,
                      errors=errors)


def _status(i: int, passed: list[str], items: list[GateItem], when: dict[str, pd.Timestamp]) -> GateStatus:
    rec = GATES[i] in passed
    return GateStatus(gate=GATES[i], met=all(it.met for it in items if it.required), recorded=rec, items=items,
                      recorded_utc=when.get(GATES[i]) if rec else None)


def _earliest(start: pd.Timestamp | None, days: float) -> pd.Timestamp | None:
    """The calendar date a day count is reached (never a forecast of trades); None before its clock starts."""
    return None if start is None else start + pd.Timedelta(days=days)


def _registry_path(state_dir: Path, settings: Settings) -> Path:
    p = Path(settings.research.registry)
    return p if p.is_absolute() else Path(state_dir).parent / p


def _fill_slippage(state_dir: Path) -> tuple[float, float, int] | None:
    """(fill-weighted measured market-order slippage $/oz, the modelled prior, fills) over every cost table."""
    tot, n, prior = 0.0, 0, None
    for c in _cost_tables(state_dir):
        for key, s in (c.get("slippage") or {}).items():
            if str(key).endswith(":market") and not s.get("from_prior") and int(s.get("n") or 0) > 0:
                tot += float(s["mean"]) * int(s["n"])
                n += int(s["n"])
        if c.get("slippage_prior_usd") is not None:
            prior = float(c["slippage_prior_usd"])
    return (tot / n, prior, n) if n and prior is not None else None


def _shadow_item(state_dir: Path) -> GateItem:
    f = Path(state_dir) / "shadow_book.json"
    rets: list[float] = []
    try:
        for book in (_read_json(f) if f.exists() else {}).values():
            rets += [float(t["ret"]) for t in book.get("closed", []) if t.get("taken", True) and t.get("ret") is not None]
    except (ValueError, OSError, AttributeError, TypeError, KeyError):
        pass
    m = float(np.mean(rets)) if rets else None
    return GateItem(name="shadow_record", met=True, required=False,
                    evidence=f"{len(rets)} closed shadow trades, mean {_fmt(m)}", need="context only (not a gate item)")


# ---------------------------------------------------------------------------------------------- output
def render(report: GateReport) -> str:
    lines = [f"phase {report.phase}; gates recorded: {', '.join(report.gates_passed) or 'none'}"]
    for g in report.gates:
        rec = "recorded" if g.recorded else ("MET, not recorded" if g.met else "not recorded")
        lines.append(f"\n{g.gate}: {'MET' if g.met else 'NOT MET'} ({rec})")
        for it in g.items:
            mark = "info" if not it.required else ("ok" if it.met else "--")
            note = f" — {it.detail}" if it.detail else ""
            lines.append(f"  [{mark:4}] {it.name:<22} {it.evidence}{note}  (need {it.need})")
    s = report.stop_rule
    lines.append(f"\nstop rule: {'BREACHED' if s.breached else 'not breached'} — {s.detail}")
    for r in s.reasons:
        lines.append(f"  ! {r}")
    if s.breached:
        lines.append("  The design says the project stops. Nothing here halts trading: send /halt (or Halt on the dashboard)"
                     " to stop entries; the RiskGate caps still apply.")
    for e in report.errors:
        lines.append(f"error: {e}")
    lines.append(f"\n{report.note}")
    return "\n".join(lines)


def write_report(state_dir: Path, report: GateReport) -> Path:
    f = Path(state_dir) / REPORT_FILE
    write_atomic(f, report.model_dump_json(indent=1), durable=False)
    return f


def main(argv: list[str], state_dir: str | Path = "state", settings: Settings | None = None,
         now: pd.Timestamp | None = None) -> int:
    """`gates [--json] [--no-write]`: print every gate as met / not met with its evidence (exit 0 always: a gate
    not met is the normal state of a phase); writes state/gate_report.json unless --no-write."""
    from goldbot.config import load_settings
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run gates")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    settings = settings or load_settings()
    now = now if now is not None else pd.Timestamp.now("UTC")
    _, when, _ = phase_record(Path(state_dir))
    rep = evaluate_gates(Path(state_dir), settings, now,
                         buy_hold=store_buy_hold(settings, when.get("backtest_to_paper"), now))
    print(rep.model_dump_json(indent=1) if args.json else render(rep))
    if not args.no_write:
        write_report(Path(state_dir), rep)
    return 0


def evidence_main(argv: list[str], state_dir: str | Path = "state") -> int:
    """`gate-evidence <name> (--value X | --passed yes|no) [--detail TEXT]`."""
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run gate-evidence")
    ap.add_argument("name", help="one of: " + ", ".join(EVIDENCE_NAMES))
    ap.add_argument("--value", type=float)
    ap.add_argument("--passed", choices=("yes", "no"))
    ap.add_argument("--detail", default="")
    args = ap.parse_args(argv)
    try:
        record_evidence(Path(state_dir), args.name, value=args.value,
                        passed=None if args.passed is None else args.passed == "yes", detail=args.detail)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"recorded evidence {args.name} (no gate recorded; see `run.py gates`)")
    return 0
