"""The exploration labs as the owner's morning report and Sunday digest read them (docs/ux/daily-learning-report.md,
lane L5a of the closed learning loop): read-only figures from aux.db (`exploration_obs`, `exploration_trades`,
`exploration_broker_weights`) and the words and number formats both messages share.

The spec's rules, as built here:

* "Lab figures are from the exploration ledger only": signals and refusals from `exploration_obs` (quarantined rows
  excluded), fills, closes, P&L and costs from `exploration_trades`. The P0 cost probe lives in `cost_probes` and is
  never read here, so it is in neither the counts nor the arms.
* "Net only, labelled as such": P&L is the broker's net `pnl`; a close without it is shown apart with its gross from
  the fill and exit prices, never folded into the net. Per-trade R = pnl / `stop_loss` (the loss at the gate's stop).
* Costs: spread `spread_usd x oz x rate` at the fill, slippage the ADVERSE part of `(fill - requested) x side`
  ("favourable slippage is not netted against it"), commission and paid swap as the broker reports them. The exit's
  requested price is not recorded, so slippage is the entry's only and is labelled "slippage (entry)".
* "Arms are blinded until their read" (quant review 2026-10-26, spec section 3.4): `arm_statuses` returns per arm only
  its admissible trade count and a band (running / within 50% of its stop / at its stop). The running R that decides
  the band is computed in memory and never leaves this module; no share, mean, sum, rank or sign does.
* "Accounts appear by role label, not slug" (D6): `role` is the one table; an unmapped account is "Other account".

Reporting only: nothing here changes an order, a halt, a share or a setting, and aux.db is opened read-only (never
created or migrated by a report).
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import Field

from goldbot.base import Record
from goldbot.db.connection import db_path
from goldbot.engine.exploration import ExplorationRecord
from goldbot.ops.monthly_report import SYMBOLS

log = logging.getLogger(__name__)

MINUS = "−"                    # the true minus sign (copy rule: "+1.4R", "−3.9R")
CONTRACT_OZ = 100.0                 # XAUUSD oz per lot when the engine has not published `lot_terms.contract_oz`
LABS = ("icm-probe", "icm-demo", "vantage-demo")    # the labs in report order
# D6: one table from account to the owner's word; the slug never reaches the chat
ROLE = {"icm-probe": "Scalp lab", "icm-demo": "Day/swing lab", "vantage-demo": "Vantage lab",
        "icm-live": "Live (ICM)", "vantage-live": "Live (Vantage)"}
OTHER = "Other account"
READ_AT = 300                       # prereg H-61: "each arm is read once, at its 300th admissible trade"
# prereg 2026-10-25 H-61 "Per-arm stop (permanent)": running net R in exit order reaching `r` within the first `n`
# trades: scalp -30 R within 300, intraday -15 R within 100, swing -10 R within 40
ARM_STOP: dict[str, tuple[float, int]] = {"scalp": (-30.0, 300), "intraday": (-15.0, 100), "swing": (-10.0, 40)}
# quant 2026-10-26: a lab or style total (£ or R) needs closes from at least this many strategy arms in its window
MIN_ARMS_FOR_TOTAL = 3
BAND_SHARE = 0.5                    # "within 50% of its stop": the running R at or past half the stop's R
_TF_STYLE = {"1m": "scalp", "5m": "scalp", "15m": "intraday", "30m": "intraday", "1h": "intraday", "4h": "swing",
             "1d": "swing"}
# the refusal codes (engine `_send` and the RiskGate's reasons through the order core) in the owner's short words;
# anything else is "other reason". Policy skips (not taken, no reason) are not refusals and are never shown
REFUSAL_WORDS = {
    "global_cap": "slots full", "pot_slots": "slots full", "arm_max_open": "slots full",
    "spread_too_wide": "spread wide", "news_blackout": "news window",
    "budget": "budget used", "pot": "budget used", "arm_budget": "budget used",
    "stale": "signal too old", "paused_dd": "paused (drawdown)", "weekend_hold": "would hold over the weekend",
    "same_side": "same-side limit", "not_auto": "auto entries off", "arm_cap": "stop over the arm's limit",
    "order_unsettled": "order still settling", "arm_retired": "arm retired at its stop",
    "arm_status_unread": "arm status not read yet",
}
OTHER_REASON = "other reason"

Band = Literal["running", "within_50pct", "at_stop"]


# ---------------------------------------------------------------------------------------------- words and numbers
def role(account: str) -> str:
    """The owner's word for an account (D6); never its slug, login, server or host."""
    return ROLE.get(account, OTHER)


