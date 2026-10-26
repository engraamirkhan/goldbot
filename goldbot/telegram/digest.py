"""The owner's morning Telegram message: the daily learning report (docs/ux/daily-learning-report.md, lane L5a), sent
once a day at telegram.digest_at (default 06:45 UTC, before London). It answers, on a phone, in ten seconds: "did the
labs run, what did learning cost, is anything near a line, and do I need to do anything?"

Decisions of the spec, as built here:

* D1 "One morning message, not two": this report REPLACES the old digest's body. The old digest read
  closed_trades_*.jsonl only, which by design never holds exploration trades, so it said "Trades closed: none" while
  the labs closed sixty. The labs' figures come from aux.db (goldbot/telegram/labs.py); the engines' own proposals
  and closes still show, on their own lines, when there are any.
* D2 "Weekly digest replaces the Sunday daily" (goldbot/telegram/weekly_digest.py): `report_text` picks by the slot.
* D3 "Arms are blinded until their read": per arm only its trade count and a band; never its R, sum, rank, sign or
  budget share (labs.ArmStatus has no such field). D4 "The learner line says only that it ran".
* D5 no buttons: plain text, no reply_markup. D6 accounts by role label (labs.role), never their slug, login, server
  or host; check names lose their account suffix; halts do not say who set them.
* Budget: at most DAILY_LINES non-empty lines (spec 2.2). Over budget, `fit` drops priority-3 lines, then merges a
  lab's detail lines, then drops priority-2 lines; health, halts, any line at or past a risk line, a stop or read
  notice and "For you" are priority 1 and never cut, and a cut lab says "· more on the dashboard".
* "Day" is the opportunity day before the slot (report_days.day_before, D-2 22:00 to D-1 22:00 UTC), the day the
  dashboard and the monthly report use. Every number carries its unit, every time its zone; £ and R with an explicit
  sign and the true minus; currencies and exploration and engine P&L are never summed together.
* The review lines (`review_section`) are the daily learning-ops review's own blinded lines
  (goldbot/research/daily_review.py `report_lines`, written at 06:15 for the same day): costs per broker, volume
  against the plan, incidents and the count of arms retired at their stop; absent when no review exists for the day.
* The drawdown lines are the engine's own (goldbot/engine/exploration.py DD_WARN, DD_PAUSE: the pause the engine
  applies) and settings `risk.drawdown_stage2` (the halt rail), read at build time, never copied here.

Robust by construction: each section is built on its own; an unreadable source gives that section one "could not
read" line and the message still goes out. Reporting only: nothing here changes an order, a halt or a setting.

`DigestSchedule` decides when to send (no network, so it is unit-tested): once per scheduled slot, recorded in
state/telegram_digest.json only after delivery (a failed send is retried on the next pass), and a slot missed while
the service was down is still sent when it starts, if it is under LATE_LIMIT (6 h) late; later than that it is
skipped (a stale morning report at night is noise).
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, NamedTuple
from zoneinfo import ZoneInfo

import pandas as pd

from goldbot.base import Record, load_record_json, write_atomic
from goldbot.config import ROOT, Settings
from goldbot.execution.not_placed import CAUSE_PHRASE, approval_cause
from goldbot.ops.accounts import engine_state_files
from goldbot.ops.report_days import day_before, day_bounds, month_days, weekdays
from goldbot.telegram import labs
from goldbot.telegram.approvals import window_over

STATE_FILE = "telegram_digest.json"
LATE_LIMIT = pd.Timedelta(hours=6)
MAX_NAMES = 5                       # check names listed per status before "+N more"
DAILY_LINES = 15                    # spec 2.2: "hard rule: <= 15 non-empty logical lines"
ARMS_WIDTH = 72                     # the arms line: two phone rows of about 40 characters, less word-wrap slack
DEFAULT_ROADMAP = ROOT / "docs" / "ROADMAP.md"
_DECISION_ROW = re.compile(r"^\|\s*(?:OD-|D)\d+\s*\|")   # "| D3 |" or, since the 2026-10-16 re-plan, "| OD-3 |"
OWNER_TZ = "Europe/London"          # the header's local time when telegram.owner_tz is not set ("UK")
NO_PROPOSALS = "• Proposals: none"
NO_TRADES = "• Trades closed: none"
# a Saturday or Sunday opportunity day with nothing in it: one line instead of the labs (the Sunday reopen opens
# Monday's day, so its closes are in Tuesday's report)
WEEKEND_LINE = "Market closed Sat–Sun: no trading. Sunday-evening trades show in Tuesday's report."
# spec 2.4: until the rotation ADR names the engine's resume rule, the pause copy reads this
RESUME_RULE = "when the drawdown is back under the clear line"
MORE = " · more on the dashboard"


# ---------------------------------------------------------------------------------------------- schedule and dedupe
class DigestState(Record):
    last_slot: str | None = None     # ISO date (UTC) of the last scheduled slot whose digest was delivered
    sent_utc: str | None = None


class DigestSchedule:
    """When the digest is due: the most recent slot (today's digest_at, or yesterday's if today's is still ahead) not
    yet delivered, and less than LATE_LIMIT ago. One rule covers the running loop (due within a pass of the slot) and a
    start after downtime (due if under 6 h late)."""

    def __init__(self, state_dir: str | Path, digest_at: str = "06:45"):
        self.path = Path(state_dir) / STATE_FILE
        hh, mm = digest_at.split(":")
        self.at = pd.Timedelta(hours=int(hh), minutes=int(mm))
        try:
            self.state = load_record_json(DigestState, self.path.read_text(encoding="utf-8"), source=STATE_FILE) \
                if self.path.exists() else DigestState()
        except (ValueError, OSError):
            self.state = DigestState()      # unreadable: at worst one digest is sent twice, never one missed

    def slot(self, now: pd.Timestamp) -> pd.Timestamp:
        now = _utc(now)
        today = now.normalize() + self.at
        return today if now >= today else today - pd.Timedelta(days=1)

    def due(self, now: pd.Timestamp) -> bool:
        s = self.slot(now)
        return self.state.last_slot != s.date().isoformat() and _utc(now) - s < LATE_LIMIT

    def record(self, now: pd.Timestamp) -> None:
        """Mark the current slot delivered; call only after the send succeeded."""
        self.state = DigestState(last_slot=self.slot(now).date().isoformat(), sent_utc=_utc(now).isoformat())
        write_atomic(self.path, self.state.model_dump_json())


# ---------------------------------------------------------------------------------------------- helpers
def _utc(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _engines(state_dir: Path) -> list[dict[str, Any]]:
    """The enabled accounts' engine state (a disabled account's leftover is not reported). A torn file of an enabled
    account still fails the section ("could not read"): skipping it would report its open positions as none."""
    out = []
    for f in engine_state_files(state_dir):
        try:
            e = _json(f)
        except (ValueError, OSError):
            raise ValueError(f"{f.name} unreadable") from None
        if isinstance(e, dict):
            out.append(e)
    return out


def _section(build: Callable[[], list[str]], what: str) -> list[str]:
    """One section; any error becomes a single line so the rest of the digest still goes out."""
    try:
        return build()
    except Exception as exc:                # noqa: BLE001 - one broken input must never stop the digest
        return [f"• {what}: could not read ({type(exc).__name__})"]


# ---------------------------------------------------------------------------------------------- sections
def proposals_line(state_dir: Path, day0: pd.Timestamp, day1: pd.Timestamp, now: pd.Timestamp) -> list[str]:
    """Proposals created in [day0, day1): outcome from the archive; a pending one past its window counts as expired.
    An approved card with a refusal code is counted by its cause, "(1 refused by the risk check · 1 not placed by the
    MT5 box or the broker · 1 not confirmed by the broker)", naming only the causes that occurred (BACKLOG 83: "count
    the three causes separately"); a not-confirmed card the broker showed opened is an opened trade, in none."""
    root = state_dir / "approvals"
    lo, hi = day0.timestamp(), day1.timestamp()
    counts = {"APPROVED": 0, "REJECTED": 0, "EXPIRED_UNAPPROVED": 0, "open": 0}
    causes = dict.fromkeys(CAUSE_PHRASE, 0)
    bad = 0
    for sub in ("done", "pending"):
        d = root / sub
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            try:
                if f.stat().st_mtime < lo:          # written before the day began: cannot hold a proposal from it
                    continue
                p = _json(f)
                created = float(p["created"])
            except (ValueError, OSError, KeyError, TypeError):
                bad += 1
                continue
            if not lo <= created < hi:
                continue
            outcome = p.get("outcome")
            if outcome in counts:
                counts[outcome] += 1
                if outcome == "APPROVED" and (cause := _approval_cause(p)) in causes:
                    causes[cause] += 1
            elif sub == "pending" and window_over(created, p.get("window_s", 90), now.timestamp()):   # BACKLOG 60
                counts["EXPIRED_UNAPPROVED"] += 1
            else:
                counts["open"] += 1
    n = sum(counts.values())
    named = [f"{k} {CAUSE_PHRASE[c]}" for c, k in causes.items() if k]
    note = f" ({' · '.join(named)})" if named else ""
    tail = f" · {counts['open']} open" if counts["open"] else ""
    tail += f" · {bad} unreadable" if bad else ""
    if n == 0:
        return [NO_PROPOSALS + tail]
    return [f"• Proposals {n}: ✅ {counts['APPROVED']} approved{note} · ❌ {counts['REJECTED']} rejected · "
            f"⌛ {counts['EXPIRED_UNAPPROVED']} expired{tail}"]


