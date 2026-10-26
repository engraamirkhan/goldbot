"""The daily learning-ops review (research-director spec 2026-10-26; loop lane L2 made daily). Scheduler job
`daily_review` at 06:15 UTC, after the nightly learner (inside nightly_costs) and before the 06:45 owner report, over
the opportunity day before the slot (report_days: D-1 22:00 to D 22:00 UTC, the day the owner's report uses).

What it measures, per broker (accounts.yaml `broker`), host and session (Asia / London / New York, the cost table's
sessions), from the exploration ledger (aux.db `exploration_obs`, `exploration_trades`, `cost_probes`) and the brain's
store (`decisions`, `exit_fills`, `bars_1m`):
* costs in $/oz: spread at the fill, entry slippage `side x (fill - requested)`, exit slippage `side x (requested -
  filled)` of the exit legs, the stop overshoot past the server stop (`requested - side x stop_usd`, as
  cost_probe._place sets it) in $ and as a multiple of the $0.70 scenario (docs/research/cost-model-1m-2026-10-25.md),
  commission and the paid swap per oz; and the fill rate (taken candidates that filled);
* refusal, skip and stale reasons with counts, from the journal and from the candidates' `blocked_reason`;
* candidates and fills per style against the ~290 a day plan (settings `daily_review.volume_plan`);
* arm health against the pre-registered H-61 stops ("running net R in exit order reaches -30 R within its first 300
  trades (scalp) / -15 R within 100 (intraday) / -10 R within 40 (swing)", prereg-2026-10-25-high-volume.md), the same
  computation the owner's report uses (telegram/labs.py `arm_statuses`), reported ONLY as `breached` or `ok`;
* integrity: missing open-market minutes and duplicates in each host's 1m bars, clock faults, engine incidents from
  the journal, failing health checks.

What it does on its own:
* retires an arm the day its pre-registered stop is breached ("each arm stops for good ... re-entry only by a new
  pre-registration"): aux.db `exploration_arm_status`, per broker, permanent. The engine reads it once a minute and
  sends no new candidate of a retired arm; its open positions keep every exit (goldbot/engine/exploration.py);
* refits a (broker, session) slippage cell once it has `refit_min_fills` (200) fills since its last fit, floored at
  the research slippage floor (never credited) and never lowered within a day; the label replay charges a cell only
  when it is dearer than the nightly table (jobs.exploration_costs);
* opens or comments on the open GitHub issue labelled `learning-ops` for any incident, or when volume is under half
  the plan on two trading days running, with the counted cause; and one status comment a day on issue #34. Each
  kind is posted at most once a day. Without the keyring's github-token, or when a post fails, the body is written
  to state (`outbox/`) and the review carries on.

What it NEVER does: report a per-arm R, mean, sum, rank, sign or share anywhere (the record refuses such a field at
construction and every text is checked before it is written or sent), add an arm, or change a threshold, prior, cap
or the bandit. Weekly (Monday's run, the Sunday day) it writes a roll-up of the week's trends and at most four variant
proposals for the research director, which nothing runs.

Outputs: state/aux/daily_review/<day>.json (`DailyReview`), an `artifacts` row in program.db when that store exists,
the blinded lines of the owner's report (`report_lines`, read by goldbot/telegram/digest.py).
CLI: `python -m goldbot.ops.run daily-review [--dry-run] --date YYYY-MM-DD [--state DIR]` (dry run: computes and
prints, writes and posts nothing).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, Record, UtcTimestamp, load_record_json, write_atomic
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.db.connection import Database, db_path
from goldbot.ops.report_days import day_bounds

if TYPE_CHECKING:
    from goldbot.config import DailyReviewSettings, Settings
    from goldbot.engine.exploration import ExplorationRecord
    from goldbot.ops.github_issues import IssueSink

log = logging.getLogger("goldbot.daily_review")

SCHEMA_VERSION = 1
SESSIONS: tuple[str, ...] = ("asia", "london", "newyork")
ALL = "all"
OZ_PER_LOT = 100.0
STOP_EXITS = frozenset({"stop", "engine_stop_close"})
RULE = "H-61 per-arm stop (docs/research/prereg-2026-10-25-high-volume.md)"
BROKER_NAMES = {"icm": "ICM", "vantage": "Vantage"}
# journal actions that are engine incidents (goldbot/engine/exploration.py, runner.py)
ENGINE_INCIDENTS = ("exploration_error", "exploration_rule_error", "exploration_refused:ledger_unreadable",
                    "exploration_arm_dropped", "auto_unconfirmed", "auto_paused")
CLOCK_ACTIONS = ("exploration_clock_ahead",)
REFUSAL_ACTION = re.compile(r"refus|stale|skip|blocked|paused|^gate:|drift_halt|unconfirmed", re.I)
# blinding (quant review 2026-10-26, spec section 3): no key or text may carry a per-arm R, rank, sign or share
_BANNED_TOKENS = frozenset({"r", "rank", "ranks", "ranked", "ranking", "sign", "signs", "signed", "pnl", "share",
                            "shares", "expectancy", "winrate", "win", "best", "worst", "mean", "sum"})
_TEXT_R = re.compile(r"[+\-−]?\d+(\.\d+)?\s?R\b")
_TEXT_WORDS = re.compile(r"\b(rank|ranked|ranking|sign|signs|best|worst|top|bottom|leading|lagging|win rate|"
                         r"expectancy|shares?|mean|sum|p-value|significant|t =)\b", re.I)
Health = Literal["breached", "ok"]
IncidentKind = Literal["data_gap", "data_duplicate", "clock", "engine", "health", "unreadable"]


def banned_key(key: str) -> bool:
    """A key that names a per-arm R, rank, sign, share or P&L (any token of it, so `S1A_r` too)."""
    k = str(key).lower()
    return k in _BANNED_TOKENS or any(t in _BANNED_TOKENS for t in re.split(r"[^a-z0-9]+", k))


def _keys(obj: Any) -> Iterable[str]:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _keys(v)


def _refuse_banned(rec: Record) -> None:
    bad = sorted({k for k in _keys(rec.model_dump()) if banned_key(k)})
    if bad:
        raise ValueError(f"per-arm R, rank, sign or share field refused (blinded until the read): {bad}")


def text_problems(text: str) -> list[str]:
    """What in `text` would unblind an arm: an R figure or a ranking word."""
    return [m.group(0) for m in (*_TEXT_R.finditer(text), *_TEXT_WORDS.finditer(text))]


def blind(text: str) -> str:
    """`text` as it may be written or sent: a line with an R figure or a ranking word is dropped and logged."""
    out = []
    for line in text.splitlines():
        if text_problems(line):
            log.error("daily review: a line with a blinded figure or word was dropped")
            continue
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- the record
class SessionCosts(FrozenRecord):
    """One (broker, host, session) cell; host and session `all` is the broker's own line. $ per oz."""
    broker: str
    host: str
    session: str
    taken: int = 0                          # candidates the policy took, decided in the day
    filled: int = 0                         # ... of which the broker filled
    fill_rate: float | None = None
    fills: int = 0                          # entry legs with a fill price sent in the day (the cost sample)
    spread_usd: float | None = None
    entry_slip_usd: float | None = None     # signed mean, adverse positive
    exit_slip_usd: float | None = None      # signed mean of the exit legs, adverse positive
    commission_usd: float | None = None
    swap_usd: float | None = None           # paid swap only
    round_trip_usd: float | None = None     # spread + adverse entry and exit slippage + commission
    stop_exits: int = 0
    overshoot_usd: float | None = None      # mean past the server stop, adverse positive
    overshoot_x: float | None = None        # ... as a multiple of the overshoot scenario ($0.70)


