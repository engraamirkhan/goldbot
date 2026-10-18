"""The risk-limit change log in the owner's words: one row per limit that moved, read from core.db, for the dashboard's
Limits card (`GET /api/limits`, `/api/limits/changes`) and the Telegram FYI (goldbot/telegram/limits_notices.py).

Read-only. Two sources, both append-only (docs/ux/limits-and-tradingview.md "Source note"):

* ADR 0032 `limits_changes` (id, account_id, ts_ns, body = `LimitsChange`): the engine appends a SNAPSHOT of the
  account's current limits whenever they differ from the last it applied, and once at every start. A row is turned
  into one `LimitChangeEntry` per field (daily_cap, weekly_cap, min_lot_risk_cap) that differs from the account's
  previous row; the first row of an account and a start that logs the same limits again are not changes;
* ADR 0031 `adaptive_changes` (id, account_id, day, field, old, new, reasons, evidence_ids, ts_ns): already one row per
  field, with reason codes. Once an account has rows here, its snapshot rows that the adaptive rule made are left out,
  so a change is never listed twice.

Values are fractions of equity (None = the minimum-lot exception is off). Reasons are plain sentences; evidence ids,
logins and servers are never carried. A core.db that cannot be read raises `LimitsLogError` (the callers report it),
never a guess. The day rolls at 00:00 UTC and the week at Sunday 00:00 UTC, as the RiskGate rolls them
(`goldbot.risk.gate.new_day`)."""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Literal

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.db.connection import connect, db_path
from goldbot.risk.limits_profile import LimitsChange, LimitsState

LimitField = Literal["daily_cap", "weekly_cap", "min_lot_risk_cap", "supervisor_daily_cap", "supervisor_weekly_cap"]
Source = Literal["static", "adaptive", "revert", "owner", "default", "fault"]
Table = Literal["limits_changes", "adaptive_changes"]
SNAPSHOT_FIELDS: tuple[LimitField, ...] = ("daily_cap", "weekly_cap", "min_lot_risk_cap")
_ADAPTIVE_FIELDS = frozenset({"daily_cap", "weekly_cap", "min_lot_risk_cap", "supervisor_daily_cap",
                              "supervisor_weekly_cap"})

#: ADR 0031 reason codes (goldbot/risk/adaptive.py `step`) in words; the first code an entry carries decides
ADAPTIVE_REASONS: dict[str, str] = {
    "evidence_invalid": "the trade evidence could not be checked, so the limits went to the floors (fails safe)",
    "dd_floor": "the account fell 4% or more from its peak, so the limits went to the floors",
    "loss_streak_floor": "four losing trades in a row, so the limits are at the floors today and tomorrow",
    "floors_until": "the pause after four losing trades in a row still holds",
    "cost_floor": "trading costs ran more than 25% above the model, so the limits went to the floors",
    "cost_step": "trading costs ran 10% to 25% above the model, so the limits went one step down",
    "stage_floor": "the account is in a drawdown stage, so the limits are at the floors until it clears",
    "minlot_explore_stop": "the minimum-lot trades lost 10R in total, so the minimum-lot exception is off for good",
    "atr_minlot_off": "gold is in its most volatile tenth of the year, so the minimum-lot exception is paused",
    "target_below": "the account's own results no longer support this level, so it was lowered",
    "bounds": "the value was brought back inside the approved floor and ceiling",
    "minlot_explore": "the demo's fixed budget for testing minimum-lot trades (the first 60 trades) applies",
    "loosen": "three days of results above the next level and 10 new demo trades, so it went one step up",
}
_STATIC = "the owner's approved demo ceilings apply (ADR 0032)"
_OWNER = "the owner re-armed the account, so the approved demo ceilings apply again (ADR 0032)"
_REVERT = "the account reached a drawdown stage, so the limits went back to the defaults until the owner re-arms"
_DEFAULT = "the default limits apply"
_FAULT = "the limits controller failed, so the default limits apply (fails safe)"
_ADAPTIVE = "the adaptive limits rule moved it (ADR 0031)"
#: limits_state.reason (goldbot/engine/runner.py `_limits_after_stage`, LimitsStore.load) in words
REVERT_REASONS: dict[str, str] = {
    "stage size_down": "the account fell 8% from its peak (size-down stage)",
    "stage halted": "the account fell 12% from its peak (halt)",
    "combined size-down": "all accounts together reached the size-down stage",
    "limits state unreadable": "the revert state could not be read, so the defaults apply (fails safe)",
}


