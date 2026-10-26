"""Void as evidence the S1A/S1B rows decided on a 00:00 UTC close since 2026-10-25 (reason `spec-bug-midnight`;
goldbot/research/evidence_exclusions.py `midnight_spec_bug`; docs/research/arms-2026-10-25.md section 1.1 note).

Dry run by default: prints how many order ids would be void per account and their ids. --apply inserts them into aux.db
`evidence_exclusions` (append-only; a re-run inserts nothing new). Ids, accounts and counts only: never an R multiple
or a P&L (blinding). Run on the brain once the S1 cut fix is deployed:

    sudo -n -u goldbot env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring /opt/goldbot/.venv/bin/python \\
        scripts/exclude_spec_bug_midnight.py [--apply]
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


def _utc(text: str) -> pd.Timestamp:
    t = pd.Timestamp(text)
    return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="record (default: dry run)")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--now", default=None, help="decision time (default: now, UTC)")
    a = ap.parse_args(argv)
    now = _utc(a.now) if a.now else pd.Timestamp.now("UTC")
    db = Database.open(Path(a.state_dir), "aux")
    found = ev.midnight_spec_bug(db, now=now)
    already = ev.excluded_ids(db, "n")
    new = [e for e in found if e.client_order_id not in already]
    report: dict[str, Any] = {
        "mode": "apply" if a.apply else "dry-run", "reason": ev.SPEC_BUG_MIDNIGHT,
        "criterion_version": ev.SPEC_BUG_MIDNIGHT_VERSION, "void": dict(Counter(e.account_id for e in found)),
        "new_ids": [{"client_order_id": e.client_order_id, "account_id": e.account_id} for e in new],
    }
    report["inserted"] = ev.record(db, new) if a.apply else 0
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
