"""The weekly review's ranked action list (aux.db `review_actions`, aux migration 30; goldbot/research/weekly_review.py
writes it, goldbot/research/director.py consumes it, the Sunday digest counts it).

Rules kept (architect plan 2026-11-13 item 2):
* "The only automatic actions are stops already registered in config/arm_stops.yaml (tighten only)": `stop` is the one
  automatic kind; every other kind is advice for the research director or the backlog, never applied here.
* One action set per week: `write_week` records the week in `review_weeks` and its actions in one transaction; a rerun
  of the week (a restart mid-run) gets the stored set back and writes nothing.
* An action id is stable over its subject (a roll-up proposal: its file and title; a stop: broker and arm), so the same
  finding read by two weekly reviews joins the list once.
* Consumed once: `consume` marks every unconsumed action with the director's slot in one transaction and returns that
  slot's set, so a rerun of the same slot gets the same actions and a later slot none of them.

This module never imports the strategy ranking (ADR 0053 6f): it stores what the review hands it.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Literal

import pandas as pd

from goldbot.base import FrozenRecord, load_record_json
from goldbot.db.connection import Database

ActionKind = Literal["stop", "read_due", "below_control", "loss_driver", "cost_drift", "exit_diagnostic",
                     "variant_proposal", "unpaired"]
# the ranking order of the kinds: what was done, then what a registered rule says is due, then the evidence's
# warnings, then proposals and housekeeping
KIND_ORDER: tuple[str, ...] = ("stop", "read_due", "below_control", "loss_driver", "cost_drift", "exit_diagnostic",
                               "variant_proposal", "unpaired")
AUTOMATIC_KINDS = frozenset({"stop"})
BACKLOG_KINDS = frozenset({"unpaired", "cost_drift"})     # engineering suggestions: a GitHub issue labelled `review`


class ReviewAction(FrozenRecord):
    action_id: str
    week: str                               # the review's Saturday, YYYY-MM-DD
    rank: int = 0                           # 1 = first; set when the week is written
    kind: ActionKind
    subject: str                            # a family, a read unit, a broker and session, an attribution cell or arm
    title: str
    detail: str
    automatic: bool = False                 # only a registered stop; applied by the review, never by a reader
    source: str                             # ranking | attribution | cost_of_record | exits | daily_review | rollup
    score: float = 0.0                      # the order within a kind (larger first)
    broker: str | None = None               # stop: the broker it is recorded on
    rule: str | None = None                 # stop: the registered rule that fired


def action_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _actions(rows: list[tuple[str]]) -> list[ReviewAction]:
    return [load_record_json(ReviewAction, b, source="review_actions") for (b,) in rows]


def week_actions(db: Database, week: str) -> list[ReviewAction]:
    with db.connection() as conn:
        return _actions(conn.execute("SELECT body FROM review_actions WHERE week = ? ORDER BY rank", (week,)).fetchall())


def write_week(db: Database, week: str, actions: list[ReviewAction], now: pd.Timestamp) -> tuple[list[ReviewAction],
                                                                                                    bool]:
    """Write `actions` (ranked) as `week`'s set, unless the week is written already: (the stored set, written now).
    An action whose id is already stored (an earlier week's) is left out of this week's set."""
    ns = int(pd.Timestamp(now).value)
    with db.write() as conn:
        if conn.execute("SELECT 1 FROM review_weeks WHERE week = ?", (week,)).fetchone() is None:
            known = {i for (i,) in conn.execute("SELECT action_id FROM review_actions")}
            fresh = [a for a in actions if a.action_id not in known]
            for i, a in enumerate(fresh, 1):
                a = a.model_copy(update={"rank": i, "week": week})
                conn.execute("INSERT INTO review_actions (action_id, week, rank, kind, automatic, recorded_ns, body) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (a.action_id, week, i, a.kind, int(a.automatic), ns,
                                                              a.model_dump_json()))
            conn.execute("INSERT INTO review_weeks (week, recorded_ns, n_actions) VALUES (?, ?, ?)",
                         (week, ns, len(fresh)))
            written = True
        else:
            written = False
    return week_actions(db, week), written


def consume(db: Database, slot: str, now: pd.Timestamp) -> list[ReviewAction]:
    """The actions the director's `slot` takes as queue input: every unconsumed one, marked with the slot (once), and
    on a rerun of the same slot the same set again."""
    with db.write() as conn:
        conn.execute("UPDATE review_actions SET consumed_by = ?, consumed_ns = ? WHERE consumed_by IS NULL",
                     (slot, int(pd.Timestamp(now).value)))
        return _actions(conn.execute("SELECT body FROM review_actions WHERE consumed_by = ? ORDER BY week, rank",
                                     (slot,)).fetchall())


def recent(conn: sqlite3.Connection, lo: pd.Timestamp, hi: pd.Timestamp) -> list[ReviewAction] | None:
    """The actions of the latest week written in [lo, hi) (a read-only connection); None when no week was."""
    row = conn.execute("SELECT week FROM review_weeks WHERE recorded_ns >= ? AND recorded_ns < ? "
                       "ORDER BY recorded_ns DESC LIMIT 1", (int(lo.value), int(hi.value))).fetchone()
    if row is None:
        return None
    return [load_record_json(ReviewAction, b, source="review_actions") for (b,) in conn.execute(
        "SELECT body FROM review_actions WHERE week = ? ORDER BY rank", (row[0],))]
