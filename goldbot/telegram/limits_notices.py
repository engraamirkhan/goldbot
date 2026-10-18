"""Telegram FYI when an account's risk limits change (docs/ux/limits-and-tradingview.md "Telegram"): no buttons, owner
chat only, nothing to approve. "Silent unless protection changes": a raise is sent without sound, a tighten or a
safety revert with sound.

The changes come from core.db through goldbot/ops/limits_log.py (ADR 0032 `limits_changes`, ADR 0031
`adaptive_changes`); money figures use the account's engine file (`mode`, `currency`, day- and week-start equity,
`daily_loss_limit_abs`), so a daily figure is the gate's min(cap x day-start equity, absolute limit). Without an engine
file a message gives percentages only, never a guessed amount.

Copy rules: at most 2 lines, units on every number, money and percent together, local time with the zone name, no
account id, login, profile or evidence id. Delivery is at least once: `pending` reads, and the cursor (aux.db
`limits_notice_cursor`) moves only when the service calls `ack` after the messages went out. A first start begins at
the end of both logs, so a fresh install does not replay history."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.db import Database
from goldbot.ops import limits_log
from goldbot.ops.limits_log import LimitChangeEntry

log = logging.getLogger(__name__)

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
_NAMES = {"daily_cap": "daily loss limit", "weekly_cap": "weekly loss limit",
          "min_lot_risk_cap": "minimum-lot exception", "supervisor_daily_cap": "all-accounts daily loss limit",
          "supervisor_weekly_cap": "all-accounts weekly loss limit"}


class LimitNotice(FrozenRecord):
    text: str
    silent: bool


class NoticesAck(FrozenRecord):
    limits_id: int
    adaptive_id: int


def pct(x: float) -> str:
    """0.04 -> "4.0%", 0.0375 -> "3.75%": one decimal at least, never rounding a configured step away."""
    s = f"{100 * x:.2f}".rstrip("0")
    return (s + "0" if s.endswith(".") else s) + "%"


def money(v: float, currency: str | None) -> str:
    sym = SYMBOLS.get((currency or "").upper())
    n = f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"
    return f"{sym}{n}" if sym else f"{n} {currency}"


def local_time(ts: pd.Timestamp, tz: str) -> str:
    t = ts.tz_convert(tz)
    return f"{t.day} {t:%b %H:%M %Z}"


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) and v > 0 else None


class _Account:
    """The engine file's figures a message needs (None when unknown)."""

    def __init__(self, engine: dict[str, Any] | None):
        e = engine or {}
        mode = e.get("mode")
        self.mode = mode if mode in ("demo", "live") else None
        self.currency = e.get("currency") if isinstance(e.get("currency"), str) else None
        self.day0, self.week0 = _num(e.get("day_start_equity")), _num(e.get("week_start_equity"))
        self.absolute = _num(e.get("daily_loss_limit_abs"))

    def amount(self, field: str, frac: float) -> float | None:
        if self.currency is None:
            return None
        if field == "weekly_cap":
            return frac * self.week0 if self.week0 else None
        if field in ("daily_cap", "min_lot_risk_cap") and self.day0:
            v = frac * self.day0
            return min(v, self.absolute) if field == "daily_cap" and self.absolute else v
        return None

    def base(self, field: str) -> str:
        b = self.week0 if field == "weekly_cap" else self.day0
        if b is None or self.currency is None:
            return "of the week's start" if field == "weekly_cap" else "of the day's start"
        return f"of {money(b, self.currency)}"

    def level(self, field: str, frac: float | None) -> str:
        """`frac` with its money: "5.0% (£50)"; "off" for a minimum-lot exception that is off."""
        if frac is None:
            return "off"
        a = self.amount(field, frac)
        return pct(frac) + (f" ({money(a, self.currency)})" if a is not None else "")


def _phrase(e: LimitChangeEntry, acc: _Account) -> str:
    name = _NAMES[e.field]
    if e.field in ("daily_cap", "weekly_cap") and e.old is not None and e.new is not None:
        a, b = acc.amount(e.field, e.old), acc.amount(e.field, e.new)
        if a is not None and b is not None:
            return f"{name} {money(a, acc.currency)} -> {money(b, acc.currency)} ({pct(e.old)} -> {pct(e.new)} " \
                   f"{acc.base(e.field)})"
        return f"{name} {pct(e.old)} -> {pct(e.new)} {acc.base(e.field)}"
    return f"{name} {acc.level(e.field, e.old)} -> {acc.level(e.field, e.new)}"


def _unchanged(group: list[LimitChangeEntry], acc: _Account) -> str:
    after = group[0].after
    if after is None:
        return ""
    moved = {e.field for e in group}
    out = []
    for f, label in (("daily_cap", "Day"), ("weekly_cap", "Week")):
        v = after.get(f)
        if f not in moved and v is not None:
            a = acc.amount(f, v)
            out.append(f" {label} limit unchanged at {money(a, acc.currency) if a is not None else pct(v)}.")
    return "".join(out)


