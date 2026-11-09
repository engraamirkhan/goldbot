"""The daily exploration entry budget of a demo account (accounts.yaml `exploration.daily`, `ops.accounts.DailyEntries`;
owner decision 2026-10-30: "demo 150-250 fills a day in total"; PD 2026-11-06, quant ruling 2026-11-06 Q1/Q3,
docs/research/prereg-amendment-2026-11-06-control-thinning.md).

The rules, as built here (`exploration._send` asks them before any other cap; entries only, never an exit):

* Count: the account's exploration fills (an order the broker filled), every book together, by their SIGNAL close
  (aux migrations 23-24 `signal_ns`: the engine's clock, so a replay counts its own day), since the UTC day's start.
* The day (`max_per_day`), the clock hour (`max_per_hour`) and the session: each of asia / london / newyork has its
  share of max_per_day (at least one fill, the rest by largest remainder, so the three sum to max_per_day), where the
  share is the account's ADMISSIBLE edge (strategy, learning, cost) candidates in that session over the trailing
  TRAIL_DAYS trading days (equal thirds with none): candidates of the pass's active arms (not retired, refused or
  unqualified) whose decision time passes the static entry refusals (`entry_refusals.STATIC`, e.g. the 02:00-07:00
  gold block), so a blocked hour or a retired arm strands no budget (2026-11-09 volume diagnosis, cause 4). A
  session's unspent budget rolls forward to the next session of the same UTC day (asia, london, newyork, then the
  asia after the New York close): a signal is refused once the day's fills reach the budgets' sum through its
  segment, so a busy Asia still never spends New York's fills (Q3) and the day never passes max_per_day.
* Unpaired per-instrument controls (`thin`: S0-<key>, I0-<key>): at most `thin_control_share` x max_per_day a day,
  and none once their instrument's cost cell has `thin_control_fills` fills on the account (every arm's fills of
  that instrument count, LB-<key> and LBC-<key> too, as the cost of record counts them).
* A paired control (`pairs`: a learning arm's `learning.control`, a row's `params.control` "learning:<ids>",
  `research.arms.COST_CONTROLS`, `arm_rules_levels.CONTROLS` and EXTRA_PAIRS) is admitted per pair by matched-time
  Bernoulli thinning (Q1): p = min(1, controls_per_edge x its arms' fills / its own signals, in the signal's (session,
  UTC hour) cell over the trailing TRAIL_DAYS trading days before today), admitted when hash_u(control, signal
  close) < p; p is the candidate's recorded propensity. A cell where its arms filled but it never signalled takes
  p = 1, a cell where its arms never filled p = 0; a pair whose arms have no fill in the window at all (new or
  quiet) takes p = 1 (cold start; the day, hour and session budgets bind), a pair with no active arm p = 0. S0 and
  S0R pair the account's active gold scalp edge arms that no other non-learning control is read against (quant (d)).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.instruments import GOLD

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration
    from goldbot.engine.runner import Engine
    from goldbot.ops.accounts import DailyEntries

EntryKind = Literal["edge", "control", "thin"]
SESSIONS: tuple[str, ...] = ("asia", "london", "newyork")
LATE_ASIA = len(SESSIONS)           # `day_segment` of an Asia stamp after the New York close (the day's last segment)
TRAIL_DAYS = 10                     # trading days the session shares and the pair propensities are read over
# refusals that stop every signal of the pass (all of them share the pass's time): recorded blocked at once
PASS_WIDE = frozenset({"daily_entries", "hourly_entries", "session_entries"})
# controls that pair arms no row names (quant ruling 2026-11-06 Q1: ZCD -> Z3D/Z9D/Z10D; ZCV and GS1C likewise)
EXTRA_PAIRS: dict[str, tuple[str, ...]] = {"ZCD": ("Z3D", "Z9D", "Z10D"), "ZCV": ("Z6V", "Z7V"), "GS1C": ("GS1",)}
# quant ruling 2026-11-06 (d): gold's own scalp controls pair the account's gold scalp edge arms
GOLD_SCALP_CONTROLS = ("S0", "S0R")
SIGNAL = "COALESCE(signal_ns, ts_ns)"   # a fill's signal close; its send time on a row written without one


def _ns(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).value)


def entry_kind(arm: Arm) -> EntryKind:
    """`edge` a strategy, learning, cost or owner-experiment arm; `control` a random control on gold or one dedicated
    to learning arms (`params.control` "learning:<ids>", any instrument); `thin` an unpaired per-instrument control."""
    if arm.control_share is None:
        return "edge"
    paired = str(arm.spec.params.get("control", "")).startswith("learning:")
    return "thin" if arm.instrument != GOLD and not paired else "control"


def pairs(arms: Mapping[str, Arm]) -> dict[str, tuple[str, ...]]:
    """control id -> the arms it is read against (the module docstring's sources)."""
    from goldbot.research.arm_rules_levels import CONTROLS
    from goldbot.research.arms import COST_CONTROLS, dedicated_control_of
    out: dict[str, set[str]] = {c: set(a) for c, a in EXTRA_PAIRS.items()}
    for a in arms.values():
        block = a.spec.learning if isinstance(a.spec.learning, Mapping) else {}
        if a.spec.role == "learning" and isinstance(block.get("control"), str):
            out.setdefault(block["control"], set()).add(a.arm_id)
        for x in dedicated_control_of(a.spec):
            out.setdefault(a.arm_id, set()).add(x)
    for arm_id, control in COST_CONTROLS.items():
        out.setdefault(control, set()).add(arm_id)
    for arm_id, (control, _tf) in CONTROLS.items():
        out.setdefault(control, set()).add(arm_id)
    # (d): S0 and S0R pair the gold scalp edge arms no other non-learning control is read against (a learning arm's
    # dedicated twin does not take it out: the H-81 strata were registered against the borrowed S0)
    other = {a for c, v in out.items() if c not in GOLD_SCALP_CONTROLS and not c.startswith("LC") for a in v}
    scalp = {a.arm_id for a in arms.values() if entry_kind(a) == "edge" and a.instrument == GOLD and a.style == "scalp"
             and a.arm_id not in other}
    for c in GOLD_SCALP_CONTROLS:
        out[c] = set(scalp)
    return {c: tuple(sorted(v)) for c, v in out.items()}


def session_of(ts: pd.Timestamp) -> str:
    return str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([pd.Timestamp(ts)]))[0])