def _approval_cause(p: dict[str, Any]) -> str | None:
    """`approval_cause` for an archived card read as JSON; a malformed field reads as absent, as the codes would."""
    codes, nc = p.get("gate_refusal"), p.get("not_confirmed")
    codes = [c for c in codes if isinstance(c, str)] if isinstance(codes, list) else None
    outcome = nc.get("outcome") if isinstance(nc, dict) else None
    return approval_cause(codes, outcome if isinstance(outcome, str) else None)


def trades_line(state_dir: Path, day0: pd.Timestamp, day1: pd.Timestamp) -> list[str]:
    from goldbot.ops.gates_phase import load_closed_trades
    trades, err = load_closed_trades(state_dir)
    day = [t for t in trades if day0 <= t.exit_utc < day1]
    warn = " (⚠️ unreadable record lines skipped)" if err else ""
    if not day:
        return [NO_TRADES + warn]
    # P&L summed per currency, never across (BACKLOG 28): a row's currency is its pnl_currency, or for a legacy row
    # the account's currency from engine_<account>.json; each sum is printed with its own symbol ("+£5.00 · -$10.90")
    from goldbot.ops.monthly_report import money
    try:
        ccy = {str(e.get("account")): str(e.get("currency") or "").strip().upper() for e in _engines(state_dir)}
    except ValueError:
        ccy = {}
    def summed(rows: list[Any]) -> str:
        sums: dict[str, float] = {}
        for t in rows:
            c = (t.pnl_currency or ccy.get(t.account_id, "")).strip().upper()
            sums[c] = sums.get(c, 0.0) + t.pnl
        return " · ".join(money(v, c or None) + ("" if c else " (currency not reported)")
                          for c, v in sorted(sums.items(), key=lambda kv: (kv[0] not in ccy.values(), kv[0] == "", kv[0])))

    # finance audit GAP-05 (as perf_report.py): net figures from net rows only (gates_phase.pnl_basis); the rest are
    # shown apart as "N trades gross only (costs not recorded)", so nothing gross is labelled net
    from goldbot.ops.gates_phase import pnl_basis
    net_rows = [t for t in day if pnl_basis(t) == "net"]
    gross_rows = [t for t in day if pnl_basis(t) != "net"]
    if net_rows:
        rs = [t.r for t in net_rows if t.r is not None]
        r = f" · {sum(rs):+.2f}R" + (f" ({len(rs)} of {len(net_rows)} with R)" if len(rs) < len(net_rows) else "") \
            if rs else " · R n/a"
        wins = sum(t.pnl > 0 for t in net_rows)
        net = f"net {summed(net_rows)}{r} ({wins} won, {len(net_rows) - wins} lost)"
    else:
        net = "net not recorded"
    gross = ""
    if gross_rows:
        grs = [t.r for t in gross_rows if t.r is not None]
        gross = (f" · {_plural(len(gross_rows), 'trade')} gross only (costs not recorded): {summed(gross_rows)}"
                 + (f" · {sum(grs):+.2f}R" if grs else "") + " gross")
    return [f"• Closed {_plural(len(day), 'trade')}: {net}{gross}{warn}"]


