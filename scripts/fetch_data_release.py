"""Fetch the 1m bar Parquet files published by the data-dukascopy workflow (release tag data-v1) into the
store. Works anywhere github.com is reachable. Usage: python scripts/fetch_data_release.py [--years 2015 2026]"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.resample import resample_bars  # noqa: E402
from goldbot.data.store import Store  # noqa: E402

REPO = "engraamirkhan/goldbot"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs=2, type=int, default=None)
    ap.add_argument("--token", default=None, help="GitHub token for a private repo (or GH_TOKEN env)")
    args = ap.parse_args()
    import os
    token = args.token or os.environ.get("GH_TOKEN")
    hdr = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    rel = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/tags/data-v1", headers=hdr)).read())
    store = Store(load_settings()["data_root"])
    frames = []
    for a in rel["assets"]:
        name = a["name"]
        if not name.endswith(".parquet"):
            continue
        year = int(name.rsplit("_", 1)[1].split(".")[0])
        if args.years and not (args.years[0] <= year <= args.years[1]):
            continue
        print("fetching", name, f"{a['size']/1e6:.1f} MB")
        req = urllib.request.Request(a["url"], headers={**hdr, "Accept": "application/octet-stream"})
        dest = Path("raw") / name
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(urllib.request.urlopen(req).read())
        frames.append(pd.read_parquet(dest))
    if not frames:
        print("no assets yet; run the data-dukascopy workflow first")
        return
    b1 = pd.concat(frames).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    store.append("bars_1m", b1.drop(columns=["source", "dq_flag"], errors="ignore"), source="dukascopy")
    for tf in ("15m", "1h", "4h", "1d", "1w"):
        b = resample_bars(b1, tf)
        store.append(f"bars_{tf}", b, source="dukascopy")
        print(tf, len(b))
    print("done:", len(b1), "1m bars in store")


if __name__ == "__main__":
    main()
