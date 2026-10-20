"""`goldbot run shadow-report [--since ISO] [--account <id>] [--include-quarantined] [--json]`: the shadow book, the
cost-probe ledger and each engine's history-gap / guard state, for an operator who cannot read the state directory.

Read-only: it reads shadow_book.json, the cost-probe ledger (aux.db `cost_probes`, opened without migrating) and
engine_<account>.json and nothing else; no bridge call, no write, no order ("RiskGate is the only path to an order").
Parsing is the engine's own (`VersionBook`, `ProbeRecord` through `load_record`/`LEDGER`), never a second copy.

Research integrity (H-52, promotion.EVIDENCE_START_UTC): a trade opened before the history-gap fix is quarantined. It
is hidden by default, counted in the header, and shown only with --include-quarantined, each row marked
`[quarantined: history_gap]`, so earlier rows are never read as evidence by mistake. Accounts appear by config name
with any run of 5+ digits cut (entry_text.account_name); a login number is never printed. A source that cannot be
read is named and the exit code is 1: a missing source is not "no trades".

R gross is the trade's gross return in units of its initial risk, `ret * entry / |entry - stop|` (no costs; the
shadow book's `ret` already follows the exit policy). Cost-probe `round trip` = gross move per oz
minus net P&L per oz (commission, swap, exit slip), in USD per oz: net P&L is converted with the engine's published
usd_to_account (today's factor, so "approx" off a USD account) and the contract size from its lot_terms; either one
missing prints "?" (never a guessed rate or a mixed-unit figure). The ledger is opened read-only (`mode=ro`,
`query_only`), so the report neither switches the journal mode nor creates -wal/-shm files nor changes permissions.
Every string taken from an engine file is passed through account_name (a login inside it never prints).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.base import load_record
from goldbot.db.migrations import check_version
from goldbot.engine.cost_probe import LEDGER, ProbeRecord
from goldbot.engine.shadow import ShadowTrade, VersionBook
from goldbot.ops.account_view import usd_to_account
from goldbot.research.promotion import EVIDENCE_START_UTC, quarantine_reason
from goldbot.telegram.entry_text import account_name

__all__ = ["main"]

ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _iso(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None else pd.Timestamp(ts).isoformat()


def _keep(ts: pd.Timestamp, since: pd.Timestamp | None, include_quarantined: bool) -> tuple[bool, str | None]:
    """(show it, quarantine reason): `since` cuts older rows; quarantined rows show only when asked."""
    if since is not None and pd.Timestamp(ts) < since:
        return False, None
    reason = quarantine_reason(ts)
    return (reason is None or include_quarantined), reason


def shadow_trades(state: Path, since: pd.Timestamp | None, include_quarantined: bool) -> dict[str, Any] | None:
    """Taken shadow trades by agent (open and closed); None when the book cannot be read (reported, never empty)."""
    f = state / "shadow_book.json"
    if not f.exists():
        return {"trades": [], "hidden_quarantined": 0}
    try:
        raw = json.loads(f.read_text())
        books = [load_record(VersionBook, v, source="shadow_book.json") for v in raw.values()]
    except Exception:                   # noqa: BLE001 - reporting only
        return None
    rows: list[dict[str, Any]] = []
    hidden = 0
    for b in books:
        for t, state_name in [(t, "open") for t in b.open] + [(t, "closed") for t in b.closed]:
            if not t.taken:
                continue
            show, reason = _keep(t.entry_ts, since, include_quarantined)
            if not show:
                hidden += reason is not None and (since is None or pd.Timestamp(t.entry_ts) >= since)
                continue
            rows.append(_trade_row(t, state_name, reason))
    rows.sort(key=lambda r: (r["agent"], r["entry_utc"]))
    return {"trades": rows, "hidden_quarantined": int(hidden)}


def _trade_row(t: ShadowTrade, state_name: str, reason: str | None) -> dict[str, Any]:
    risk = abs(t.entry - t.stop)
    r_gross = round(t.ret * t.entry / risk, 3) if t.ret is not None and risk > 0 else None
    return {"agent": _m(t.agent_id), "version": _m(t.version), "timeframe": t.timeframe, "state": state_name,
            "side": "long" if t.side > 0 else "short", "entry_utc": _iso(t.entry_ts), "entry": t.entry,
            "exit_utc": _iso(t.exit_ts), "exit": t.exit, "r_gross": r_gross, "exit_reason": t.barrier,
            "quarantined": reason}


def _read_ledger(state: Path) -> list[ProbeRecord]:
    """The ledger read-only: `mode=ro` + `query_only`, schema version checked (fails closed), nothing created or changed."""
    uri = f"{(state / 'aux.db').resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
    try:
        conn.execute("PRAGMA query_only=1")
        check_version(conn, "aux")
        return LEDGER.select(conn, order_by="ts_ns")
    finally:
        conn.close()


def probe_rows(state: Path, accounts: list[str], since: pd.Timestamp | None, include_quarantined: bool,
               engines: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The cost-probe ledger's rows for `accounts`; None when the ledger cannot be read."""
    if not (state / "aux.db").exists():
        return {"probes": [], "hidden_quarantined": 0}
    try:
        recs = _read_ledger(state)
    except Exception:                   # noqa: BLE001 - reporting only
        return None
    rows: list[dict[str, Any]] = []
    hidden = 0
    for r in recs:
        if r.account_id not in accounts:
            continue
        show, reason = _keep(r.sent_utc, since, include_quarantined)
        if not show:
            hidden += reason is not None and (since is None or pd.Timestamp(r.sent_utc) >= since)
            continue
        rows.append(_probe_row(r, reason, engines.get(r.account_id, {})))
    return {"probes": rows, "hidden_quarantined": int(hidden)}