def _owner_halt(state_dir: Path) -> str | None:
    f = state_dir / "control.json"
    if not f.exists():
        return None
    c = _json(f)
    if not c.get("halted"):
        return None
    since = pd.Timestamp(float(c.get("ts") or 0), unit="s", tz="UTC")
    return f"owner halt since {since:%a %d %b %H:%M} UTC"


def _drift_halt(state_dir: Path) -> str | None:
    f = state_dir / "drift.json"
    if not f.exists():
        return None
    d = _json(f)
    if d.get("system_halt"):
        return "drift system halt (review pending)"
    if d.get("halted"):
        n = len(d["halted"])
        return f"drift: {n} strateg{'y' if n == 1 else 'ies'} halted"
    return None


def _supervisor_halt(state_dir: Path) -> str | None:
    f = state_dir / "supervisor.json"
    if not f.exists():
        return None
    return "supervisor combined-cap halt" if _json(f).get("halt") else None


def decisions_line(state_dir: Path, roadmap: Path) -> list[str]:
    """👉 For you: pending approvals (real money only ever has any), owner decision rows, a deploy on offer; only the
    ones that are not zero, and "nothing" when all are."""
    parts: list[str] = []
    try:
        from goldbot.telegram.bus import ApprovalBus
        if n := len(ApprovalBus(state_dir).pending()):
            parts.append(f"{_plural(n, 'approval')} pending")
    except Exception:                       # noqa: BLE001
        parts.append("approvals unreadable")
    try:
        if n := owner_decisions(roadmap):
            parts.append(f"{_plural(n, 'owner decision')} open (see OWNER_GUIDE)")
    except FileNotFoundError:
        pass                                # no roadmap here: no queue to count
    except OSError:
        parts.append("owner decisions: see OWNER_GUIDE")
    try:
        pend = state_dir / "deploy" / "pending.json"
        if pend.exists() and _json(pend).get("sha"):
            parts.append("1 deploy on offer")
    except (ValueError, OSError):
        pass
    return ["👉 For you: " + (" · ".join(parts) if parts else "nothing")]



def owner_decisions(roadmap: Path) -> int:
    """Rows of docs/ROADMAP.md's '## 5. Owner decision queue' table (lines starting '| OD-<n> |' or '| D<n> |')."""
    lines = roadmap.read_text(encoding="utf-8").splitlines()
    n, inside = 0, False
    for line in lines:
        if line.startswith("## "):
            inside = "owner decision queue" in line.lower()
            continue
        n += bool(inside and _DECISION_ROW.match(line))
    return n