def notice_text(group: list[LimitChangeEntry], acc: _Account, tz: str, revert_cause: str | None = None) -> LimitNotice:
    """One message for the changes of one account at one time (one snapshot row, or one adaptive evaluation)."""
    first = group[0]
    when = local_time(first.ts_utc, tz)
    mode = f" ({acc.mode})" if acc.mode else ""
    if first.source == "revert":
        after = first.after or {}
        cause = f"{revert_cause[:1].lower()}{revert_cause[1:].rstrip('.')}, " if revert_cause else ""
        day, week, mlr = after.get("daily_cap"), after.get("weekly_cap"), after.get("min_lot_risk_cap")
        parts = []
        if day is not None:
            parts.append(f"Day {acc.level('daily_cap', day)}")
        if week is not None:
            parts.append(f"week {acc.level('weekly_cap', week)}")
        lot = "Minimum-lot exception off." if mlr is None else f"Minimum-lot exception {acc.level('min_lot_risk_cap', mlr)}."
        body = f"Safety revert{mode}: {cause}limits back to default. " + (", ".join(parts) + ". " if parts else "") + lot
        return LimitNotice(text=f"{body}\n{when}", silent=False)
    why = first.reason[:1].lower() + first.reason[1:]
    body = f"Limit change{mode}: " + "; ".join(_phrase(e, acc) for e in group) + f". Why: {why}" + _unchanged(group, acc)
    return LimitNotice(text=f"{body}\n{when}", silent=not any(e.tightened for e in group))


class LimitsNotices:
    def __init__(self, state_dir: str | Path, owner_tz: str = "Europe/London"):
        self.dir = Path(state_dir)
        try:                                                  # an unknown zone shows UTC, as Telegram /status does
            ZoneInfo(owner_tz)
            self.tz = owner_tz
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = "UTC"
        self.db = Database.open(self.dir, "aux")

    def _cursor(self) -> NoticesAck | None:
        with self.db.connection() as conn:
            r = conn.execute("SELECT limits_id, adaptive_id FROM limits_notice_cursor WHERE id = 1").fetchone()
        return NoticesAck(limits_id=int(r[0]), adaptive_id=int(r[1])) if r else None

    def ack(self, ack: NoticesAck) -> None:
        """Move the cursor after every message went out (never backwards)."""
        with self.db.write() as conn:
            conn.execute("INSERT INTO limits_notice_cursor (id, limits_id, adaptive_id) VALUES (1, ?, ?) "
                         "ON CONFLICT (id) DO UPDATE SET limits_id = MAX(limits_id, excluded.limits_id), "
                         "adaptive_id = MAX(adaptive_id, excluded.adaptive_id)", (ack.limits_id, ack.adaptive_id))

    def _engine(self, account_id: str) -> dict[str, Any] | None:
        try:
            d = json.loads((self.dir / f"engine_{account_id}.json").read_text())
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def _revert_cause(self, account_id: str) -> str | None:
        try:
            st = limits_log.revert_state(self.dir, account_id)
        except limits_log.LimitsLogError:
            return None
        return limits_log.revert_reason(st.reason) if st is not None and st.reverted and st.reason else None

    def pending(self) -> tuple[list[LimitNotice], NoticesAck | None]:
        """The messages for changes logged since the last ack, and the ack that moves past them (None: nothing to
        ack). A core.db that cannot be read gives nothing this pass (logged); the next pass tries again."""
        cur = self._cursor()
        try:
            upto = limits_log.last_ids(self.dir)
            if cur is None:                                   # first start: no replay
                self.ack(NoticesAck(limits_id=upto[0], adaptive_id=upto[1]))
                return [], None
            entries = limits_log.changes_after(self.dir, (cur.limits_id, cur.adaptive_id), upto)
        except limits_log.LimitsLogError as exc:
            log.warning("limit changes not announced this pass: %s", exc)
            return [], None
        ack = NoticesAck(limits_id=max(upto[0], cur.limits_id), adaptive_id=max(upto[1], cur.adaptive_id))
        groups: dict[tuple[str, str, pd.Timestamp], list[LimitChangeEntry]] = {}
        for e in entries:
            groups.setdefault((e.account_id, e.table, e.ts_utc), []).append(e)
        msgs = []
        for (acct, _, _), group in groups.items():
            cause = self._revert_cause(acct) if group[0].source == "revert" else None
            msgs.append(notice_text(group, _Account(self._engine(acct)), self.tz, cause))
        if not msgs:
            if ack != cur:
                self.ack(ack)                                  # only rows that changed nothing: move on
            return [], None
        return msgs, ack
