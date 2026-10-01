"""Load raw ticks or MT5 bar exports into the point-in-time store and build all timeframes.

  python scripts/build_bars.py --source dukascopy raw/dukascopy/*.csv
  python scripts/build_bars.py --source icm --server-tz Europe/Athens raw/mt5_export/XAUUSD_M1.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.config import load_settings  # noqa: E402
from goldbot.data.loaders import load_dukascopy_ticks_csv, load_mt5_bars_csv  # noqa: E402
from goldbot.data.quality import check_bars, events_frame  # noqa: E402
from goldbot.data.resample import resample_bars, ticks_to_1m  # noqa: E402
from goldbot.data.store import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--source", required=True, help="dukascopy | icm | vantage")
    ap.add_argument("--server-tz", default="Europe/Athens")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    st = load_settings()
    store = Store(args.data_root or st.data_root)
    frames = []
    for f in args.files:
        if args.source == "dukascopy":
            frames.append(ticks_to_1m(load_dukascopy_ticks_csv(f)))
        else:
            frames.append(load_mt5_bars_csv(f, server_tz=args.server_tz))
        print(f"loaded {f}: {len(frames[-1]):,} 1m bars")
    b1 = pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    b1, dq = check_bars(b1)
    print(f"quality: {len(dq)} events; errors: {sum(e.severity == 'error' for e in dq)}")
    if dq:
        store.append("dq_events", events_frame(dq), source=args.source)
    store.append("bars_1m", b1, source=args.source)
    for tf in ("15m", "1h", "4h", "1d", "1w"):
        b = resample_bars(b1, tf)
        store.append(f"bars_{tf}", b, source=args.source)
        print(f"{tf}: {len(b):,} bars")


if __name__ == "__main__":
    main()