# ---------------------------------------------------------------------------------------------- shared lines
class Line(NamedTuple):
    """One logical line: its text, its priority (1 never cut, 3 cut first; spec 2.2), the lab it belongs to (None:
    not a lab's) and whether it is one of that lab's detail lines."""
    text: str
    prio: int = 1
    lab: str | None = None
    detail: bool = False


def fit(lines: list[Line], budget: int) -> list[str]:
    """The spec's cut order, only as far as the budget needs: priority-3 lines (from the bottom up), then each lab's
    detail lines merged into one, then priority-2 lines (from the bottom up). A lab that lost a line says "· more on
    the dashboard" on its result line. Priority-1 lines are never cut."""
    cut: set[str] = set()
    rows = list(lines)

    def over() -> bool:
        return sum(1 for x in rows if x.text.strip()) > budget

    def shed(prio: int) -> None:
        for i in range(len(rows) - 1, -1, -1):
            if not over():
                return
            if rows[i].prio == prio:
                if (lab := rows[i].lab) is not None:
                    cut.add(lab)
                del rows[i]

    shed(3)
    if over():
        merged: list[Line] = []
        for x in rows:
            prev = merged[-1] if merged else None
            if x.detail and x.prio > 1 and prev is not None and prev.detail and prev.prio > 1 and prev.lab == x.lab:
                merged[-1] = Line(f"{prev.text} · {x.text.strip()}", min(prev.prio, x.prio), x.lab, True)
            else:
                merged.append(x)
        rows = merged
    shed(2)
    return [x.text + (MORE if x.lab in cut and not x.detail and MORE not in x.text else "") for x in rows]


def _owner_zone(settings: Settings | None) -> tuple[ZoneInfo | None, str]:
    tz = (settings.telegram.owner_tz if settings is not None else "") or OWNER_TZ
    try:
        return ZoneInfo(tz), tz
    except (KeyError, ValueError, OSError):     # ZoneInfoNotFoundError is a KeyError; a malformed key a ValueError
        return None, tz


def local_end(end: pd.Timestamp, settings: Settings | None) -> str:
    """" (23:00 UK)": a time in the owner's zone (telegram.owner_tz, else Europe/London, labelled UK; another zone by
    its abbreviation); empty when the zone cannot be resolved (the UTC time stays)."""
    zone, tz = _owner_zone(settings)
    if zone is None:
        return ""
    local = end.tz_convert(zone)
    return f" ({local:%H:%M} {'UK' if tz == OWNER_TZ else local.tzname()})"


def day_name(day: str | pd.Timestamp) -> str:
    """"Mon 26 Oct" (no leading zero)."""
    d = pd.Timestamp(day)
    return f"{d:%a} {d.day} {d:%b}"


def health_part(report: Any) -> str:
    """✅ every check ok, ⚠️ failing then warning checks named (account suffixes removed), or ❔ unknown."""
    if report is None:
        return "❔ Health: unknown (the check did not run)"

    def names(status: str) -> list[str]:
        seen: list[str] = []
        for c in report.checks:
            n = c.name.split(":", 1)[0]
            if c.status == status and n not in seen:
                seen.append(n)
        return seen

    def listed(ns: list[str]) -> str:
        more = f" +{len(ns) - MAX_NAMES} more" if len(ns) > MAX_NAMES else ""
        return ", ".join(ns[:MAX_NAMES]) + more

    fails, warns = names("fail"), names("warn")
    if not fails and not warns:
        n = len(report.checks)
        return f"✅ Health {n} of {n} ok"
    parts = ([f"fail {listed(fails)}"] if fails else []) + ([f"warn {listed(warns)}"] if warns else [])
    return "⚠️ Attention: " + " · ".join(parts)


def halts_part(state_dir: Path, engines: list[dict[str, Any]], settings: Settings | None) -> str | None:
    """Every halt in force (an engine at the halt rail by its lab's name, the owner's, drift's, the supervisor's), or
    None. An unreadable halt file is a halt (the services fail closed on it)."""
    rail = f"{labs.pct(settings.risk.drawdown_stage2)} drawdown" if settings is not None else "the halt rail"
    rails = [f"{labs.role(str(e.get('account')))} at {rail}, no new trades" for e in engines
             if str(e.get("stage")) == "halted"]
    others: list[str] = []
    for name, read in (("owner", _owner_halt), ("drift", _drift_halt), ("supervisor", _supervisor_halt)):
        try:
            h = read(state_dir)
        except Exception:                   # noqa: BLE001 - unreadable halt files fail closed in the services
            h = f"{name} state unreadable (entries blocked)"
        if h:
            others.append(h)
    if not rails and not others:
        return None
    return ("🛑 Halted: " if not others else "🛑 Halts: ") + " · ".join(others + rails) + " · exits still run"


def entries_part(state_dir: Path, now: pd.Timestamp, settings: Settings | None) -> str:
    from goldbot.api import entry_status as es
    rows = es.statuses_from_state(state_dir, now, settings)
    if not rows:
        return "Entries: no engine reporting"
    return f"Entries open on {sum(s.state == 'open' for _, _, s in rows)} of {len(rows)}"


