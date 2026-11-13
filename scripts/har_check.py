"""N-3 enable checks (backlog 145, docs/research/prereg-2026-11-07-n3-har.md §2): read-only, nothing is written.

Run on the brain as the service user, from the repo, after the refilled Dukascopy year reached the store
(`scripts/local_bars.sh push` on the Mac, then the Saturday `sync_release` or a manual load of the inbox):

  sudo -u goldbot .venv/bin/python scripts/har_check.py overlap     # median ln(ICM/Duka) of daily TR, W, w
  sudo -u goldbot .venv/bin/python scripts/har_check.py dry-run     # the row har_days would write tonight

`overlap` exits 0 on `pass` (|median ln w| <= 0.02 on >= 20 overlap days), 1 on `fail` or `insufficient`.
`dry-run` exits 0 when an `ok` row could be written (the gap, freshness, fit and trailing checks all hold), 1 with the
reason otherwise. Neither writes har_days: a dry-run `ok` row would register N-3 in the engine and could never be
replaced (first row kept), so the enable check never goes through `run_job`. Output: account labels, dates, numbers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.research import har_abstention as har  # noqa: E402


def run(ctx: Any, cmd: str, slot: pd.Timestamp) -> tuple[int, dict[str, Any]]:
    """(exit code, report) of one check against `ctx` (settings, store, accounts, state_dir)."""
    cfg = ctx.settings.research.har_abstention
    if cmd == "overlap":
        acc = next((a for a in ctx.accounts if a.broker == cfg.broker and a.enabled), None)
        if acc is None:
            return 1, {"check": cmd, "error": f"no enabled {cfg.broker} account"}
        got = har.measure_overlap(ctx.store, broker=acc.broker, account_id=acc.account_id, symbol=acc.symbol,
                                  cut=har.cut_off(slot))
        return (0 if got.status == "pass" else 1), {"check": cmd, "account": acc.account_id,
                                                     **json.loads(got.model_dump_json())}
    try:
        row = har.dry_run(ctx, slot)
    except har.HarUnavailable as exc:
        return 1, {"check": cmd, "status": "unavailable", "reason": str(exc)}
    return 0, {"check": cmd, **json.loads(row.model_dump_json())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("check", choices=("overlap", "dry-run"))
    ap.add_argument("--slot", default=None, help="UTC time of the run (default: now); the cut-off is the 22:00 UTC "
                                                 "close at or before it")
    args = ap.parse_args(argv)
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.ops import accounts
    settings = load_settings()
    ctx = SimpleNamespace(settings=settings, store=Store(settings.data_root), state_dir=Path("state"),
                          accounts=accounts.enabled_accounts(logins=False))
    slot = pd.Timestamp(args.slot, tz="UTC") if args.slot else pd.Timestamp.now(tz="UTC")
    code, report = run(ctx, args.check, slot)
    print(json.dumps(report, indent=2, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
