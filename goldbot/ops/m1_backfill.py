"""Back-fill the broker's M1 (`bars_1m_broker`) by hand: `python -m goldbot.ops.run m1-backfill`.

The nightly `feed_reconcile` re-pulls holes over the last week (goldbot/data/crossfeed.py BACKFILL_MAX); this runs the
same back-fill over a longer span, by default READ_DAYS (100) calendar days, the span the nightly discovered-arm read
(goldbot/research/arm_integrity.py) reads before a decision: a 4h arm's window is about 42 trading days and the read
needs the broker's M1 to observe all of it. Read-only on the broker (get_bars_range, one UTC day per call, paced by
--pause-s); writes only minutes not stored yet, each with its write time as `available_utc`, so it is idempotent and
safe to run again or next to the scheduler. A day the store already holds (all but BACKFILL_SLACK open minutes) is
never asked. How far back a terminal serves M1 is bounded by its "Max bars in chart" setting and the broker's history:
`broker-history-depth <account> --tf 1m` shows it; what it could not serve stays in `max_open_hole`.

The holdout (2025-10-01 .. 2026-09-30, goldbot/data/holdout_guard.py) is never asked for (quant R10: "m1_backfill
--days N and 'to present' pulls put holdout minutes into stores"): the span is split around it (`pull_spans`), and a
span that lies wholly inside it is refused.

  python -m goldbot.ops.run m1-backfill (--all | <account_id> ...) [--days 100] [--pause-s 1] [--budget-s S]
                                        [--data-root DIR]

Exit 0 when every account ran without a failed call, 1 otherwise, 2 on a refused request.
"""
from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd

DEFAULT_DAYS = 100        # = research.arm_integrity.READ_DAYS (tests hold them equal; no research import here)


# seams for tests (the keyring, the bridge and the brain's store are not reachable there)
def _accounts(ids: list[str] | None) -> list[Any]:
    from goldbot.ops import accounts
    enabled = accounts.enabled_accounts()
    if ids is None:
        return list(enabled)
    known = accounts.load_accounts()
    return [known[i] for i in ids if i in known]


def _store(root: str | None) -> Any:
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    return Store(root or load_settings().data_root)


def _broker(acc: Any) -> Any:
    # the nightly feed_reconcile's read-only broker (the account's bridge)
    from goldbot.ops.services.scheduler import _job_broker
    return _job_broker(acc)


def _now() -> pd.Timestamp:
    return pd.Timestamp.now("UTC")


def _report(account_id: str, r: Any) -> int:
    """Print one span's result as it finishes; 1 when it failed or stopped short, else 0."""
    first = "-" if r.first_stored is None else f"{r.first_stored:%Y-%m-%d %H:%M}"
    print(f"{account_id}: {r.minutes_added} minutes added; {r.chunks_pulled} of {r.chunks_needed} days with holes "
          f"pulled ({r.chunks} days checked); first stored minute {first}; {r.open_missing} open minutes still "
          f"missing, longest hole {r.max_open_hole} min (the arm read needs <= 120)"
          + (f"; stopped: {r.stopped}" if r.stopped else "")
          + (f"; {len(r.errors)} failed calls, first: {r.errors[0]}" if r.errors else ""))
    return 1 if r.errors or r.stopped in ("no_range", "errors") else 0


def main(argv: list[str]) -> int:
    from goldbot.data.crossfeed import backfill_broker_m1
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run m1-backfill",
                                 description="Back-fill the broker's M1 (bars_1m_broker) per account, read-only.")
    ap.add_argument("accounts", nargs="*", help="account ids (config/accounts.yaml)")
    ap.add_argument("--all", action="store_true", help="every enabled account")
    ap.add_argument("--days", type=float, default=DEFAULT_DAYS, help=f"calendar days back from now ({DEFAULT_DAYS})")
    ap.add_argument("--pause-s", type=float, default=1.0, help="seconds between broker calls (1)")
    ap.add_argument("--budget-s", type=float, default=None, help="seconds per account before it stops (none)")
    ap.add_argument("--data-root", default=None, help="the store (default: settings data_root)")
    a = ap.parse_args(argv)
    if a.days <= 0 or (not a.all and not a.accounts):
        print("refused: give --all or account ids, and --days > 0")
        return 2
    accs = _accounts(None if a.all else list(a.accounts))
    if not accs:
        print("refused: no such enabled account")
        return 2
    from goldbot.data import holdout_guard
    end = _now().floor("min")
    start = end - pd.Timedelta(days=a.days)
    spans = holdout_guard.pull_spans(start, end)
    if not spans:
        print(f"refused: {start:%Y-%m-%d} .. {end:%Y-%m-%d} lies inside the sealed holdout")
        return 2
    if spans != [(start, end)]:
        print("holdout minutes are never pulled: " + ", ".join(f"{s:%Y-%m-%d %H:%M} .. {e:%Y-%m-%d %H:%M}"
                                                                 for s, e in spans))
    store = _store(a.data_root)
    code = 0
    for acc in accs:
        broker = None
        t0 = time.monotonic()
        try:
            broker = _broker(acc)
            if broker is None:
                print(f"{acc.account_id}: skipped, no read-only broker connection (bridge endpoint in the keyring)")
                code = 1
                continue
            for s0, e0 in spans:             # one --budget-s per account, shared by its spans
                left = None if a.budget_s is None else max(0.0, a.budget_s - (time.monotonic() - t0))
                r = backfill_broker_m1(store, broker, acc.account_id, acc.symbol, start=s0, end=e0,
                                       budget_s=left, pause_s=a.pause_s)
                code = max(code, _report(acc.account_id, r))
        except Exception as exc:          # one account's failure does not stop the others
            print(f"{acc.account_id}: failed, {type(exc).__name__}: {exc}")
            code = 1
            continue
        finally:
            if broker is not None and hasattr(broker, "shutdown"):
                broker.shutdown()
    return code
