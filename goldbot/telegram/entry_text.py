"""Telegram /status in the dashboard's entry-status language (docs/ux/entry-status.md, Telegram section).

Every word comes from goldbot.api.entry_status (the function behind the dashboard's Entry status panel): the badge
word, the headline, each reason with its fix, the exits line. This module only lays it out as plain text for a phone:
per account at most MAX_LINES lines, "Needs you" (act) before the waits, every time in the owner's zone with UTC
beside it. Nothing here decides whether an entry is allowed (the RiskGate does, in the engine).

Shape (one account):

    BLOCKED · icm-demo (demo) · as of 15:00 BST
    No new trades: Broker clock differs from the expected time zone (+1 more)
    Needs you:
    • Broker clock differs from the expected time zone
      → See the Runbook: Server clock. Entries resume a minute after the clocks agree.
    Waits:
    • Market closed · opens Sun 23:02 BST (22:02 UTC, in 1 d 12 h)
    Exits keep running: open trades keep their stop, target and automatic exits.

No login, server or host: only the account's config name, mode and the API's mapped texts reach the message.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from goldbot.api import entry_status as es
from goldbot.api.schema import EntryBlock, EntryStatus
from goldbot.config import Settings

log = logging.getLogger(__name__)
_DIGITS = re.compile(r"\d{5,}")
MAX_LINES = 15                 # per account: one phone screen
BADGE = {"open": "OPEN", "partial": "PARTLY OPEN", "waiting": "PAUSED", "blocked": "BLOCKED"}
NO_ENGINE = ("No engine has reported yet: no account can take new trades. Check the engine service and the "
             "dashboard's Health tab.")
FAILED = "Entry status couldn't be worked out. Check the dashboard's Health tab; the engine still applies every rule."


def account_name(account: str) -> str:
    """The account's config name with any run of 5+ digits cut to its last two ("icm-demo-51234567" ->
    "icm-demo-…67"): a config name that embeds an MT5 login never reaches the chat (CLAUDE.md: no account ids)."""
    return _DIGITS.sub(lambda m: "…" + m.group(0)[-2:], account)


def _zone(tz: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _span(seconds: float) -> str:
    """"1 d 12 h", "2 h 5 min", "30 min" (as web/src/lib/entry.ts)."""
    m = max(1, round(abs(seconds) / 60))
    d, h, mm = m // 1440, (m % 1440) // 60, m % 60
    if d:
        return f"{d} d {h} h" if h else f"{d} d"
    if h:
        return f"{h} h {mm} min" if mm else f"{h} h"
    return f"{m} min"


def clock(at: pd.Timestamp, now: pd.Timestamp, tz: str) -> tuple[str, str | None]:
    """("Sun 23:02 BST", "22:02 UTC"): the owner's zone, with the weekday when it is not today there, and UTC beside
    it. Without a known zone: ("Sun 22:02 UTC", None)."""
    at, now = at.tz_convert("UTC"), now.tz_convert("UTC")
    z = None if tz == "UTC" else _zone(tz)
    loc, loc_now = (at, now) if z is None else (at.tz_convert(z), now.tz_convert(z))
    day = "" if loc.date() == loc_now.date() else f"{loc:%a} "
    if z is None:
        return f"{day}{at:%H:%M} UTC", None
    return f"{day}{loc:%H:%M} {loc.tzname()}", f"{at:%H:%M} UTC"


def when(at: pd.Timestamp, label: str, now: pd.Timestamp, tz: str) -> str:
    """"opens Sun 23:02 BST (22:02 UTC, in 1 d 12 h)", "since 14:10 BST (13:10 UTC, 50 min ago)"."""
    delta = (at - now).total_seconds()
    rel = f"in {_span(delta)}" if delta >= 0 else f"{_span(delta)} ago"
    local, utc = clock(at, now, tz)
    return f"{label} {local} ({utc + ', ' if utc else ''}{rel})"


def _block_lines(b: EntryBlock, now: pd.Timestamp, tz: str) -> list[str]:
    head = f"• {b.text}"
    if b.at is not None and b.at_label:
        head += " · " + when(pd.Timestamp(b.at), b.at_label, now, tz)
    # a timed wait that clears by itself is one line (its time says when); anything else carries its next step
    if b.kind == "wait" and b.fix.startswith(es.NOTHING) and b.at is not None:
        return [head]
    return [head, f"  → {b.fix.replace('on this page', 'on the dashboard (Overview)')}"]    # the API words a page


def account_text(account: str, mode: str, s: EntryStatus, *, now: pd.Timestamp, tz: str) -> str:
    """One account's status, at most MAX_LINES lines; blocks that do not fit are counted, never silently dropped."""
    now = pd.Timestamp(now).tz_convert("UTC")
    top = f"{BADGE[s.state]} · {account_name(account)} ({mode})"
    if s.as_of is not None:
        top += f" · as of {clock(pd.Timestamp(s.as_of), now, tz)[0]}"
    headline = s.headline
    if s.clears_at is not None:
        headline += " · " + when(pd.Timestamp(s.clears_at), "clears", now, tz)
    notes = s.notes[:1]
    fixed = 2 + len(notes) + 1                          # top, headline, notes, exits
    acts = [b for b in s.blocks if b.kind == "act"]
    waits = [b for b in s.blocks if b.kind != "act"]

    def body(limit: int) -> tuple[list[str], int]:
        out: list[str] = []
        shown = 0
        for title, group in (("Needs you:", acts), ("Waits:", waits)):
            for i, b in enumerate(group):
                add = ([title] if i == 0 else []) + _block_lines(b, now, tz)
                if len(out) + len(add) > limit:
                    return out, shown
                out += add
                shown += 1
        return out, shown

    room = MAX_LINES - fixed
    lines, shown = body(room)
    if shown < len(s.blocks):
        lines, shown = body(room - 1)
        lines.append(f"+{len(s.blocks) - shown} more on the dashboard (Overview → Entry status)")
    return "\n".join([top, headline, *lines, *notes, s.exits])