class LimitsLogError(RuntimeError):
    """core.db exists but its limit logs cannot be read."""


class LimitChangeEntry(FrozenRecord):
    """One limit of one account that moved: from `old` to `new` (fractions of equity; None = off)."""

    account_id: str
    ts_utc: UtcTimestamp
    field: LimitField
    old: float | None
    new: float | None
    profile: str | None = None            # the profile after the change (snapshot rows only)
    # snapshot rows only: every limit after the change (daily_cap, weekly_cap, min_lot_risk_cap), so a notice can say
    # what did not move
    after: dict[str, float | None] | None = None
    source: Source
    reason: str                           # one plain sentence, ending with a full stop
    adr: str | None = None
    table: Table
    row_id: int

    @property
    def tightened(self) -> bool:
        """The protection got stronger: a cap went down, or the minimum-lot exception was lowered or switched off."""
        if self.old is None:
            return False
        return self.new is None or self.new < self.old - 1e-12


def _sentence(s: str) -> str:
    return s[:1].upper() + s[1:] + "."


def current_source(reason: str) -> tuple[Source, str | None]:
    """The source of an account's current limits from `RiskLimitsCurrent.reason` (goldbot/risk/limits_profile.py:
    "static: ...", "reverted", "default", "controller_error:<type>"; anything else is the adaptive controller's)."""
    if reason.startswith("static"):
        return "static", "0032"
    if reason == "reverted":
        return "revert", "0032"
    if reason == "default":
        return "default", None
    if reason.startswith("controller_error"):
        return "fault", None
    return "adaptive", "0031"


def _snapshot_reason(reason: str, prev_reason: str | None) -> tuple[Source, str, str | None]:
    source, adr = current_source(reason)
    if source == "static" and prev_reason == "reverted":
        return "owner", _OWNER, "0032"
    text = {"static": _STATIC, "revert": _REVERT, "default": _DEFAULT, "fault": _FAULT}.get(source, _ADAPTIVE)
    return source, text, adr


def adaptive_reason(codes: list[str]) -> str:
    plain = [c for c in codes if ":" not in c]
    for code in ADAPTIVE_REASONS:
        if code in plain:
            return ADAPTIVE_REASONS[code]
    return _ADAPTIVE


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _open(state_dir: Path) -> sqlite3.Connection | None:
    path = db_path(state_dir, "core")
    if not path.exists():
        return None
    try:
        return connect(path, "core")
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        raise LimitsLogError(f"core.db unreadable ({type(exc).__name__})") from exc


def _entries(conn: sqlite3.Connection, account_id: str | None) -> list[LimitChangeEntry]:
    where, args = ("WHERE account_id = ?", (account_id,)) if account_id else ("", ())
    out: list[LimitChangeEntry] = []
    adaptive_accounts: set[str] = set()
    if _has_table(conn, "adaptive_changes"):
        for rid, acct, field, old, new, reasons, ts_ns in conn.execute(
                f"SELECT id, account_id, field, old, new, reasons, ts_ns FROM adaptive_changes {where} ORDER BY id",
                args):
            if field not in _ADAPTIVE_FIELDS:
                continue
            adaptive_accounts.add(str(acct))
            codes = json.loads(reasons) if reasons else []
            out.append(LimitChangeEntry(
                account_id=str(acct), ts_utc=pd.Timestamp(int(ts_ns), unit="ns", tz="UTC"), field=field, old=old,
                new=new, source="adaptive", reason=_sentence(adaptive_reason([str(c) for c in codes])), adr="0031",
                table="adaptive_changes", row_id=int(rid)))
    if _has_table(conn, "limits_changes"):
        last: dict[str, LimitsChange] = {}
        for rid, body in conn.execute(f"SELECT id, body FROM limits_changes {where} ORDER BY id", args):
            ch = load_record_json(LimitsChange, body, source="db:limits_changes")
            prev = last.get(ch.account_id)
            last[ch.account_id] = ch
            if prev is None:
                continue
            source, text, adr = _snapshot_reason(ch.current.reason, prev.current.reason)
            if source == "adaptive" and ch.account_id in adaptive_accounts:
                continue                       # adaptive_changes has the same change, per field, with its reasons
            for f in SNAPSHOT_FIELDS:
                old, new = getattr(prev.current, f), getattr(ch.current, f)
                if not _same(old, new):
                    out.append(LimitChangeEntry(
                        account_id=ch.account_id, ts_utc=ch.ts_utc, field=f, old=old, new=new,
                        profile=ch.current.profile, source=source, reason=_sentence(text), adr=adr,
                        table="limits_changes", row_id=int(rid),
                        after={k: getattr(ch.current, k) for k in SNAPSHOT_FIELDS}))
    return out