class StyleVolume(FrozenRecord):
    broker: str
    style: str                              # scalp / intraday / swing (exploration arms) or probe (cost probes)
    candidates: int
    fills: int
    plan: int
    of_plan: float | None


class Integrity(FrozenRecord):
    host: str
    bars_rows: int = 0
    bars_missing_min: int | None = None     # None: no bars of the host recorded here, or not a trading day
    bars_duplicate: int = 0
    clock_events: int = 0
    engine_incidents: int = 0


class Incident(FrozenRecord):
    kind: IncidentKind
    host: str | None
    count: int
    detail: str


class CellRefit(FrozenRecord):
    broker: str
    session: str
    day: str
    fills: int
    slippage_usd: float
    previous_usd: float | None = None


class Retirement(FrozenRecord):
    broker: str
    arm_id: str
    day: str


class DailyReview(Record):
    """state/aux/daily_review/<day>.json. Refuses any field or key naming a per-arm R, rank, sign or share."""
    schema_version: int = SCHEMA_VERSION
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    generated_utc: UtcTimestamp
    dry_run: bool = False
    trading_day: bool = True
    costs: list[SessionCosts] = Field(default_factory=list)
    refusals: dict[str, dict[str, int]] = Field(default_factory=dict)       # host -> reason -> count
    volume: list[StyleVolume] = Field(default_factory=list)
    volume_total: int = 0
    plan_total: int = 0
    of_plan: float | None = None
    volume_low_days: int = 0                                                # low trading days running
    arm_health: dict[str, dict[str, Health]] = Field(default_factory=dict)  # broker -> arm -> breached | ok
    arms_retired: dict[str, int] = Field(default_factory=dict)              # broker -> arms retired so far
    integrity: list[Integrity] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    refits: list[CellRefit] = Field(default_factory=list)
    retired: list[Retirement] = Field(default_factory=list)                 # retired in this review
    posted: dict[str, str] = Field(default_factory=dict)                    # kind -> posted | outbox
    weekly: str | None = None
    # prereg Amendment 4: the day's exploration trades void as evidence (aux.db evidence_exclusions), a count only
    evidence_void: int = 0

    @model_validator(mode="after")
    def _blinded(self) -> DailyReview:
        _refuse_banned(self)
        return self

    def cost(self, broker: str, host: str, session: str) -> SessionCosts:
        for c in self.costs:
            if (c.broker, c.host, c.session) == (broker, host, session):
                return c
        raise KeyError((broker, host, session))


class DayTrend(FrozenRecord):
    day: str
    volume_total: int
    plan_total: int
    of_plan: float | None
    fill_rate: float | None
    incidents: int
    round_trip_usd: dict[str, float | None]
    overshoot_x: dict[str, float | None]
    refusals: dict[str, int]
    styles_under_half: list[str]


class VariantProposal(FrozenRecord):
    title: str
    rationale: str
    cost: Literal["one registry trial if pre-registered"] = "one registry trial if pre-registered"
    status: Literal["proposed, not run"] = "proposed, not run"


class WeeklyRollup(Record):
    """state/aux/daily_review/weekly-<sunday>.json: the week's trends and at most four variant proposals for the
    research director (never run here). Blinded like the daily record."""
    week_ending: str
    generated_utc: UtcTimestamp
    days: list[DayTrend] = Field(default_factory=list)
    trials_left: int | None = None    # the quarter's registry trials left; 0 or unknown: no proposal is made
    proposals: list[VariantProposal] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _blinded(self) -> WeeklyRollup:
        _refuse_banned(self)
        if any(text_problems(f"{p.title} {p.rationale}") for p in self.proposals):
            raise ValueError("a variant proposal carries a blinded figure or word")
        return self


# ---------------------------------------------------------------------------------------------- paths
def review_root(state_dir: str | Path) -> Path:
    return Path(state_dir) / "aux" / "daily_review"


def review_path(state_dir: str | Path, day: str) -> Path:
    return review_root(state_dir) / f"{day}.json"


def weekly_path(state_dir: str | Path, day: str) -> Path:
    return review_root(state_dir) / f"weekly-{day}.json"


def outbox_dir(state_dir: str | Path) -> Path:
    return review_root(state_dir) / "outbox"


def _posted_path(state_dir: str | Path) -> Path:
    return review_root(state_dir) / "posted.json"


def load_review(state_dir: str | Path, day: str) -> DailyReview | None:
    p = review_path(state_dir, day)
    try:
        return load_record_json(DailyReview, p.read_text(encoding="utf-8"), source=p.name) if p.exists() else None
    except (ValueError, OSError):
        log.warning("daily review %s unreadable", p.name)
        return None


def load_weekly(state_dir: str | Path, day: str) -> WeeklyRollup | None:
    p = weekly_path(state_dir, day)
    return load_record_json(WeeklyRollup, p.read_text(encoding="utf-8"), source=p.name) if p.exists() else None


# ---------------------------------------------------------------------------------------------- arm status
def retired_arms(db: Database, broker: str) -> set[str]:
    """The arms retired at their stop on `broker` (aux.db exploration_arm_status)."""
    with db.connection() as conn:
        return {str(a) for (a,) in conn.execute(
            "SELECT arm_id FROM exploration_arm_status WHERE broker = ? AND status = 'retired'", (broker,))}


def retire(db: Database, *, broker: str, arm_id: str, day: str, now: pd.Timestamp) -> bool:
    """Retire `arm_id` on `broker` for good. True when recorded now, False when it already was (the first stands)."""
    with db.write() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO exploration_arm_status (broker, arm_id, status, day, rule, "
                           "recorded_ns) VALUES (?, ?, 'retired', ?, ?, ?)",
                           (broker, arm_id, day, RULE, int(pd.Timestamp(now).value)))
        done = cur.rowcount == 1
    if done:
        log.warning("exploration arm %s on %s retired at its pre-registered stop (%s)", arm_id, broker, day)
    return done