def overview_line(state_dir: Path, now: pd.Timestamp, report: Any, settings: Settings | None,
                  engines: list[dict[str, Any]], *, health_words: Callable[[Any], str] = health_part,
                  entries: bool = True) -> str:
    """Line 2: health · halts · entries (the weekly: no entries, they are a now-figure). A halt leads the line (it
    must reach the lock-screen preview) and the health part then drops its emoji (one per line)."""
    h = health_words(report)
    try:
        halts = halts_part(state_dir, engines, settings)
    except Exception as exc:                # noqa: BLE001
        halts = f"🛑 Halts: could not read ({type(exc).__name__})"
    tail = ""
    if entries:
        try:
            tail = " · " + entries_part(state_dir, now, settings)
        except Exception as exc:            # noqa: BLE001
            tail = f" · Entries: could not read ({type(exc).__name__})"
    if halts is None:
        return f"{h} · Halts: none{tail}"
    return f"{halts} · {h.split(' ', 1)[1]}{tail}"


def lab_engines(engines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The enabled exploration hosts (probe_only), in the labs' report order."""
    order = {a: i for i, a in enumerate(labs.LABS)}
    hosts = [e for e in engines if e.get("probe_only")]
    return sorted(hosts, key=lambda e: (order.get(str(e.get("account")), len(order)), str(e.get("account"))))


class Drawdown(NamedTuple):
    dd: float | None                    # 1 - equity / closed-balance mark (the RiskGate's arithmetic); None unknown
    hwm: float
    equity: float


def drawdown(e: dict[str, Any]) -> Drawdown:
    try:
        hwm, eq = float(e.get("balance_closed_hwm") or 0), float(e.get("equity") or 0)
    except (TypeError, ValueError):
        return Drawdown(None, 0.0, 0.0)
    if not (hwm > 0 and eq > 0):
        return Drawdown(None, hwm, eq)
    return Drawdown(max(0.0, 1 - eq / hwm), hwm, eq)


def lines_at() -> tuple[float, float]:
    """The exploration warn and pause lines the engine applies now (read at call time, never copied)."""
    from goldbot.engine import exploration
    return exploration.DD_WARN, exploration.DD_PAUSE


def down_text(d: Drawdown, ccy: str | None) -> str:
    return f"down {labs.pct(d.dd or 0.0)} ({labs.whole_money(d.hwm - d.equity, ccy)})"


def at_text(line: float, d: Drawdown, ccy: str | None) -> str:
    return f"{labs.pct(line)} ({labs.whole_money(d.hwm * line, ccy)})"


def oz_per_lot(e: dict[str, Any]) -> float:
    terms = e.get("lot_terms") or {}
    try:
        v = float(terms.get("contract_oz") or 0) if isinstance(terms, dict) else 0.0
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else labs.CONTRACT_OZ


def result_text(name: str, s: labs.LabSpan, ccy: str | None, *, withheld: str | None = None) -> str:
    """"Scalp lab: 214 signals · 63 taken · 61 filled · 58 closed −£17.60 · −3.9R" and its states (spec 2.4). The £,
    R and gross figures print only when `s.totals_shown` (quant 2026-10-26, MIN_ARMS_FOR_TOTAL strategy arms closed
    in the window) and no `withheld` reason applies (the weekly: a day of the week was withheld); else "—"."""
    head = f"{name}: {labs.plural(s.signals, 'signal')} · {s.taken:,} taken"
    if not (s.taken or s.filled or s.closed):
        return head
    head += f" · {s.filled:,} filled · {s.closed:,} closed"
    if s.closed and (withheld or not s.totals_shown):
        head += f" — ({withheld or f'under {labs.MIN_ARMS_FOR_TOTAL} arms'})"
        return head + (f" · {s.no_pnl:,} closed without costs recorded" if s.no_pnl else "")
    if s.net_closed:
        head += f" {labs.money(s.pnl, ccy)} · {labs.r_text(s.r)}"
    elif s.still_open and not s.closed:
        head += f" ({s.still_open:,} still open)"
    if s.no_pnl:
        gross = f" (gross {labs.money(s.gross_no_pnl, ccy)})" if s.gross_no_pnl is not None else ""
        head += f" · {s.no_pnl:,} closed without costs recorded{gross}"
    return head


def open_hours(e: dict[str, Any], lo: pd.Timestamp, hi: pd.Timestamp) -> int:
    """Hours the market was open in [lo, hi) by the engine's session table (its published cache, else the static
    table), rounded: the "no signals in N h of open market" figure; 0 is a closed day (a holiday or the weekend)."""
    from goldbot.data.calendar import DEFAULT_SESSIONS, session_table_from_cache
    table = session_table_from_cache(e.get("sessions")) or DEFAULT_SESSIONS
    minutes = pd.date_range(lo, hi, freq="1min", inclusive="left")
    return int(round(float(table.is_open(minutes).sum()) / 60))


def halted_since(state_dir: Path, account: str) -> pd.Timestamp | None:
    """When the RiskGate halted the account (risk_<account>.json `halted_at`); None when not recorded."""
    from goldbot.engine.store import risk_file
    try:
        at = _json(risk_file(state_dir, account)).get("halted_at")
        return _utc(pd.Timestamp(str(at))) if at else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def since_text(ts: pd.Timestamp | None) -> str:
    return f" since {day_name(ts)} {ts:%H:%M} UTC" if ts is not None else ""


def costs_text(c: labs.Costs, ccy: str | None, *, breakdown: bool) -> str:
    total = f"costs {labs.money(c.total, ccy, sign=False)} ({c.r:.1f}R)"
    note = f" · rate not read on {labs.plural(c.no_rate, 'trade')}" if c.no_rate else ""
    if not breakdown:
        return total + note
    parts = [f"spread {labs.money(c.spread, ccy, sign=False)}", f"slippage (entry) {labs.money(c.slippage, ccy, sign=False)}",
             f"commission {labs.money(c.commission, ccy, sign=False)}"]
    if c.swap > 0.005:
        parts.append(f"swap {labs.money(c.swap, ccy, sign=False)}")
    return f"{total}: " + " · ".join(parts) + note


def refused_text(s: labs.LabSpan) -> str | None:
    top = list(s.refused.items())[:3]
    return "refused: " + " · ".join(f"{n:,} {w}" for w, n in top) if top else None


class LabBlock(NamedTuple):
    lines: list[Line]
    span: labs.LabSpan | None           # None: paused, halted or unreadable (not an empty day)


def lab_block(state_dir: Path, conn: sqlite3.Connection | None, e: dict[str, Any], lo: pd.Timestamp,
              hi: pd.Timestamp, settings: Settings | None, *, full: bool, weekday: bool,
              by_day: list[str] | None = None) -> LabBlock:
    """One lab's lines: halted (the rail), paused (the engine's pause), else its result and details. `full`: the
    costs breakdown, refusals and drawdown each on their own line (the first lab); else one merged detail line.
    `by_day` (the weekly): the opportunity days whose net R is listed under the result; no refusals."""
    acct = str(e.get("account"))
    name, ccy = labs.role(acct), (str(e.get("currency") or "") or None)
    d = drawdown(e)
    warn, pause = lines_at()
    if str(e.get("stage")) == "halted":
        return LabBlock([Line(f"🛑 {name} halted{since_text(halted_since(state_dir, acct))}: {down_text(d, ccy)} · "
                              "re-arm on the dashboard (Risk)", 1, acct)],
                        None)
    if d.dd is None or d.dd >= pause - 1e-12:   # the engine's own rule (exploration.paused): unknown pauses too
        # the engine records no pause time: its first paused candidate is the record (exploration_obs `paused_dd`)
        when = since_text(labs.paused_since(conn, acct) if conn is not None else None)
        what = f"{down_text(d, ccy)} from peak" if d.dd is not None else "drawdown unknown"
        return LabBlock([Line(f"⏸ {name} paused{when}: {what}", 1, acct),
                         Line(f"  arms stopped · cost probe and exits still running · resumes: {RESUME_RULE}", 1, acct,
                              True)], None)
    s = labs.lab_span(conn, acct, lo, hi, oz_per_lot=oz_per_lot(e)) if conn is not None else labs.LabSpan()
    hours = open_hours(e, lo, hi) if s.signals == 0 and s.closed == 0 and weekday else None
    withheld = "a day under 3 arms" if by_day and not all(s.day_shown(x) for x in by_day) else None
    if hours == 0:
        out = [Line(f"{name}: market closed · nothing to report", 1, acct)]
    elif hours is not None:
        out = [Line(f"{name}: no signals in {hours} h of open market ⚠️ Warning: check the feed", 1, acct)]
    else:
        out = [Line(result_text(name, s, ccy, withheld=withheld), 1, acct)]

    def day_r(x: str) -> str:
        if x not in s.by_day_arms:
            return "none"
        return labs.r_text(s.by_day.get(x, 0.0)) if s.day_shown(x) else "—"
    if by_day:
        out.append(Line("  by day: " + " · ".join(f"{pd.Timestamp(x):%a} {day_r(x)}" for x in by_day), 2, acct, True))
    warned = d.dd >= warn - 1e-12
    if warned:
        out.append(Line(f"  ⚠️ Warning: {down_text(d, ccy)} from peak · pauses at {at_text(pause, d, ccy)}", 1, acct,
                        True))
    costs = costs_text(s.costs, ccy, breakdown=full) if s.closed else None
    refused = refused_text(s)
    if full:
        if costs:
            out.append(Line(f"  {costs}", 2, acct, True))
        if refused:
            out.append(Line(f"  {refused}", 3, acct, True))
        if not warned:
            out.append(Line(f"  {down_text(d, ccy)} from peak · warns at {at_text(warn, d, ccy)} · pauses at "
                            f"{at_text(pause, d, ccy)}", 3, acct, True))
    else:
        # the weekly (`by_day`) leaves refusals to the dailies and says the drawdown is the one now
        now_dd = None if warned else f"now {down_text(d, ccy)} from peak" if by_day else down_text(d, ccy)
        parts = [p for p in (costs, None if by_day else refused, now_dd) if p]
        if parts:
            out.append(Line("  " + " · ".join(parts), 2, acct, True))
    return LabBlock(out, s)


def labs_section(state_dir: Path, engines: list[dict[str, Any]], lo: pd.Timestamp, hi: pd.Timestamp,
                 settings: Settings | None, *, weekday: bool, by_day: list[str] | None = None
                 ) -> tuple[list[Line], list[labs.LabSpan], bool]:
    """Every enabled lab's block (the daily's first in full), the spans read, and whether aux.db could be read at all (a
    broken file is one line, and the arms and learner lines are then left out rather than repeating it)."""
    hosts = lab_engines(engines)
    if not hosts:
        return [], [], True
    with labs.aux(state_dir) as conn:
        if conn is not None:
            try:
                conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
            except sqlite3.Error as exc:
                return [Line(f"Labs: could not read ({type(exc).__name__})", 1)], [], False
        out: list[Line] = []
        spans: list[labs.LabSpan] = []
        for i, e in enumerate(hosts):
            try:
                b = lab_block(state_dir, conn, e, lo, hi, settings, full=i == 0 and by_day is None, weekday=weekday,
                              by_day=by_day)
            except Exception as exc:        # noqa: BLE001 - one lab's ledger never stops the report
                name = labs.role(str(e.get("account")))
                b = LabBlock([Line(f"{name}: could not read the trade ledger ({type(exc).__name__})", 1)], None)
            out += b.lines
            if b.span is not None:
                spans.append(b.span)
        return out, spans, True


def arms_section(state_dir: Path, engines: list[dict[str, Any]], width: int, lo: pd.Timestamp, hi: pd.Timestamp
                 ) -> list[Line]:
    """The arms block (labs.arms_lines): notices are priority 1, the counts and band summary priority 2."""
    accounts = [str(e.get("account")) for e in lab_engines(engines)]
    if not accounts:
        return []
    try:
        with labs.aux(state_dir) as conn:
            arms = labs.arm_statuses(conn, accounts) if conn is not None else []
    except Exception as exc:                # noqa: BLE001
        return [Line(f"Arms: could not read ({type(exc).__name__})", 2)]
    rows = labs.arms_lines(arms, width, lo, hi)
    return [Line(x, 1 if "pre-set stop after" in x or "read due" in x else 2) for x in rows]


def review_section(state_dir: Path, day: str) -> list[Line]:
    """The daily review's lines for `day`: its incidents and retirements line is priority 1, the costs and volume
    line priority 2; nothing when the review has not run for the day."""
    from goldbot.research.daily_review import load_review, report_lines
    r = load_review(state_dir, day)
    if r is None:
        return []
    return [Line(x, 2 if i == 0 else 1) for i, x in enumerate(report_lines(r))]


def learner_line(state_dir: Path, lo: pd.Timestamp, hi: pd.Timestamp, now: pd.Timestamp) -> str:
    """D4: whether the nightly learner ran in the day and whether any share changed; never which, how or by how much."""
    from goldbot.research.exploration_data import MAX_WEIGHTS_AGE
    with labs.aux(state_dir) as conn:
        lr = labs.learner_runs(conn, lo, hi) if conn is not None else labs.LearnerRuns()
    if lr.runs:
        return f"Learner {lr.runs[-1]:%H:%M} UTC: ran · " + ("shares updated" if lr.changed else "no share changed")
    stale = lr.latest is None or lr.latest + MAX_WEIGHTS_AGE < now
    return "⚠️ Learner did not run last night: " + ("equal shares in use" if stale else "the last run's shares in use")


def lab_money(state_dir: Path, engines: list[dict[str, Any]], lo: pd.Timestamp, hi: pd.Timestamp
              ) -> dict[str, tuple[float, float, int]]:
    """Net P&L, R and closes of the labs' trades closed in [lo, hi), per currency (never summed across)."""
    out: dict[str, tuple[float, float, int]] = {}
    with labs.aux(state_dir) as conn:
        if conn is None:
            return out
        for e in lab_engines(engines):
            ccy = str(e.get("currency") or "")
            for t in labs.trades(conn, "account_id = ? AND closed = 1 AND exit_ns >= ? AND exit_ns < ?",
                                 (str(e.get("account")), lo.value, hi.value)):
                if t.pnl is None or t.fill_price is None:
                    continue
                p, r, n = out.get(ccy, (0.0, 0.0, 0))
                out[ccy] = (p + t.pnl, r + (labs.trade_r(t) or 0.0), n + 1)
    return out


def open_part(engines: list[dict[str, Any]]) -> str:
    n = sum(int(e.get("open_positions") or 0) for e in engines)
    lots = sum(float(e.get("open_lots") or 0.0) for e in engines)
    return f"open now {labs.plural(n, 'trade')}" + (f" ({lots:.2f} lot)" if n else "")


def month_lines(state_dir: Path, engines: list[dict[str, Any]], day: str, as_of: pd.Timestamp, *, live: bool
                ) -> list[str]:
    """"Month: 18 trading days so far · open now 4 trades (0.04 lot)". No running lab total (quant 2026-10-26: the
    difference of two days' month figures is a day's total, withheld or not). A real-money account's own month
    (monthly_report.month_to_date_line) follows on its own line, never summed with anything."""
    from goldbot.ops.monthly_report import month_to_date_line
    first, _ = month_days(day[:7])
    out = [f"Month: {labs.plural(weekdays(first, day), 'trading day')} so far · {open_part(engines)}"]
    if live:
        out += [x.removeprefix("• ") for x in month_to_date_line(state_dir, as_of, day=day)]
    return out


def _text(build: Callable[[], str], what: str) -> str:
    return _section(lambda: [build()], what)[0].removeprefix("• ")


def _engine_rows(state_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return _engines(state_dir), None
    except ValueError:                      # its message names the file, so the account: never printed
        return [], "Engine state: could not read (a torn engine file)"


# ---------------------------------------------------------------------------------------------- the message
def build_digest(state_dir: str | Path, now: pd.Timestamp, *, as_of: pd.Timestamp | None = None, report: Any = None,
                 settings: Settings | None = None, roadmap: Path = DEFAULT_ROADMAP) -> str:
    """The daily learning report for `now` (UTC). `report` is a goldbot.ops.health.HealthReport (None: unknown). The day
    is the opportunity day before the one holding `as_of` (report_days.day_before; default `now`); the bot passes its
    scheduled slot, so a send up to 6 h late neither drops a day nor repeats one."""
    sd = Path(state_dir)
    now = _utc(now)
    as_of = now if as_of is None else _utc(as_of)
    day = day_before(as_of)
    lo, hi = day_bounds(day)
    weekday = pd.Timestamp(day).dayofweek < 5
    engines, torn = _engine_rows(sd)
    out = [Line(f"☀️ {day_name(day)} results · day ended {hi:%H:%M} UTC{local_end(hi, settings)}"),
           Line(_text(lambda: overview_line(sd, now, report, settings, engines), "Health"))]
    if torn:
        out.append(Line(torn))
    # D1: an engine's own proposals and closes come back only for a real-money account (the decision the owner makes);
    # on the demo hosts the labs are the report. Which mode, from the engine files
    live = torn is not None or any(str(e.get("mode")) == "live" for e in engines)   # unreadable: shown, not hidden
    other = [x for x in _section(lambda: proposals_line(sd, lo, hi, now), "Proposals")
             + _section(lambda: trades_line(sd, lo, hi), "Trades closed")
             if not x.startswith((NO_PROPOSALS, NO_TRADES))] if live else []
    out += [Line(x) for x in other]
    try:
        lab_lines, spans, aux_ok = labs_section(sd, engines, lo, hi, settings, weekday=weekday)
    except Exception as exc:                # noqa: BLE001
        lab_lines, spans, aux_ok = [Line(f"Labs: could not read ({type(exc).__name__})")], [], False
    quiet = all(s.signals == 0 and s.closed == 0 for s in spans) and len(spans) == len(lab_engines(engines))
    if not weekday and quiet and not other:
        lab_lines = [Line(WEEKEND_LINE)]
    out += lab_lines
    if aux_ok:
        out += arms_section(sd, engines, ARMS_WIDTH, lo, hi)
        if lab_engines(engines):
            out.append(Line(_text(lambda: learner_line(sd, lo, hi, now), "Learner"), 2))
    try:
        out += review_section(sd, day)
    except Exception as exc:                # noqa: BLE001
        out.append(Line(f"Review: could not read ({type(exc).__name__})", 2))
    out += [Line(x, 2) for x in _section(lambda: month_lines(sd, engines, day, as_of, live=live), "Month")]
    out.append(Line(_section(lambda: decisions_line(sd, roadmap), "For you")[0]))
    return labs.guard("\n".join(fit(out, DAILY_LINES)))


def report_text(state_dir: str | Path, now: pd.Timestamp, slot: pd.Timestamp, *, report: Any = None,
                settings: Settings | None = None, roadmap: Path = DEFAULT_ROADMAP,
                registry_path: Path | None = None) -> str:
    """The morning message for scheduled `slot`: the weekly digest on a Sunday slot (D2: it replaces Sunday's daily,
    which would only say the market was closed), else the daily report of the day before the slot."""
    if _utc(slot).dayofweek == 6:
        from goldbot.telegram.weekly_digest import build_weekly
        return build_weekly(state_dir, now, as_of=slot, report=report, settings=settings, roadmap=roadmap,
                            registry_path=registry_path)
    return build_digest(state_dir, now, as_of=slot, report=report, settings=settings, roadmap=roadmap)


def digest_from_runtime(state_dir: str | Path, now: pd.Timestamp,
                        as_of: pd.Timestamp | None = None) -> str:  # pragma: no cover - VPS wiring
    """The morning message on the VPS: health checks as the alert loop runs them, settings re-read from disk; `as_of`
    is the scheduled slot (DigestSchedule.slot)."""
    report, settings = None, None
    try:
        from goldbot.ops.health import HealthContext, run_checks
        ctx = HealthContext.from_runtime(state_dir, now=_utc(now))
        settings = ctx.settings
        report = run_checks(ctx)
    except Exception:                       # noqa: BLE001 - the report still goes out, health shown as unknown
        pass
    registry = None
    if settings is not None:
        registry = Path(settings.research.registry)
        registry = registry if registry.is_absolute() else ROOT / registry
    return report_text(state_dir, now, _utc(now) if as_of is None else as_of, report=report, settings=settings,
                       registry_path=registry)