def signed(x: float, dp: int) -> str:
    """"+1.4" / "−3.9" with the true minus; a value that rounds to zero is "+0.0"."""
    v = round(x, dp)
    return f"{MINUS if v < 0 else '+'}{abs(v):,.{dp}f}"


def r_text(x: float, dp: int = 1) -> str:
    return f"{signed(x, dp)}R"


def money(x: float, ccy: str | None, *, sign: bool = True) -> str:
    """"+£21.60" / "−£17.60" (sign) or "£230.00" in the account's currency; an unknown currency prints its code."""
    sym = SYMBOLS.get((ccy or "").upper())
    body = f"{sym}{abs(x):,.2f}" if sym else f"{abs(x):,.2f} {ccy or '(currency not reported)'}"
    if sign:
        return (MINUS if round(x, 2) < 0 else "+") + body
    return (MINUS if round(x, 2) < 0 else "") + body


def whole_money(x: float, ccy: str | None) -> str:
    """A risk line's amount: "£657", no sign, no pence (the % beside it carries the precision)."""
    sym = SYMBOLS.get((ccy or "").upper())
    return f"{sym}{abs(x):,.0f}" if sym else f"{abs(x):,.0f} {ccy or ''}".rstrip()


def pct(x: float) -> str:
    """0.021 -> "2.1%"; a whole percentage drops its decimal ("6%")."""
    v = round(100 * x, 1)
    return f"{v:.0f}%" if v == int(v) else f"{v:.1f}%"


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _ns(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).value)


# ---------------------------------------------------------------------------------------------- aux.db, read-only
@contextmanager
def aux(state_dir: str | Path) -> Iterator[sqlite3.Connection | None]:
    """aux.db read-only (never created or migrated by a report); None when the file does not exist yet. A table a
    release has not created yet raises sqlite3.OperationalError in the reader: that line says "could not read"."""
    p = db_path(state_dir, "aux")
    if not p.exists():
        yield None
        return
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        yield conn
    finally:
        conn.close()


def trade_r(t: ExplorationRecord) -> float | None:
    """Net R: pnl over the loss at the gate's stop (None without either)."""
    if t.pnl is None or not t.stop_loss or t.stop_loss <= 0:
        return None
    return t.pnl / t.stop_loss


def admissible(t: ExplorationRecord) -> bool:
    """Toward an arm's n: filled, closed by its own barrier (not censored), net P&L recorded."""
    return t.fill_price is not None and t.exit_utc is not None and t.pnl is not None and not t.censored


def trades(conn: sqlite3.Connection, where: str = "", params: Sequence[object] = ()) -> list[ExplorationRecord]:
    """`exploration_trades` rows (a trusted `where` over its columns), in exit order then send order."""
    from goldbot.engine.exploration import LEDGER
    rows = LEDGER.select(conn, where, params)
    return sorted(rows, key=lambda t: (t.exit_utc is None, t.exit_utc or t.sent_utc, t.sent_utc, t.client_order_id))


# ---------------------------------------------------------------------------------------------- one lab, one span
class Costs(Record):
    total: float = 0.0
    r: float = 0.0                      # sum over trades of cost_i / stop_loss_i
    spread: float = 0.0
    slippage: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    no_rate: int = 0                    # closes whose USD -> account rate was not read: spread/slippage left out


