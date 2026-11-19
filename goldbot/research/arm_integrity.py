"""A discovered learning arm's bracket integrity at night, fail closed (docs/research/prereg-amendment-2026-11-18-
discovered-arms.md 4, 5.1 (5) and 2.2; the quant, trading-safety, sre and code reviews of 611d5adb, 2026-11-18).
`research.arm_lifecycle` reads it for every discovered arm (`discovered`); nothing here decides, it only voids,
refuses and says whether the night saw enough to call the read verified.

Design rules:
* The night may RETIRE on what it sees and may call a read verified only when it can show it saw everything. So:
  - Positive evidence always acts: a journalled engine void at the entry retires ("bracket integrity: void at entry
    (engine)", the research-director ruling, quant concurring 2026-11-18; docs/research/arm-lifecycle-2026-11-18.md
    section 5), a journalled control change voids from its day and retires (2.2), a refused bracket on a taken
    candidate or on 3 candidates retires (5.1 (5)). A void row whose detail does not parse counts as on a taken
    candidate.
  - Coverage (`coverage_refusal`), per arm and per account the arm has candidates on since its `since`: the account's
    whole journal (every row, before the void actions are filtered) was read with its schema, has a row on every
    session day with a candidate, runs to or past the last candidate, and carries the engine's capability marker
    (`arm_registry_capable` >= CAPABILITY) of the arm since `since`, at the very time of every load of the arm since
    then (S1: a load without one, e.g. a rollback to an older engine, proves nothing): an empty journal is evidence
    only from an engine that can write the void actions. Any miss: unverified (HOLD, never evidence; the health check tells the owner after 2
    nights, `ops.checks.feeds.check_discovered_arm_reads`).
* The recompute (section 4, quant Q6) reads only what the engine held at the decision (`AccountMinutes`): the
  engine's own stored minutes (`bars_1m_live`, rank 0) and the release minutes before its cut-over (rank 3; the
  engine never holds one at or after it, `engine.history`), each version as of the decision by its write time, at
  its nominal close, in the engine's `arm_bars` window. Never the broker's M1 (rank 1) or a legacy feed as data (the
  engine never reads them); the broker's M1 is read only as evidence of whether the broker had a bar. Where the
  store cannot show what the engine held, the candidate is void with its cause, never refused: a 1d/1w window (its
  start is the engine's last restart); a gap after the cut-over (`gap_after_cutover`: a window minute the stored
  minutes lack as of the decision that the broker's M1 has, or that the release has where the broker's M1 is not
  seen silent around it within BROKER_SILENCE_MAX: the engine may have held it and its write been lost or late; a
  minute the broker's M1 shows it never had was never an engine minute and is left out, as the engine left it out);
  a minute replaced after the decision (flagged on any stored version of a window minute, `engine.history.
  REPLACED_COL`, read before the as-of filter, K1); a `broker_bar_replaced` event within REPLACE_SPAN before it that
  the store cannot account for (`stored_replacement`: the event says that minutes were replaced, never when; the
  flagged copy's write time says when, and K1 judges it. No flagged copy of its minute, fewer flagged minutes than
  it counts, or a count that does not parse, e.g. an event from an engine before the flag: void); a minute
  quarantined (quarantine has no as-of time), no bar at the decision, and a window whose minutes are not all stored
  yet (`window_incomplete`: shorter than the engine's, or ending before the minute before the decision; tried again
  the next night). An account whose minutes, dq events or
  quarantine cannot be read, or that has no minutes, leaves the arm unverified (UNVERIFYING): never a silent drop.
* Each candidate's final verdict (ok, or refused with its cause), once its day has settled (the frozen terms' lag)
  and its account's coverage passes (quant Q1), is written once to aux.db `arm_lifecycle_verdicts`
  (append-only by trigger) with the engine's (stored) and the night's bracket; later nights read it instead of
  recomputing, so the minutes are read only from the oldest candidate without a verdict. A cached ok whose stored
  bracket has changed since is refused (`bracket_updated`). Voids are not cached: they are tried again.
* Isolation: a recompute that raises voids its candidate (`error:<type>`), logged.

Reads only (the store, aux.db), plus the verdict rows; no order path.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.account_bars import QUARANTINE, as_of_minutes, minute_versions
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database
from goldbot.research.arm_rules_spec import BRACKET_PREFIX, Bracket, bracket_void
from goldbot.research.arms import PER_TRADE_KEY, Arm
from goldbot.research.exploration_data import session_day

log = logging.getLogger(__name__)

# the engine's journal actions (engine.arm_registry BRACKET_VOID, CONTROL_CHANGED, CAPABLE, CAPABILITY; tests hold
# them equal: research never imports the engine at module level)
BRACKET_VOID_ACTION = "arm_registry_bracket_void"
CONTROL_CHANGED_ACTION = "arm_registry_control_changed"
CAPABLE_ACTION = "arm_registry_capable"
LOADED_ACTION = "exploration_arm_hot_loaded"   # engine.arm_registry.LOADED: one row per load of a hot-loaded arm
CAPABILITY = 1                  # the void-journalling contract a marker proves; a lower one proves nothing
JOURNAL_ACTIONS = (BRACKET_VOID_ACTION, CONTROL_CHANGED_ACTION)
JOURNAL_COLS = ("ts_utc", "account_id", "agent_id", "action", "detail")
ENGINE_MINUTES = 90_000         # the engine's 1m memory (EngineConfig.max_bars_in_memory; tests hold them equal)
READ_DAYS = 100                 # calendar days of minutes read before a decision (>= ENGINE_MINUTES)
# a minute replaced this long before a decision was replaced before it: broker_bars.GIVE_UP + one rates call
# (FETCH_N minutes) + the minute itself; a replacement logged nearer the decision may postdate it (tests hold it)
REPLACE_SPAN = pd.Timedelta(minutes=27)
# the broker's M1 with bars on both sides of a minute at most this far apart, none at it: the broker had no bar then
# (its break, a quiet minute), so no engine minute either. Longer than the daily break (about 63 minutes, an hour off
# the session table in the US/EU DST gap weeks), shorter than the day between two reconcile pulls (jobs.
# feed_reconcile): a missed pull never reads as silence
BROKER_SILENCE_MAX = pd.Timedelta(hours=2)
BRACKET_RETIRE_N = 3            # amendment 5.1 (5): "A refused bracket (section 4) on 3 or more candidates"
ENGINE_VOID_KILL = "bracket integrity: void at entry (engine)"
# data.account_bars.minute_versions: bars_1m_live, bars_1m_broker (evidence only, never a bar held), the release
LIVE_RANK, BROKER_M1_RANK, RELEASE_RANK = 0, 1, 3
_REPLACED_N = re.compile(r"^\s*(\d+) minute")       # broker_bars._replace: "{n} minute(s) from sampled ticks ..."

# void causes: the candidate is left out of the read, never refused
NO_RECOMPUTE = "no_recompute"
NO_MINUTES = "no_minutes"
MINUTES_UNREADABLE = "minutes_unreadable"
DQ_UNREADABLE = "dq_unreadable"
QUARANTINE_UNREADABLE = "quarantine_unreadable"
REPLACED_MINUTE = "replaced_minute"
GAP_AFTER_CUTOVER = "gap_after_cutover"
QUARANTINED_MINUTE = "quarantined_minute"
WINDOW_UNPROVABLE = "window_unprovable"
NO_BAR = "no_bar_at_decision"
WINDOW_INCOMPLETE = "window_incomplete"   # the window's minutes are not all in the store yet (quant Q1): tried again
# ... of which these say the night cannot read the arm as the engine did: the arm is unverified, and told (F4, Q2)
UNVERIFYING = frozenset({NO_MINUTES, MINUTES_UNREADABLE, DQ_UNREADABLE, QUARANTINE_UNREADABLE, WINDOW_UNPROVABLE})
# refusal causes (`bracket_void`'s, the journal's, a change after a cached verdict)
REFUSAL_CAUSES = frozenset({"journalled", "journal_unparseable", "bracket_not_in_insert", "bracket_updated",
                            "bracket_unreadable", "bracket_late", "bracket_mismatch"})

# (arm, candidate row) -> the bracket recomputed from what the engine held at its decision, or the void cause
Recompute = Callable[[Arm, Mapping[str, Any]], "Bracket | str | None"]


def discovered(arm: Arm) -> bool:
    """A discovered learning arm (the amendment's): role learning with a per-trade bracket `spec_exit:<hash12>`."""
    return arm.role == "learning" and str(arm.params.get(PER_TRADE_KEY, "")).startswith(BRACKET_PREFIX)


# ---------------------------------------------------------------------------------------------- the journal
class HostRead(FrozenRecord):
    """One host account's journal as the night read it (F1-F3, S1): why it is unusable (None: read, with its schema),
    the session days with a row, its latest row, and per arm the times (epoch ns) of its loads and of its capability
    markers (CAPABILITY or later)."""
    account_id: str
    journal_why: str | None = None
    days: tuple[str, ...] = ()
    last_ns: int | None = None
    loads: dict[str, tuple[int, ...]] = {}
    marks: dict[str, tuple[int, ...]] = {}


def _detail(v: Any) -> dict[str, Any] | None:
    """A journal row's detail as a dict; None when it does not parse."""
    if isinstance(v, dict):
        return dict(v)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return {}
    try:
        got = json.loads(str(v))
    except (ValueError, TypeError):
        return None
    return got if isinstance(got, dict) else None


def host_read(account: str, frame: pd.DataFrame | None) -> HostRead:
    """`account`'s coverage from its unfiltered `decisions` read (None: unreadable). An empty read with no columns is
    the store's "no rows": readable, covering no day."""
    if frame is None:
        return HostRead(account_id=account, journal_why="unreadable")
    if frame.empty and not len(frame.columns):
        return HostRead(account_id=account)
    if missing := sorted(set(JOURNAL_COLS) - set(frame.columns)):
        return HostRead(account_id=account, journal_why=f"without its schema columns {missing}")
    if frame.empty:
        return HostRead(account_id=account)
    ts = pd.DatetimeIndex(pd.to_datetime(frame["ts_utc"], utc=True))
    ns = epoch_ns(ts)
    action = frame["action"].astype(str).to_numpy()
    loads: dict[str, list[int]] = {}
    marks: dict[str, list[int]] = {}
    for i in np.flatnonzero((action == LOADED_ACTION) | (action == CAPABLE_ACTION)):
        d = _detail(frame["detail"].iloc[i]) or {}
        arm = str(d.get("arm") or str(frame["agent_id"].iloc[i]).removeprefix("explore:"))
        if action[i] == LOADED_ACTION:
            loads.setdefault(arm, []).append(int(ns[i]))
        elif isinstance(c := d.get("capability"), int | float) and c >= CAPABILITY:
            marks.setdefault(arm, []).append(int(ns[i]))
    return HostRead(account_id=account, days=tuple(sorted(set(session_day(ts)))), last_ns=int(ns.max()),
                    loads={a: tuple(sorted(v)) for a, v in loads.items()},
                    marks={a: tuple(sorted(v)) for a, v in marks.items()})


def engine_journal(store: Any, accounts: Collection[str], *, start: pd.Timestamp,
                   now: pd.Timestamp) -> tuple[pd.DataFrame | None, dict[str, HostRead]]:
    """(the void actions of `accounts` in [start, now), every symbol; each account's coverage). The journal is None
    when any account's read is unusable (unreadable, or rows without the schema): a partial journal could miss a
    void, so every discovered arm reads unverified (F1)."""
    parts, hosts = [], {}
    for account in sorted(accounts):
        try:
            d = store.read("decisions", source=account, symbol=None, start=start, end=now)
        except Exception as exc:                # noqa: BLE001 - fail closed: unverified, never evidence
            log.error("arm integrity: %s journal unreadable (%s)", account, type(exc).__name__)
            d = None
        hosts[account] = host_read(account, d)
        if hosts[account].journal_why is None and d is not None and not d.empty:
            parts.append(d.loc[d["action"].astype(str).isin(JOURNAL_ACTIONS), list(JOURNAL_COLS)])
    if any(h.journal_why is not None for h in hosts.values()):
        return None, hosts
    parts = [p for p in parts if not p.empty]
    return (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(JOURNAL_COLS))), hosts


