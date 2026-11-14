"""The monthly review (R142, BACKLOG 223-226; architect plan 2026-11-13, review loop item 3): what the month did, whether
the money ties out, what it cost and how reliable the system was, for the finance-auditor, the CFO and the CTO.

    python -m goldbot.ops.monthly_review [--state DIR] [--month YYYY-MM]      (prints the stored review)

The scheduler's `monthly_review` job (goldbot/ops/jobs.py) ticks daily at 06:30 UTC and reviews the calendar month
before its slot in UTC (`review_month`): a slot that arrives in another zone is converted first, so the month never
depends on the host's clock. The 1st builds it; a later tick builds it when the 1st was missed (up to
`monthly_review.catch_up_days` after the month ends) or when the stored row is a failed build (every tick until it
succeeds: the scheduler marks a slot done before its job runs, so the retry lives here, not in the scheduler). A done
month is a no-op. Per review:

* Per account, the month's report (goldbot/ops/monthly_report.py, reused as is: its days are opportunity days, D-1
  22:00 to D 22:00 UTC, net rows only; `MonthlyReport.day_boundary` says so).
* The tie-out of goldbot's net P&L against the broker balances in aux.db (finance audit RC-9: "Opening balance + net
  closed P&L + deposits - withdrawals = closing balance"), over the UTC calendar month:
      opening   the last balance snapshot (ops/ledger.py, daily broker_ledger job) before 00:00 UTC on the 1st, else
                the month's first snapshot (a note says so);
      closing   the last snapshot before 00:00 UTC on the next 1st;
      explained every closed record (ops/gates_phase.py) and every exploration, control and cost-probe close (the
                trade ledger over aux.db, ops/learning_ledger.py, matched by `account_name`; rows voided as evidence
                included: "account P&L and the broker reconciliation keep every trade") whose exit is after the opening
                and at or before the closing read, plus the deposits, withdrawals and broker adjustments booked between
                them (balance_deals; credit is not balance). Every demo trade is a learning close, never a record
                (issue #394: without them the tie-out saw no trade on a demo account);
      difference = closing - opening - explained.
  |difference| above `monthly_review.balance_tolerance` + `balance_tolerance_per_trade` x trades is UNRECONCILED: it
  FLAGS the review and never passes. So does a month with no snapshot to tie out (`no_data`), a daily ledger report
  that failed in the month, a login or currency change between the two reads, a baseline deal inside the window, a
  record in another currency than the account's, a learning close without a recorded P&L, a trade ledger that cannot
  be read ("missing, not zero"), or a snapshot without a login fingerprint (`identity`): before 2026-10-26 two
  accounts could share one terminal login, and without the fingerprint a balance moved by the other account's trades
  cannot be ruled out. Gross-only records (costs not recorded) are summed as recorded, so
  their missing costs show up as a difference rather than being hidden.
* Agent and infra spend: aux.db agent_spend for the month (USD) and the known hosting costs
  (`monthly_review.hosting_gbp_month`, from docs/finance), in GBP at `monthly_review.gbp_per_usd`.
* The trial budget used (K): the trial registry's count for the quarter holding the month, quarter to date, against
  its budget (research.quarter_budget). Read only: the budget is never changed here.
* A reliability scorecard: scheduler_jobs (jobs failing now, jobs whose last due slot is past its max_late_hours,
  failures recorded), health_alerts in the month (Telegram health messages, dead man's switch changes) and the market
  minutes down: from each dead man's switch `fail` to its next `ok`, counting only the minutes gold trades (the
  weekend close, Friday 22:00 to Sunday 23:00 UTC, left out; the daily break is not). scheduler_jobs keeps each job's
  current state, not a history, so failures are cumulative and failing/stale are as of the review.

Outputs: one row in aux.db `monthly_reviews` (migration 31), one Telegram message to the owner (a notice row in
state/agent_runs.jsonl, relayed by the outbox, as the limits job does), and one GitHub issue labelled `review-monthly`
(the keyring's github-token). Each goes out once per month, and the row records which did (`posted`): every daily tick
sends again whatever has not gone out for ANY stored month, so a GitHub outage or a missing token on the 1st is posted
by a later tick (meanwhile the text is in state/aux/monthly_review/outbox/<month>.md). A build that raises (a busy
aux.db, a bug) stores a `flagged` row with the error, sends nothing and is rebuilt next tick. The health check
`monthly_review` (`review_problems`) warns on a failed build and on a review not fully sent a day after it was built.
The program-director clears open `review-monthly` issues first each session (docs/process/review-cadence.md).

What it NEVER does: name an MT5 login or a host (the account's config name with any long digit run cut, as
telegram.entry_text.account_name, and every text scrubbed as research/daily_review.scrub), or put a balance or an
amount of the owner's money in the public issue (returns, R, counts, verdicts and the difference as a share of the
closing balance only; the amounts are in aux.db and the owner's Telegram). It reads only: never a broker, an order, a
halt, a limit, the trial budget or a setting.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json, write_atomic
from goldbot.config import MonthlyReviewSettings, Settings
from goldbot.data.timeutil import epoch_ns
from goldbot.db.bookkeeping import load_jobs
from goldbot.db.connection import Database
from goldbot.db.repo import RecordRepo
from goldbot.ops import ledger
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trades
from goldbot.ops.learning_ledger import LearningClose, closed_learning_trades
from goldbot.ops.limits_job import queue_notice
from goldbot.ops.monthly_report import AccountMonth, build_monthly_report, money, read_learning
from goldbot.ops.report_days import DAY_BOUNDARY, month_days
from goldbot.ops.scheduler import JobState, schedule_of
from goldbot.research.daily_review import scrub
from goldbot.telegram.entry_text import account_name

log = logging.getLogger("goldbot.monthly_review")

NOTICE_ROLE = "monthly_review"
TieStatus = Literal["reconciled", "unreconciled", "no_data"]


class IssueOpener(Protocol):
    """Where the review's issue goes: `GitHubIssues` on the brain, a recorder in tests."""

    def open_issue(self, label: str, title: str, body: str) -> str: ...