def retired_on(db: Database, day: str) -> list[Retirement]:
    """The arms retired by the review of `day` (a rerun of the day reports them again, from the table)."""
    with db.connection() as conn:
        return [Retirement(broker=str(b), arm_id=str(a), day=day) for b, a in conn.execute(
            "SELECT broker, arm_id FROM exploration_arm_status WHERE day = ? ORDER BY broker, arm_id", (day,))]


def refits_on(db: Database, day: str) -> list[CellRefit]:
    """The cost cells fitted for `day`, from the table (a rerun of the day reports them again)."""
    with db.connection() as conn:
        rows = conn.execute("SELECT broker, session, fills, slippage_usd FROM exploration_cost_cells WHERE day = ? "
                            "ORDER BY broker, session", (day,)).fetchall()
        out = []
        for b, sess, n, v in rows:
            prev = conn.execute("SELECT slippage_usd FROM exploration_cost_cells WHERE broker = ? AND session = ? "
                                "AND day < ? ORDER BY day DESC LIMIT 1", (b, sess, day)).fetchone()
            out.append(CellRefit(broker=str(b), session=str(sess), day=day, fills=int(n), slippage_usd=float(v),
                                 previous_usd=float(prev[0]) if prev else None))
    return out


# ---------------------------------------------------------------------------------------------- cost cells
def write_cell(db: Database, *, broker: str, session: str, day: str, slippage_usd: float, fills: int,
               through: pd.Timestamp, now: pd.Timestamp) -> float:
    """Record a (broker, session) slippage fit for `day`; within a day the cell is only ever raised (and stays
    effective from the day's first fit: the dearer value, never a cheaper one). Returns the cell's value."""
    with db.write() as conn:
        conn.execute(
            "INSERT INTO exploration_cost_cells (broker, session, day, slippage_usd, fills, through_ns, fitted_ns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(broker, session, day) DO UPDATE SET "
            "slippage_usd = MAX(slippage_usd, excluded.slippage_usd), fills = excluded.fills, "
            "through_ns = MAX(through_ns, excluded.through_ns), fitted_ns = MIN(fitted_ns, excluded.fitted_ns)",
            (broker, session, day, float(slippage_usd), int(fills), int(pd.Timestamp(through).value),
             int(pd.Timestamp(now).value)))
        return float(conn.execute("SELECT slippage_usd FROM exploration_cost_cells WHERE broker = ? AND session = ? "
                                  "AND day = ?", (broker, session, day)).fetchone()[0])


def _cells(conn: sqlite3.Connection, broker: str, as_of: pd.Timestamp) -> dict[str, tuple[float, str, int, int]]:
    """session -> (slippage, day, fitted_ns, through_ns) of the latest fit effective at `as_of`."""
    out: dict[str, tuple[float, str, int, int]] = {}
    for session, slip, day, fitted, through in conn.execute(
            "SELECT session, slippage_usd, day, fitted_ns, through_ns FROM exploration_cost_cells "
            "WHERE broker = ? AND fitted_ns <= ? ORDER BY day", (broker, int(pd.Timestamp(as_of).value))):
        out[str(session)] = (float(slip), str(day), int(fitted), int(through))
    return out


def cost_cells(db: Database, broker: str, as_of: pd.Timestamp) -> dict[str, float]:
    """session -> the refit slippage cell of `broker` in effect at `as_of` (point in time)."""
    with db.connection() as conn:
        return {s: v[0] for s, v in _cells(conn, broker, as_of).items()}


def replay_cells(state_dir: str | Path, broker: str, as_of: pd.Timestamp) -> tuple[float, str, pd.Timestamp] | None:
    """For the label replay (jobs.exploration_costs): (the dearest refit cell of `broker` in effect at `as_of`, the
    newest fit day, when it took effect); None without any, without aux.db or before migration 18. Read only."""
    p = db_path(state_dir, "aux")
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            cells = _cells(conn, broker, as_of)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not cells:
        return None
    return (max(v[0] for v in cells.values()), max(v[1] for v in cells.values()),
            pd.Timestamp(max(v[2] for v in cells.values()), unit="ns", tz="UTC"))


def refit_cells(db: Database, *, hosts: Mapping[str, str], day: str, hi: pd.Timestamp, min_fills: int,
                now: pd.Timestamp, write: bool) -> list[CellRefit]:
    """Per (broker, session): the entry fills sent after the cell's last fit and before `hi`; at `min_fills` or more,
    a new fit = max(research floor, mean adverse-positive slippage) (prereg H-60 costs: "slippage max(0.15, measured)
    ... never credited")."""
    from goldbot.research.evidence_exclusions import excluded
    from goldbot.research.exploration_data import SLIPPAGE_FLOOR_USD
    from goldbot.telegram.labs import trades
    out: list[CellRefit] = []
    hi_ns = int(hi.value)
    for broker in sorted(set(hosts.values())):
        accts = sorted(a for a, b in hosts.items() if b == broker)
        marks = ",".join("?" * len(accts))
        with db.connection() as conn:
            last = _cells(conn, broker, pd.Timestamp(2**62, unit="ns", tz="UTC"))
            void = excluded(conn, "cost_cells")                # prereg Amendment 4
            rows = [t for t in trades(conn, f"account_id IN ({marks}) AND ts_ns < ?", (*accts, hi_ns))
                    if t.fill_price is not None and t.requested_price is not None and t.client_order_id not in void]
        if not rows:
            continue
        labels = _sessions([t.sent_utc for t in rows])
        for session in SESSIONS:
            since = last[session][3] if session in last else -1
            sel = [t for t, s in zip(rows, labels, strict=True) if s == session and int(t.sent_utc.value) > since]
            if len(sel) < min_fills:
                continue
            slip = max(SLIPPAGE_FLOOR_USD,
                       float(np.mean([t.side * (float(t.fill_price or 0.0) - float(t.requested_price or 0.0))
                                      for t in sel])))
            prev = last[session][0] if session in last else None
            if write:
                value = write_cell(db, broker=broker, session=session, day=day, slippage_usd=slip, fills=len(sel),
                                   through=hi, now=now)
            else:                     # what write_cell would keep: never cheaper than the same day's fit
                value = max(slip, prev) if prev is not None and last[session][1] == day else slip
            out.append(CellRefit(broker=broker, session=session, day=day, fills=len(sel), slippage_usd=round(value, 6),
                                 previous_usd=prev))
    return out


