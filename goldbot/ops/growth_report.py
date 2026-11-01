"""The growth test section of perf-report and health (growth sprint 2026-11-01, docs/research/growth-sprint-2026-11-01.md
section 3: "(a) did real equity double ... and did the virtual ledgers double; (b) max drawdown, real and virtual; (c)
GS1 minus GS1C mean R"). Reads only: the engine's state file (equity, the risk week's start equity), the exploration
ledger (aux.db `exploration_trades`: GS1/GS1C fills, pyramid groups folded by `group_id`) and the candidates
(`exploration_obs`: each GS1/GS1C entry a cap refused, counted per reason and, once the nightly replay labelled it,
a shadow fill). The risk week starts Sunday 00:00 UTC, as the RiskGate's."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.research.growth_ledger import (
    GROWTH_ARMS,
    NOT_SHADOW,
    GrowthRead,
    LedgerTrade,
    fold_groups,
    growth_read,
)

GROWTH_ACCOUNT = "icm-demo"


class GrowthTest(FrozenRecord):
    account_id: str
    week_start: str                         # the risk week's start, UTC
    week_start_equity: float | None = None  # the engine's own (RiskGate) week-start equity
    equity: float | None = None
    multiple: float | None = None           # equity / week_start_equity
    growth_pnl: float | None = None         # GS1 + GS1C net P&L this week, account currency
    growth_drawdown: float | None = None    # max drawdown of week-start equity + the growth arms' realised P&L
    read: GrowthRead
    errors: list[str] = []


def week_start(now: pd.Timestamp) -> pd.Timestamp:
    """Sunday 00:00 UTC of `now`'s week (the RiskGate's risk week)."""
    from goldbot.risk.gate import _risk_week
    return _risk_week(now)


def recorded(state_dir: str | Path) -> bool:
    """Whether aux.db holds any GS1/GS1C order or candidate (read-only; False when it cannot be read)."""
    aux = Path(state_dir) / "aux.db"
    if not aux.exists():
        return False
    marks = ",".join("?" * len(GROWTH_ARMS))
    try:
        conn = sqlite3.connect(f"file:{aux}?mode=ro", uri=True, timeout=5)
        try:
            return any(conn.execute(f"SELECT 1 FROM {t} WHERE arm_id IN ({marks}) LIMIT 1", GROWTH_ARMS).fetchone()
                       for t in ("exploration_trades", "exploration_obs"))
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _num(d: dict[str, object], k: str) -> float | None:
    v = d.get(k)
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def growth_test(state_dir: str | Path, *, account_id: str = GROWTH_ACCOUNT,
                now: pd.Timestamp | None = None) -> GrowthTest | None:
    """This risk week's growth test on `account_id`; None when nothing of it is recorded (no engine state, no GS1/GS1C
    order or candidate). Never raises on a bad source: it is named in `errors`."""
    from goldbot.engine.exploration import LEDGER
    state = Path(state_dir)
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    start = week_start(now)
    errors: list[str] = []
    engine: dict[str, object] = {}
    try:
        raw = json.loads((state / f"engine_{account_id}.json").read_text())
        engine = raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        errors.append(f"engine_{account_id}.json unreadable: equity not shown")
    shadow: list[LedgerTrade] = []
    refused: dict[str, dict[str, int]] = {}
    marks = ",".join("?" * len(GROWTH_ARMS))
    recs: list[Any] = []
    rows: list[Any] = []
    aux = state / "aux.db"
    if aux.exists():
        try:                                        # read-only: never migrated or written from a report
            conn = sqlite3.connect(f"file:{aux}?mode=ro", uri=True, timeout=5)
            try:
                recs = [r for r in LEDGER.select(conn, f"arm_id IN ({marks}) AND account_id = ?",
                                                 (*GROWTH_ARMS, account_id)) if r.signal_utc >= start]
                rows = conn.execute(
                    "SELECT arm_id, decision_ns, blocked_reason, label_sim_r, label_exit_ns, max_hold_s "
                    f"FROM exploration_obs WHERE arm_id IN ({marks}) AND account_id = ? AND taken = 0 "
                    "AND decision_ns >= ?", (*GROWTH_ARMS, account_id, int(start.value))).fetchall()
            finally:
                conn.close()
        except Exception as exc:                    # noqa: BLE001 - reporting only
            errors.append(f"exploration ledger unreadable ({type(exc).__name__}): growth trades missing, not zero")
            recs, rows = [], []
    trades = [t for t in fold_groups(recs) if t.entry_utc >= start]
    pnl = sorted((r.exit_utc, float(r.pnl)) for r in recs if r.exit_utc is not None and r.pnl is not None)
    for arm, d_ns, why, label, exit_ns, hold_s in rows:
        reason = str(why or "unknown")
        refused.setdefault(str(arm), {})
        refused[str(arm)][reason] = refused[str(arm)].get(reason, 0) + 1
        if reason in NOT_SHADOW or label is None:
            continue
        entry = pd.Timestamp(int(d_ns), unit="ns", tz="UTC")
        end = pd.Timestamp(int(exit_ns), unit="ns", tz="UTC") if exit_ns is not None \
            else entry + pd.Timedelta(seconds=int(hold_s or 0))
        shadow.append(LedgerTrade(arm_id=str(arm), entry_utc=entry, exit_utc=end, r=float(label), shadow=True))
    if not (recs or rows):
        return None                                 # no GS1/GS1C order or candidate: the test has not started
    ws, eq = _num(engine, "week_start_equity"), _num(engine, "equity")
    dd = None
    if ws is not None and ws > 0:
        level, peak, dd = ws, ws, 0.0
        for _, p in pnl:
            level += p
            peak = max(peak, level)
            dd = max(dd, 1.0 - level / peak if peak > 0 else 0.0)
    return GrowthTest(account_id=account_id, week_start=start.isoformat(), week_start_equity=ws, equity=eq,
                      multiple=eq / ws if eq is not None and ws else None,
                      growth_pnl=sum(p for _, p in pnl) if pnl else None, growth_drawdown=dd,
                      read=growth_read(trades, refused=refused, shadow=shadow), errors=errors)


def lines(g: GrowthTest) -> list[str]:
    """The section as text lines (perf-report markdown, the health detail)."""
    out = [f"week from {g.week_start[:10]} on {g.account_id}: equity {g.week_start_equity} -> {g.equity}"
           f" (x{g.multiple:.3f})" if g.multiple is not None else
           f"week from {g.week_start[:10]} on {g.account_id}: equity not recorded"]
    if g.growth_pnl is not None:
        out.append(f"GS1+GS1C realised {g.growth_pnl:+.2f}, max drawdown {100 * (g.growth_drawdown or 0):.1f}%")
    for arm, a in g.read.arms.items():
        mean = f"{a.mean_r:+.2f}" if a.mean_r is not None else "n/a"
        led = ", ".join(f"f={k}: x{v.final_multiple:.2f} dd {100 * v.max_drawdown:.0f}%"
                        f"{' doubled' if v.doubled else ''}{' ruined' if v.ruined else ''}" for k, v in a.ledgers.items())
        refused = ", ".join(f"{k} {v}" for k, v in sorted(a.refused.items())) or "none"
        out.append(f"{arm}: n {a.n}, mean R {mean}, shadow {a.shadow_n}, refused: {refused}; virtual {led}")
    ex = f"{g.read.paired_excess_r:+.2f} R" if g.read.paired_excess_r is not None else "n/a"
    out.append(f"GS1 - GS1C over {g.read.pairs} paired sessions: {ex}" + (f" ({g.read.note})" if g.read.note else ""))
    return out + [f"error: {e}" for e in g.errors]
