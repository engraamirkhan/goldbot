"""`goldbot run positions [--account <id>] [--json]`: the open trades without the dashboard login.

Read-only: it reads state files (orders_<account>.json, engine_<account>.json, shadow_book.json) and nothing else; no
bridge call, no write, no order ("RiskGate is the only path to an order"). The positions come from the function behind
GET /api/positions/open (home_cards.open_positions), so the CLI and the dashboard cannot disagree. Accounts appear by
config name with any run of 5+ digits cut (entry_text.account_name); a login number is never printed. An orders file
that cannot be read is named and the exit code is 1: missing positions are not "none".

How a trade entered is derived, not stored: a cost probe is "probe" (ADR 0037), a demo account's trade is "demo auto"
(ADR 0036), a live one "owner-confirmed" (the owner's one-click confirmation), anything else "unknown".
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.api.home_cards import open_positions
from goldbot.api.schema import OpenPosition
from goldbot.base import load_record
from goldbot.engine.shadow import VersionBook
from goldbot.telegram.entry_text import account_name

__all__ = ["main"]


def entered_how(p: OpenPosition) -> str:
    if p.probe:
        return "probe"
    return {"demo": "demo auto", "live": "owner-confirmed"}.get(p.mode or "", "unknown")


def shadow_open_by_agent(state: Path) -> dict[str, int] | None:
    """Open taken shadow trades by agent; None when the book cannot be read (reported, never zero)."""
    f = state / "shadow_book.json"
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text())
        books = [load_record(VersionBook, v, source="shadow_book.json") for v in raw.values()]
    except Exception:                   # noqa: BLE001 - reporting only
        return None
    return dict(sorted(Counter(t.agent_id for b in books for t in b.open if t.taken and t.exit_ts is None).items()))


def _row(p: OpenPosition) -> dict[str, Any]:
    return {"position_id": p.position_id, "agent": p.agent_id, "timeframe": p.timeframe, "side": p.side,
            "lots": p.lots, "entry": p.entry, "opened_utc": p.opened_utc.isoformat(), "stop": p.stop,
            "target": p.target, "unbounded": p.stop is None, "open_risk": p.open_risk, "floating_pnl": p.floating_pnl, "currency": p.currency,
            "entered": entered_how(p), "stale": p.stale}


def _sum(vals: list[float | None]) -> float | None:
    """None when any figure is unknown: a partial total is not a total."""
    return None if any(v is None for v in vals) else round(sum(v for v in vals if v is not None), 2)


def build(state: Path, now: pd.Timestamp, enabled: list[str], only: str | None) -> dict[str, Any]:
    view = open_positions(state, now)
    accounts = []
    for acc in [only] if only is not None else enabled:
        name = account_name(acc)
        mine = [p for p in view.positions if p.account == name]
        ccys = sorted({p.currency for p in mine if p.currency})
        accounts.append({"account": name, "readable": name not in view.unreadable,
                         "orders_file": (state / f"orders_{acc}.json").exists(),
                         "positions": [_row(p) for p in mine],
                         "totals": {"open": len(mine), "lots": round(sum(p.lots for p in mine), 2),
                                    "open_risk": _sum([p.open_risk for p in mine]),
                                    "floating_pnl": _sum([p.floating_pnl for p in mine]),
                                    "currency": ccys[0] if len(ccys) == 1 else None,
                                    "stale": any(p.stale for p in mine),
                                    "unbounded": any(p.stop is None for p in mine)}})
    return {"as_of": now.isoformat(), "accounts": accounts, "shadow_open": shadow_open_by_agent(state)}


def _num(v: float | None, signed: bool = False) -> str:
    return "?" if v is None else (f"{v:+,.2f}" if signed else f"{v:,.2f}")


def _risk(v: float | None, unbounded: bool) -> str:
    return "no stop: risk unbounded" if unbounded else _num(v)


def render(doc: dict[str, Any]) -> str:
    out = [f"open positions as of {doc['as_of']} (state files only; money in the account currency)"]
    for a in doc["accounts"]:
        t = a["totals"]
        ccy = t["currency"] or ""
        out.append(f"\n{a['account']}: {t['open']} open" + ("" if a["readable"] else "  [orders file UNREADABLE: positions missing]"))
        if not a["orders_file"]:
            out.append("  no engine order file (engine not run yet?)")
        elif not a["positions"] and a["readable"]:
            out.append("  no open positions")
        for p in a["positions"]:
            out.append(f"  #{p['position_id']} {p['agent']} {p['timeframe'] or '-'} {p['side']} {p['lots']:g} lots "
                       f"entry {_num(p['entry'])} at {p['opened_utc']} stop {_num(p['stop'])} target {_num(p['target'])} "
                       f"risk {_risk(p['open_risk'], p['unbounded'])} {'' if p['unbounded'] else p['currency'] or ''} pnl {_num(p['floating_pnl'], True)} "
                       f"{p['currency'] or ''} [{p['entered']}]" + (" (stale)" if p["stale"] else ""))
        if a["positions"]:
            out.append(f"  total: {t['open']} open, {t['lots']:g} lots, risk {_risk(t['open_risk'], t['unbounded'])} "
                       f"{'' if t['unbounded'] else ccy}, floating {_num(t['floating_pnl'], True)} {ccy}"
                       + (" (stale)" if t["stale"] else ""))
    sh = doc["shadow_open"]
    if sh is None:
        out.append("\nshadow book: unreadable")
    else:
        out.append(f"\nshadow: {sum(sh.values())} open" + (": " + ", ".join(f"{k} {v}" for k, v in sh.items()) if sh else ""))
    return "\n".join(out)


def main(argv: list[str], now: pd.Timestamp | None = None, enabled: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot run positions", description="Open trades, read-only (state files).")
    ap.add_argument("--account", help="one enabled account id (default: every enabled account)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--state-dir", default="state")
    args = ap.parse_args(argv)
    if enabled is None:
        from goldbot.ops.accounts import enabled_accounts
        enabled = [a.account_id for a in enabled_accounts()]
    if args.account is not None and args.account not in enabled and not (Path(args.state_dir) / f"orders_{args.account}.json").exists():
        print(f"unknown account {args.account!r}: enabled accounts are {', '.join(account_name(a) for a in enabled) or 'none'}")
        return 2
    doc = build(Path(args.state_dir), now if now is not None else pd.Timestamp.now("UTC"), enabled, args.account)
    print(json.dumps(doc, indent=1) if args.json else render(doc))
    return 0 if all(a["readable"] for a in doc["accounts"]) and doc["shadow_open"] is not None else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