def changes(state_dir: str | Path, account_id: str | None = None, limit: int = 20) -> list[LimitChangeEntry]:
    """The newest `limit` changes (of one account, or all), newest first. [] before core.db exists."""
    conn = _open(Path(state_dir))
    if conn is None:
        return []
    try:
        rows = _entries(conn, account_id)
    except (sqlite3.Error, ValueError) as exc:
        raise LimitsLogError(f"limit logs unreadable ({type(exc).__name__})") from exc
    finally:
        conn.close()
    rows.sort(key=lambda e: (e.ts_utc, e.row_id), reverse=True)
    return rows[:max(limit, 0)]


def changes_after(state_dir: str | Path, after: tuple[int, int], upto: tuple[int, int]) -> list[LimitChangeEntry]:
    """Every change in rows with ids in (after, upto] (limits_changes, adaptive_changes), oldest first: the Telegram
    notices' cursor; `upto` is `last_ids()` read first, so a row appended meanwhile waits for the next pass."""
    conn = _open(Path(state_dir))
    if conn is None:
        return []
    try:
        rows = _entries(conn, None)
    except (sqlite3.Error, ValueError) as exc:
        raise LimitsLogError(f"limit logs unreadable ({type(exc).__name__})") from exc
    finally:
        conn.close()
    k = {"limits_changes": 0, "adaptive_changes": 1}
    new = [e for e in rows if after[k[e.table]] < e.row_id <= upto[k[e.table]]]
    return sorted(new, key=lambda e: (e.ts_utc, e.table, e.row_id))


def last_ids(state_dir: str | Path) -> tuple[int, int]:
    """(the newest limits_changes id, the newest adaptive_changes id); 0 for a table that does not exist yet."""
    conn = _open(Path(state_dir))
    if conn is None:
        return 0, 0
    try:
        ids = []
        for t in ("limits_changes", "adaptive_changes"):
            ids.append(int(conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0])
                       if _has_table(conn, t) else 0)
        return ids[0], ids[1]
    except sqlite3.Error as exc:
        raise LimitsLogError(f"limit logs unreadable ({type(exc).__name__})") from exc
    finally:
        conn.close()


def revert_state(state_dir: str | Path, account_id: str) -> LimitsState | None:
    """The account's stored revert (core.db `limits_state`), None when there is none or no core.db."""
    conn = _open(Path(state_dir))
    if conn is None:
        return None
    try:
        if not _has_table(conn, "limits_state"):
            return None
        r = conn.execute("SELECT body FROM limits_state WHERE account_id = ?", (account_id,)).fetchone()
        return None if r is None else load_record_json(LimitsState, r[0], source="db:limits_state")
    except (sqlite3.Error, ValueError) as exc:
        raise LimitsLogError(f"limits revert unreadable ({type(exc).__name__})") from exc
    finally:
        conn.close()


def revert_reason(reason: str) -> str:
    return _sentence(REVERT_REASONS.get(reason, "a drawdown stage was reached"))


def next_day_reset(now: pd.Timestamp) -> pd.Timestamp:
    """The next risk-day roll: 00:00 UTC."""
    return now.tz_convert("UTC").normalize() + pd.Timedelta(days=1)


def next_week_reset(now: pd.Timestamp) -> pd.Timestamp:
    """The next risk-week roll: Sunday 00:00 UTC (gate._risk_week: weeks start Sunday 00:00 UTC)."""
    day = now.tz_convert("UTC").normalize()
    return day + pd.Timedelta(days=7 - (day.dayofweek + 1) % 7)
