"""Execution audit (design: "An execution auditor compares fills with the cost model per broker and flags slippage
drift"). The numbers are computed here, deterministically; the execution-auditor agent only explains them.

Per session and order type: slippage over the window and over the most recent days against the nightly cost
table's value (or its prior), with a drift flag when the recent mean exceeds the table by more than two standard
errors (and at least DRIFT_MIN_USD); per session: recent median spread against the table's. Failed orders are counted
from the journal.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.execution.costs import SESSIONS, CostTable

DRIFT_MIN_USD = 0.05          # $/oz: below this a "drift" is noise whatever the standard error says
SPREAD_WIDENING = 1.5         # recent median spread this many times the table's is flagged
MIN_RECENT_FILLS = 10


def _slippage(fills: pd.DataFrame) -> np.ndarray:
    return fills["side"].to_numpy(dtype=float) * (fills["filled"].to_numpy(dtype=float) - fills["requested"].to_numpy(dtype=float))


def execution_audit(account_id: str, fills: pd.DataFrame, ticks: pd.DataFrame, table: CostTable | None,
                    decisions: pd.DataFrame, now: pd.Timestamp, recent_days: int = 7,
                    sessions: SessionTable = DEFAULT_SESSIONS) -> dict[str, Any]:
    out: dict[str, Any] = {"account_id": account_id, "now": now.isoformat(), "recent_days": recent_days,
                           "cost_table": None, "slippage": [], "spread": [], "failed_orders": 0, "flags": []}
    if table is not None:
        out["cost_table"] = {"built_utc": table.built_utc.isoformat(),
                             "age_h": round((now - table.built_utc).total_seconds() / 3600, 1),
                             "commission_per_lot_side_usd": table.commission_per_lot_side_usd,
                             "slippage_prior_usd": table.slippage_prior_usd}
        if (now - table.built_utc) > pd.Timedelta(days=2):
            out["flags"].append("cost table older than 2 days: the nightly cost job may be failing")
    else:
        out["flags"].append("no cost table: the engine prices entries with its configured fallback")
    recent_from = now - pd.Timedelta(days=recent_days)

    f = fills.dropna(subset=["requested", "filled"]) if not fills.empty else fills
    if not f.empty:
        ts = pd.DatetimeIndex(pd.to_datetime(f["ts_utc"], utc=True))
        label = sessions.session_label(ts)
        otype = f["order_type"].astype(str).to_numpy() if "order_type" in f else np.full(len(f), "market")
        slip = _slippage(f)
        recent = np.asarray(ts >= recent_from)
        for s in SESSIONS:
            for ot in sorted({str(x) for x in otype}):
                m = (label == s) & (otype == ot)
                if not m.any():
                    continue
                v, r = slip[m], slip[m & recent]
                cell = table.slippage.get(f"{s}:{ot}") if table is not None else None
                ref = cell.mean if cell is not None else (table.slippage_prior_usd if table is not None else None)
                row: dict[str, Any] = {"session": s, "order_type": ot, "n": int(len(v)), "mean_usd": round(float(v.mean()), 4),
                                       "p90_usd": round(float(np.percentile(v, 90)), 4), "recent_n": int(len(r)),
                                       "recent_mean_usd": round(float(r.mean()), 4) if len(r) else None,
                                       "table_usd": ref, "table_from_prior": cell.from_prior if cell is not None else None,
                                       "drift": False}
                if ref is not None and len(r) >= MIN_RECENT_FILLS:
                    se = float(r.std(ddof=1)) / np.sqrt(len(r)) if len(r) > 1 else 0.0
                    excess = float(r.mean()) - ref
                    if excess > max(DRIFT_MIN_USD, 2 * se):
                        row["drift"] = True
                        out["flags"].append(f"slippage drift {s}/{ot}: recent {r.mean():.3f} vs table {ref:.3f} $/oz "
                                            f"over {len(r)} fills")
                out["slippage"].append(row)

    t = ticks[(ticks["ask"] >= ticks["bid"]) & (ticks["bid"] > 0)] if not ticks.empty else ticks
    if not t.empty:
        ts = pd.DatetimeIndex(pd.to_datetime(t["ts_utc"], utc=True))
        keep = np.asarray(sessions.is_open(ts)) & np.asarray(ts >= recent_from)
        t, ts = t[keep], ts[keep]
        label = sessions.session_label(ts)
        spread = (t["ask"] - t["bid"]).to_numpy(dtype=float)
        for s in SESSIONS:
            v = spread[label == s]
            if not len(v):
                continue
            tab = table.spread.get(s) if table is not None else None
            row = {"session": s, "recent_n": int(len(v)), "recent_median_usd": round(float(np.median(v)), 4),
                   "recent_p90_usd": round(float(np.percentile(v, 90)), 4),
                   "table_median_usd": tab.median if tab is not None else None, "widened": False}
            if tab is not None and tab.median > 0 and np.median(v) > SPREAD_WIDENING * tab.median:
                row["widened"] = True
                out["flags"].append(f"spread widened in {s}: recent median {np.median(v):.3f} vs table {tab.median:.3f} $/oz")
            out["spread"].append(row)

    if not decisions.empty and "action" in decisions:
        orders = decisions[decisions["action"].astype(str) == "order"]
        failed = 0
        for d in orders.get("detail", pd.Series(dtype=str)):
            try:
                failed += int(json.loads(d).get("ok") is False)
            except (TypeError, ValueError):
                continue
        out["failed_orders"] = failed
        out["orders"] = int(len(orders))
        if failed:
            out["flags"].append(f"{failed} of {len(orders)} orders failed at the broker (see retcodes in the journal)")
    return out
