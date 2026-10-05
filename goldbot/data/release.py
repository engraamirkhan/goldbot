"""Load the 1m bars the data-dukascopy workflow publishes on release `data-v1` into the store, and derive the
higher timeframes. Used by scripts/fetch_data_release.py and by the Saturday retrain on the VPS (best effort:
without network the retrain uses what the store already holds)."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pandas as pd

from goldbot.data.resample import resample_bars
from goldbot.data.store import Store

REPO = "engraamirkhan/goldbot"
HIGHER_TFS = ("15m", "1h", "4h", "1d", "1w")


def _headers(token: str | None) -> dict[str, str]:
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}


def list_assets(token: str | None = None, repo: str = REPO, tag: str = "data-v1") -> list[dict]:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", headers=_headers(token))
    return [a for a in json.loads(urllib.request.urlopen(req, timeout=60).read())["assets"] if a["name"].endswith(".parquet")]


def asset_year(name: str) -> int:
    return int(name.rsplit("_", 1)[1].split(".")[0])


def download(asset: dict, dest_dir: Path, token: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / asset["name"]
    if dest.exists() and dest.stat().st_size == asset["size"]:
        return dest   # unchanged since the last sync
    req = urllib.request.Request(asset["url"], headers={**_headers(token), "Accept": "application/octet-stream"})
    dest.write_bytes(urllib.request.urlopen(req, timeout=600).read())
    return dest


def load_into_store(store: Store, files: list[Path], *, source: str = "dukascopy") -> dict[str, int]:
    """Append 1m bars and their resampled timeframes; the store de-duplicates, so re-loading is idempotent."""
    if not files:
        return {}
    b1 = pd.concat([pd.read_parquet(f) for f in files]).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    counts = {"1m": store.append("bars_1m", b1.drop(columns=["source", "dq_flag"], errors="ignore"), source=source)}
    for tf in HIGHER_TFS:
        counts[tf] = store.append(f"bars_{tf}", resample_bars(b1, tf), source=source)
    return counts


def sync_release_bars(store: Store, *, years: tuple[int, int] | None = None, raw_dir: Path = Path("raw"),
                      token: str | None = None) -> dict[str, int]:
    token = token or os.environ.get("GH_TOKEN")
    files = [download(a, raw_dir, token) for a in list_assets(token)
             if years is None or years[0] <= asset_year(a["name"]) <= years[1]]
    return load_into_store(store, files)
