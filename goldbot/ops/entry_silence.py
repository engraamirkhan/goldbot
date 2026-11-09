"""Entry silence on a demo exploring account (SRE, incident 2026-11-09: vantage-demo entered nothing for 9.5 h of open
market and nothing alerted; `daily_review.volume_alert_below` only opens an issue after two low days).

Two conditions, one health check per account (`entry_silence:<account>`, goldbot/ops/checks/entries.py), told on
Telegram through the health alert path (NOTIFY_ON_WARN):

(a) **no supply**: the account has no exploration arm that may enter today while its market has been open today and
    its day budget is not spent. An arm may not enter when its config refuses it (not `run`, no rule, a spec_rule arm
    off demo, spec-retired, unqualified or hash-refused, a timeframe outside `engine.timeframes_live`, a learning arm
    with `learning_max_open` 0), when the daily review retired it at its stop (aux.db `exploration_arm_status`), when
    it is a paired control none of whose arms may enter (p = 0), or when it is an unpaired per-instrument control its
    budget stops (`thin_cell_complete`, `thin_controls`: `exploration_budget.daily_refusal`, the engine's own rule).
(b) **silence**: today, the account went SILENCE_MIN minutes of London and New York market time without an exploration
    fill while (a) did not hold. Minutes after a session's or the day's budget was spent do not count (silence by
    design), Asia never counts (the 02:00-07:00 gold window and thin hours make it quiet by design).

Both are day-stable by construction, so the check warns at most once per account per UTC day: retirements and the cost
cells only grow within a day, the thin share resets at 00:00 UTC, and (b) is the longest silent run so far today,
which a later fill does not shorten. A condition that persists into the next day stays one warning (no daily repeat);
it clears at the latest when the day starts with supply again or the market is closed all day.

The supply mirrors the engine's selection (`Exploration.__init__`, `_Pass.active`, `_send`); where it is coarser
(window_0207, the paired controls' propensity table, learning loss caps, owner-experiment expiry, the hour budget) it
counts an arm as enterable, so (a) errs towards silence and (b) is the backstop: an account whose arms are all
refused for a reason (a) does not model fills nothing, and (b) tells. Reporting only: nothing here changes an order,
a cap or an arm. aux.db is opened read-only, never migrated.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.engine import exploration_budget as budget
from goldbot.instruments import GOLD
from goldbot.ops.accounts import DailyEntries

if TYPE_CHECKING:
    from goldbot.data.calendar import SessionTable
    from goldbot.ops.accounts import Account

SILENCE_MIN = 240                       # (b): four hours of London/New York market time without a fill
SILENT_SESSIONS = ("london", "newyork")
RUNBOOK = "RUNBOOK 'Entry silence on a demo account'"


class ArmSupply(FrozenRecord):
    """One configured arm as the supply needs it: its budget kind, instrument and the config's refusal (None: it may
    enter as far as the config goes)."""
    kind: budget.EntryKind
    instrument: str = GOLD
    blocked: str | None = None


class AccountSupply(FrozenRecord):
    """An exploring account's configured arms (`supply_for`): what the check reads beside aux.db."""
    broker: str
    daily: DailyEntries | None = None
    arms: dict[str, ArmSupply] = Field(default_factory=dict)
    pairs: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class Silence(FrozenRecord):
    """The verdict for one account at `now`."""
    market_today: bool
    enterable: tuple[str, ...]
    blocked: dict[str, int]              # reason -> arms
    configured: int
    fills_today: int
    max_per_day: int | None
    day_spent: bool
    last_fill: UtcTimestamp | None = None
    run_min: int = 0                     # the longest London/NY silent run today, market minutes
    run_from: UtcTimestamp | None = None
    run_to: UtcTimestamp | None = None

    @property
    def no_supply(self) -> bool:
        return self.market_today and not self.enterable and not self.day_spent

    @property
    def silent(self) -> bool:
        return not self.no_supply and self.run_min >= SILENCE_MIN