# ---------------------------------------------------------------------------------------------- the record
class MonthTieOut(FrozenRecord):
    """RC-9 over the UTC calendar month, in the account's currency (module docstring)."""
    status: TieStatus
    currency: str | None = None
    from_utc: UtcTimestamp | None = None           # the opening snapshot's read
    to_utc: UtcTimestamp | None = None             # the closing snapshot's read
    opening: float | None = None
    closing: float | None = None
    trade_pnl: float = 0.0                         # closed records and learning closes in the window, as recorded
    trades: int = 0
    learning_trades: int = 0                       # ... of which exploration, control and cost-probe closes
    deposits: float = 0.0
    withdrawals: float = 0.0                       # broker sign (negative)
    adjustments: float = 0.0
    difference: float | None = None                # closing - opening - everything explained
    tolerance: float | None = None
    ledger_days: int = 0                           # daily ledger reports in the month
    ledger_fail_days: int = 0                      # ... of which FAILed (RC-9 or RC-8)
    reasons: list[str] = []                        # why it is not reconciled (no amounts: they go to the issue)
    notes: list[str] = []


class AccountReview(FrozenRecord):
    account_id: str
    report: AccountMonth | None = None             # None: no closed trade in the month
    tie_out: MonthTieOut


class Spend(FrozenRecord):
    agent_usd: float
    agent_gbp: float
    hosting_gbp: dict[str, float]
    total_gbp: float
    gbp_per_usd: float


class TrialBudget(FrozenRecord):
    quarter: str
    budget: int | None = None
    used: int | None = None                        # registry trials stamped in the quarter, to the review's day
    error: str | None = None


class Reliability(FrozenRecord):
    jobs: int
    failing: list[str] = []                        # last run failed (as of the review)
    stale: list[str] = []                          # a due slot older than its max_late_hours has not run
    failures_total: int = 0                        # cumulative failures of the jobs registered (no per-month history)
    telegram_alerts: int = 0                       # health messages sent to the owner in the month
    deadman_changes: int = 0                       # dead man's switch state changes in the month
    market_minutes_down: int = 0                   # dead man's switch fail -> ok, gold's open minutes only


