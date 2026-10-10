"""Load the 1m bars the data-dukascopy workflow publishes on release `data-v1` into the store, and derive the
higher timeframes. Used by scripts/fetch_data_release.py and by the Saturday retrain on the VPS (best effort:
without network the retrain uses what the store already holds). The macro series the data-macro workflow publishes
on release `macro-v1` load the same way into the `macro` table. `upload_asset` is the VPS's way back: it puts the
canonical broker's measured cost table on release `costs-v1` (goldbot/ops/jobs.py `publish_costs`)."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from goldbot.data.resample import resample_bars
from goldbot.data.store import Store

log = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ uploads (VPS side, github-token from the keyring)
def _api(url: str, token: str, *, method: str = "GET", data: bytes | None = None, ctype: str | None = None) -> object:
    hdr = _headers(token) | ({"Content-Type": ctype} if ctype else {})
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    return json.loads(body) if body else None


def upload_asset(token: str, tag: str, name: str, data: bytes, *, title: str, notes: str, repo: str = REPO,
                 ctype: str = "application/json") -> str:
    """Replace asset `name` on release `tag` (created, not marked latest, when missing). Returns the asset's
    download URL. Network errors propagate."""
    try:
        rel = _api(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", token)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        rel = _api(f"https://api.github.com/repos/{repo}/releases", token, method="POST", ctype="application/json",
                   data=json.dumps({"tag_name": tag, "name": title, "body": notes, "make_latest": "false"}).encode())
    if not isinstance(rel, dict):
        raise ValueError(f"unexpected GitHub response for release {tag}")
    for a in rel.get("assets", []):
        if a["name"] == name:
            _api(f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}", token, method="DELETE")
    up = _api(rel["upload_url"].split("{")[0] + f"?name={name}", token, method="POST", data=data, ctype=ctype)
    return str(up.get("browser_download_url", "")) if isinstance(up, dict) else ""


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


# ------------------------------------------------------------------ macro series (data-macro workflow, release macro-v1)
MACRO_TAG = "macro-v1"
MACRO_SOURCE = "fred"
MACRO_COLUMNS = ("series", "value_date", "value", "vintage", "available_utc")


def read_macro_files(path: Path) -> pd.DataFrame:
    """The macro release as one long frame (series, series_id, value_date, value, vintage, available_utc, ts_utc):
    `path` is a Parquet file or a folder of them. Empty when nothing is there; a file without the point-in-time
    columns is refused (nothing may join on a nominal date)."""
    files = [path] if path.is_file() else sorted(path.glob("*.parquet")) if path.is_dir() else []
    if not files:
        return pd.DataFrame(columns=list(MACRO_COLUMNS))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    missing = [c for c in MACRO_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"macro release {path} lacks {missing}")
    df["available_utc"] = pd.to_datetime(df["available_utc"], utc=True)
    df["ts_utc"] = df["available_utc"]
    return df.drop_duplicates(["series", "value_date", "vintage"], keep="last").sort_values("available_utc").reset_index(drop=True)


def load_macro_into_store(store: Store, macro: pd.DataFrame) -> int:
    """Append the macro rows to the store's `macro` table (source fred); de-duplicated on series, value_date, vintage."""
    return store.append("macro", macro, source=MACRO_SOURCE)


def store_macro(store: Store) -> pd.DataFrame:
    """The macro rows the store holds (what research and the VPS pass as ctx["macro"])."""
    df = store.read("macro", source=MACRO_SOURCE)
    if df.empty:
        return df
    df["available_utc"] = pd.to_datetime(df["available_utc"], utc=True)
    return df.drop(columns=[c for c in ("source", "symbol", "year", "month") if c in df.columns])


def sync_release_macro(store: Store, *, raw_dir: Path = Path("raw-macro"), token: str | None = None) -> int:
    """Download the macro release and load it into the store. 0 when the release does not exist yet (the data-macro
    workflow has not run); other network errors propagate."""
    token = token or os.environ.get("GH_TOKEN")
    try:
        assets = list_assets(token, tag=MACRO_TAG)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 0
        raise
    files = [download(a, raw_dir, token) for a in assets]
    return sum(load_macro_into_store(store, read_macro_files(f)) for f in files)


def sync_release(store: Store, *, token: str | None = None) -> dict[str, int]:
    """Bars from data-v1 and macro series from macro-v1 (the Saturday retrain's refresh). A macro failure is logged
    and leaves the bar refresh standing."""
    counts = sync_release_bars(store, token=token)
    try:
        counts["macro"] = sync_release_macro(store, token=token)
    except Exception as exc:     # macro is optional input; bars are what the retrain needs
        log.warning("macro release sync failed: %s", exc)
    return counts