def _probe_row(r: ProbeRecord, reason: str | None, engine: dict[str, Any]) -> dict[str, Any]:
    lot = engine.get("lot_terms")
    contract = lot.get("contract_oz") if isinstance(lot, dict) else None
    fx = usd_to_account(engine)
    ccy = engine.get("currency") if isinstance(engine.get("currency"), str) else None
    round_trip = None
    if (r.fill_price is not None and r.exit_price is not None and r.pnl is not None and isinstance(contract, (int, float))
            and contract > 0 and fx is not None and fx > 0):
        round_trip = round(r.side * (r.exit_price - r.fill_price) - r.pnl / fx / (r.lots * contract), 3)
    return {"account": account_name(r.account_id), "agent": _m(r.agent_id), "side": "long" if r.side > 0 else "short",
            "lots": r.lots, "sent_utc": _iso(r.sent_utc), "requested": r.requested_price, "fill": r.fill_price,
            "spread_at_fill": r.spread_usd, "commission": r.commission, "swap": r.swap, "currency": _m(ccy),
            "exit_utc": _iso(r.exit_utc), "exit": r.exit_price, "exit_reason": _m(r.exit_reason), "pnl": r.pnl,
            "round_trip_usd_per_oz": round_trip,
            "round_trip_approx": round_trip is not None and (ccy or "").upper() != "USD", "quarantined": reason}


def _m(v: Any) -> Any:
    """Mask account identifiers in a string taken from a file (account_name); other values pass unchanged."""
    return account_name(v) if isinstance(v, str) else v


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _ts(v: Any) -> str | None:
    """A timestamp re-formatted to whole seconds, or None: only a parsed time prints, never the file's own string."""
    try:
        return pd.Timestamp(v).floor("s").isoformat()
    except (ValueError, TypeError):
        return None


def read_engine(state: Path, acc: str) -> dict[str, Any] | None:
    """engine_<account>.json as a dict: {} when absent, None when unreadable."""
    f = state / f"engine_{acc}.json"
    if not f.exists():
        return {}
    try:
        e = json.loads(f.read_text())
    except Exception:                   # noqa: BLE001 - reporting only
        return None
    return e if isinstance(e, dict) else None


def engine_state(state: Path, acc: str, e: dict[str, Any] | None) -> dict[str, Any]:
    """History-gap and guard facts from engine_<account>.json (what the engine itself published)."""
    name = account_name(acc)
    if e is None:
        return {"account": name, "file": True, "readable": False}
    if not e:
        return {"account": name, "file": (state / f"engine_{acc}.json").exists(), "readable": True}
    th = _d(e.get("throttle"))
    ths = _d(th.get("agents"))
    agents = {_m(str(a)): {"blocked": [_m(str(x)) for x in (v.get("blocked") or [])], "cooldown_until": _ts(v.get("cooldown_until"))
                           if v.get("cooldown_until") else None}
              for a, v in sorted(ths.items()) if isinstance(v, dict)}
    gaps = _d(e.get("history_gap"))
    backfill = _d(e.get("history_backfill"))
    return {"account": name, "file": True, "readable": True, "mode": _m(e.get("mode")),
            "history_gap": {_m(str(tf)): {"from": _ts(g.get("from")), "to": _ts(g.get("to")), "open_min": g.get("open_min")
                                          if isinstance(g.get("open_min"), (int, float)) else None}
                            for tf, g in sorted(gaps.items()) if isinstance(g, dict)},
            "history_backfill": json.loads(account_name(json.dumps(backfill, sort_keys=True))) if backfill else {},
            "throttle_enabled": th.get("enabled"), "throttle_error": _m(th.get("error")), "agents": agents}


def build(state: Path, accounts: list[str], since: pd.Timestamp | None, include_quarantined: bool) -> dict[str, Any]:
    raw = {a: read_engine(state, a) for a in accounts}
    return {"evidence_start_utc": EVIDENCE_START_UTC.isoformat(), "since": _iso(since),
            "include_quarantined": include_quarantined,
            "shadow": shadow_trades(state, since, include_quarantined),
            "cost_probes": probe_rows(state, accounts, since, include_quarantined,
                                      {a: e for a, e in raw.items() if e}),
            "engines": [engine_state(state, a, raw[a]) for a in accounts]}


def _f(v: float | None, signed: bool = False) -> str:
    return "?" if v is None else (f"{v:+.2f}" if signed else f"{v:.2f}")