def journalled(journal: pd.DataFrame, action: str, arm_id: str, accounts: Collection[str]) -> list[dict[str, Any]]:
    """The journal's `action` rows of `arm_id` on `accounts`, each its detail plus `ts_utc`, `account_id` and
    `unparseable` (its detail did not parse: matched on the agent alone), in time order."""
    if journal.empty:
        return []
    j = journal[(journal["action"] == action) & journal["account_id"].astype(str).isin(set(accounts))]
    out = []
    for rec in j.sort_values("ts_utc").to_dict("records"):
        detail = _detail(rec.get("detail"))
        mine = rec.get("agent_id") == "explore:" + arm_id or (detail or {}).get("arm") == arm_id
        if mine:
            out.append({**(detail or {}), "ts_utc": pd.Timestamp(rec["ts_utc"]),
                        "account_id": str(rec["account_id"]), "unparseable": detail is None})
    return out


def coverage_refusal(cands: pd.DataFrame, hosts: Mapping[str, HostRead] | None, since: pd.Timestamp,
                     arm_id: str) -> str | None:
    """Why the night did not see everything of `arm_id`'s candidates `cands` (F2/F3/S1), None when it did: per
    account with a candidate, its journal read with its schema, a row on every candidate session day, its latest row
    at or after its last candidate, a capability marker of the arm since `since`, and one at the very time of every
    load of the arm since `since` (a load without it is an engine without the contract, e.g. after a rollback)."""
    if cands.empty:
        return None
    if hosts is None:
        return "no journal coverage read"
    for key, part in cands.groupby(cands["account_id"].astype(str), sort=True):
        acc = str(key)
        h = hosts.get(acc)
        if h is None:
            return f"{acc}: journal not read"
        if h.journal_why is not None:
            return f"{acc}: journal {h.journal_why}"
        if missing := sorted(set(part["session_day"].astype(str)) - set(h.days)):
            return f"{acc}: no journal row on candidate day(s) {', '.join(missing[:3])}" + \
                (f" (+{len(missing) - 3})" if len(missing) > 3 else "")
        last = int(epoch_ns(pd.DatetimeIndex(pd.to_datetime(part["decision_utc"], utc=True))).max())
        if h.last_ns is None or h.last_ns < last:
            return f"{acc}: journal ends before its last candidate"
        marks = {n for n in h.marks.get(arm_id, ()) if n >= int(since.value)}
        if not marks:
            return (f"{acc}: no engine capability marker ({CAPABLE_ACTION} >= {CAPABILITY}) of {arm_id} since "
                    f"{since:%Y-%m-%d}")
        if bare := [n for n in h.loads.get(arm_id, ()) if n >= int(since.value) and n not in marks]:
            return (f"{acc}: the load of {arm_id} at {pd.Timestamp(bare[0], tz='UTC'):%Y-%m-%d %H:%M} has no "
                    f"capability marker (an engine without the {CAPABLE_ACTION} contract)")
    return None