# ------------------------------------------------------------------------------------------------ the config side
def supply_for(acc: Account, timeframes_live: Sequence[str]) -> AccountSupply | None:
    """The account's arms as the engine selects them at its start; None when it does not explore."""
    from goldbot.engine.exploration import SPEC_RULE, config_for, entry_refused, spec_retired
    from goldbot.research import arm_rules
    cfg = config_for(acc)
    if cfg is None or not cfg.enabled:
        return None
    refused, retired, live = entry_refused(cfg), spec_retired(cfg), set(timeframes_live)
    ok = {a.arm_id: a for a in cfg.arms if a.spec.status == "run" and a.spec.rule in arm_rules.RULE_FUNCS
          and (a.spec.rule != SPEC_RULE or acc.mode == "demo")}
    arms: dict[str, ArmSupply] = {}
    for a in cfg.arms:
        why = ("not run" if a.arm_id not in ok else "spec retired" if a.arm_id in retired
               else "unqualified" if a.arm_id in refused else "timeframe dark" if a.tf not in live
               else "no learning slot" if a.spec.role == "learning" and cfg.learning_max_open == 0 else None)
        arms[a.arm_id] = ArmSupply(kind=budget.entry_kind(a), instrument=a.instrument, blocked=why)
    return AccountSupply(broker=acc.broker, daily=cfg.daily, arms=arms, pairs=budget.pairs(ok))


def supply_by_account(accounts: list[Account], timeframes_live: Sequence[str]) -> dict[str, AccountSupply]:
    """The exploring demo accounts' supply. A weekend_only lane (ADR 0062) is left out: its market is the weekend's
    24/7 symbol, not the gold week these sessions judge (weekend_247 watches it)."""
    out = {}
    for acc in accounts:
        if acc.mode == "demo" and not acc.weekend_only and (s := supply_for(acc, timeframes_live)) is not None:
            out[acc.account_id] = s
    return out


VIEW_TTL_S = 600.0                      # the config is read again after this, or at once when a config file changed
_VIEW: dict[tuple[object, ...], tuple[float, dict[str, AccountSupply]]] = {}
# (state dir, account) -> the last verdict this process reported: kept over a torn aux.db read (checks/entries.py)
_LAST: dict[tuple[str, str], Any] = {}


def cached_supply(accounts: list[Account], timeframes_live: Sequence[str],
                  clock: Callable[[], float] = time.monotonic) -> dict[str, AccountSupply]:
    """`supply_by_account`, kept for VIEW_TTL_S in this process: reading the arm spec costs about 2 s (measured on the
    Mac, 2026-11-09, four accounts) and the Telegram service runs health every 60 s on a 2-vCPU brain. Keyed by the
    accounts, the live timeframes and the config files' mtimes, so an edited config is read on the next pass."""
    from goldbot.engine.exploration import SPEC_ARMS_PATH, SPEC_PATH
    from goldbot.ops.accounts import ACCOUNTS_FILE
    stamps = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in (ACCOUNTS_FILE, SPEC_PATH, SPEC_ARMS_PATH))
    key = (tuple(sorted(a.account_id for a in accounts)), tuple(timeframes_live), stamps)
    now = clock()
    hit = _VIEW.get(key)
    if hit is not None and now - hit[0] < VIEW_TTL_S:
        return hit[1]
    view = supply_by_account(accounts, timeframes_live)
    _VIEW.clear()
    _VIEW[key] = (now, view)
    return view


# ------------------------------------------------------------------------------------------------ the ledger side
class _Ledger(FrozenRecord):
    retired: frozenset[str] = frozenset()
    fills: list[tuple[str, int, int]] = Field(default_factory=list)    # (arm, sent ns, signal ns) today, by sent time
    cells: dict[str, int] = Field(default_factory=dict)                # instrument -> fills ever
    trail: list[int] = Field(default_factory=list)                     # edge candidates' decision ns, trailing days