def status_message(state_dir: str | Path, now: pd.Timestamp, *, tz: str, settings: Settings | None,
                   footer: str | None = None) -> str:
    """The /status reply: a dated title, every reporting account (es.statuses_from_state, as the dashboard builds
    them) separated by a blank line, then the optional footer (approval mode, pending proposals)."""
    now = pd.Timestamp(now).tz_convert("UTC")
    local, utc = clock(now, now, tz)
    title = f"Entry status · {now.tz_convert(_zone(tz) or 'UTC'):%a %d %b} {local}" + (f" ({utc})" if utc else "")
    try:
        rows = es.statuses_from_state(state_dir, now, settings)
        parts = [account_text(a, m, s, now=now, tz=tz) for a, m, s in rows] or [NO_ENGINE]
    except Exception:                       # noqa: BLE001 - reporting only; never an empty or OPEN reply
        parts = [FAILED]
    return "\n\n".join([title, *parts, *([footer] if footer else [])])


def status_reply(state_dir: str | Path, now: pd.Timestamp, *, tz: str, bus: Any,
                 load: Callable[[], Settings | None]) -> str:
    """What the bot sends for /status: settings re-read (the RiskGate's defaults when they fail to load, as the API),
    the approval mode and pending count from the bus as the footer, then `status_message`. Any failure answers with
    FAILED: the owner never gets silence."""
    try:
        try:
            settings = load()
        except Exception:                   # noqa: BLE001 - a bad settings edit: the defaults
            settings = None
        c = bus.control()
        mode = c.approval_mode or "each account's setting"        # None: no owner override (ADR 0041)
        footer = f"Approval mode: {mode} · pending proposals: {len(bus.pending())}"
        return status_message(state_dir, now, tz=tz, settings=settings, footer=footer)
    except Exception:                       # noqa: BLE001
        log.exception("/status reply failed")
        return FAILED


def digest_line(state_dir: str | Path, now: pd.Timestamp, settings: Settings | None) -> list[str]:
    """The daily digest's entry line: the accounts not OPEN at digest time, each with its badge and first reason."""
    rows = es.statuses_from_state(state_dir, now, settings)
    if not rows:
        return ["Entries: no engine reporting (no account can take new trades)"]
    shut = [f"{account_name(a)} {BADGE[s.state]}" + (f" ({s.blocks[0].text})" if s.blocks else "")
            for a, _, s in rows if s.state != "open"]
    return ["Entries not open: " + " · ".join(shut)] if shut else ["Entries: open on every account"]