# ---------------------------------------------------------------------------------------------- the minutes
def replaced_count(detail: Any) -> int | None:
    """The number of minutes a `broker_bar_replaced` event replaced, from its detail; None when it does not parse."""
    got = _REPLACED_N.match(detail) if isinstance(detail, str) else None
    return int(got.group(1)) if got else None


def _ns(frame: pd.DataFrame) -> np.ndarray:
    return epoch_ns(pd.DatetimeIndex(pd.to_datetime(frame["ts_utc"], utc=True)))


class AccountMinutes:
    """What one account's engine held, from its stored minute versions (`minute_versions`: its own minutes and the
    release minutes before its cut-over; legacy feeds dropped, the broker's M1 kept apart as evidence), the minutes
    quarantined and the `broker_bar_replaced` events (first replaced minute in epoch ns, the count it gives or
    None)."""

    def __init__(self, versions: pd.DataFrame, *, quarantined: Sequence[int] = (),
                 replaced: Sequence[tuple[int, int | None]] = ()):
        v = versions
        m1 = np.zeros(0, dtype=np.int64)
        if "rank" in v.columns:
            rank = v["rank"].to_numpy()
            m1 = np.unique(_ns(v[rank == BROKER_M1_RANK]))
            v = v[np.isin(rank, (LIVE_RANK, RELEASE_RANK))]
        v = v.sort_values(["ts_utc", "rank"] if "rank" in v.columns else ["ts_utc"], kind="stable")
        ts = _ns(v)
        live = ts[v["rank"].to_numpy() == LIVE_RANK] if "rank" in v.columns else ts
        self.cutover: int | None = int(live.min()) if len(live) else None
        # the engine holds release minutes before its cut-over only (engine.history: `rel[ts < cutover]` at the warm
        # start, a re-read and a release reload): one at or after it is evidence of a bar, never a bar it held
        after = np.zeros(len(v), dtype=bool)
        if self.cutover is not None and "rank" in v.columns:
            after = (v["rank"].to_numpy() == RELEASE_RANK) & (ts >= self.cutover)
        self.release_after = np.unique(ts[after])
        self.v = v[~after].reset_index(drop=True)
        self.ts = ts[~after]
        self.broker_m1 = m1
        self.quarantined = np.sort(np.asarray(quarantined, dtype=np.int64))
        self.replaced = sorted((int(m), n) for m, n in replaced)

    def stored_replacement(self, minute: int, n: int | None) -> bool:
        """Whether the store accounts for a `broker_bar_replaced` event of first minute `minute` and count `n`: the
        engine's flagged copy of that minute with its write time is stored, and at least `n` flagged minutes from it
        within REPLACE_SPAN (one `_replace` call queues its minutes as one batch, written together). K1 then judges
        each by its write time, which is after the engine swapped the bar in. A count that does not parse, or a store
        without the flag, accounts for nothing (fail closed)."""
        if n is None or n < 1 or not {"replaced", "written_at"} <= set(self.v.columns):
            return False
        lo = int(np.searchsorted(self.ts, minute, side="left"))
        hi = int(np.searchsorted(self.ts, minute + REPLACE_SPAN.value, side="left"))
        part = self.v.iloc[lo:hi]
        ok = part["replaced"].astype("boolean").fillna(False).to_numpy(dtype=bool) \
            & pd.to_datetime(part["written_at"], utc=True).notna().to_numpy(dtype=bool)
        if "rank" in part.columns:
            ok &= part["rank"].to_numpy() == LIVE_RANK
        stamps = set(self.ts[lo:hi][ok].tolist())
        return minute in stamps and len(stamps) >= n

    def gap_after_cutover(self, own: np.ndarray, start: int, at: pd.Timestamp) -> bool:
        """Whether a minute from max(`start`, the cut-over) to the minute before `at` that the engine's stored minutes
        `own` (as of `at`) lack is one the engine may have held: the broker's M1 has it, or the release has it and the
        broker's M1 is not seen silent around it (its bars on both sides at most BROKER_SILENCE_MAX apart)."""
        if self.cutover is None:
            return False
        lo, hi = max(start, self.cutover), int((at - pd.Timedelta(minutes=1)).value)
        seen = np.union1d(self.broker_m1, self.release_after)
        missing = np.setdiff1d(seen[(seen >= lo) & (seen <= hi)], own)
        if not len(missing):
            return False
        m1 = self.broker_m1
        if len(m1) < 2:
            return True
        k = np.searchsorted(m1, missing, side="left")
        nxt = m1[np.minimum(k, len(m1) - 1)]
        between = (k > 0) & (k < len(m1)) & (nxt != missing)       # not in the broker's M1, which runs on both sides
        silent = between & (nxt - m1[np.maximum(k - 1, 0)] <= BROKER_SILENCE_MAX.value)
        return not bool(silent.all())

    def held(self, arm: Arm, at: pd.Timestamp) -> pd.DataFrame | str:
        """The 1m bars the engine held at `at` for `arm`'s window (module docstring), or the void cause."""
        from goldbot.config import tf_seconds
        from goldbot.engine.exploration import LOOKBACK_1M, lookback
        if arm.timeframe in ("1d", "1w"):
            return WINDOW_UNPROVABLE
        at = pd.Timestamp(at).tz_convert("UTC")
        if any((at - REPLACE_SPAN).value <= m < at.value and not self.stored_replacement(m, n)
               for m, n in self.replaced):
            return REPLACED_MINUTE
        need = max((lookback(arm) + 1) * tf_seconds(arm.timeframe) // 60, LOOKBACK_1M)
        hi = int(np.searchsorted(self.ts, at.value, side="left"))
        span = pd.Timedelta(days=need // (23 * 60) * 7 // 5 + 7)        # trading minutes -> calendar days, padded
        lo = int(np.searchsorted(self.ts, (at - span).value, side="left"))
        seen = as_of_minutes(self.v.iloc[lo:hi], at)
        if len(seen) < need and lo > 0:                                # a long gap: the whole prefix
            seen = as_of_minutes(self.v.iloc[:hi], at)
        if seen.empty:
            return WINDOW_INCOMPLETE
        win = seen.tail(need)
        wts = epoch_ns(pd.DatetimeIndex(pd.to_datetime(win["ts_utc"], utc=True)))
        # K1: on every stored version of the window, before the as-of filter (which drops a replacement written
        # after the decision): a minute replaced after `at` was not what the engine held
        if "replaced" in self.v.columns and "written_at" in self.v.columns:
            span_v = self.v.iloc[int(np.searchsorted(self.ts, int(wts.min()), side="left")):hi]
            flagged = span_v["replaced"].astype("boolean").fillna(False).to_numpy(dtype=bool)
            late = (pd.to_datetime(span_v["written_at"], utc=True) > at).fillna(False).to_numpy(dtype=bool)
            if bool((flagged & late).any()):
                return REPLACED_MINUTE
        # Q1: the window's minutes not all in the store yet (the last one is the minute before `at`)
        if len(win) < need or int(wts.max()) < int((at - pd.Timedelta(minutes=1)).value):
            return WINDOW_INCOMPLETE
        if self.gap_after_cutover(wts, int(wts.min()), at):
            return GAP_AFTER_CUTOVER
        if bool(((self.quarantined >= wts.min()) & (self.quarantined < at.value)).any()):
            return QUARANTINED_MINUTE
        seen = seen.tail(ENGINE_MINUTES).reset_index(drop=True)
        seen["visible_at"] = pd.to_datetime(seen["ts_utc"], utc=True) + pd.Timedelta(minutes=1)
        return seen

    def bracket(self, arm: Arm, at: pd.Timestamp) -> Bracket | str:
        from goldbot.engine.arm_registry import decision_bracket
        held = self.held(arm, at)
        if isinstance(held, str):
            return held
        return decision_bracket(held, arm, pd.Timestamp(at)) or NO_BAR


def engine_recompute(minutes: Mapping[str, pd.DataFrame | AccountMinutes | str]) -> Recompute:
    """The recompute per candidate account: its `AccountMinutes` (a frame with `rank` is read as stored versions),
    the engine's plain 1m bars (`engine.arm_registry.decision_bracket` on them), or the account's void cause."""
    from goldbot.engine.arm_registry import decision_bracket

    def recompute(arm: Arm, row: Mapping[str, Any]) -> Bracket | str:
        m = minutes.get(str(row.get("account_id")))
        at = pd.Timestamp(row["decision_utc"])
        if isinstance(m, str):
            return m
        if m is None or (isinstance(m, pd.DataFrame) and m.empty):
            return NO_MINUTES
        if isinstance(m, pd.DataFrame) and "rank" in m.columns:
            m = AccountMinutes(m)
        if isinstance(m, AccountMinutes):
            return m.bracket(arm, at)
        return decision_bracket(m, arm, at) or NO_BAR
    return recompute


def _account_minutes(store: Any, account: str, broker: str, symbol: str, *, start: pd.Timestamp,
                     now: pd.Timestamp) -> AccountMinutes | str:
    from goldbot.engine.broker_bars import REPLACED
    try:
        v = minute_versions(store, account_id=account, broker=broker, symbol=symbol, start=start, end=now, as_of=now)
    except Exception as exc:                    # noqa: BLE001 - fail closed: the arm reads unverified
        log.error("arm integrity: %s minutes unreadable (%s)", account, type(exc).__name__)
        return MINUTES_UNREADABLE
    if v.empty:
        return NO_MINUTES
    try:
        dq = store.read("dq_events", source=account, symbol=None, start=start, end=now)
        hit = dq[dq["check"].astype(str) == REPLACED] if dq is not None and not dq.empty else None
        details = [] if hit is None else (hit["detail"] if "detail" in hit.columns else [None] * len(hit))
        replaced: list[tuple[int, int | None]] = [] if hit is None else \
            list(zip(_ns(hit).tolist(), [replaced_count(d) for d in details], strict=True))
    except Exception as exc:                    # noqa: BLE001 - fail closed: the arm reads unverified
        log.error("arm integrity: %s dq_events unreadable (%s)", account, type(exc).__name__)
        return DQ_UNREADABLE
    try:
        q: list[int] = []
        for source in dict.fromkeys((broker, account)):
            got = store.read(QUARANTINE, source=source, symbol=symbol, start=start, end=now, columns=["ts_utc"])
            if got is not None and not got.empty:
                q += epoch_ns(pd.DatetimeIndex(pd.to_datetime(got["ts_utc"], utc=True))).tolist()
    except Exception as exc:                    # noqa: BLE001 - fail closed: the arm reads unverified
        log.error("arm integrity: %s quarantine unreadable (%s)", account, type(exc).__name__)
        return QUARANTINE_UNREADABLE
    return AccountMinutes(v, quarantined=q, replaced=replaced)


def stored_recompute(store: Any, hosts: Mapping[str, tuple[str, str]], *, now: pd.Timestamp) -> Recompute:
    """The recompute from the minutes each host's engine stored (`hosts`: account -> (broker, symbol)), read lazily
    per account from READ_DAYS before the earliest decision asked (P4: only candidates without a verdict ask), as of
    `now`."""
    now = pd.Timestamp(now).tz_convert("UTC")
    loaded: dict[str, tuple[pd.Timestamp, AccountMinutes | str]] = {}

    def recompute(arm: Arm, row: Mapping[str, Any]) -> Bracket | str:
        acc = str(row.get("account_id"))
        if acc not in hosts:
            return NO_MINUTES
        at = pd.Timestamp(row["decision_utc"]).tz_convert("UTC")
        start = at - pd.Timedelta(days=READ_DAYS)
        got = loaded.get(acc)
        if got is None or start < got[0]:
            got = (start, _account_minutes(store, acc, *hosts[acc], start=start, now=now))
            loaded[acc] = got
        m = got[1]
        return m if isinstance(m, str) else m.bracket(arm, at)
    return recompute


@dataclass(frozen=True)
class NightInputs:
    """What the nightly job hands `arm_lifecycle.run` for the discovered arms (all None: none is decided)."""
    journal: pd.DataFrame | None = None
    recompute: Recompute | None = None
    hosts: dict[str, HostRead] | None = None


def integrity_inputs(store: Any, hosts: Mapping[str, tuple[str, str]], arms: Collection[Arm], *,
                     now: pd.Timestamp) -> NightInputs:
    """The journal, the coverage and the recompute, read only when a running discovered arm is among `arms`: the
    hosts' journal from the earliest such arm's `since` (`now` - READ_DAYS when one has no learning block)."""
    from goldbot.research.arms import Learning
    disc = [a for a in arms if discovered(a) and a.status == "run" and not a.retired]
    if not disc:
        return NightInputs()
    now = pd.Timestamp(now).tz_convert("UTC")
    starts = []
    for a in disc:
        try:
            starts.append(pd.Timestamp(Learning.model_validate(a.learning).since, tz="UTC"))
        except ValueError:
            starts.append(now - pd.Timedelta(days=READ_DAYS))
    journal, read = engine_journal(store, hosts, start=min(starts), now=now)
    return NightInputs(journal=journal, recompute=stored_recompute(store, hosts, now=now), hosts=read)


# ---------------------------------------------------------------------------------------------- the verdicts
def _ns_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return None if pd.isna(v) else int(pd.Timestamp(v).value)
    except (ValueError, TypeError):
        return None


def _num(v: Any) -> float | int | None:
    return None if v is None or pd.isna(v) else v


def candidate_row(row: Mapping[str, Any]) -> dict[str, object]:
    """A read_obs row as `bracket_void` reads a candidate row (plain ints, epoch ns)."""
    hold = _num(row.get("max_hold_s"))
    return {"stop_dist": None if (v := _num(row.get("stop_dist"))) is None else float(v),
            "target_dist": None if (v := _num(row.get("target_dist"))) is None else float(v),
            "max_hold_s": None if hold is None else int(hold),
            "decision_ns": _ns_or_none(row.get("decision_utc")),
            "recorded_ns": None if (v := _num(row.get("recorded_ns"))) is None else int(v),
            "entry_ns": _ns_or_none(row.get("entry_utc"))}


def _engine_bracket(row: Mapping[str, Any]) -> dict[str, Any]:
    c = candidate_row(row)
    return {k: c[k] for k in ("stop_dist", "target_dist", "max_hold_s")}


Key = tuple[str, int | None]


def verdicts(db: Database, broker: str, arm_id: str) -> dict[Key, dict[str, Any]]:
    """The arm's cached verdicts on `broker`: (account, decision ns) -> verdict, cause, engine and night brackets."""
    with db.connection() as conn:
        rows = conn.execute("SELECT account_id, decision_ns, verdict, cause, engine, night FROM "
                            "arm_lifecycle_verdicts WHERE broker = ? AND arm_id = ?", (broker, arm_id)).fetchall()
    return {(str(a), int(n)): {"verdict": v, "cause": c, "engine": json.loads(e),
                               "night": json.loads(nt) if nt is not None else None}
            for a, n, v, c, e, nt in rows}


def _write_verdicts(db: Database, broker: str, arm_id: str, rows: list[tuple[Key, str, str | None, dict[str, Any],
                                                                             dict[str, Any] | None]]) -> None:
    if not rows:
        return
    ns = int(pd.Timestamp.now("UTC").value)
    with db.write() as conn:
        conn.executemany("INSERT OR IGNORE INTO arm_lifecycle_verdicts (broker, arm_id, account_id, decision_ns, "
                         "verdict, cause, engine, night, recorded_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(broker, arm_id, k[0], k[1], v, c, json.dumps(e), None if n is None else json.dumps(n), ns)
                          for k, v, c, e, n in rows if k[1] is not None])


@dataclass
class Integrity:
    """One discovered arm's integrity read: the read's candidates (void ones out), the refusals and their causes,
    the kill evidence and the arm's own candidates since `since` (for `coverage_refusal`)."""
    obs: pd.DataFrame
    cands: pd.DataFrame
    n_refused: int = 0
    on_taken: bool = False
    engine_void: bool = False
    change: pd.Timestamp | None = None
    refusals: list[dict[str, Any]] = field(default_factory=list)
    refused_by_cause: dict[str, int] = field(default_factory=dict)
    void_by_cause: dict[str, int] = field(default_factory=dict)

    @property
    def unverifying(self) -> list[str]:
        return sorted(c for c in self.void_by_cause if c in UNVERIFYING)


def _count(d: dict[str, int], cause: str) -> None:
    d[cause] = d.get(cause, 0) + 1


def integrity(db: Database | None, obs: pd.DataFrame, arm: Arm, *, broker: str, journal: pd.DataFrame,
              recompute: Recompute, accounts: Collection[str], since: pd.Timestamp,
              hosts: Mapping[str, HostRead] | None = None, now: pd.Timestamp | None = None,
              settled: Callable[[str, float], pd.Timestamp] | None = None) -> Integrity:
    """The module docstring's voids, refusals and kill evidence for `arm` on `broker`. A verdict is written to the
    cache (`db`) only once its inputs have settled (quant Q1): `now` at or after `settled(session day, its hold s)`
    (the frozen terms' settle lag) and its account's coverage passing (`coverage_refusal`). Without `db`, `now` or
    `settled` nothing is cached."""
    voids = journalled(journal, BRACKET_VOID_ACTION, arm.id, accounts)
    changes = journalled(journal, CONTROL_CHANGED_ACTION, arm.id, accounts)
    out = Integrity(obs=obs, cands=obs.iloc[:0], engine_void=bool(voids),
                    change=changes[0]["ts_utc"] if changes else None)
    by_journal: dict[Key, str] = {}
    for e in voids:
        bar = _ns_or_none(e.get("bar"))
        if e["unparseable"] or bar is None:      # F6: what it voided is unknown: as on a taken candidate
            out.n_refused += 1
            out.on_taken = True
            _count(out.refused_by_cause, "journal_unparseable")
            out.refusals.append({"account": e["account_id"], "decision_ns": bar, "cause": "journal_unparseable",
                                 "engine": None, "night": None})
        else:
            by_journal[(str(e["account_id"]), bar)] = "journalled"
    mine = obs[obs["arm_id"] == arm.id]
    mine = mine[pd.to_datetime(mine["decision_utc"], utc=True) >= since].sort_values("decision_utc", kind="stable")
    out.cands = mine
    covered = {str(a) for a, part in mine.groupby(mine["account_id"].astype(str))
               if coverage_refusal(part, hosts, since, arm.id) is None} if hosts is not None else set()

    def frozen(row: Mapping[str, Any]) -> bool:
        if db is None or now is None or settled is None or str(row.get("account_id")) not in covered:
            return False
        hold = _num(row.get("max_hold_s"))
        return pd.Timestamp(now) >= settled(str(row.get("session_day")), float(hold) if hold is not None else 0.0)
    cached = verdicts(db, broker, arm.id) if db is not None else {}
    new: list[tuple[Key, str, str | None, dict[str, Any], dict[str, Any] | None]] = []
    drop: list[Any] = []
    refused: set[Key] = set()
    rows: list[dict[str, Any]] = [{str(k): v for k, v in r.items()} for r in mine.to_dict("records")]
    for i, row in zip(mine.index, rows, strict=True):
        key: Key = (str(row.get("account_id")), _ns_or_none(row.get("decision_utc")))
        engine = _engine_bracket(row)
        cause: str | None = None
        night: dict[str, Any] | None = None
        if key in by_journal:
            cause = by_journal[key]
            if key not in cached and frozen(row):
                new.append((key, "refused", cause, engine, None))
        elif key in cached:
            hit = cached[key]
            night = hit["night"]
            if hit["verdict"] == "refused":
                cause = str(hit["cause"])
            elif hit["engine"] != engine:
                cause, night = "bracket_updated", hit["engine"]
        else:
            try:
                rec = recompute(arm, row)
            except Exception as exc:            # noqa: BLE001 - F5: this candidate is void, the others go on
                log.exception("arm integrity: %s candidate %s recompute raised", arm.id, key)
                rec = f"error:{type(exc).__name__}"
            if rec is None:
                rec = NO_RECOMPUTE
            if isinstance(rec, str):
                _count(out.void_by_cause, rec)
                drop.append(i)
                continue
            night = rec.model_dump()
            why = bracket_void(candidate_row(row), rec)
            cause = None if why is None else why.split(":", 1)[0]
            if frozen(row):
                new.append((key, "ok" if cause is None else "refused", cause, engine, night))
        if cause is not None:
            drop.append(i)
            refused.add(key)
            _count(out.refused_by_cause, cause)
            out.on_taken = out.on_taken or bool(row.get("taken"))
            out.refusals.append({"account": key[0], "decision_ns": key[1], "cause": cause, "engine": engine,
                                 "night": night})
    if db is not None:
        _write_verdicts(db, broker, arm.id, new)
    out.n_refused += len(refused)
    if out.change is not None:
        drop += list(mine.index[mine["session_day"].astype(str) >= session_day(out.change)])
    out.obs = obs.drop(index=sorted(set(drop)))
    return out


__all__ = ["BRACKET_RETIRE_N", "BRACKET_VOID_ACTION", "BROKER_SILENCE_MAX", "CAPABILITY", "CAPABLE_ACTION",
           "CONTROL_CHANGED_ACTION", "DQ_UNREADABLE", "ENGINE_MINUTES", "ENGINE_VOID_KILL", "GAP_AFTER_CUTOVER",
           "JOURNAL_ACTIONS", "JOURNAL_COLS", "LOADED_ACTION", "MINUTES_UNREADABLE", "NO_BAR", "NO_MINUTES",
           "NO_RECOMPUTE", "QUARANTINED_MINUTE", "QUARANTINE_UNREADABLE", "READ_DAYS", "REFUSAL_CAUSES",
           "REPLACED_MINUTE", "REPLACE_SPAN", "UNVERIFYING", "WINDOW_INCOMPLETE", "WINDOW_UNPROVABLE",
           "AccountMinutes", "HostRead", "Integrity", "NightInputs", "Recompute", "candidate_row", "coverage_refusal",
           "discovered", "engine_journal", "engine_recompute", "host_read", "integrity", "integrity_inputs",
           "journalled", "replaced_count", "stored_recompute", "verdicts"]
