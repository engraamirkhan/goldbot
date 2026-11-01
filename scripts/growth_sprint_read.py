"""Growth sprint weekly read, GS1 against its control GS1C (docs/research/growth-sprint-2026-11-01.md section 3).
Read only: aux.db is opened `mode=ro`, nothing is written anywhere.

    uv run scripts/growth_sprint_read.py --aux STATE/aux.db [--since 2026-11-01T22:00Z] [--account icm-demo] [--json]
    on the brain: sudo -n -u goldbot /opt/goldbot/.venv/bin/python scripts/growth_sprint_read.py \
        --aux /opt/goldbot/state/aux.db

Sources: the closed rows of `exploration_trades` (the engine's exploration ledger, ExplorationRecord bodies) for the arms;
`balance_snapshots` (the broker's equity, one a day) net of `balance_deals` (deposits, withdrawals) for the real account.
A trade's R is net: broker P&L over the loss at the gate's stop (`pnl / stop_loss`, both account currency). Pyramid adds ride on their first unit when the body
carries `group_id` (the first unit's client order id): the group's P&L over the first unit's stop. Day = the session
day (17:00 New York roll) of the signal. Refused candidates (`exploration_obs`, taken = 0, a cap's reason; quant Q4
2026-11-01, `growth_ledger.split_shadow`): a refused first unit is a shadow trade (R = its replay's label_sim_r, in the
matched read and the ledgers, not in n); a refused pyramid add is never a trade: counted per arm as `refused_adds`, its
replayed R added to its group only in the "uncapped (approx.)" ledgers, and the days holding one dropped from the
matched-read sensitivity. The prereg items:
  (a) real equity multiple and (b) max drawdown since the week start (daily snapshots: a lower bound on the drawdown);
  virtual ledgers at f = 5/10/20 % on each arm's R in exit order (multiple, max drawdown, doubled);
  (c) GS1 minus GS1C mean R on the days both traded, day-bootstrap 90 % CI, n;
  (d) the share of total R from the top 10 % of trades.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.engine.exploration import signal_close
from goldbot.research.growth_ledger import split_shadow

ARM, CONTROL = "GS1", "GS1C"
FRACTIONS = (0.05, 0.10, 0.20)
DRAWS, SEED = 10_000, 20261101
SMALL_N = 40                         # the prereg kill-rule read size; below it the read is descriptive only
WARNING = ("Small sample: with n = {n} trades a week cannot separate edge from luck. Read the CI, not the point "
           "estimate; no decision is taken before the pre-registered read (40+ GS1 trades or 2026-11-27).")


@dataclass
class Ledger:
    fraction: float
    multiple: float
    max_drawdown: float
    doubled: bool                    # the final multiple is 2x or more
    touched_2x: bool                 # it reached 2x at some point


@dataclass
class ArmRead:
    arm: str
    n: int = 0
    days: int = 0
    censored: int = 0
    unreadable: int = 0              # closed rows without a P&L or a stop loss: not in R
    mean_r: float | None = None
    total_r: float = 0.0
    top10_share: float | None = None
    ledgers: list[Ledger] = field(default_factory=list)
    shadow: int = 0                  # refused first units read as shadow trades (in the matched read and ledgers)
    refused_adds: dict[str, int] = field(default_factory=dict)
    ledgers_uncapped: list[Ledger] = field(default_factory=list)   # with the refused adds' replayed R (approx.)


@dataclass
class Read:
    account: str
    since: str
    real_multiple: float | None = None
    real_max_drawdown: float | None = None
    snapshots: int = 0
    arms: list[ArmRead] = field(default_factory=list)
    matched_days: int = 0
    diff_mean_r: float | None = None
    diff_ci90: tuple[float, float] | None = None
    warning: str = ""
    matched_days_no_refused_add: int = 0      # sensitivity: the days without a refused add on either leg
    diff_mean_no_refused_add_r: float | None = None


def week_start(now: pd.Timestamp) -> pd.Timestamp:
    """The latest Sunday 22:00 UTC at or before `now` (the weekly open)."""
    t = now.tz_convert("UTC")
    sun = (t - pd.Timedelta(days=(t.weekday() + 1) % 7)).normalize() + pd.Timedelta(hours=22)
    return sun if sun <= t else sun - pd.Timedelta(days=7)


def session_day(ts: pd.Timestamp) -> str:
    return str((ts.tz_convert("America/New_York") + pd.Timedelta(hours=7)).strftime("%Y-%m-%d"))


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _ns(ts: pd.Timestamp) -> int:
    return int(ts.value)


def trades(conn: sqlite3.Connection, account: str, arm: str, since: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    """One row per trade (adds folded into their group; R over the stop loss of the row whose client order id is the
    group key, the first unit): exit, day, R, censored, group. Second: unreadable rows."""
    rows = conn.execute("SELECT body FROM exploration_trades WHERE account_id = ? AND arm_id = ? AND closed = 1 "
                        "AND ts_ns >= ?", (account, arm, _ns(since))).fetchall()
    groups: dict[str, list[dict[str, Any]]] = {}
    for (body,) in rows:
        b = json.loads(body)
        groups.setdefault(str(b.get("group_id") or b["client_order_id"]), []).append(b)
    out, bad = [], 0
    for key, g in groups.items():
        first = next((b for b in g if str(b["client_order_id"]) == key), None)
        risk, pnls = (first or {}).get("stop_loss"), [b.get("pnl") for b in g]
        if first is None or risk is None or float(risk) <= 0 or any(p is None for p in pnls) \
                or any(b.get("exit_utc") is None for b in g):
            bad += len(g)
            continue
        exit_utc = max(pd.Timestamp(b["exit_utc"]) for b in g)
        out.append({"exit": exit_utc, "day": session_day(pd.Timestamp(first["signal_utc"])),
                    "r": sum(float(p) for p in pnls if p is not None) / float(risk),
                    "censored": any(bool(b.get("censored")) for b in g), "group": key, "shadow": False})
    df = pd.DataFrame(out, columns=["exit", "day", "r", "censored", "group", "shadow"])
    return df.sort_values("exit", kind="stable").reset_index(drop=True), bad


def max_drawdown(path: np.ndarray) -> float:
    """Largest fall from a running peak, as a fraction of that peak (0 for an empty or rising path)."""
    if len(path) == 0:
        return 0.0
    peak = np.maximum.accumulate(path)
    return float(np.max(1.0 - path / peak))


def ledger(r: np.ndarray, f: float) -> Ledger:
    """Equity 1, each trade multiplies it by 1 + f R (floored at 0: a loss of f R >= 1 is ruin)."""
    path = np.concatenate([[1.0], np.cumprod(np.maximum(1.0 + f * r, 0.0))])
    return Ledger(fraction=f, multiple=float(path[-1]), max_drawdown=max_drawdown(path),
                  doubled=bool(path[-1] >= 2.0), touched_2x=bool(np.max(path) >= 2.0))


def top10_share(r: np.ndarray) -> float | None:
    """Share of total R from the best ceil(10 %) of trades; None when total R <= 0 (a share of a loss means nothing)."""
    total = float(r.sum())
    if len(r) == 0 or total <= 0:
        return None
    k = max(1, math.ceil(0.1 * len(r)))
    return float(np.sort(r)[::-1][:k].sum() / total)


def matched_diff(a: pd.DataFrame, c: pd.DataFrame, *, draws: int = DRAWS,
                 seed: int = SEED) -> tuple[int, float | None, tuple[float, float] | None]:
    """GS1 minus GS1C mean R on the days both traded; the 90 % CI resamples those days with replacement."""
    days = sorted(set(a["day"]) & set(c["day"]))
    if not days:
        return 0, None, None
    ar = [a.loc[a["day"] == d, "r"].to_numpy(dtype=float) for d in days]
    cr = [c.loc[c["day"] == d, "r"].to_numpy(dtype=float) for d in days]
    point = float(np.concatenate(ar).mean() - np.concatenate(cr).mean())
    if len(days) < 2:
        return 1, point, None
    a_sum, a_n = np.array([x.sum() for x in ar]), np.array([len(x) for x in ar])
    c_sum, c_n = np.array([x.sum() for x in cr]), np.array([len(x) for x in cr])
    idx = np.random.default_rng(seed).integers(0, len(days), size=(draws, len(days)))
    boot = a_sum[idx].sum(1) / a_n[idx].sum(1) - c_sum[idx].sum(1) / c_n[idx].sum(1)
    lo, hi = np.quantile(boot, [0.05, 0.95])
    return len(days), point, (float(lo), float(hi))


def real_equity(conn: sqlite3.Connection, account: str, since: pd.Timestamp) -> tuple[float | None, float | None, int]:
    """Time-weighted multiple and max drawdown of the broker's equity from the last snapshot at or before `since`:
    each step's return nets out the deposits, withdrawals, credit and adjustments (`balance_deals`, signed amounts)
    booked inside it, so a balance reset is not growth."""
    snaps = [(int(t), json.loads(b).get("equity")) for t, b in conn.execute(
        "SELECT ts_ns, body FROM balance_snapshots WHERE account_id = ? ORDER BY ts_ns", (account,))]
    eq = [(t, float(e)) for t, e in snaps if e is not None and float(e) > 0]
    before = [x for x in eq if x[0] <= _ns(since)]
    path = ([before[-1]] if before else []) + [x for x in eq if x[0] > _ns(since)]
    if len(path) < 2:
        return None, None, len(path)
    flows = [(int(t), float(json.loads(b).get("amount") or 0.0)) for t, b in conn.execute(
        "SELECT ts_ns, body FROM balance_deals WHERE account_id = ? AND ts_ns > ?", (account, path[0][0]))]
    index = [1.0]
    for (t0, e0), (t1, e1) in zip(path, path[1:]):
        flow = sum(a for t, a in flows if t0 < t <= t1)
        index.append(index[-1] * (e1 - flow) / e0)
    v = np.array(index)
    return float(v[-1]), max_drawdown(v), len(path)


def refused(conn: sqlite3.Connection, account: str, since: pd.Timestamp) -> list[tuple[Any, ...]]:
    """The refused GS1/GS1C candidates since `since` (none when aux.db has no candidate table yet)."""
    try:
        return conn.execute(
            "SELECT candidate_id, arm_id, decision_ns, blocked_reason, label_sim_r, label_exit_ns, max_hold_s "
            "FROM exploration_obs WHERE account_id = ? AND arm_id IN (?, ?) AND taken = 0 AND decision_ns >= ?",
            (account, ARM, CONTROL, _ns(since))).fetchall()
    except sqlite3.OperationalError:
        return []


def build(aux: Path, account: str, since: pd.Timestamp) -> Read:
    conn = _connect(aux)
    try:
        out = Read(account=account, since=since.isoformat())
        out.real_multiple, out.real_max_drawdown, out.snapshots = real_equity(conn, account, since)
        split = split_shadow(refused(conn, account, since), account)
        frames = {}
        for arm in (ARM, CONTROL):
            real, bad = trades(conn, account, arm, since)
            shadow = pd.DataFrame([{"exit": t.exit_utc, "day": session_day(t.entry_utc), "r": t.r, "censored": False,
                                    "group": t.group_id, "shadow": True} for t in split.firsts if t.arm_id == arm],
                                  columns=real.columns)
            df = pd.concat([real, shadow], ignore_index=True) if len(shadow) else real
            df = df.sort_values("exit", kind="stable").reset_index(drop=True)
            frames[arm] = df
            r, n = df["r"].to_numpy(dtype=float), len(real)
            wide = r + np.array([sum(split.add_r.get(str(g), [])) for g in df["group"]], dtype=float)
            out.arms.append(ArmRead(arm=arm, n=n, days=int(real["day"].nunique()),
                                    censored=int(real["censored"].sum()), unreadable=bad,
                                    mean_r=float(real["r"].mean()) if n else None, total_r=float(real["r"].sum()),
                                    top10_share=top10_share(real["r"].to_numpy(dtype=float)),
                                    ledgers=[ledger(r, f) for f in FRACTIONS], shadow=len(df) - n,
                                    refused_adds=dict(split.refused_adds.get(arm, {})),
                                    ledgers_uncapped=[ledger(wide, f) for f in FRACTIONS]))
    finally:
        conn.close()
    out.matched_days, out.diff_mean_r, out.diff_ci90 = matched_diff(frames[ARM], frames[CONTROL])
    drop = {session_day(t) for t in (signal_close(g) for g in split.add_groups) if t is not None}
    kept = {a: f[~f["day"].isin(drop)] for a, f in frames.items()}
    out.matched_days_no_refused_add, out.diff_mean_no_refused_add_r, _ = matched_diff(kept[ARM], kept[CONTROL])
    out.warning = WARNING.format(n=out.arms[0].n) if out.arms[0].n < SMALL_N else ""
    return out


def _f(x: float | None, fmt: str = "{:+.3f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def markdown(rd: Read) -> str:
    lines = [f"# Growth sprint read: {ARM} vs {CONTROL}, {rd.account}, since {rd.since}", ""]
    if all(a.n == 0 for a in rd.arms):
        lines.append(f"No closed {ARM} or {CONTROL} trades since the week start: nothing to read.")
    lines += ["", f"Real equity ({rd.snapshots} daily snapshots): multiple {_f(rd.real_multiple, '{:.3f}x')}, "
              f"max drawdown {_f(rd.real_max_drawdown, '{:.1%}')} (daily closes: a lower bound)", "",
              "| Arm | n | days | censored | unreadable | mean R | total R | top 10% share | "
              + " | ".join(f"f={f:.0%} multiple / max DD / doubled" for f in FRACTIONS) + " |",
              "|---" * (8 + len(FRACTIONS)) + "|"]
    for a in rd.arms:
        led = " | ".join(f"{x.multiple:.3f}x / {x.max_drawdown:.1%} / {'yes' if x.doubled else 'no'}"
                         + (" (touched 2x)" if x.touched_2x and not x.doubled else "") for x in a.ledgers)
        lines.append(f"| {a.arm} | {a.n} | {a.days} | {a.censored} | {a.unreadable} | {_f(a.mean_r)} | "
                     f"{a.total_r:+.2f} | {_f(a.top10_share, '{:.0%}')} | {led} |")
    ci = "n/a (needs 2+ matched days)" if rd.diff_ci90 is None else f"[{rd.diff_ci90[0]:+.3f}, {rd.diff_ci90[1]:+.3f}]"
    lines += ["", f"{ARM} minus {CONTROL} mean R on matched days: {_f(rd.diff_mean_r)} R, day-bootstrap 90% CI {ci}, "
              f"matched days {rd.matched_days}, n {rd.arms[0].n} vs {rd.arms[1].n}",
              f"Sensitivity without the days holding a refused add: {_f(rd.diff_mean_no_refused_add_r)} R over "
              f"{rd.matched_days_no_refused_add} matched days"]
    for a in rd.arms:
        adds = ", ".join(f"{k} {v}" for k, v in sorted(a.refused_adds.items())) or "none"
        wide = ", ".join(f"f={x.fraction:.0%} {x.multiple:.3f}x" for x in a.ledgers_uncapped)
        lines.append(f"{a.arm}: shadow first units {a.shadow}, refused adds {adds}; uncapped (approx.) {wide}")
    if rd.warning:
        lines += ["", f"**{rd.warning}**"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--aux", required=True, type=Path, help="aux.db (opened read only)")
    ap.add_argument("--account", default="icm-demo")
    ap.add_argument("--since", help="week start, UTC (default: the latest Sunday 22:00 UTC)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.aux.is_file():
        print(f"no such file: {a.aux}", file=sys.stderr)
        return 2
    if a.since:
        t = pd.Timestamp(a.since)
        since = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    else:
        since = week_start(pd.Timestamp.now(tz="UTC"))
    rd = build(a.aux, a.account, since)
    print(json.dumps(asdict(rd), indent=2) if a.json else markdown(rd), end="" if not a.json else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