# ---------------------------------------------------------------------------------------------- figures
def _sessions(ts: Sequence[pd.Timestamp]) -> list[str]:
    if not ts:
        return []
    return [str(x) for x in DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(list(ts), utc=True)))]


def _mean(v: Sequence[float]) -> float | None:
    return round(float(np.mean(v)), 6) if len(v) else None


def _cell(broker: str, host: str, session: str, *, entries: Sequence[ExplorationRecord],
          closes: Sequence[ExplorationRecord], exit_slips: Sequence[float], taken: int, filled: int,
          scenario: float) -> SessionCosts:
    spread = _mean([float(t.spread_usd) for t in entries if t.spread_usd is not None])
    entry = _mean([t.side * (float(t.fill_price) - float(t.requested_price)) for t in entries
                   if t.fill_price is not None and t.requested_price is not None])
    exit_ = _mean(list(exit_slips))
    per_oz = [(t, t.lots * OZ_PER_LOT * float(t.fx_acct_per_usd)) for t in closes
              if t.fx_acct_per_usd and t.fx_acct_per_usd > 0 and t.lots > 0]
    commission = _mean([abs(float(t.commission)) / d for t, d in per_oz if t.commission is not None])
    swap = _mean([max(0.0, -float(t.swap)) / d for t, d in per_oz if t.swap is not None])
    over = [t.side * ((float(t.requested_price) - t.side * float(t.stop_usd)) - float(t.exit_price))
            for t in closes if t.exit_reason in STOP_EXITS and t.requested_price is not None
            and t.exit_price is not None and t.stop_usd]
    overshoot = _mean(over)
    rt = None if spread is None else round(spread + max(entry or 0.0, 0.0) + max(exit_ or 0.0, 0.0)
                                           + (commission or 0.0), 6)
    return SessionCosts(broker=broker, host=host, session=session, taken=taken, filled=filled,
                        fill_rate=round(filled / taken, 6) if taken else None, fills=len(entries), spread_usd=spread,
                        entry_slip_usd=entry, exit_slip_usd=exit_, commission_usd=commission, swap_usd=swap,
                        round_trip_usd=rt, stop_exits=len(over), overshoot_usd=overshoot,
                        overshoot_x=round(overshoot / scenario, 6) if overshoot is not None else None)