class LabSpan(Record):
    """One lab's figures over [lo, hi): the spec's field definitions (section 2.5)."""
    signals: int = 0
    taken: int = 0
    refused: dict[str, int] = Field(default_factory=dict)        # owner word -> count, refusals only (policy skips excluded)
    filled: int = 0
    closed: int = 0
    still_open: int = 0                 # of the span's fills, not closed now
    pnl: float = 0.0                    # net over closes with a net figure
    r: float = 0.0
    net_closed: int = 0
    no_pnl: int = 0                     # closes without the broker's net figure (shown apart)
    gross_no_pnl: float | None = None   # their gross from prices, when every one of them has prices and a rate
    costs: Costs = Field(default_factory=Costs)
    by_day: dict[str, float] = Field(default_factory=dict)       # opportunity day -> net R of its closes (weekly)
    arms_contributing: int = 0          # distinct strategy (non-control) arms with a close in the span
    by_day_arms: dict[str, int] = Field(default_factory=dict)    # opportunity day -> the same count for that day

    @property
    def totals_shown(self) -> bool:
        """Quant 2026-10-26: a lab or style total (£, R, the gross of closes without a net figure) is printed only
        when at least MIN_ARMS_FOR_TOTAL strategy arms contributed a close; below that it is an arm's own figure by
        subtraction."""
        return self.arms_contributing >= MIN_ARMS_FOR_TOTAL

    def day_shown(self, day: str) -> bool:
        """A day of the span may be printed (no close, or enough contributing arms)."""
        return day not in self.by_day_arms or self.by_day_arms[day] >= MIN_ARMS_FOR_TOTAL


def refusal_word(code: str | None) -> str:
    first = (code or "").split(",", 1)[0].strip()
    return REFUSAL_WORDS.get(first, OTHER_REASON)


def _costs(rows: Sequence[ExplorationRecord], oz_per_lot: float) -> Costs:
    total = r = spread = slip = comm = swap = 0.0
    no_rate = 0
    for t in rows:
        oz, fx = t.lots * oz_per_lot, t.fx_acct_per_usd
        s = sl = 0.0
        if fx is not None and fx > 0:
            s = max(0.0, t.spread_usd or 0.0) * oz * fx
            if t.requested_price is not None and t.fill_price is not None:
                sl = max(0.0, (t.fill_price - t.requested_price) * t.side) * oz * fx   # adverse only, never a credit
        else:
            no_rate += 1
        c, w = abs(t.commission or 0.0), max(0.0, -(t.swap or 0.0))
        each = s + sl + c + w
        spread, slip, comm, swap, total = spread + s, slip + sl, comm + c, swap + w, total + each
        if t.stop_loss and t.stop_loss > 0:
            r += each / t.stop_loss
    return Costs(total=total, r=r, spread=spread, slippage=slip, commission=comm, swap=swap, no_rate=no_rate)


def _gross(t: ExplorationRecord, oz_per_lot: float) -> float | None:
    if t.fill_price is None or t.exit_price is None or not t.fx_acct_per_usd:
        return None
    return (t.exit_price - t.fill_price) * t.side * t.lots * oz_per_lot * t.fx_acct_per_usd


def lab_span(conn: sqlite3.Connection, account: str, lo: pd.Timestamp, hi: pd.Timestamp, *,
             oz_per_lot: float = CONTRACT_OZ) -> LabSpan:
    """`account`'s exploration over [lo, hi) (UTC): signals by `decision_ns`, fills by send time, closes by exit."""
    from goldbot.ops.report_days import report_day
    a, b = _ns(lo), _ns(hi)
    obs = conn.execute(
        "SELECT taken, blocked_reason FROM exploration_obs WHERE account_id = ? AND decision_ns >= ? AND "
        "decision_ns < ? AND quarantine IS NULL", (account, a, b)).fetchall()
    refused: dict[str, int] = {}
    for taken, why in obs:
        if not taken and why:
            w = refusal_word(why)
            refused[w] = refused.get(w, 0) + 1
    sent = [t for t in trades(conn, "account_id = ? AND ts_ns >= ? AND ts_ns < ?", (account, a, b))
            if t.fill_price is not None]
    closed = [t for t in trades(conn, "account_id = ? AND closed = 1 AND exit_ns >= ? AND exit_ns < ?",
                                (account, a, b)) if t.fill_price is not None]
    net = [t for t in closed if t.pnl is not None]
    apart = [t for t in closed if t.pnl is None]
    grosses = [_gross(t, oz_per_lot) for t in apart]
    controls = {str(x) for (x,) in conn.execute(
        "SELECT DISTINCT arm_id FROM exploration_obs WHERE account_id = ? AND is_control = 1", (account,))}
    by_day: dict[str, float] = {}
    day_arms: dict[str, set[str]] = {}
    for t in closed:
        if t.exit_utc is None:
            continue
        d = report_day(t.exit_utc)
        if t.arm_id not in controls:
            day_arms.setdefault(d, set()).add(t.arm_id)
        if trade_r(t) is not None:
            by_day[d] = by_day.get(d, 0.0) + (trade_r(t) or 0.0)
    return LabSpan(
        signals=len(obs), taken=sum(int(x) for x, _ in obs),
        refused=dict(sorted(refused.items(), key=lambda kv: (-kv[1], kv[0]))),
        filled=len(sent), closed=len(closed), still_open=sum(t.exit_utc is None for t in sent),
        pnl=sum(t.pnl or 0.0 for t in net), r=sum(trade_r(t) or 0.0 for t in net), net_closed=len(net),
        no_pnl=len(apart),
        gross_no_pnl=sum(g for g in grosses if g is not None) if apart and all(g is not None for g in grosses)
        else None,
        costs=_costs(closed, oz_per_lot), by_day=by_day,
        arms_contributing=len({t.arm_id for t in closed if t.arm_id not in controls}),
        by_day_arms={d: len(v) for d, v in day_arms.items()})