class MonthlyReview(FrozenRecord):
    month: str                                     # YYYY-MM, a UTC calendar month
    as_of_utc: UtcTimestamp
    day_boundary: str = DAY_BOUNDARY               # the report section's days (the tie-out runs on the UTC month)
    accounts: list[AccountReview] = []
    # None only on a failed build's row (`error`), which is never sent and is rebuilt on the next daily tick
    spend: Spend | None = None
    trials: TrialBudget | None = None
    reliability: Reliability | None = None
    flags: list[str] = []                          # each one keeps the review from passing
    status: Literal["ok", "flagged"]
    errors: list[str] = []
    error: str | None = None                       # the build raised: flagged, nothing sent, rebuilt later
    posted: dict[str, str] = {}                    # issue: posted | outbox; telegram: queued | failed

    def sent(self) -> bool:
        """Both outputs went out (a failed build has nothing to send and is rebuilt instead)."""
        return self.posted.get("issue") == "posted" and self.posted.get("telegram") == "queued"


REVIEWS: RecordRepo[MonthlyReview] = RecordRepo(
    "monthly_reviews", MonthlyReview, key="month", tolerant=True,
    columns={"month": lambda r: r.month, "created_ns": lambda r: _ns(r.as_of_utc), "status": lambda r: r.status})


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