def render(doc: dict[str, Any]) -> str:
    out = [f"shadow report (state files only); evidence starts {doc['evidence_start_utc']}"
           + (f"; since {doc['since']}" if doc["since"] else "")
           + ("; quarantined rows INCLUDED and marked" if doc["include_quarantined"] else "")]
    sh = doc["shadow"]
    if sh is None:
        out.append("\nshadow book: UNREADABLE")
    else:
        out.append(f"\nshadow book: {len(sh['trades'])} taken trade(s)"
                   + (f", {sh['hidden_quarantined']} quarantined (history_gap) hidden; --include-quarantined shows them"
                      if sh["hidden_quarantined"] else ""))
        agent = None
        for r in sh["trades"]:
            if r["agent"] != agent:
                agent = r["agent"]
                out.append(f"  {agent}")
            out.append(f"    {r['state']:6} {r['side']:5} in {r['entry_utc']} @ {_f(r['entry'])}  out {r['exit_utc'] or '-'} "
                       f"R {_f(r['r_gross'], True)} {r['exit_reason'] or '-'}"
                       + (f"  [quarantined: {r['quarantined']}]" if r["quarantined"] else ""))
    cp = doc["cost_probes"]
    if cp is None:
        out.append("\ncost-probe ledger: UNREADABLE")
    else:
        out.append(f"\ncost probes: {len(cp['probes'])} row(s)"
                   + (f", {cp['hidden_quarantined']} quarantined hidden" if cp["hidden_quarantined"] else ""))
        for r in cp["probes"]:
            out.append(f"  {r['account']} {r['agent']} {r['side']} {r['lots']:g} lots sent {r['sent_utc']} req {_f(r['requested'])} "
                       f"fill {_f(r['fill'])} spread {_f(r['spread_at_fill'])} comm {_f(r['commission'])} {r['currency'] or '?'} "
                       f"exit {r['exit_utc'] or '-'} @ {_f(r['exit'])} {r['exit_reason'] or '-'} "
                       f"round trip {'~' if r['round_trip_approx'] else ''}{_f(r['round_trip_usd_per_oz'])} USD/oz"
                       + (" (approx: today's fx)" if r["round_trip_approx"] else "")
                       + (f"  [quarantined: {r['quarantined']}]" if r["quarantined"] else ""))
    for e in doc["engines"]:
        out.append(f"\nengine {e['account']}:")
        if not e["file"]:
            out.append("  no engine state file (engine not run yet?)")
        elif not e["readable"]:
            out.append("  engine state file UNREADABLE")
        else:
            gaps = e["history_gap"]
            out.append("  history_gap: " + ("none (every evaluated window unbroken)" if not gaps else
                       "; ".join(f"{tf} {g['open_min']} min {g['from']} -> {g['to']}" for tf, g in sorted(gaps.items()))))
            if e["history_backfill"]:
                out.append(f"  history_backfill: {json.dumps(e['history_backfill'], sort_keys=True)}")
            if e["throttle_error"]:
                out.append(f"  guard state unavailable: {e['throttle_error']}")
            elif not e["throttle_enabled"]:
                out.append("  guard (throttle): off")
            for a, v in e["agents"].items():
                out.append(f"  guard {a}: " + ("blocked " + ", ".join(map(str, v["blocked"])) if v["blocked"] else "clear")
                           + (f" (cooldown until {v['cooldown_until']})" if v["cooldown_until"] else ""))
    return "\n".join(out)


def main(argv: list[str], enabled: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot run shadow-report", description="Shadow book, cost probes and engine "
                                 "history-gap/guard state, read-only (state files).")
    ap.add_argument("--since", help="only rows opened at or after this ISO timestamp (UTC if no zone)")
    ap.add_argument("--account", help="one enabled account id (default: every enabled account)")
    ap.add_argument("--include-quarantined", action="store_true",
                    help="also show trades opened before the evidence start (H-52), marked")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--state-dir", default="state")
    args = ap.parse_args(argv)
    since: pd.Timestamp | None = None
    if args.since is not None:
        try:
            since = pd.Timestamp(args.since)
            since = since.tz_localize("UTC") if since.tzinfo is None else since.tz_convert("UTC")
        except (ValueError, TypeError):
            print(f"--since {args.since!r} is not an ISO timestamp")
            return 2
    if enabled is None:
        from goldbot.ops.accounts import enabled_accounts
        enabled = [a.account_id for a in enabled_accounts()]
    state = Path(args.state_dir)
    if args.account is not None and not ACCOUNT_ID.match(args.account):
        print(f"invalid --account {account_name(args.account)[:40]!r}: letters, digits, '-' and '_' only")
        return 2
    if args.account is not None and args.account not in enabled and not (state / f"engine_{args.account}.json").exists():
        print(f"unknown account {account_name(args.account)!r}: enabled accounts are {', '.join(account_name(a) for a in enabled) or 'none'}")
        return 2
    doc = build(state, [args.account] if args.account is not None else enabled, since, args.include_quarantined)
    print(json.dumps(doc, indent=1) if args.json else render(doc))
    ok = doc["shadow"] is not None and doc["cost_probes"] is not None and all(e["readable"] for e in doc["engines"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
