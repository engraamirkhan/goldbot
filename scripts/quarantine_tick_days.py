"""Repair the broker tick record after the short-page bug (fix/tick-record): quarantine recorded days that stop short of
their server day's end, then (optionally) one catch-up run with a longer backfill. Dry run by default.

Run on the brain as the service user, from the repo, after the fix is deployed (an older recorder would write the
same truncated day again):

  sudo -u goldbot .venv/bin/python scripts/quarantine_tick_days.py                      # dry run: what it would do
  sudo -u goldbot .venv/bin/python scripts/quarantine_tick_days.py --apply              # step 1: quarantine
  sudo -u goldbot .venv/bin/python scripts/quarantine_tick_days.py --apply --backfill   # + step 2 (gold closed only)

Step 1 (goldbot.data.ticks.quarantine_truncated_day) is idempotent: a day already moved, or one that reaches its close,
is left alone. Files move to state/quarantine/ticks/<account>/ with a manifest and an index backup; nothing is
deleted. Step 2 is one `record_ticks` run per account with `ticks.backfill_days` = --backfill-days and up to
--max-days days, through the scheduler's read-only broker; it refuses to start while gold trades (`market_quiet`), and
the run's own pacing stops it if the quiet window ends. Output names account labels and dates only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.data import ticks as tk  # noqa: E402

# the three days the short-page bug truncated (22:02 -> 00:12-01:00 UTC, 0 London/NY ticks), on both ICM accounts
DAYS = ("2026-10-12", "2026-10-13", "2026-10-19")
ACCOUNTS = ("icm-demo", "icm-probe")
REASON = "short copy_ticks page taken as the end of the day (fix/tick-record): ticks stop near 01:00 UTC"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--accounts", nargs="+", default=list(ACCOUNTS))
    ap.add_argument("--days", nargs="+", default=list(DAYS))
    ap.add_argument("--apply", action="store_true", help="change files (default: dry run)")
    ap.add_argument("--backfill", action="store_true", help="step 2: one catch-up record run per account")
    ap.add_argument("--backfill-days", type=int, default=14)
    ap.add_argument("--max-days", type=int, default=10)
    args = ap.parse_args(argv)
    now = pd.Timestamp.now("UTC")
    for acc in args.accounts:
        for day in args.days:
            print(tk.quarantine_truncated_day(args.state_dir, acc, day, reason=REASON, apply=args.apply, now=now))
    if not args.backfill:
        return 0
    return _backfill(args, now)


def _backfill(args: argparse.Namespace, now: pd.Timestamp) -> int:
    import time

    from goldbot.config import load_settings
    from goldbot.ops import accounts
    from goldbot.ops.services.scheduler import _job_broker
    cfg = load_settings().ticks.model_copy(update={"backfill_days": args.backfill_days,
                                                   "max_days_per_run": args.max_days})
    known = accounts.load_accounts(logins=False)
    rc = 0
    for label in args.accounts:
        acc = known.get(label)
        if acc is None:
            print(f"{label}: not in config/accounts.yaml, skipped")
            rc = 1
            continue
        have = tk.read_index(args.state_dir, label)
        due = [d.isoformat() for d in tk.days_due(now, acc.server_tz, backfill_days=args.backfill_days)
               if d.isoformat() not in have]
        if not args.apply:
            print(f"{label}: would pull {len(due)} missing days: {', '.join(due) or 'none'}")
            continue
        if not tk.market_quiet(pd.Timestamp.now("UTC")):
            print(f"{label}: gold is trading; run step 2 in the daily break (17:05-17:50 New York) or at the weekend")
            return 2
        broker = _job_broker(acc)
        if broker is None:
            print(f"{label}: no read-only broker connection, skipped")
            rc = 1
            continue
        try:
            out = tk.record_ticks(broker, args.state_dir, label, acc.symbol, acc.server_tz, cfg,
                                  clock=lambda: pd.Timestamp.now("UTC"), sleep=time.sleep)
        finally:
            if hasattr(broker, "shutdown"):
                broker.shutdown()
        print(f"{label}: written {out['written']}, behind {out['behind']}"
              + (f", deferred {out['deferred']}" if "deferred" in out else "")
              + (f", incomplete {len(out['incomplete'])}" if "incomplete" in out else ""))
    return rc


if __name__ == "__main__":
    sys.exit(main())
