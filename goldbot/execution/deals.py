"""A position's exit from the broker's deal history (moved out of `Engine._exit_deals`, ADR 0023: runner.py only
shrinks; the engine and `goldbot run repair-lost-record` read a close the same way).

The paper broker's deals and MT5's (DEAL_ENTRY 0 in, 1 out, 2 in/out, 3 out by; DEAL_REASON 4 SL, 5 TP, 6 stop-out)
are read alike; the summary is None when no exit deal is there, including an empty history (never a confirmation).
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def exit_summary(d: pd.DataFrame, pid: int) -> dict[str, Any] | None:
    """The exit of position `pid` in the deal history `d` (which has a `position_id` column): exit and entry price
    (volume-weighted), exit time and reason, P&L net of commission, fee and swap, commission and swap."""
    d = d[d["position_id"] == pid]
    mt5 = "entry" in d.columns
    is_exit = d["entry"].isin([1, 2, 3]) if mt5 else d["type"] != "entry"
    vol = "volume" if mt5 else "lots"
    ex, ent = d[is_exit], d[~is_exit]
    lots = float(ex[vol].sum()) if not ex.empty else 0.0
    if lots <= 0:
        return None

    def total(col: str, rows: pd.DataFrame) -> float | None:
        """None when the broker does not report the column or reports it as unknown on every deal."""
        if col not in rows.columns or bool(rows[col].isna().all()):
            return None
        return float(pd.to_numeric(rows[col], errors="coerce").fillna(0.0).sum())
    comm = total("commission", d)
    fee = total("fee", d) or 0.0
    swap = total("swap", d)
    gross = total("profit", ex)
    pnl = gross + (comm or 0.0) + fee + (swap or 0.0) if gross is not None else float(total("pnl", ex) or 0.0)
    last = ex.sort_values("ts_utc").iloc[-1]
    if mt5:
        reason = {4: "stop", 5: "target", 6: "stop_out"}.get(int(last.get("reason", -1)))
    else:
        reason = str(last["type"]) if last["type"] in ("stop", "target") else None
    return {"exit_price": float((ex["price"] * ex[vol]).sum()) / lots, "exit_utc": pd.Timestamp(last["ts_utc"]),
            "last_price": float(last["price"]), "last_lots": float(last[vol]),
            "pnl": pnl, "commission": None if comm is None else -(comm + fee), "swap": swap,
            "entry_price": float(ent["price"].iloc[0]) if not ent.empty else None,
            "lots": float(ent[vol].sum()) if not ent.empty else None, "reason": reason}
