"""Cross-feed survival check (design D23/F10: "every signal must survive on both feeds"; "a signal that only works on
broker-fed bars is treated as a feed artefact and dropped").

Runs the rule-only screen of one specialist configuration on Dukascopy 1m bars and on the broker's own M1
(`bars_1m_broker`, accumulated by the VPS's daily feed_reconcile job) over their common period outside the holdout,
and applies the survival rule in goldbot/data/crossfeed.py. Until enough broker history exists the verdict is
"insufficient_overlap", which is not a pass. Exit code 0 only when the configuration survives.

Not a trial: it can only discard a configuration that was already screened and recorded, never select one.

  python scripts/crossfeed_check.py --specialist session_open --account icm-demo [--variant '{"target_atr": 2.0}'] \
      [--store data] [--duka-source dukascopy] [--from 2026-01-01] [--report crossfeed.md] [--json crossfeed.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.crossfeed import BROKER_TABLE, survival_check, survival_lines  # noqa: E402
from goldbot.data.resample import BAR_COLUMNS  # noqa: E402
from goldbot.data.store import Store  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402


def _clean(b: pd.DataFrame) -> pd.DataFrame:
    if b.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    if "dq_flag" in b.columns:
        b = b[~b["dq_flag"].fillna("").astype(str).str.contains("error:")]
    return b.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc").reset_index(drop=True)[BAR_COLUMNS]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--specialist", required=True, choices=sorted(SPECIALISTS))
    ap.add_argument("--variant", default="{}", help="JSON config overrides of the screened configuration")
    ap.add_argument("--account", required=True, help="account whose broker M1 is the second feed")
    ap.add_argument("--store", default=None, help="data root (default: settings data_root)")
    ap.add_argument("--duka-source", default="dukascopy")
    ap.add_argument("--from", dest="start", default=None, help="earliest bar (UTC date)")
    ap.add_argument("--extra-cost-usd", type=float, default=0.0, help="net labels only; the verdict uses gross R")
    ap.add_argument("--report", default=None)
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)
    settings = load_settings()
    store = Store(args.store or settings.data_root)
    spec = SPECIALISTS[args.specialist](**json.loads(args.variant))
    broker = _clean(store.read(BROKER_TABLE, source=args.account, start=args.start))
    duka = pd.DataFrame(columns=BAR_COLUMNS)
    if not broker.empty:                  # only the broker's period can overlap: read no more Dukascopy than that
        duka = _clean(store.read("bars_1m", source=args.duka_source, start=broker["ts_utc"].iloc[0],
                                 end=broker["ts_utc"].iloc[-1] + pd.Timedelta(minutes=1)))
    res = survival_check(spec, duka, broker, holdout=settings.research.holdout_window(),
                         extra_cost_usd=args.extra_cost_usd)
    label = f"{args.specialist} {args.variant if args.variant != '{}' else 'defaults'} vs {args.account}"
    text = "\n".join(survival_lines(label, res))
    print(text)
    print(f"broker M1 bars: {len(broker):,}; Dukascopy bars on that period: {len(duka):,}")
    if args.report:
        Path(args.report).write_text(text)
    if args.json_out:
        Path(args.json_out).write_text(res.model_dump_json(indent=1))
    return 0 if res.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