def paused_since(conn: sqlite3.Connection, account: str) -> pd.Timestamp | None:
    """When the current run of drawdown-paused candidates began (the first `paused_dd` refusal after the account's
    last candidate that was not paused); None when no candidate has been paused since."""
    last_ok = conn.execute(
        "SELECT MAX(decision_ns) FROM exploration_obs WHERE account_id = ? AND (blocked_reason IS NULL OR "
        "blocked_reason != 'paused_dd')", (account,)).fetchone()[0]
    first = conn.execute(
        "SELECT MIN(decision_ns) FROM exploration_obs WHERE account_id = ? AND blocked_reason = 'paused_dd' AND "
        "decision_ns > ?", (account, int(last_ok) if last_ok is not None else -1)).fetchone()[0]
    return pd.Timestamp(int(first), unit="ns", tz="UTC") if first is not None else None


def labels_written(conn: sqlite3.Connection, accounts: Sequence[str], lo: pd.Timestamp, hi: pd.Timestamp) -> int:
    """Usable labels written in [lo, hi): labelled, not quarantined (ADR 0043 ROI: demo money per admissible label)."""
    if not accounts:
        return 0
    marks = ",".join("?" * len(accounts))
    return int(conn.execute(
        f"SELECT COUNT(*) FROM exploration_obs WHERE account_id IN ({marks}) AND labelled_ns >= ? AND labelled_ns < ? "
        "AND quarantine IS NULL", (*accounts, _ns(lo), _ns(hi))).fetchone()[0])


# ---------------------------------------------------------------------------------------------- arms (blinded)
class ArmStatus(Record):
    """What the owner may see of an arm before its read (spec section 3.4): its admissible n and its band. No share,
    rank or running R is a field here, so none can be printed. The one R is `stop_r`, the running R at which its stop
    fired: quant 2026-10-26 lets the stop notice carry it once, in the report whose window holds `stop_utc`."""
    arm_id: str
    account_id: str
    control: bool
    n: int
    band: Band
    stop_at: int | None = None          # the trade count at which its stop was reached
    stop_utc: pd.Timestamp | None = None    # that trade's exit
    stop_r: float | None = None         # the running net R there (at or past the stop)


CONTROL_RULES = frozenset({"random_bracket"})   # ARM-SPEC: random-entry brackets are the null, never a strategy


def control_arm_ids() -> frozenset[str]:
    """The control arms by their frozen spec (config/exploration_arms.yaml: rule `random_bracket`, twins included):
    S0, S0R, I0, W0, VI0, VW0 today. Controls are the null each arm is measured against (quant 2026-10-26): no
    H-61 stop applies to them and they are never retired."""
    from goldbot.engine.exploration import SPEC_PATH
    from goldbot.research.arms import load_arms
    try:
        return frozenset(a.id for a in load_arms(SPEC_PATH, check_hash=False) if a.rule in CONTROL_RULES)
    except (OSError, ValueError):
        return frozenset()