def _cells(ns: list[int]) -> list[tuple[str, int]]:
    if not ns:
        return []
    idx = pd.to_datetime(np.asarray(ns, dtype="int64"), utc=True)
    return list(zip((str(x) for x in DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(idx))), idx.hour, strict=True))


def trailing_start(day_start: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(day_start) - pd.offsets.BDay(TRAIL_DAYS)


def day_segment(ts: pd.Timestamp) -> int:
    """The stamp's place in its UTC day: 0 asia, 1 london, 2 newyork, LATE_ASIA the asia after the New York close."""
    sess = session_of(ts)
    return LATE_ASIA if sess == "asia" and pd.Timestamp(ts).tz_convert("UTC").hour >= 12 else SESSIONS.index(sess)


def rolled_budget(budgets: Mapping[str, int], segment: int, max_per_day: int) -> int:
    """The day's fills allowed through `segment` (roll-forward: every earlier session's unspent budget passes on)."""
    return sum(budgets.get(s, max_per_day) for s in SESSIONS[:segment + 1])


def daily_refusal(daily: DailyEntries | None, kind: EntryKind, n: Mapping[str, int], *, segment: int,
                  budgets: Mapping[str, int], cell_fills: int = 0) -> str | None:
    """Why the budget refuses one more fill of `kind` signalled in day segment `segment` (`day_segment`), or None.
    `n`: today's fills (`day`), this clock hour's (`hour`) and by kind; `budgets`: each session's fills a day, rolled
    forward (`rolled_budget`); `cell_fills`: the fills, ever, of the instrument's cost cell (a thin control's stop). A
    paired control's admission is `control_propensity`'s, after this."""
    if daily is None:
        return None
    if kind == "thin" and daily.thin_control_fills is not None and cell_fills >= daily.thin_control_fills:
        return "thin_cell_complete"
    if n.get("day", 0) >= daily.max_per_day:
        return "daily_entries"
    if daily.max_per_hour is not None and n.get("hour", 0) >= daily.max_per_hour:
        return "hourly_entries"
    if n.get("day", 0) >= rolled_budget(budgets, segment, daily.max_per_day):
        return "session_entries"
    if kind == "thin" and n.get("thin", 0) + 1 > daily.thin_control_share * daily.max_per_day + 1e-9:
        return "thin_controls"
    return None


def entries_today(eng: Engine, ex: Exploration, since: pd.Timestamp, now: pd.Timestamp,
                  arms: Mapping[str, Arm]) -> dict[str, int]:
    """Today's fills of the account by signal close: `day` (since the reset), `hour` (this clock hour) and by
    `entry_kind` (an arm no longer configured counts in the day and the hour only).
    Raises when the ledger cannot be read (the caller refuses: fail closed)."""
    hour = max(since, now.floor("h"))
    with ex.db.connection() as conn:
        # COALESCE: a row an older release wrote during a rollback has no signal_ns; its send time counts instead
        rows = conn.execute(f"SELECT arm_id, {SIGNAL} FROM exploration_trades WHERE account_id = ? AND {SIGNAL} >= ? "
                            "AND position_id IS NOT NULL", (eng.cfg.account_id, _ns(since))).fetchall()
    out = {"day": len(rows), "hour": sum(int(t) >= _ns(hour) for _, t in rows)}
    for arm_id, _ in rows:
        if (arm := arms.get(str(arm_id))) is not None:
            k = entry_kind(arm)
            out[k] = out.get(k, 0) + 1
    return out


def counted(n: dict[str, int], arm: Arm, signal: pd.Timestamp, now: pd.Timestamp) -> None:
    """A fill this pass sent, into the pass's counts."""
    for k in ("day", entry_kind(arm), *(("hour",) if signal >= now.floor("h") else ())):
        n[k] = n.get(k, 0) + 1


def apportion(max_per_day: int, by: Mapping[str, int]) -> dict[str, int]:
    """max_per_day split over SESSIONS by the candidate counts `by` (equal with none): a session with no candidate
    keeps one fill, the rest go by largest remainder (ties to the earlier session), so the sum is max_per_day (or
    len(SESSIONS) when max_per_day is smaller; the day's cap binds first)."""
    total = sum(by.get(s, 0) for s in SESSIONS)
    weights = {s: by.get(s, 0) if total else 1 for s in SESSIONS}
    floor = {s: int(weights[s] == 0) for s in SESSIONS}
    rest = max(0, max_per_day - sum(floor.values()))
    w = sum(weights.values())
    exact = {s: rest * weights[s] / w for s in SESSIONS}
    out = {s: floor[s] + math.floor(exact[s]) for s in SESSIONS}
    left = rest - sum(math.floor(v) for v in exact.values())
    for s in sorted(SESSIONS, key=lambda s: (-(exact[s] - math.floor(exact[s])), SESSIONS.index(s)))[:left]:
        out[s] += 1
    return {s: max(1, v) for s, v in out.items()}


def session_budgets(eng: Engine, ex: Exploration, daily: DailyEntries, day_start: pd.Timestamp,
                    arms: Mapping[str, Arm]) -> dict[str, int]:
    """Each session's fills a day (Q3; `apportion`), from the account's admissible edge candidates over the trailing
    TRAIL_DAYS trading days: those of `arms` (the pass's active arms) that no static entry refusal would refuse at
    their decision time (`entry_refusals.statically_refused`). Read once a day: an arm retired during the day moves
    the shares from the next day on."""
    from goldbot.engine.entry_refusals import statically_refused
    key = ("sessions", _ns(day_start))
    if key in ex.budget_cache:
        return dict(ex.budget_cache[key])
    with ex.db.connection() as conn:
        rows = [(str(a), int(t)) for a, t in conn.execute(
            "SELECT arm_id, decision_ns FROM exploration_obs WHERE account_id = ? AND is_control = 0 "
            "AND decision_ns >= ? AND decision_ns < ?",
            (eng.cfg.account_id, _ns(trailing_start(day_start)), _ns(day_start))) if str(a) in arms]
    # every static refusal's window is whole minutes: one verdict per arm and minute
    minute = 60_000_000_000
    verdict: dict[tuple[str, int], bool] = {}
    ns: list[int] = []
    for arm_id, t in rows:
        k = (arm_id, t // minute)
        if k not in verdict:
            verdict[k] = statically_refused(eng, arms[arm_id], pd.Timestamp(t, unit="ns", tz="UTC"))
        if not verdict[k]:
            ns.append(t)
    by = {s: 0 for s in SESSIONS}
    for sess, _h in _cells(ns):
        by[sess] = by.get(sess, 0) + 1
    out = apportion(daily.max_per_day, by)
    ex.budget_cache = {k: v for k, v in ex.budget_cache.items() if k[0] != "sessions"}
    ex.budget_cache[key] = out
    return dict(out)


def cell_fills(eng: Engine, ex: Exploration, now: pd.Timestamp) -> dict[str, int]:
    """instrument -> the account's exploration fills of it, ever (the per-instrument cost cell's n, every arm), read at
    most once a minute and only ever raised (counts never fall; this pass's fills are added by `cell_counted`)."""
    minute = now.floor("min")
    seen: tuple[pd.Timestamp, dict[str, int]] | None = ex.budget_cache.get(("cells",))
    if seen is not None and seen[0] == minute:
        return seen[1]
    with ex.db.connection() as conn:
        got = {str(sym or GOLD): int(n) for sym, n in conn.execute(
            "SELECT json_extract(body, '$.symbol'), COUNT(*) FROM exploration_trades WHERE account_id = ? "
            "AND position_id IS NOT NULL GROUP BY 1", (eng.cfg.account_id,))}
    old = seen[1] if seen is not None else {}
    merged = {k: max(got.get(k, 0), old.get(k, 0)) for k in {*got, *old}}
    ex.budget_cache[("cells",)] = (minute, merged)
    return merged


def cell_counted(ex: Exploration, arm: Arm) -> None:
    seen = ex.budget_cache.get(("cells",))
    if seen is not None:
        seen[1][arm.instrument] = seen[1].get(arm.instrument, 0) + 1


def control_propensity(eng: Engine, ex: Exploration, control: str, arms_of: tuple[str, ...] | None,
                       signal: pd.Timestamp, day_start: pd.Timestamp, controls_per_edge: float) -> float:
    """The paired control's admission probability at `signal` (Q1; the module docstring); 1.0 for an unpaired one.
    Read once a day per control."""
    if not arms_of:
        return 1.0
    key = ("pair", control, _ns(day_start))
    table: dict[Any, float] | None = ex.budget_cache.get(key)
    if table is None:
        lo, hi = _ns(trailing_start(day_start)), _ns(day_start)
        marks = ",".join("?" * len(arms_of))
        with ex.db.connection() as conn:
            edge = [int(r[0]) for r in conn.execute(
                f"SELECT {SIGNAL} FROM exploration_trades WHERE account_id = ? AND {SIGNAL} >= ? AND {SIGNAL} < ? "
                f"AND position_id IS NOT NULL AND arm_id IN ({marks})", (eng.cfg.account_id, lo, hi, *arms_of))]
            mine = [int(r[0]) for r in conn.execute(
                "SELECT decision_ns FROM exploration_obs WHERE account_id = ? AND arm_id = ? AND decision_ns >= ? "
                "AND decision_ns < ?", (eng.cfg.account_id, control, lo, hi))]
        e: dict[tuple[str, int], int] = {}
        c: dict[tuple[str, int], int] = {}
        for cell in _cells(edge):
            e[cell] = e.get(cell, 0) + 1
        for cell in _cells(mine):
            c[cell] = c.get(cell, 0) + 1
        table = {"cold": 1.0 if not edge else 0.0}
        for cell in {*e, *c}:
            table[cell] = min(1.0, controls_per_edge * e.get(cell, 0) / c[cell]) if c.get(cell) else \
                (1.0 if e.get(cell) else 0.0)
        ex.budget_cache = {k: v for k, v in ex.budget_cache.items()
                           if not (k[0] == "pair" and k[1] == control)}
        ex.budget_cache[key] = table
    if table["cold"]:
        return 1.0
    [cell] = _cells([_ns(signal)])
    p = float(table.get(cell, 0.0))
    return p if math.isfinite(p) else 0.0


__all__ = ["EXTRA_PAIRS", "LATE_ASIA", "PASS_WIDE", "SESSIONS", "TRAIL_DAYS", "EntryKind", "apportion",
           "cell_counted", "cell_fills", "control_propensity", "counted", "daily_refusal", "day_segment",
           "entries_today", "entry_kind", "pairs", "rolled_budget", "session_budgets", "session_of", "trailing_start"]