def _frame(store: Any, table: str, host: str, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    df = store.read(table, source=host, start=lo, end=hi)
    if df is None or df.empty or "ts_utc" not in df:
        return pd.DataFrame()
    ts = pd.to_datetime(df["ts_utc"], utc=True)
    return df.assign(ts_utc=ts)[(ts >= lo) & (ts < hi)].reset_index(drop=True)


class _Day:
    """What one review reads: the day's candidates, trades, probes and journal of the hosts."""

    def __init__(self, db: Database, store: Any, hosts: Mapping[str, str], lo: pd.Timestamp, hi: pd.Timestamp,
                 incidents: list[Incident]):
        from goldbot.engine import cost_probe
        from goldbot.telegram.labs import trades
        self.hosts, self.lo, self.hi = dict(hosts), lo, hi
        a, b = int(lo.value), int(hi.value)
        marks = ",".join("?" * len(hosts))
        with db.connection() as conn:
            self.obs = pd.read_sql_query(
                f"SELECT candidate_id, account_id, arm_id, style, taken, blocked_reason, decision_ns FROM "
                f"exploration_obs WHERE account_id IN ({marks}) AND decision_ns >= ? AND decision_ns < ? AND "
                "quarantine IS NULL ORDER BY decision_ns", conn, params=(*hosts, a, b))
            self.style_of = {str(c): str(s) for c, s in conn.execute(
                f"SELECT candidate_id, style FROM exploration_obs WHERE account_id IN ({marks})", tuple(hosts))}
            self.sent = [t for t in trades(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?",
                                           (*hosts, a, b)) if t.fill_price is not None]
            self.closed = [t for t in trades(conn, f"account_id IN ({marks}) AND closed = 1 AND exit_ns >= ? "
                                             "AND exit_ns < ?", (*hosts, a, b)) if t.fill_price is not None]
            # a candidate taken late in the day may fill just after it: every fill from the day on counts
            self.filled_ids = {t.client_order_id for t in trades(conn, f"account_id IN ({marks}) AND ts_ns >= ?",
                                                                 (*hosts, a)) if t.fill_price is not None}
            self.probes = cost_probe.LEDGER.select(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?",
                                                   (*hosts, a, b))
        self.obs["session"] = _sessions(list(pd.to_datetime(self.obs["decision_ns"], unit="ns", utc=True)))
        self.journal: dict[str, pd.DataFrame] = {}
        self.exits: dict[str, pd.DataFrame] = {}
        self.bars: dict[str, pd.DataFrame | None] = {}
        for host in hosts:
            for name, table, into in (("journal", "decisions", self.journal), ("exit legs", "exit_fills", self.exits)):
                try:
                    into[host] = _frame(store, table, host, lo, hi)
                except Exception as exc:     # noqa: BLE001 - one unreadable source is an incident, not a crash
                    into[host] = pd.DataFrame()
                    incidents.append(Incident(kind="unreadable", host=host, count=1,
                                              detail=f"{name} unreadable ({type(exc).__name__})"))
            try:
                self.bars[host] = _frame(store, "bars_1m", host, lo, hi)
            except Exception as exc:         # noqa: BLE001
                self.bars[host] = None
                incidents.append(Incident(kind="unreadable", host=host, count=1,
                                          detail=f"1m bars unreadable ({type(exc).__name__})"))


def _exit_slips(d: _Day, host: str) -> list[tuple[str, float]]:
    """(session, side x (requested - filled)) of the host's exploration exit legs in the day."""
    df = d.exits.get(host, pd.DataFrame())
    if df.empty or not {"agent_id", "side", "requested", "filled"} <= set(df.columns):
        return []
    df = df[df["agent_id"].astype(str).str.startswith("explore:")].dropna(subset=["requested", "filled"])
    if df.empty:
        return []
    slips = df["side"].to_numpy(dtype=float) * (df["requested"].to_numpy(dtype=float)
                                                - df["filled"].to_numpy(dtype=float))
    return list(zip(_sessions(list(df["ts_utc"])), (float(x) for x in slips), strict=True))


def _costs(d: _Day, scenario: float) -> list[SessionCosts]:
    out: list[SessionCosts] = []
    sent_s = dict(zip((t.client_order_id for t in d.sent), _sessions([t.sent_utc for t in d.sent]), strict=True))
    close_s = dict(zip((t.client_order_id for t in d.closed), _sessions([t.exit_utc or t.sent_utc for t in d.closed]),
                       strict=True))
    taken = d.obs[d.obs["taken"].astype(bool)]
    for broker in sorted(set(d.hosts.values())):
        hosts = sorted(h for h, b in d.hosts.items() if b == broker)
        b_entries, b_closes, b_exits, b_taken, b_filled = [], [], [], 0, 0
        for host in hosts:
            slips = _exit_slips(d, host)
            for session in SESSIONS:
                entries = [t for t in d.sent if t.account_id == host and sent_s[t.client_order_id] == session]
                closes = [t for t in d.closed if t.account_id == host and close_s.get(t.client_order_id) == session]
                tk = taken[(taken["account_id"] == host) & (taken["session"] == session)]
                n_taken = len(tk)
                n_filled = int(tk["candidate_id"].astype(str).isin(d.filled_ids).sum())
                ex_s = [v for s, v in slips if s == session]
                if n_taken or entries or closes or ex_s:
                    out.append(_cell(broker, host, session, entries=entries, closes=closes, exit_slips=ex_s,
                                     taken=n_taken, filled=n_filled, scenario=scenario))
                b_entries += entries
                b_closes += closes
                b_exits += ex_s
                b_taken += n_taken
                b_filled += n_filled
        out.append(_cell(broker, ALL, ALL, entries=b_entries, closes=b_closes, exit_slips=b_exits, taken=b_taken,
                         filled=b_filled, scenario=scenario))
    return out


def _refusals(d: _Day) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for host in d.hosts:
        counts: dict[str, int] = {}
        o = d.obs[(d.obs["account_id"] == host) & ~d.obs["taken"].astype(bool)]
        for why in o["blocked_reason"].dropna().astype(str):
            k = "obs:" + why.split(",", 1)[0].strip()
            counts[k] = counts.get(k, 0) + 1
        j = d.journal.get(host, pd.DataFrame())
        for action in (j["action"].astype(str) if not j.empty and "action" in j else []):
            if REFUSAL_ACTION.search(action):
                k = "journal:" + action.split(",", 1)[0].strip()
                counts[k] = counts.get(k, 0) + 1
        # a reason whose words would read as a blinded figure is counted under a neutral key, never dropped
        clean: dict[str, int] = {}
        for k, v in counts.items():
            key = k if not banned_key(k) else k.split(":", 1)[0] + ":other"
            clean[key] = clean.get(key, 0) + v
        if clean:
            out[host] = clean
    return out


def _volume(d: _Day, plan: Mapping[str, Mapping[str, int]], probe_plan: Mapping[str, int]) -> list[StyleVolume]:
    """Per broker: exploration candidates and fills per style, and the cost probes (`probe`), each against its plan."""
    out: list[StyleVolume] = []
    for broker in sorted(set(d.hosts.values())):
        hosts = {h for h, b in d.hosts.items() if b == broker}
        bplan = {**plan.get(broker, {}), "probe": int(probe_plan.get(broker, 0))}
        cands: dict[str, int] = dict.fromkeys(bplan, 0)
        fills: dict[str, int] = dict.fromkeys(bplan, 0)
        for s in d.obs.loc[d.obs["account_id"].isin(hosts), "style"].astype(str):
            cands[s] = cands.get(s, 0) + 1
        for t in d.sent:
            if t.account_id in hosts:
                s = d.style_of.get(t.client_order_id, "scalp")
                fills[s] = fills.get(s, 0) + 1
        probes = [p for p in d.probes if p.account_id in hosts]
        cands["probe"] += len(probes)
        fills["probe"] += sum(p.fill_price is not None for p in probes)
        for s in [*bplan, *sorted(set(cands) - set(bplan))]:       # the plan's order, then any style it lacks
            out.append(StyleVolume(broker=broker, style=s, candidates=cands.get(s, 0), fills=fills.get(s, 0),
                                   plan=int(bplan.get(s, 0)),
                                   of_plan=round(cands.get(s, 0) / bplan[s], 6) if bplan.get(s) else None))
    return out


def _arm_health(db: Database, hosts: Mapping[str, str], hi: pd.Timestamp
                ) -> tuple[dict[str, dict[str, Health]], set[str]]:
    """Per broker: arm -> `breached` (its H-61 stop reached within its first trades) or `ok`; and the control arms
    (labs.ArmStatus.control: the candidates' flag or the spec's random bracket), which are never breached nor
    retired (quant 2026-10-26: the controls are the null). The running R is computed in labs and never leaves it."""
    from goldbot.telegram.labs import arm_statuses, control_arm_ids
    out: dict[str, dict[str, Health]] = {}
    controls: set[str] = set(control_arm_ids())
    with db.connection() as conn:
        for broker in sorted(set(hosts.values())):
            accts = sorted(h for h, b in hosts.items() if b == broker)
            st = arm_statuses(conn, accts, until=hi)
            controls |= {a.arm_id for a in st if a.control}
            out[broker] = {a.arm_id: ("breached" if a.band == "at_stop" and a.arm_id not in controls else "ok")
                           for a in st}
    return out, controls


def _integrity(d: _Day, trading: bool, max_gap: int, incidents: list[Incident]) -> list[Integrity]:
    out: list[Integrity] = []
    grid = pd.date_range(d.lo, d.hi, freq="1min", inclusive="left")
    expected = set(grid[DEFAULT_SESSIONS.is_open(grid)]) if trading else set()
    for host in d.hosts:
        j = d.journal.get(host, pd.DataFrame())
        actions = j["action"].astype(str) if not j.empty and "action" in j else pd.Series([], dtype=str)
        clock = int(actions.isin(CLOCK_ACTIONS).sum())
        eng = actions[actions.str.startswith(ENGINE_INCIDENTS)]
        bars = d.bars.get(host)
        rows, missing, dup = 0, None, 0
        if bars is not None and not bars.empty:
            ts = pd.DatetimeIndex(bars["ts_utc"])
            rows, dup = len(ts), int(ts.duplicated().sum())
            missing = len(expected - set(ts)) if trading else None
        out.append(Integrity(host=host, bars_rows=rows, bars_missing_min=missing, bars_duplicate=dup,
                             clock_events=clock, engine_incidents=len(eng)))
        if missing is not None and missing > max_gap:
            incidents.append(Incident(kind="data_gap", host=host, count=missing,
                                      detail=f"{missing} open-market minutes missing from the 1m bars"))
        if dup:
            incidents.append(Incident(kind="data_duplicate", host=host, count=dup, detail=f"{dup} duplicate 1m bars"))
        if clock:
            incidents.append(Incident(kind="clock", host=host, count=clock,
                                      detail=f"{clock} bars stamped ahead of the host clock"))
        if len(eng):
            vc = eng.value_counts()
            incidents.append(Incident(kind="engine", host=host, count=int(len(eng)),
                                      detail=", ".join(f"{int(n)} {a}" for a, n in vc.sort_index().items())))
    return out


def _health(state_dir: Path, incidents: list[Incident]) -> None:
    p = state_dir / "health_last.json"
    if not p.exists():
        return
    try:
        statuses = json.loads(p.read_text(encoding="utf-8")).get("statuses", {})
    except (ValueError, OSError, AttributeError):
        incidents.append(Incident(kind="unreadable", host=None, count=1, detail="health_last.json unreadable"))
        return
    fails = sorted(str(k) for k, v in statuses.items() if v == "fail")
    if fails:
        incidents.append(Incident(kind="health", host=None, count=len(fails), detail="failing: " + ", ".join(fails)))


def _previous(state_dir: Path, day: str) -> DailyReview | None:
    d = pd.Timestamp(day)
    for back in range(1, 8):
        prev = load_review(state_dir, f"{d - pd.Timedelta(days=back):%Y-%m-%d}")
        if prev is not None:
            return prev
    return None


# ---------------------------------------------------------------------------------------------- texts
def _usd(x: float | None) -> str:
    return "no fills" if x is None else f"${x:.2f}/oz"


def _broker(b: str) -> str:
    return BROKER_NAMES.get(b, b.upper())


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def report_lines(r: DailyReview) -> list[str]:
    """The blinded lines of the 06:45 owner report: costs per broker, volume against the plan, incidents and arms
    retired at their stop (a count, never which or by how much)."""
    costs = " · ".join(f"{_broker(c.broker)} {_usd(c.round_trip_usd)}" for c in r.costs if c.host == ALL)
    first = (f"Review: costs {costs or 'none'} (round trip) · volume {r.volume_total:,} of {r.plan_total:,} planned "
             f"({_pct(r.of_plan)})")
    out = [first]
    n_ret = len(r.retired)
    if r.incidents or n_ret:
        kinds = sorted({i.kind.replace('_', ' ') for i in r.incidents})
        parts = ([f"{len(r.incidents)} incident{'s' if len(r.incidents) != 1 else ''} ({', '.join(kinds)})"]
                 if r.incidents else [])
        parts += [f"{n_ret} arm{'s' if n_ret != 1 else ''} retired at {'their' if n_ret != 1 else 'its'} stop"] \
            if n_ret else []
        out.append("Review: " + " · ".join(parts))
    if r.evidence_void:
        out.append(f"Review: {r.evidence_void} trade{'s' if r.evidence_void != 1 else ''} void as evidence "
                   "(dq quarantine; H-61 stops and P&L keep it)")
    return [x for x in (blind(x) for x in out) if x]


def _status_body(r: DailyReview) -> str:
    lines = [f"**Daily learning-ops review {r.day}** (opportunity day; blinded: no per-arm figures)", ""]
    lines += [f"- {x.removeprefix('Review: ')}" for x in report_lines(r)]
    for c in (c for c in r.costs if c.host == ALL):
        lines.append(f"- {_broker(c.broker)}: {c.fills} fills, fill rate {_pct(c.fill_rate)}, spread "
                     f"{_usd(c.spread_usd)}, entry slippage {_usd(c.entry_slip_usd)}, exit slippage "
                     f"{_usd(c.exit_slip_usd)}, stop overshoot {_usd(c.overshoot_usd)}"
                     + (f" ({c.overshoot_x:.1f}x the scenario)" if c.overshoot_x is not None else ""))
    lines += [f"- Volume {_broker(v.broker)} {v.style}: {v.candidates} candidates, {v.fills} fills, plan {v.plan}"
              for v in r.volume]
    if r.refits:
        lines.append(f"- Cost cells refit: {', '.join(f'{_broker(c.broker)} {c.session}' for c in r.refits)}")
    lines.append(f"- Arm health: {sum(v == 'breached' for b in r.arm_health.values() for v in b.values())} breached, "
                 f"{sum(v == 'ok' for b in r.arm_health.values() for v in b.values())} ok")
    return blind("\n".join(lines))


def _issue(r: DailyReview, low_alert: bool, settings: DailyReviewSettings) -> tuple[str, str] | None:
    """(title, body) of the learning-ops issue, or None when there is nothing to raise."""
    if not r.incidents and not low_alert:
        return None
    what = [k for k, on in (("incidents", bool(r.incidents)), ("low volume", low_alert)) if on]
    title = f"Learning ops {r.day}: {' and '.join(what)}"
    lines = [f"Daily learning-ops review of the opportunity day {r.day} (blinded: no per-arm figures).", ""]
    if r.incidents:
        lines.append("**Incidents (counted)**")
        lines += [f"- {i.kind.replace('_', ' ')}{f' on {i.host}' if i.host else ''}: {i.detail}" for i in r.incidents]
    if low_alert:
        lines += ["", f"**Volume** {r.volume_total:,} of {r.plan_total:,} planned ({_pct(r.of_plan)}), under "
                      f"{settings.volume_alert_below:.0%} of the plan on {r.volume_low_days} trading days running.",
                  "Counted cause:"]
        lines += [f"- {_broker(v.broker)} {v.style}: {v.candidates} of {v.plan} planned" for v in r.volume
                  if v.plan and v.candidates < settings.volume_alert_below * v.plan]
        reasons: dict[str, int] = {}
        for counts in r.refusals.values():
            for k, n in counts.items():
                reasons[k] = reasons.get(k, 0) + n
        for k, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
            lines.append(f"- {n} {k}")
        if not reasons:
            lines.append("- no refusal recorded: the arms produced fewer candidates (signals), not refusals")
    return title, blind("\n".join(lines))


def _post(state_dir: Path, day: str, kind: str, body: str, sink: IssueSink | None, *, title: str = "",
          number: int | None = None, label: str | None = None) -> str:
    """Post `body` once a day per kind; without a sink (no token) or when the post fails it goes to outbox/."""
    posted_file = _posted_path(state_dir)
    try:
        posted: dict[str, str] = json.loads(posted_file.read_text(encoding="utf-8")) if posted_file.exists() else {}
    except (ValueError, OSError):
        posted = {}
    key = f"{day}:{kind}"
    if posted.get(key) == "posted":
        return "posted"

    def outbox() -> str:
        write_atomic(outbox_dir(state_dir) / f"{day}-{kind}.md", (f"# {title}\n\n" if title else "") + body + "\n")
        return "outbox"

    if sink is None:
        log.warning("daily review: no token (github-token not in the keyring); the %s text is in %s", kind,
                    outbox_dir(state_dir))
        return outbox()
    try:
        if number is not None:
            sink.comment(number, body)
        else:
            sink.open_or_comment(str(label), title, body)
    except Exception as exc:          # noqa: BLE001 - a failed post never stops the review; the text stays in state
        log.warning("daily review: the %s post failed (%s); the text is in %s", kind, type(exc).__name__,
                    outbox_dir(state_dir))
        return outbox()
    posted[key] = "posted"
    write_atomic(posted_file, json.dumps(dict(sorted(posted.items())[-120:]), indent=1))
    (outbox_dir(state_dir) / f"{day}-{kind}.md").unlink(missing_ok=True)    # an earlier failed attempt's copy
    return "posted"


def stale_outbox(state_dir: str | Path, now: pd.Timestamp, days: float = 2.0) -> int:
    """Issue texts waiting in outbox/ (no token or a failed post) for at least `days` (the owner's report says so)."""
    d = outbox_dir(state_dir)
    if not d.exists():
        return 0
    cut = pd.Timestamp(now).timestamp() - days * 86400
    return sum(1 for f in d.glob("*.md") if f.stat().st_mtime <= cut)


# ---------------------------------------------------------------------------------------------- weekly
_SLOT_REASONS = {"global_cap", "pot_slots", "arm_max_open"}


def _trend(r: DailyReview) -> DayTrend:
    alls = [c for c in r.costs if c.host == ALL]
    taken, filled = sum(c.taken for c in alls), sum(c.filled for c in alls)
    reasons: dict[str, int] = {}
    for counts in r.refusals.values():
        for k, n in counts.items():
            reasons[k] = reasons.get(k, 0) + n
    return DayTrend(day=r.day, volume_total=r.volume_total, plan_total=r.plan_total, of_plan=r.of_plan,
                    fill_rate=round(filled / taken, 6) if taken else None, incidents=len(r.incidents),
                    round_trip_usd={c.broker: c.round_trip_usd for c in alls},
                    overshoot_x={c.broker: c.overshoot_x for c in alls}, refusals=reasons,
                    styles_under_half=[v.style for v in r.volume if v.plan and v.candidates < 0.5 * v.plan]
                    if r.trading_day else [])


def _proposals(days: Sequence[DayTrend], limit: int) -> list[VariantProposal]:
    """Deterministic variant proposals from the week's operational figures only (costs, refusals, fill rate):
    never from any arm's result, and never a new timeframe or arm for low volume (quant 2026-10-26: volume is not
    evidence). Each costs one registry trial if the research director pre-registers it."""
    out: list[VariantProposal] = []
    for b in sorted({b for d in days for b in d.overshoot_x}):
        xs = [v for d in days if (v := d.overshoot_x.get(b)) is not None]
        if xs and float(np.mean(xs)) >= 1.0:
            out.append(VariantProposal(
                title=f"Resting broker stop variant on {_broker(b)}",
                rationale=f"stop exits overshot the server stop by {float(np.mean(xs)):.1f}x the $0.70 scenario on "
                          f"{len(xs)} days; a native resting stop would settle whether this is exchange slippage"))
    reasons: dict[str, int] = {}
    for d in days:
        for k, n in d.refusals.items():
            reasons[k] = reasons.get(k, 0) + n
    if reasons:
        k, n = max(sorted(reasons.items()), key=lambda kv: kv[1])
        code = k.split(":", 1)[1] if ":" in k else k
        title = ("Shorter hold variant to free open slots" if code in _SLOT_REASONS
                 else "Earlier send variant for signals going stale" if "stale" in code
                 else "Session filter variant for the widest-spread hours" if "spread" in code
                 else f"Review of the {code} refusals")
        out.append(VariantProposal(title=title, rationale=f"{n} candidates refused as {k} this week, the most "
                                                          "frequent refusal"))
    fr = [v for d in days if (v := d.fill_rate) is not None]
    if fr and float(np.mean(fr)) < 0.9:
        out.append(VariantProposal(title="Limit-entry variant", rationale=f"fill rate {float(np.mean(fr)):.0%} "
                                                                          "over the week"))
    return out[:limit]


def weekly_rollup(state_dir: Path, today: DailyReview, now: pd.Timestamp, limit: int,
                  trials_left: int | None = None) -> WeeklyRollup:
    """The week's trends and at most `limit` variant proposals, each costing one registry trial: none at all when
    the quarter's trial budget is spent or unknown (`trials_left`), and never more than the trials left."""
    end = pd.Timestamp(today.day)
    days: list[DayTrend] = []
    for back in range(6, 0, -1):
        r = load_review(state_dir, f"{end - pd.Timedelta(days=back):%Y-%m-%d}")
        if r is not None:
            days.append(_trend(r))
    days.append(_trend(today))
    cap = max(0, min(limit, trials_left)) if trials_left is not None else 0
    return WeeklyRollup(week_ending=today.day, generated_utc=now, days=days, trials_left=trials_left,
                        proposals=_proposals(days, cap))


# ---------------------------------------------------------------------------------------------- the run
def run_daily_review(*, settings: Settings, state_dir: str | Path, store: Any, hosts: Mapping[str, str], day: str,
                     now: pd.Timestamp, dry_run: bool = False, sink: IssueSink | None = None,
                     trials_left: int | None = None) -> DailyReview:
    """Review opportunity day `day` (YYYY-MM-DD) of the learning `hosts` (account -> broker). `dry_run`: compute
    only (what would be retired and refit is in the record); nothing is written, retired, refit or posted. A rerun
    of a day reports that day's retirements and refits from the tables and keeps what was already posted.
    `trials_left`: the quarter's registry trials left, for the Monday roll-up's proposals (None: none proposed)."""
    cfg = settings.daily_review
    sd = Path(state_dir)
    if not hosts:
        raise ValueError("no learning host (an enabled account with exploration on) to review")
    now = pd.Timestamp(now).tz_convert("UTC")
    lo, hi = day_bounds(day)
    trading = pd.Timestamp(day).dayofweek < 5
    db = Database.open(sd, "aux")
    incidents: list[Incident] = []
    d = _Day(db, store, hosts, lo, hi, incidents)
    costs = _costs(d, cfg.overshoot_scenario_usd)
    volume = _volume(d, cfg.volume_plan, cfg.probe_plan)
    total = sum(v.candidates for v in volume)
    plan_total = sum(v.plan for v in volume)
    of_plan = round(total / plan_total, 6) if plan_total else None
    integrity = _integrity(d, trading, cfg.max_gap_minutes, incidents)
    _health(sd, incidents)
    health, controls = _arm_health(db, hosts, hi)
    retired: list[Retirement] = []
    for broker, arms in health.items():
        already = retired_arms(db, broker)
        for arm, h in arms.items():
            if h == "breached" and arm not in already and arm not in controls:    # a control is never retired
                if dry_run or retire(db, broker=broker, arm_id=arm, day=day, now=now):
                    retired.append(Retirement(broker=broker, arm_id=arm, day=day))
    refits = refit_cells(db, hosts=hosts, day=day, hi=hi, min_fills=cfg.refit_min_fills, now=now,
                         write=not dry_run)
    if not dry_run:               # idempotent rerun: the day's actions as the tables hold them
        retired, refits = retired_on(db, day), refits_on(db, day)
    prev = _previous(sd, day)
    low = trading and of_plan is not None and of_plan < cfg.volume_alert_below
    carried = prev.volume_low_days if prev is not None else 0
    low_days = (carried + 1 if low else 0) if trading else carried
    review = DailyReview(day=day, generated_utc=now, dry_run=dry_run, trading_day=trading, costs=costs,
                         refusals=_refusals(d), volume=volume, volume_total=total, plan_total=plan_total,
                         of_plan=of_plan, volume_low_days=low_days, arm_health=health,
                         arms_retired={b: len(retired_arms(db, b)) for b in sorted(set(hosts.values()))},
                         integrity=integrity, incidents=incidents, refits=refits, retired=retired,
                         evidence_void=_void(db, hosts, lo, hi))
    if dry_run:
        return review
    before = load_review(sd, day)
    posted: dict[str, str] = dict(before.posted) if before is not None else {}
    issue = _issue(review, low and low_days >= cfg.volume_alert_days, cfg)
    if issue is not None:
        posted["learning-ops"] = _post(sd, day, "learning-ops", issue[1], sink, title=issue[0],
                                       label=cfg.issue_label)
    posted["status"] = _post(sd, day, "status", _status_body(review), sink, number=cfg.status_issue)
    review.posted = posted
    if pd.Timestamp(day).dayofweek == 6:        # Monday's run reviews the Sunday day: the week's roll-up
        week = weekly_rollup(sd, review, now, cfg.weekly_proposals, trials_left)
        write_atomic(weekly_path(sd, day), week.model_dump_json(indent=1))
        review.weekly = weekly_path(sd, day).name
    body = review.model_dump_json(indent=1)
    write_atomic(review_path(sd, day), body)
    _ledger(sd, day, body)
    return review


def _void(db: Database, hosts: Mapping[str, str], lo: pd.Timestamp, hi: pd.Timestamp) -> int:
    from goldbot.research.evidence_exclusions import counts
    with db.connection() as conn:
        return counts(conn, sorted(hosts), lo, hi)


def _ledger(state_dir: Path, day: str, body: str) -> None:
    """The review as a private `artifacts` row of program.db, only when that store exists (never created here)."""
    if not db_path(state_dir, "program").exists():
        return
    try:
        from goldbot.ledger.models import NewEntry
        from goldbot.ledger.store import Ledger
        Ledger.open(state_dir).add(NewEntry(
            table="artifacts", key=f"daily_review/{day}", body=f"daily learning-ops review {day}",
            source="daily_review", privacy="private", fields={"path": f"aux/daily_review/{day}.json"},
            blob=body.encode("utf-8")))
    except Exception as exc:      # noqa: BLE001 - the program ledger is a mirror: the state file stands
        log.warning("daily review %s not recorded in program.db (%s)", day, type(exc).__name__)


def summary(r: DailyReview) -> dict[str, Any]:
    """The scheduler's job result (state/scheduler.json): counts only."""
    return {"day": r.day, "volume": r.volume_total, "plan": r.plan_total, "incidents": len(r.incidents),
            "retired": len(r.retired), "refits": len(r.refits), "posted": dict(r.posted), "weekly": r.weekly}


# ---------------------------------------------------------------------------------------------- CLI
def _hosts() -> dict[str, str]:
    """The learning hosts: enabled accounts with exploration on, account -> broker (accounts.yaml)."""
    from goldbot.ops.accounts import enabled_accounts
    return {a.account_id: a.broker for a in enabled_accounts(logins=False) if a.exploration.enabled}


def trials_left(settings: Settings, trials: Any, now: pd.Timestamp) -> int | None:
    """The quarter's registry trials left (research budget less the trials stamped in it); None when unreadable."""
    from goldbot.research.director import quarter_budget
    from goldbot.research.registry import quarter_of
    try:
        q = quarter_of(pd.Timestamp(now).to_pydatetime())
        return max(0, quarter_budget(settings.research, q) - int(trials.budget_used(q)))
    except Exception as exc:          # noqa: BLE001 - unknown budget: no proposal (fail closed)
        log.warning("trial budget unreadable (%s): no variant proposals", type(exc).__name__)
        return None


def _trials_left(settings: Settings) -> int | None:
    from goldbot.research.registry import TrialRegistry
    return trials_left(settings, TrialRegistry(settings.research.registry), pd.Timestamp.now("UTC"))


def _store_for(settings: Settings) -> Any:
    from goldbot.data.store import Store
    return Store(settings.data_root)


def sink_for(settings: Settings, get_secret: Any = None) -> IssueSink | None:
    """GitHub issues with the keyring's github-token; None without one (the review then writes to outbox/)."""
    from goldbot.ops.github_issues import GitHubIssues
    if get_secret is None:
        from goldbot.ops.accounts import get_secret
    token = get_secret("github-token")
    return GitHubIssues(token, settings.daily_review.repo) if token else None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="daily-review", description="The daily learning-ops review of one day.")
    p.add_argument("--date", required=True, help="the opportunity day to review, YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="compute and print only: write, retire, refit, post nothing")
    p.add_argument("--state", default="state")
    a = p.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.date):
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    from goldbot.config import load_settings
    settings = load_settings()
    hosts = _hosts()
    if not hosts:
        print("no learning host: no enabled account has exploration on", file=sys.stderr)
        return 1
    review = run_daily_review(settings=settings, state_dir=a.state, store=_store_for(settings), hosts=hosts,
                              day=a.date, now=pd.Timestamp.now("UTC"), dry_run=a.dry_run,
                              sink=None if a.dry_run else sink_for(settings), trials_left=_trials_left(settings))
    print(review.model_dump_json(indent=1))
    return 0


__all__ = ["DailyReview", "WeeklyRollup", "banned_key", "blind", "cost_cells", "load_review", "load_weekly",
           "main", "outbox_dir", "refit_cells", "refits_on", "replay_cells", "report_lines", "retire",
           "retired_arms", "retired_on", "review_path", "run_daily_review", "stale_outbox", "summary",
           "trials_left", "write_cell"]