def _arm_order() -> list[str]:
    """The frozen ARM-SPEC order (config/exploration_arms.yaml): the arms block is never sorted by outcome."""
    from goldbot.engine.exploration import SPEC_PATH
    from goldbot.research.arms import load_arms
    try:
        return [a.id for a in load_arms(SPEC_PATH, check_hash=False)]
    except (OSError, ValueError):
        return []


def arm_statuses(conn: sqlite3.Connection, accounts: Sequence[str], *,
                 until: pd.Timestamp | None = None) -> list[ArmStatus]:
    """Every arm with an admissible trade on `accounts`, in the frozen spec's order (unknown arms after, by id): its
    n toward READ_AT and its H-61 stop band from the running net R in exit order over the stop's first trades.
    `until`: only closes before it (the daily review reads a day point in time)."""
    if not accounts:
        return []
    marks = ",".join("?" * len(accounts))
    meta = {str(a): (str(s), bool(c)) for a, s, c in conn.execute(
        f"SELECT arm_id, MAX(style), MAX(is_control) FROM exploration_obs WHERE account_id IN ({marks}) "
        "GROUP BY arm_id", tuple(accounts))}
    by_arm: dict[str, list[ExplorationRecord]] = {}
    cut, args = ("", tuple(accounts)) if until is None else (" AND exit_ns < ?", (*accounts, _ns(until)))
    for t in trades(conn, f"account_id IN ({marks}) AND closed = 1{cut}", args):
        if admissible(t):
            by_arm.setdefault(t.arm_id, []).append(t)
    order = {a: i for i, a in enumerate(_arm_order())}
    spec_controls = control_arm_ids()
    out = []
    for arm in sorted(by_arm, key=lambda a: (order.get(a, len(order)), a)):
        rows = by_arm[arm]
        style, control = meta.get(arm, (_TF_STYLE.get(rows[0].tf, "scalp"), False))
        control = control or arm in spec_controls
        stop_r, within = ARM_STOP.get(style, ARM_STOP["scalp"])
        band: Band = "running"
        stop_at, stop_utc, at_r = None, None, None
        run = 0.0
        # a control is the null: no stop applies to it (quant 2026-10-26), so it is never "at its stop"
        for i, t in enumerate(rows[:within] if not control else [], 1):
            run += trade_r(t) or 0.0
            if run <= stop_r + 1e-9:
                band, stop_at, stop_utc, at_r = "at_stop", i, t.exit_utc, run
                break
        if not control and band == "running" and len(rows) <= within and run <= BAND_SHARE * stop_r + 1e-9:
            band = "within_50pct"
        out.append(ArmStatus(arm_id=arm, account_id=rows[0].account_id, control=control, n=len(rows), band=band,
                             stop_at=stop_at, stop_utc=stop_utc, stop_r=at_r))
    return out


def arms_lines(arms: Sequence[ArmStatus], width: int, lo: pd.Timestamp, hi: pd.Timestamp) -> list[str]:
    """The arms block: a stop notice (with its R) only in the report whose window [lo, hi) holds the stop, and read-due
    notices, first (never cut); then "Arms (trades): S1A 96 · …" within `width` characters ("+N more" for the rest);
    then the band summary as counts only, never arm ids (quant 2026-10-26)."""
    if not arms:
        return ["Arms: none running yet"]
    notices = [f"{a.arm_id} retired at its pre-set stop after {plural(a.stop_at or a.n, 'trade')}"
               + (f" ({r_text(a.stop_r)})" if a.stop_r is not None else "")
               for a in arms if a.band == "at_stop" and a.stop_utc is not None and lo <= a.stop_utc < hi]
    notices += [f"{a.arm_id} reached {READ_AT} trades: read due" for a in arms
                if a.band != "at_stop" and not a.control and a.n >= READ_AT]
    head, shown = "Arms (trades): ", list[str]()
    for i, a in enumerate(arms):
        rest = len(arms) - i - 1
        cand = head + " · ".join([*shown, f"{a.arm_id} {a.n:,}"]) + (f" · +{rest} more" if rest else "")
        if len(cand) > width and shown:
            break
        shown.append(f"{a.arm_id} {a.n:,}")
    more = len(arms) - len(shown)
    line = head + " · ".join(shown) + (f" · +{more} more" if more else "")
    near = sum(a.band == "within_50pct" for a in arms)
    retired = sum(a.band == "at_stop" for a in arms)
    near_txt = f"{near} within 50% of a stop" if near else "none within 50% of a stop"
    return notices + [line, f"  {near_txt} · {f'{retired} retired' if retired else 'none retired'} · "
                            f"each read once at {READ_AT}"]


