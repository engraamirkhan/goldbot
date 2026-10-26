"""Record the engine restarts after a dq correction and the evidence exclusions of criterion v1 (quant ruling
2026-10-26, blind; docs/research/prereg-2026-10-25-high-volume.md Amendment 4; goldbot/research/evidence_exclusions.py).

Dry run by default: prints how many order ids would be void per account, kind and reason, and the spans they rest on.
--apply records, for every account with a recorded span, C (`--corrected`, when the corrected bars became visible in
the store) and R (`--reloaded ACCOUNT=...`, the account's first engine warm start at or after C) in aux.db
`evidence_reloads`, then inserts the exclusions into `evidence_exclusions`. Both are append-only; a re-run inserts
nothing new. An R before C is refused (that restart loaded the bad bars). It prints ids, accounts and counts only:
never an R multiple or a P&L (blinding).

Brain, 2026-10-26 span (C 15:07:59Z; both engines' first warm start after it 15:09:35Z):

    sudo -n -u goldbot env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring /opt/goldbot/.venv/bin/python \\
        scripts/evidence_exclusions.py --corrected 2026-10-26T15:07:59Z \\
        --reloaded icm-demo=2026-10-26T15:09:35Z --reloaded icm-probe=2026-10-26T15:09:35Z [--apply]
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
    return {acc: _utc(ts) for acc, _, ts in (i.partition("=") for i in items)}


def _utc(text: str) -> pd.Timestamp:
    t = pd.Timestamp(text)
    return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="record (default: dry run)")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--data-root", default=None, help="default: settings.data_root")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--hosts", action="append", default=[], help="ACCOUNT=BROKER (default: enabled accounts)")
    ap.add_argument("--corrected", default=None, help="ISO UTC: when the corrected bars became visible (C)")
    ap.add_argument("--reloaded", action="append", default=[],
                    help="ACCOUNT=ISO-UTC: the account's first engine warm start at or after C (R)")
    ap.add_argument("--now", default=None, help="decision time (default: now, UTC)")
    a = ap.parse_args(argv)
    now = _utc(a.now) if a.now else pd.Timestamp.now("UTC")
    hosts, reloaded = _hosts(a), _pairs(a.reloaded)
    store = _store(a)
    spans = ev.spans_of(store, hosts, symbol=a.symbol, now=now)
    missing = sorted(acc for acc in spans if acc not in reloaded)
    if missing:
        raise SystemExit(f"refused: --reloaded ACCOUNT=ISO-UTC is needed for {', '.join(missing)} (its first engine "
                         "warm start on the corrected store bars)")
    if spans and a.corrected is None:
        raise SystemExit("refused: --corrected ISO-UTC (when the corrected bars became visible) is needed")
    corrected = _utc(a.corrected) if a.corrected else None
    early = sorted(acc for acc in spans if corrected is not None and reloaded[acc] < corrected)
    if early:
        raise SystemExit(f"refused: the restart of {', '.join(early)} is before the correction "
                         f"{corrected.isoformat() if corrected is not None else ''}: it loaded the bad bars")
    db = Database.open(Path(a.state_dir), "aux")
    recorded = []
    if a.apply and corrected is not None:
        for acc, ss in spans.items():
            for s in ss:
                if ev.record_reload(db, account_id=acc, span=s[2], corrected=corrected, reloaded=reloaded[acc],
                                    now=now):
                    recorded.append(f"{acc} {s[2]}")
    planned = {} if a.apply or corrected is None else \
        {(acc, s[2]): (corrected, reloaded[acc]) for acc, ss in spans.items() for s in ss}   # dry run: as if recorded
    found = ev.screen(db, store, hosts=hosts, symbol=a.symbol, now=now, planned=planned)
    already = ev.excluded_ids(db, "cost_cells") | ev.excluded_ids(db, "posterior")
    new = [e for e in found.exclusions if e.client_order_id not in already]
    report: dict[str, Any] = {
        "mode": "apply" if a.apply else "dry-run", "criterion_version": ev.CRITERION_VERSION,
        "spans": {acc: [s[2] for s in ss] for acc, ss in spans.items()},
        "corrected": corrected.isoformat() if corrected is not None else None,
        "reloaded": {k: v.isoformat() for k, v in reloaded.items()}, "reloads_recorded": recorded,
        "void": dict(Counter(f"{e.account_id}:{e.kind}:{e.reason}" for e in found.exclusions)),
        "new_ids": [{"client_order_id": e.client_order_id, "account_id": e.account_id, "kind": e.kind} for e in new],
    }
    report["inserted"] = ev.record(db, new) if a.apply else 0
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
