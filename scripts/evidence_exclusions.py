"""Record the evidence exclusions of criterion v1 from the recorded dq quarantine spans (quant ruling 2026-10-26, blind;
docs/research/prereg-2026-10-25-high-volume.md Amendment 4; goldbot/research/evidence_exclusions.py).

Dry run by default: prints how many order ids would be void per account, kind and reason, and the spans they rest on.
--apply inserts them into aux.db `evidence_exclusions` (append-only; a re-run inserts nothing new). It prints ids,
accounts, arms and counts only: never an R or a P&L (blinding).

Every account with a recorded span needs its engine's restart time after the fix (`--reloaded ACCOUNT=ISO-UTC`): a
decision before it read the uncorrected bars. The nightly learner adds the lookback half of the criterion for new
candidates on its own; this script is the one that knows the restart times.

    sudo -n -u goldbot env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring /opt/goldbot/.venv/bin/python \\
        scripts/evidence_exclusions.py --reloaded icm-demo=2026-10-26T07:23:00Z --reloaded icm-probe=... [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.db.connection import Database
from goldbot.research import evidence_exclusions as ev


def _store(a: argparse.Namespace) -> Any:
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    return Store(a.data_root or load_settings().data_root)


def _hosts(a: argparse.Namespace) -> dict[str, str]:
    if a.hosts:
        return dict(h.split("=", 1) for h in a.hosts)
    from goldbot.ops import accounts
    return {acc.account_id: acc.broker for acc in accounts.enabled_accounts()}


def _pairs(items: list[str]) -> dict[str, pd.Timestamp]:
    out = {}
    for item in items:
        account, _, ts = item.partition("=")
        out[account] = pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="insert (default: dry run)")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--data-root", default=None, help="default: settings.data_root")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--hosts", action="append", default=[], help="ACCOUNT=BROKER (default: enabled accounts)")
    ap.add_argument("--reloaded", action="append", default=[], help="ACCOUNT=ISO-UTC engine restart after the fix")
    ap.add_argument("--now", default=None, help="decision time (default: now, UTC)")
    a = ap.parse_args(argv)
    now = pd.Timestamp(a.now).tz_convert("UTC") if a.now else pd.Timestamp.now("UTC")
    hosts, reloaded = _hosts(a), _pairs(a.reloaded)
    store = _store(a)
    spans = {acc: ev.recorded_spans(store, (acc, broker), symbol=a.symbol, start=now - pd.Timedelta(days=400), end=now)
             for acc, broker in hosts.items()}
    missing = sorted(acc for acc, s in spans.items() if s and acc not in reloaded)
    if missing:
        raise SystemExit(f"refused: --reloaded ACCOUNT=ISO-UTC is needed for {', '.join(missing)} (the engine "
                         "restart that loaded the corrected bars)")
    db = Database.open(Path(a.state_dir), "aux")
    rows = ev.compute(db, store, hosts=hosts, symbol=a.symbol, now=now, reloaded=reloaded)
    already = ev.excluded_ids(db, "cost_cells") | ev.excluded_ids(db, "posterior")
    new = [e for e in rows if e.client_order_id not in already]
    report: dict[str, Any] = {
        "mode": "apply" if a.apply else "dry-run", "criterion_version": ev.CRITERION_VERSION,
        "spans": {acc: sorted({ref for _, _, ref in s}) for acc, s in spans.items() if s},
        "reloaded": {k: v.isoformat() for k, v in reloaded.items()},
        "void": dict(Counter(f"{e.account_id}:{e.kind}:{e.reason}" for e in rows)),
        "new_ids": [{"client_order_id": e.client_order_id, "account_id": e.account_id, "kind": e.kind} for e in new],
    }
    report["inserted"] = ev.record(db, new) if a.apply else 0
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
