"""`goldbot run fill-count [--since ISO] [--account <id>] [--json]`: demo fills per account per UTC day against the
owner's target (300 by Friday 2026-10-23), split by kind: own entry (the engine's own trades), mirror probe (a cost
probe mirroring a shadow entry) and scheduled probe.

Read-only, like shadow-report (whose helpers it reuses): the closed-trade record files and the cost-probe ledger
(aux.db opened `mode=ro` + `query_only`, schema version checked) and nothing else; no bridge call, no write.
A fill is counted on its entry day (UTC): a closed trade by `entry_utc` (else `exit_utc`), a probe by `sent_utc`, and
only a probe that filled (fill price or position id). Probe trades also sit in the closed-trade files under the
`cost_probe:` tag; they are counted once, as probes. A position still open is in neither source yet, so the count is
a floor until it closes. A probe is "scheduled" when its client order id or basis says so, else "mirror".
The cumulative count and the progress always run from CAMPAIGN_START_UTC; --since only trims the daily rows.
Accounts print by config name (entry_text.account_name): a login number is never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.engine.cost_probe import is_probe
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.ops.shadow_report import ACCOUNT_ID, _read_ledger
from goldbot.telegram.entry_text import account_name

__all__ = ["main"]

CAMPAIGN_START_UTC = pd.Timestamp("2026-10-19 13:49", tz="UTC")
TARGET = 300
DEADLINE_UTC = pd.Timestamp("2026-10-24", tz="UTC")      # end of Friday 2026-10-23
KINDS = ("own_entry", "mirror_probe", "scheduled_probe")


def _probe_kind(client_order_id: str, basis: str | None) -> str:
    return "scheduled_probe" if "sched" in client_order_id.lower() or "sched" in (basis or "").lower() else "mirror_probe"


def collect(state: Path, accounts: list[str]) -> tuple[list[tuple[str, str, pd.Timestamp]], list[str]]:
    """(account, kind, UTC time) of every fill since the campaign start, and the sources that could not be read."""
    fills: list[tuple[str, str, pd.Timestamp]] = []
    bad: list[str] = []
    trades, err = load_closed_trades(state)
    if err:
        bad.append(f"closed trades: {account_name(err)}")
    for t in trades:
        ts = t.entry_utc or t.exit_utc
        if t.account_id in accounts and not is_probe(t.agent_id) and ts >= CAMPAIGN_START_UTC:
            fills.append((t.account_id, "own_entry", pd.Timestamp(ts)))
    if (state / "aux.db").exists():
        try:
            recs = _read_ledger(state)
        except Exception:               # noqa: BLE001 - reporting only
            bad.append("cost-probe ledger: unreadable")
            recs = []
        for r in recs:
            if (r.account_id in accounts and r.sent_utc >= CAMPAIGN_START_UTC
                    and (r.fill_price is not None or r.position_id is not None)):
                fills.append((r.account_id, _probe_kind(r.client_order_id, r.basis), pd.Timestamp(r.sent_utc)))
    return fills, bad


def build(state: Path, accounts: list[str], since: pd.Timestamp | None, now: pd.Timestamp) -> dict[str, Any]:
    fills, bad = collect(state, accounts)
    days: dict[tuple[str, str], dict[str, int]] = {}
    totals = dict.fromkeys(KINDS, 0)
    for acc, kind, ts in fills:
        totals[kind] += 1
        if since is None or ts >= since:
            days.setdefault((account_name(acc), ts.strftime("%Y-%m-%d")), dict.fromkeys(KINDS, 0))[kind] += 1
    rows = [{"account": a, "day": d, **c, "total": sum(c.values())} for (a, d), c in sorted(days.items(), key=lambda x: (x[0][1], x[0][0]))]
    cum = sum(totals.values())
    elapsed = max((now - CAMPAIGN_START_UTC) / pd.Timedelta(days=1), 1 / 24)
    left = max((DEADLINE_UTC - now) / pd.Timedelta(days=1), 0.0)
    span = (DEADLINE_UTC - CAMPAIGN_START_UTC) / pd.Timedelta(days=1)
    return {"campaign_start_utc": CAMPAIGN_START_UTC.isoformat(), "now_utc": now.floor("s").isoformat(), "since": since.isoformat() if since is not None else None,
            "daily": rows, "cumulative": {**totals, "total": cum},
            "progress": {"target": TARGET, "deadline_utc": DEADLINE_UTC.isoformat(), "pct": round(100 * cum / TARGET, 1),
                         "remaining": max(TARGET - cum, 0), "days_left": round(left, 2),
                         "need_per_day": round(max(TARGET - cum, 0) / left, 1) if left > 0 else None,
                         "pace_per_day": round(cum / elapsed, 1), "projected": round(cum / elapsed * span)},
            "unreadable": bad}


def render(doc: dict[str, Any]) -> str:
    out = [f"fill count (state files only) since {doc['campaign_start_utc']}; now {doc['now_utc']}",
           f"{'day':10} {'account':14} {'own':>5} {'mirror':>7} {'sched':>6} {'total':>6}"]
    out += [f"{r['day']:10} {r['account']:14} {r['own_entry']:5d} {r['mirror_probe']:7d} {r['scheduled_probe']:6d} {r['total']:6d}"
            for r in doc["daily"]] or ["(no fills)"]
    c, p = doc["cumulative"], doc["progress"]
    out.append(f"cumulative: {c['total']} (own {c['own_entry']}, mirror {c['mirror_probe']}, scheduled {c['scheduled_probe']})")
    out.append(f"target {p['target']} by {p['deadline_utc']}: {p['pct']}%, {p['remaining']} to go, {p['days_left']} d left, "
               f"need {p['need_per_day'] if p['need_per_day'] is not None else '-'}/day, pace {p['pace_per_day']}/day, "
               f"projected {p['projected']}" + (" ON TRACK" if p["projected"] >= p["target"] else " BEHIND"))
    out += [f"UNREADABLE {u}" for u in doc["unreadable"]]
    return "\n".join(out)


def main(argv: list[str], enabled: list[str] | None = None, now: pd.Timestamp | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot run fill-count", description="Demo fills per account per day against the "
                                 "300-by-Friday target, read-only (state files).")
    ap.add_argument("--since", help="only daily rows at or after this ISO timestamp (UTC if no zone); cumulative is unaffected")
    ap.add_argument("--account", help="one enabled account id (default: every enabled account)")
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
    if args.account is not None and not ACCOUNT_ID.match(args.account):
        print(f"invalid --account {account_name(args.account)[:40]!r}: letters, digits, '-' and '_' only")
        return 2
    if args.account is not None and args.account not in enabled:
        print(f"unknown account {account_name(args.account)!r}: enabled accounts are {', '.join(account_name(a) for a in enabled) or 'none'}")
        return 2
    doc = build(Path(args.state_dir), [args.account] if args.account is not None else enabled, since,
                now if now is not None else pd.Timestamp.now(tz="UTC"))
    print(json.dumps(doc, indent=1) if args.json else render(doc))
    return 1 if doc["unreadable"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