# ---------------------------------------------------------------------------------------------- the month
def _utc(ts: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def review_month(slot: pd.Timestamp) -> str:
    """The calendar month before the slot's month, both in UTC ("2026-11" for a slot on 1 December)."""
    first = _utc(slot).normalize().replace(day=1)
    return f"{first - pd.Timedelta(days=1):%Y-%m}"


def month_window(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[00:00 UTC on the 1st, 00:00 UTC on the next 1st)."""
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    return start, start + pd.offsets.MonthBegin(1)


# ---------------------------------------------------------------------------------------------- tie-out
def tie_out(db: Database, account_id: str, trades: list[ClosedTrade], month: str,
            cfg: MonthlyReviewSettings, learning: list[LearningClose] | None = None,
            learning_errors: list[str] | None = None) -> MonthTieOut:
    """RC-9 for one account over the UTC month (module docstring). Never passes without two snapshots to compare.
    `learning`: every account's learning closes (matched here by `account_name`); `learning_errors`: why some may be
    missing, each a reason (never read as zero)."""
    start, end = month_window(month)
    snaps = ledger.snapshots(db, account_id)
    with db.read() as conn:
        reports = ledger.REPORTS.select(conn, "account_id = ? AND ts_ns >= ? AND ts_ns < ?",
                                        [account_id, _ns(start), _ns(end)], order_by="ts_ns")
    fails = sum(r.status == "fail" for r in reports)
    reasons = [f"{fails} daily ledger tie-out{'s' if fails != 1 else ''} failed in the month"] if fails else []
    reasons += learning_errors or []
    notes: list[str] = []
    before = [s for s in snaps if s.ts_utc < start]
    inside = [s for s in snaps if start <= s.ts_utc < end]
    opening = before[-1] if before else (inside[0] if inside else None)
    if opening is not None and not before:
        notes.append(f"opening balance from the month's first snapshot ({opening.ts_utc:%Y-%m-%d})")
    closing = inside[-1] if inside and opening is not None and inside[-1].ts_utc > opening.ts_utc else None
    if opening is None or closing is None:
        return MonthTieOut(status="no_data", ledger_days=len(reports), ledger_fail_days=fails, notes=notes,
                           reasons=["no broker balance snapshot to tie out at both ends of the month", *reasons])
    ccy = closing.currency
    if opening.currency != closing.currency:
        reasons.append("the account currency changed in the month")
    if opening.identity and closing.identity and opening.identity != closing.identity:
        reasons.append("the terminal's login changed in the month (a new baseline)")
    if any(s.identity is None for s in [opening, *inside] if s.ts_utc <= closing.ts_utc):
        reasons.append("a balance snapshot has no login fingerprint: a shared login cannot be ruled out")
    window = [t for t in trades if t.account_id == account_id and opening.ts_utc < t.exit_utc <= closing.ts_utc]
    name = account_name(account_id)
    learn = [c for c in learning or [] if c.account == name and opening.ts_utc < c.exit_utc <= closing.ts_utc]

    def same(c: str | None) -> bool:
        return c is None or c.upper() == ccy.upper()

    money_rows = [t for t in window if same(t.pnl_currency)]
    learn_money = [c.pnl for c in learn if same(c.currency) and c.pnl is not None]
    if len(money_rows) < len(window):
        n = len(window) - len(money_rows)
        reasons.append(f"{n} closed record{'s' if n != 1 else ''} in another currency")
    if other := sum(not same(c.currency) for c in learn):
        reasons.append(f"{other} exploration or probe trade{'s' if other != 1 else ''} in another currency")
    if no_pnl := sum(same(c.currency) and c.pnl is None for c in learn):
        reasons.append(f"{no_pnl} exploration or probe trade{'s' if no_pnl != 1 else ''} without a recorded P&L")
    deals = [d for d in ledger.balance_deals(db, account_id)
             if opening.ts_utc < d.ts_utc <= closing.ts_utc and d.kind != "credit"]
    if any(d.baseline for d in deals):
        reasons.append("a balance deal inside the month was booked as a new baseline")
    if any(d.follows_cancel for d in deals):
        notes.append("a balance operation follows a cancelled deal (ADR 0015)")
    deps = sum(d.amount for d in deals if d.kind == "deposit")
    wds = sum(d.amount for d in deals if d.kind == "withdrawal")
    adj = sum(d.amount for d in deals if d.kind == "adjustment")
    pnl = sum(t.pnl for t in money_rows) + sum(learn_money)
    n_trades = len(window) + len(learn)
    diff = closing.balance - (opening.balance + pnl + deps + wds + adj)
    tol = cfg.balance_tolerance + cfg.balance_tolerance_per_trade * n_trades
    if abs(diff) > tol + 1e-9:
        reasons.insert(0, "the balance moved by more than goldbot's net P&L and the balance deals explain")
    return MonthTieOut(status="unreconciled" if reasons else "reconciled", currency=ccy, from_utc=opening.ts_utc,
                       to_utc=closing.ts_utc, opening=opening.balance, closing=closing.balance, trade_pnl=round(pnl, 2),
                       trades=n_trades, learning_trades=len(learn), deposits=round(deps, 2), withdrawals=round(wds, 2),
                       adjustments=round(adj, 2), difference=round(diff, 2), tolerance=round(tol, 2),
                       ledger_days=len(reports), ledger_fail_days=fails, reasons=reasons, notes=notes)


# ---------------------------------------------------------------------------------------------- spend, K, reliability
def spend(db: Database, month: str, cfg: MonthlyReviewSettings) -> Spend:
    with db.read() as conn:
        row = conn.execute("SELECT usd FROM agent_spend WHERE month = ?", (month,)).fetchone()
    usd = float(row[0]) if row else 0.0
    hosting = dict(cfg.hosting_gbp_month)
    gbp = usd * cfg.gbp_per_usd
    return Spend(agent_usd=round(usd, 2), agent_gbp=round(gbp, 2), hosting_gbp=hosting,
                 total_gbp=round(gbp + sum(hosting.values()), 2), gbp_per_usd=cfg.gbp_per_usd)


def trial_budget(settings: Settings, trials: Any, month: str) -> TrialBudget:
    from goldbot.research.director import quarter_budget
    from goldbot.research.registry import quarter_of
    q = quarter_of(month_window(month)[0].to_pydatetime())
    try:
        return TrialBudget(quarter=q, budget=quarter_budget(settings.research, q), used=int(trials.budget_used(q)))
    except Exception as exc:              # noqa: BLE001 - an unreadable registry is a flag, never a stopped review
        return TrialBudget(quarter=q, error=f"trial registry unreadable ({type(exc).__name__})")


def market_minutes(a: pd.Timestamp, b: pd.Timestamp) -> int:
    """Minutes in [a, b) while gold trades: the weekend close, Friday 22:00 to Sunday 23:00 UTC, left out."""
    if b <= a:
        return 0
    idx = pd.date_range(a.ceil("min"), b, freq="min", inclusive="left")
    closed = ((idx.dayofweek == 4) & (idx.hour >= 22)) | (idx.dayofweek == 5) | ((idx.dayofweek == 6) & (idx.hour < 23))
    return int((~closed).sum())


def reliability(db: Database, settings: Settings, month: str, now: pd.Timestamp) -> Reliability:
    start, end = month_window(month)
    with db.read() as conn:
        jobs = {n: load_record_json(JobState, b, source="aux.db:scheduler_jobs") for n, b in load_jobs(conn).items()}
        rows = conn.execute("SELECT ts_ns, channel, kinds FROM health_alerts WHERE ts_ns < ? ORDER BY ts_ns, id",
                            (_ns(end),)).fetchall()
    stale = []
    for name, st in jobs.items():
        cfg = getattr(settings.scheduler, name, None)
        if cfg is None:                                  # retired: the scheduler prunes it at its next start
            continue
        sch = schedule_of(cfg)
        due = sch.last_slot(now - pd.Timedelta(hours=sch.max_late_hours))
        if due is not None and (st.last_slot is None or st.last_slot < due):
            stale.append(name)
    down_from: pd.Timestamp | None = None
    minutes = deadman = telegram = 0
    cut = min(end, now) if now > start else end
    for ts_ns, channel, kinds in rows:
        ts = pd.Timestamp(int(ts_ns), tz="UTC")
        if ts >= start:
            deadman += channel == "deadman"
            telegram += channel == "telegram"
        if channel != "deadman":
            continue
        if "fail" in str(kinds).split(","):
            down_from = ts if down_from is None else down_from
        elif down_from is not None:
            minutes += market_minutes(max(down_from, start), min(ts, cut))
            down_from = None
    if down_from is not None:                            # still down at the month's end (or at the review)
        minutes += market_minutes(max(down_from, start), cut)
    return Reliability(jobs=len(jobs), failing=sorted(n for n, s in jobs.items() if s.last_ok is False),
                       stale=sorted(stale), failures_total=sum(s.failures for s in jobs.values()),
                       telegram_alerts=telegram, deadman_changes=deadman, market_minutes_down=minutes)


# ---------------------------------------------------------------------------------------------- build
def build_monthly_review(*, settings: Settings, state_dir: str | Path, month: str, now: pd.Timestamp,
                         account_ids: list[str], trials: Any) -> MonthlyReview:
    sd = Path(state_dir)
    cfg = settings.monthly_review
    db = Database.open(sd, "aux")
    now = _utc(now)
    trades, err = load_closed_trades(sd)
    # read once, for the report and the tie-out alike (issue #394: every demo trade is a learning close)
    learn, learn_errors = read_learning(sd, now, trades, closed_learning_trades)
    rep = build_monthly_report(sd, month=month, now=now, day=month_days(month)[1], learning=(learn, learn_errors))
    by_acc = {a.account_id: a for a in rep.accounts}
    errors = list(rep.errors) + ([err] if err and err not in rep.errors else [])
    rows = learn.rows if learn is not None else []
    accounts = [AccountReview(account_id=a, report=by_acc.get(a),
                              tie_out=tie_out(db, a, trades, month, cfg, rows, learn_errors))
                for a in sorted(set(account_ids) | set(by_acc))]
    budget = trial_budget(settings, trials, month)
    rel = reliability(db, settings, month, now)
    flags = [f"{account_name(a.account_id)}: balance {a.tie_out.status.replace('_', ' ')} ({'; '.join(a.tie_out.reasons)})"
             for a in accounts if a.tie_out.status != "reconciled"]
    if rel.failing:
        flags.append(f"{len(rel.failing)} scheduler job{'s' if len(rel.failing) != 1 else ''} failing: "
                     + ", ".join(rel.failing))
    if rel.stale:
        flags.append(f"{len(rel.stale)} scheduler job{'s' if len(rel.stale) != 1 else ''} overdue: "
                     + ", ".join(rel.stale))
    if budget.error:
        flags.append(budget.error)
    elif budget.used is not None and budget.budget is not None and budget.used > budget.budget:
        flags.append(f"trial budget exceeded in {budget.quarter}")
    if errors:
        flags.append(f"{len(errors)} trade record error{'s' if len(errors) != 1 else ''}")
    return MonthlyReview(month=month, as_of_utc=now, accounts=accounts, spend=spend(db, month, cfg), trials=budget,
                         reliability=rel, flags=flags, status="flagged" if flags else "ok", errors=errors)


# ---------------------------------------------------------------------------------------------- texts
def _name(month: str) -> str:
    return pd.Timestamp(f"{month}-01").strftime("%B %Y")


def _common(r: MonthlyReview) -> list[str]:
    s, t, rel = r.spend, r.trials, r.reliability
    if s is None or t is None or rel is None:
        return [f"Build failed: {r.error or 'unknown error'}"]
    host = " + ".join(f"{k} £{v:,.2f}" for k, v in s.hosting_gbp.items() if v) or "hosting £0.00"
    k = (f"{t.used} of {t.budget} used in {t.quarter} (quarter to date)" if t.error is None
         else f"{t.quarter}: {t.error}")
    return [f"Spend: agents ${s.agent_usd:,.2f} (£{s.agent_gbp:,.2f}) + {host} = £{s.total_gbp:,.2f}",
            f"Trial budget (K): {k}",
            f"Reliability: {len(rel.failing)} job(s) failing{' (' + ', '.join(rel.failing) + ')' if rel.failing else ''}, "
            f"{len(rel.stale)} overdue{' (' + ', '.join(rel.stale) + ')' if rel.stale else ''}, "
            f"{rel.failures_total} failures recorded over {rel.jobs} jobs; {rel.telegram_alerts} health alerts, "
            f"{rel.deadman_changes} dead man's switch changes, {rel.market_minutes_down} market minutes down"]


def _perf(a: AccountMonth | None) -> str:
    if a is None:
        return "no closed trades"
    r = f" · {a.net_r:+.2f}R" if a.net_r is not None else ""
    return f"{a.twr_pct:+.2f}% time-weighted · {a.net_trades} net trades of {a.trades}{r}"


def _verdict(t: MonthTieOut) -> str:
    return {"reconciled": "reconciled", "unreconciled": "UNRECONCILED", "no_data": "UNRECONCILED (no data)"}[t.status]


def format_message(r: MonthlyReview) -> str:
    """The owner's Telegram message: amounts in the account currency, no login or host."""
    lines = [f"goldbot monthly review · {_name(r.month)} (UTC month) · {'FLAGGED' if r.flags else 'OK'}"]
    for a in r.accounts:
        t, ccy = a.tie_out, a.tie_out.currency or (a.report.currency if a.report else None)
        net = f"net {money(a.report.pnl, ccy)} · " if a.report else ""
        lines.append(f"{account_name(a.account_id)}: {net}{_perf(a.report)}")
        if t.opening is not None and t.closing is not None and t.difference is not None:
            lines.append(f"  Balance {money(t.opening, ccy, sign=False)} -> {money(t.closing, ccy, sign=False)}: "
                         f"{_verdict(t)} (difference {money(t.difference, ccy)}, tolerance "
                         f"{money(t.tolerance or 0.0, ccy, sign=False)})")
        else:
            lines.append(f"  Balance: {_verdict(t)}")
        if t.reasons:
            lines.append("  Why: " + "; ".join(t.reasons))
    lines += _common(r)
    lines += [f"Flag: {f}" for f in r.flags]
    return scrub("\n".join(lines))


def format_issue(r: MonthlyReview, label: str) -> tuple[str, str]:
    """(title, body) for the public issue: returns, R, counts, verdicts; never a balance, a login or a host."""
    title = f"Monthly review {r.month}: {'FLAGGED' if r.flags else 'ok'}"
    lines = [f"Monthly review of {_name(r.month)} (UTC calendar month; the performance figures use opportunity days, "
             f"{r.day_boundary}). Generated {r.as_of_utc:%Y-%m-%d %H:%M} UTC by the `monthly_review` job.", "",
             "Reviewers (docs/process/review-cadence.md): finance-auditor, CFO, CTO. Close this issue once every flag "
             "has an owner.", "", "| Account | Month | Balance tie-out |", "|---|---|---|"]
    for a in r.accounts:
        t = a.tie_out
        share = (f", difference {100 * t.difference / t.closing:+.2f}% of the closing balance"
                 if t.difference is not None and t.closing else "")
        why = f": {'; '.join(t.reasons)}" if t.reasons else ""
        lines.append(f"| {account_name(a.account_id)} | {_perf(a.report)} | {_verdict(t)}{share}{why} |")
    lines += ["", *[f"- {x}" for x in _common(r)], ""]
    lines += ([f"**Flags ({len(r.flags)})**", *[f"- {f}" for f in r.flags]] if r.flags else ["No flags."])
    lines += ["", f"Amounts: aux.db `monthly_reviews` on the brain (`python -m goldbot.ops.monthly_review --month "
                  f"{r.month}`). Label: `{label}`."]
    return scrub(title), scrub("\n".join(lines))


# ---------------------------------------------------------------------------------------------- run
def outbox_path(state_dir: str | Path, month: str) -> Path:
    return Path(state_dir) / "aux" / "monthly_review" / "outbox" / f"{month}.md"


def load_review(state_dir: str | Path, month: str) -> MonthlyReview | None:
    with Database.open(state_dir, "aux").read() as conn:
        return REVIEWS.get(conn, month)


def _store(db: Database, r: MonthlyReview) -> None:
    with db.write() as conn:
        REVIEWS.upsert(conn, r)


def _post_issue(state_dir: Path, r: MonthlyReview, sink: IssueOpener | None, label: str) -> str:
    title, body = format_issue(r, label)
    box = outbox_path(state_dir, r.month)
    if sink is not None:
        try:
            sink.open_issue(label, title, body)
            box.unlink(missing_ok=True)                  # an earlier failed attempt's copy
            return "posted"
        except Exception as exc:          # noqa: BLE001 - a failed post never stops the review; the text stays in state
            log.warning("monthly review: the issue post failed (%s); the text is in %s", type(exc).__name__, box)
    else:
        log.warning("monthly review: no github-token in the keyring; the issue text is in %s", box)
    write_atomic(box, f"# {title}\n\n{body}\n")
    return "outbox"


def _send(db: Database, sd: Path, r: MonthlyReview, sink: IssueOpener | None, label: str) -> MonthlyReview:
    """Queue the Telegram message and open the issue, each only if it has not gone out yet; the row records both."""
    posted = dict(r.posted)
    if posted.get("telegram") != "queued":
        posted["telegram"] = "queued" if queue_notice(sd, format_message(r), role=NOTICE_ROLE) else "failed"
    if posted.get("issue") != "posted":
        posted["issue"] = _post_issue(sd, r, sink, label)
    r = r.model_copy(update={"posted": posted})
    _store(db, r)
    return r


def run_monthly_review(*, settings: Settings, state_dir: str | Path, slot: pd.Timestamp, account_ids: list[str],
                       trials: Any, sink: IssueOpener | None, now: pd.Timestamp | None = None) -> MonthlyReview | None:
    """One daily tick (UTC). The month before `slot` is built when it has no row and `slot` is within
    `monthly_review.catch_up_days` of its end, or when its stored row is a failed build (rebuilt on every tick until it
    succeeds). A build that raises stores a `flagged` row carrying the error and sends nothing. Then every stored review
    whose Telegram message or issue has not gone out (a GitHub outage, no token) is sent again: each output goes out
    once. Returns the month's row, or None when it was neither stored nor due (past the window, never built)."""
    sd = Path(state_dir)
    cfg = settings.monthly_review
    month = review_month(slot)
    now = pd.Timestamp.now("UTC") if now is None else _utc(now)
    db = Database.open(sd, "aux")
    review = load_review(sd, month)
    due = review is None and _utc(slot) < month_window(month)[1] + pd.Timedelta(days=cfg.catch_up_days)
    if due or (review is not None and review.error is not None):
        try:
            review = build_monthly_review(settings=settings, state_dir=sd, month=month, now=now,
                                          account_ids=account_ids, trials=trials)
        except Exception as exc:          # noqa: BLE001 - the month is kept as a flagged row and rebuilt next tick
            log.exception("monthly review: building %s failed", month)
            review = MonthlyReview(month=month, as_of_utc=now, status="flagged",
                                   error=scrub(f"{type(exc).__name__}: {exc}")[:500] or type(exc).__name__,
                                   flags=["the review could not be built; the next daily run rebuilds it"])
        _store(db, review)                                # stored before anything goes out
    with db.read() as conn:
        pending = [r for r in REVIEWS.select(conn) if r.error is None and not r.sent()]
    for r in pending:
        sent = _send(db, sd, r, sink, cfg.issue_label)
        if sent.month == month:
            review = sent
    return review


def review_problems(state_dir: str | Path, now: pd.Timestamp, *, grace_h: float = 24.0) -> list[str] | None:
    """For health: every stored review whose build failed, and every one not fully sent `grace_h` after it was built.
    Read-only; None when aux.db holds no review yet (or no table: a schema before the migration)."""
    import sqlite3

    from goldbot.db.connection import db_path
    if not db_path(state_dir, "aux").exists():
        return None
    conn = sqlite3.connect(f"file:{db_path(state_dir, 'aux')}?mode=ro", uri=True, timeout=5)
    try:
        rows = [load_record_json(MonthlyReview, b, source="db:monthly_reviews")
                for (b,) in conn.execute("SELECT body FROM monthly_reviews ORDER BY month")]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    finally:
        conn.close()
    if not rows:
        return None
    out = []
    for r in rows:
        age_h = (_utc(now) - r.as_of_utc).total_seconds() / 3600
        if r.error is not None:
            out.append(f"monthly review {r.month} failed to build ({r.error[:120]}); the next daily run rebuilds it")
        elif not r.sent() and age_h > grace_h:
            out.append(f"monthly review {r.month} not posted after {age_h:.0f} h (issue: "
                       f"{r.posted.get('issue', 'not tried')}, telegram: {r.posted.get('telegram', 'not tried')})")
    return out


def summary(r: MonthlyReview | None) -> dict[str, Any]:
    """The scheduler's job result: verdicts and counts only."""
    if r is None:
        return {"skipped": "no review due (the month is past monthly_review.catch_up_days and was never built)"}
    return {"month": r.month, "status": r.status, "flags": len(r.flags), "error": r.error,
            "accounts": {account_name(a.account_id): a.tie_out.status for a in r.accounts}, "posted": dict(r.posted)}


def issue_sink(settings: Settings, get_secret: Any = None) -> IssueOpener | None:
    """GitHub issues with the keyring's github-token (repo: daily_review.repo); None without one (outbox)."""
    from goldbot.ops.github_issues import GitHubIssues
    if get_secret is None:
        from goldbot.ops.accounts import get_secret
    token = get_secret("github-token")
    return GitHubIssues(token, settings.daily_review.repo) if token else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot.ops.monthly_review", description="Print a stored monthly review.")
    ap.add_argument("--state", default="state")
    ap.add_argument("--month", help="YYYY-MM (UTC); default the month before this one")
    a = ap.parse_args(argv)
    month = a.month or review_month(pd.Timestamp.now("UTC"))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        print(f"--month must be YYYY-MM, got {month!r}", file=sys.stderr)
        return 2
    r = load_review(a.state, month)
    if r is None:
        print(f"no monthly review stored for {month}", file=sys.stderr)
        return 1
    print(format_message(r))
    return 0


__all__ = ["AccountReview", "MonthTieOut", "MonthlyReview", "Reliability", "Spend", "TrialBudget",
           "build_monthly_review", "format_issue", "format_message", "issue_sink", "load_review", "market_minutes",
           "month_window", "outbox_path", "reliability", "review_month", "run_monthly_review", "spend", "summary",
           "tie_out", "trial_budget"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