# ---------------------------------------------------------------------------------------------- send-time guard
# spec 3.4 banned tokens in arm context (quant 2026-10-26, extended): enforced on the text that is sent, not only in
# tests. A line that names an arm and carries one of these, a percentage or an R figure is dropped and logged
ARM_ID = re.compile(r"\b[A-Z]{1,3}\d{1,2}[A-Z]{0,2}\b")
BANNED_WORDS = re.compile(
    r"\b(shares?|sum|mean|sign|signs|budget|rank|ranked|ranking|best|worst|top|bottom|leading|lagging|ahead|behind|"
    r"baseline|p-value|significant|significance|win rate|t =)\b", re.I)
_FIGURE = re.compile(r"(\d+(\.\d+)?\s?R\b|\d%)")
_STOP_NOTICE = re.compile(r"^[A-Z]{1,3}\d{1,2}[A-Z]{0,2} retired at its pre-set stop after [\d,]+ trades?"
                          r"( \([+\u2212]\d[\d,]*\.\dR\))?$")


def guard(text: str) -> str:
    """The message as sent: every line that names an arm and carries a banned word, a percentage or an R figure (the
    stop notice's own R excepted) is dropped, and the refusal logged with the arm id only. Never raises."""
    out = []
    for line in text.splitlines():
        ids = ARM_ID.findall(line)
        if ids and not _STOP_NOTICE.match(line.strip()):
            hit = BANNED_WORDS.search(line) or _FIGURE.search(line)
            if hit:
                log.error("blinding guard: a line naming arm %s with a banned %s was not sent", ids[0],
                          "word" if BANNED_WORDS.search(line) else "figure")
                continue
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- the learner
class LearnerRuns(Record):
    """The nightly bandit's runs in a span: when, and how many arms' shares moved (a count only: D4)."""
    runs: list[pd.Timestamp] = Field(default_factory=list)
    changed: int = 0                    # arms whose share differs from the run before, summed over the span's runs
    latest: pd.Timestamp | None = None  # the newest run at all (freshness)


def learner_runs(conn: sqlite3.Connection, lo: pd.Timestamp, hi: pd.Timestamp) -> LearnerRuns:
    # the bandit writes per (run, broker, arm) since aux migration 17; the old table only on a release without it
    has_broker = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'exploration_broker_weights'"
                              ).fetchone() is not None
    rows = conn.execute("SELECT run_ns, broker || ':' || arm_id, weight FROM exploration_broker_weights ORDER BY run_ns"
                        if has_broker else "SELECT run_ns, arm_id, weight FROM exploration_weights ORDER BY run_ns"
                        ).fetchall()
    by_run: dict[int, dict[str, float]] = {}
    for run, arm, w in rows:
        by_run.setdefault(int(run), {})[str(arm)] = float(w)
    runs = sorted(by_run)
    a, b = _ns(lo), _ns(hi)
    inside = [r for r in runs if a <= r < b]
    changed = 0
    for r in inside:
        i = runs.index(r)
        if i == 0:
            continue
        prev, cur = by_run[runs[i - 1]], by_run[r]
        changed += sum(1 for arm, w in cur.items() if arm not in prev or not math.isclose(w, prev[arm], abs_tol=1e-9))
    return LearnerRuns(runs=[pd.Timestamp(r, unit="ns", tz="UTC") for r in inside], changed=changed,
                       latest=pd.Timestamp(runs[-1], unit="ns", tz="UTC") if runs else None)


__all__ = ["ARM_STOP", "BANNED_WORDS", "CONTRACT_OZ", "MIN_ARMS_FOR_TOTAL", "LABS", "MINUS", "OTHER", "READ_AT", "ROLE", "ArmStatus", "Costs", "LabSpan",
           "LearnerRuns", "admissible", "arm_statuses", "arms_lines", "aux", "guard", "lab_span", "labels_written",
           "learner_runs", "money", "paused_since", "pct", "plural", "r_text", "refusal_word", "role", "signed",
           "trade_r", "trades", "whole_money"]
