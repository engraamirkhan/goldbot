"""Seed a realistic trading day into the e2e server's state dir, through goldbot's own writers, for the trades screens
(docs/design/trades-ux-2026-10-27.md; e2e/dashboard.spec.ts "Trades: ..."). Every kind the ledger reads: exploration
and control arms (aux.db), a cost probe, a closed engine trade, open engine and arm positions (orders files) and a
shadow trade, on two GBP demo accounts, with closes on earlier days for the 14-day strip. At least 3 unread arms per
account and day, so an unfiltered list shows its totals and a filter narrowed to one strategy withholds them
(ADR 0050), both from the real server.

    python e2e/seed_trades.py <state_dir>           # seed (and write fresh engine reports)
    python e2e/seed_trades.py <state_dir> --touch   # only refresh the engine reports' clocks (stale after 60 s)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.db import Database
from goldbot.engine import cost_probe, exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade

SCALP, DAY = "icm-probe", "icm-demo"


def engine(state: Path, account: str, now: pd.Timestamp, open_n: int, open_risk: float) -> None:
    f = state / f"engine_{account}.json"
    e: dict[str, Any] = json.loads(f.read_text()) if f.exists() else {}
    e.update({"account": account, "broker": "icm", "mode": "demo", "currency": "GBP", "usd_to_account": 0.8,
              "usd_to_account_utc": now.isoformat(), "ts": now.timestamp(), "price": 2404.6, "spread_points": 20,
              "lot_terms": {"contract_oz": 100.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50,
                            "stops_level_points": 0},
              "equity": 10_012.4, "balance": 10_010.0, "day_start_equity": 10_000.0, "week_start_equity": 9_990.0,
              "daily_loss_limit": 500.0, "daily_loss_remaining": 470.0, "limits": {"weekly_cap": 0.1},
              "open_positions": open_n, "open_risk": open_risk, "stage": "normal", "account_class": "raw",
              "market_closed": False, "entry_blocks": []})
    e.pop("login", None)
    f.write_text(json.dumps(e))


def arm(conn: Any, cid: str, arm_id: str, account: str, sent: pd.Timestamp, *, tf: str, minutes: float | None,
        pnl: float | None, side: int = 1, reason: str = "target", pid: int | None = None) -> None:
    closed = minutes is not None
    exploration.LEDGER.upsert(conn, ExplorationRecord(
        client_order_id=cid, account_id=account, arm_id=arm_id, family="x", tf=tf, side=side, lots=0.01,
        signal_utc=sent, sent_utc=sent, atr_usd=3.0, stop_usd=3.0, target_usd=6.0, hold_s=3600,
        requested_price=2400.0, spread_usd=0.2, fill_price=2400.1, stop_loss=2.4, position_id=pid,
        exit_utc=sent + pd.Timedelta(minutes=minutes) if closed else None,
        exit_price=(2400.1 + side * (pnl or 0) / 0.8) if closed else None, exit_reason=reason if closed else None,
        swap=0.0 if closed else None, commission=0.07 if closed else None, pnl=pnl if closed else None,
        censored=False if closed else None, fx_acct_per_usd=0.8))


def seed(state: Path, now: pd.Timestamp) -> None:
    engine(state, SCALP, now, 2, 4.8)
    engine(state, DAY, now, 1, 7.2)
    db = Database.open(state, "aux")
    m = pd.Timedelta(minutes=1)
    with db.write() as conn:
        # today on the scalp lab: five arms (one a random control), each closing a few trades
        n = 0
        for arm_id, tf, results in (("S0", "1m", [-0.4, 0.3, -0.4]), ("S1A", "5m", [1.2, -0.6, 0.9]),
                                    ("S2A", "5m", [-0.6, 1.4]), ("S3A", "5m", [0.8, -0.6]), ("S5A", "5m", [-0.6, 1.1])):
            for i, pnl in enumerate(results):
                n += 1
                arm(conn, f"e2e-{arm_id}-{i}", arm_id, SCALP, now - (20 + 9 * n) * m, tf=tf, minutes=6, pnl=pnl,
                    side=1 if n % 2 else -1, reason="target" if pnl > 0 else "stop")
        arm(conn, "e2e-S1A-open", "S1A", SCALP, now - 14 * m, tf="5m", minutes=None, pnl=None, pid=501)
        # the day/swing lab: the three box-exit arms on 1h
        for i, (arm_id, pnl) in enumerate((("BXR1H", 2.1), ("BXT1H", -1.4), ("BXF1H", 1.6))):
            arm(conn, f"e2e-{arm_id}", arm_id, DAY, now - (200 - 30 * i) * m, tf="1h", minutes=95, pnl=pnl,
                reason="target" if pnl > 0 else "stop")
        # earlier days for the 14-day strip (three arms a day on the scalp lab)
        for d in range(1, 10):
            for j, arm_id in enumerate(("S1A", "S2A", "S3A")):
                arm(conn, f"e2e-past-{d}-{arm_id}", arm_id, SCALP, now - pd.Timedelta(days=d) - (60 + 5 * j) * m,
                    tf="5m", minutes=8, pnl=round(((d * 7 + j * 3) % 11 - 4) * 0.9, 2))
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="e2e-cp-1", account_id=SCALP, agent_id="mean_reversion-g0-ab", side=-1, lots=0.01,
            requested_price=2401.0, spread_usd=0.25, sent_utc=now - 90 * m, fill_price=2400.9,
            exit_utc=now - 80 * m, exit_price=2400.6, exit_reason="time_exit", swap=0.0, commission=0.07, pnl=0.17,
            stop_loss=3.0, fx_acct_per_usd=0.8))
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=DAY, broker="icm", mode="demo", exit_utc=now - 40 * m, ret=0.001, pnl=3.95, equity_before=10_000.0,
        position_id=41, agent_id="box_break_close-g0-1h", side=1, lots=0.05, entry_utc=now - 220 * m,
        entry_price=2400.0, exit_price=2401.2, exit_reason="trail_close", r=1.2, commission=0.35, swap=-0.12,
        timeframe="1h", initial_sl=2398.0, final_tp=2404.0))
    trade = {"position_id": 77, "agent_id": "box_break_close-g0-1h", "side": 1, "lots": 0.05, "max_bars": 8,
             "timeframe": "1h", "entry_bar_ts": (now - 120 * m).isoformat(), "open_price": 2403.2, "sl": 2401.4,
             "tp": 2410.0, "initial_sl": 2400.8, "opened_utc": (now - 65 * m).isoformat()}
    held = {**trade, "position_id": 501, "agent_id": "explore:S1A", "sl": 2397.7, "tp": 2406.1, "initial_sl": 2397.7,
            "lots": 0.01, "timeframe": "5m", "open_price": 2400.1, "opened_utc": (now - 14 * m).isoformat()}
    (state / f"orders_{DAY}.json").write_text(json.dumps({"sent": {}, "open": {"77": trade}}))
    (state / f"orders_{SCALP}.json").write_text(json.dumps({"sent": {}, "open": {"501": held}}))
    book = ShadowBook(state)
    book.track("v1", now - pd.Timedelta(days=1))
    book.books["v1"].closed.append(ShadowTrade(
        version="v1", agent_id="tsmom-g0-x", side=1, entry_ts=now - 300 * m, entry=2400.0, stop=2390.0, target=2420.0,
        max_bars=12, timeframe="1h", p=0.62, exit_ts=now - 120 * m, exit=2420.0, barrier="target", ret=20 / 2400))
    book.save(now)


if __name__ == "__main__":
    state_dir = Path(sys.argv[1])
    now = pd.Timestamp.now(tz="UTC").floor("s")
    if "--touch" in sys.argv:
        engine(state_dir, SCALP, now, 2, 4.8)
        engine(state_dir, DAY, now, 1, 7.2)
    else:
        seed(state_dir, now)