def _read(aux: Path, account_id: str, broker: str, day: pd.Timestamp, now: pd.Timestamp) -> _Ledger:
    if not aux.exists():
        return _Ledger()
    d0, n1 = int(day.value), int(now.value)
    # security review: the URI from the resolved path (as_uri escapes "?" and "#"), so nothing in it changes the mode
    con = sqlite3.connect(aux.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        retired: frozenset[str] = frozenset()
        if "exploration_arm_status" in tables:
            retired = frozenset(str(a) for (a,) in con.execute(
                "SELECT arm_id FROM exploration_arm_status WHERE broker = ? AND status = 'retired'", (broker,)))
        fills: list[tuple[str, int, int]] = []
        cells: dict[str, int] = {}
        if "exploration_trades" in tables:
            fills = [(str(a), int(t), int(s)) for a, t, s in con.execute(
                f"SELECT arm_id, ts_ns, {budget.SIGNAL} FROM exploration_trades WHERE account_id = ? "
                f"AND position_id IS NOT NULL AND ts_ns <= ? AND (ts_ns >= ? OR {budget.SIGNAL} >= ?) ORDER BY ts_ns",
                (account_id, n1, d0, d0))]
            cells = {str(sym or GOLD): int(n) for sym, n in con.execute(
                "SELECT json_extract(body, '$.symbol'), COUNT(*) FROM exploration_trades WHERE account_id = ? "
                "AND position_id IS NOT NULL AND ts_ns <= ? GROUP BY 1", (account_id, n1))}
        trail: list[int] = []
        if "exploration_obs" in tables:
            trail = [int(r[0]) for r in con.execute(
                "SELECT decision_ns FROM exploration_obs WHERE account_id = ? AND is_control = 0 AND decision_ns >= ? "
                "AND decision_ns < ?", (account_id, int(budget.trailing_start(day).value), d0))]
    finally:
        con.close()
    return _Ledger(retired=retired, fills=fills, cells=cells, trail=trail)


# ------------------------------------------------------------------------------------------------ the verdict
def _enterable(s: AccountSupply, led: _Ledger, thin_today: int) -> tuple[list[str], dict[str, int]]:
    blocked: dict[str, int] = {}

    def no(why: str) -> None:
        blocked[why] = blocked.get(why, 0) + 1

    active = set()
    for arm_id, a in s.arms.items():
        if a.blocked is not None:
            no(a.blocked)
        elif arm_id in led.retired:
            no("retired at its stop")
        else:
            active.add(arm_id)
    out = []
    for arm_id in sorted(active):
        a = s.arms[arm_id]
        if a.kind == "control" and arm_id in s.pairs and not set(s.pairs[arm_id]) & active:
            no("control without an active arm")
        elif a.kind == "thin" and s.daily is not None and (why := budget.daily_refusal(
                s.daily, "thin", {"thin": thin_today}, session="asia", budgets={"asia": 1 << 30},
                cell_fills=led.cells.get(a.instrument, 0))) is not None:
            no(why)
        else:
            out.append(arm_id)
    return out, blocked


def evaluate(s: AccountSupply, aux: Path, account_id: str, now: pd.Timestamp, sessions: SessionTable) -> Silence:
    """The account's supply and today's longest London/NY silence at `now` (UTC). Raises sqlite3.Error on a torn read."""
    now = pd.Timestamp(now).tz_convert("UTC")
    day = now.floor("D")
    led = _read(aux, account_id, s.broker, day, now)
    d0 = int(day.value)
    today = [(a, t, sig) for a, t, sig in led.fills if sig >= d0]          # the budget's day: by signal close
    thin_today = sum(1 for a, _t, _s in today if (x := s.arms.get(a)) is not None and x.kind == "thin")
    enterable, blocked = _enterable(s, led, thin_today)
    cap = s.daily.max_per_day if s.daily is not None else None
    grid = pd.date_range(day, now, freq="1min", inclusive="left")
    open_ = sessions.is_open(grid) if len(grid) else sessions.is_open(pd.DatetimeIndex([now]))
    labels: list[str] = [str(x) for x in sessions.session_label(grid)] if len(grid) else []
    split = budget.session_split(led.trail, s.daily.max_per_day) if s.daily is not None else {}
    # walk the day's minutes: a fill resets the run; a London/NY open minute with its session's and the day's budget
    # unspent adds one; anything else pauses it
    counts: dict[str, int] = {}
    day_n, i, run = 0, 0, 0
    best: tuple[int, pd.Timestamp | None, pd.Timestamp | None] = (0, None, None)
    run_from: pd.Timestamp | None = None
    sent = [(t, sig) for _a, t, sig in led.fills]
    for k, m in enumerate(grid):
        end = int(m.value) + 60_000_000_000
        while i < len(sent) and sent[i][0] < end:
            t, sig = sent[i]
            if sig >= d0:
                day_n += 1
                key = budget.session_of(pd.Timestamp(sig, tz="UTC"))
                counts[key] = counts.get(key, 0) + 1
            run, run_from, i = 0, pd.Timestamp(t, tz="UTC"), i + 1
        sess = labels[k]
        spent = (cap is not None and day_n >= cap) or counts.get(sess, 0) >= split.get(sess, 1 << 30)
        if open_[k] and sess in SILENT_SESSIONS and not spent:
            if run == 0 and run_from is None:
                run_from = m
            run += 1
            if run > best[0]:
                best = (run, run_from, m + pd.Timedelta(minutes=1))
    last = pd.Timestamp(led.fills[-1][1], tz="UTC") if led.fills else None
    return Silence(market_today=bool(open_.any()), enterable=tuple(enterable), blocked=blocked, configured=len(s.arms),
                   fills_today=len(today), max_per_day=cap, day_spent=cap is not None and len(today) >= cap,
                   last_fill=last, run_min=best[0], run_from=best[1], run_to=best[2])


def _hm(ts: pd.Timestamp | None) -> str:
    return "none today" if ts is None else f"{ts:%H:%M} UTC"


def describe(v: Silence) -> str:
    """The check's reason: what holds, the counts behind it and where the recovery is."""
    why = ", ".join(f"{n} {r}" for r, n in sorted(v.blocked.items(), key=lambda x: (-x[1], x[0])))
    fills = f"{v.fills_today} of {v.max_per_day}" if v.max_per_day is not None else f"{v.fills_today}"
    tail = f"; fills today {fills}, last fill {_hm(v.last_fill)}"
    if v.no_supply:
        return (f"no exploration arm may enter while the market is open: {v.configured} configured ({why or 'none'})"
                f"{tail}. Volume returns only with new enterable arms ({RUNBOOK})")[:600]
    if v.silent:
        frm = f"{v.run_from:%H:%M}" if v.run_from is not None else "?"
        to = f"{v.run_to:%H:%M}" if v.run_to is not None else "?"
        return (f"no exploration fill for {v.run_min // 60} h {v.run_min % 60:02d} min of London/New York market time "
                f"({frm}-{to} UTC) while {len(v.enterable)} arm(s) may enter{tail}; read today's "
                f"exploration_refused:* by arm ({RUNBOOK})")[:600]
    state = ("day budget spent" if v.day_spent else "market closed today" if not v.market_today
             else f"{len(v.enterable)} arm(s) may enter")
    return (f"{state}; longest London/NY silence today {v.run_min // 60} h {v.run_min % 60:02d} min{tail}")[:600]


__all__ = ["RUNBOOK", "SILENCE_MIN", "SILENT_SESSIONS", "VIEW_TTL_S", "AccountSupply", "ArmSupply", "Silence", "cached_supply",
           "describe", "evaluate", "supply_by_account", "supply_for"]
