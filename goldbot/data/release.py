"""Load the 1m bars the data-dukascopy workflow publishes on release `data-v1` into the store, and derive the
higher timeframes. Used by scripts/fetch_data_release.py and by the Saturday retrain on the VPS (best effort:
without network the retrain uses what the store already holds). The macro series the data-macro workflow publishes
on release `macro-v1` load the same way into the `macro` table, and the COT/GLD positioning rows the data-positioning
workflow publishes on release `positioning-v1` into the `positioning` table. `upload_asset` is the VPS's way back: it puts the
canonical broker's measured cost table on release `costs-v1` (goldbot/ops/jobs.py `publish_costs`)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from goldbot.data.resample import BAR_COLUMNS, resample_bars
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


def download_release(tag: str, raw_dir: Path, *, token: str | None = None,
                     years: tuple[int, int] | None = None) -> list[Path]:
    """Download release `tag`'s Parquet assets into `raw_dir` without loading the store (for a workflow that reads
    the files directly, e.g. scripts/count_owed_events.py). `years` filters yearly bar files (data-v1). [] when the
    release does not exist yet; other network errors propagate. Re-running skips unchanged files (idempotent)."""
    token = token or os.environ.get("GH_TOKEN")
    try:
        assets = list_assets(token, tag=tag)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return [download(a, raw_dir, token) for a in assets
            if years is None or years[0] <= asset_year(a["name"]) <= years[1]]


BAR_FILE_GLOB = "xauusd_1m_dukascopy_*.parquet"


def bar_file_digests(folder: Path, from_year: int | None = None, to_year: int | None = None) -> dict[str, str]:
    """{file name: sha256} of the yearly 1m files (release data-v1) in `folder` for `from_year`..`to_year` (None: no
    bound). A queued pre-registration pins the research window's digests (years up to research.holdout_from's) in its
    plan, and the runners verify them before any look (BACKLOG 50; `bar_digest_refusal`)."""
    out: dict[str, str] = {}
    for f in sorted(Path(folder).glob(BAR_FILE_GLOB)):
        year = asset_year(f.name)
        if (from_year is None or year >= from_year) and (to_year is None or year <= to_year):
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[f.name] = h.hexdigest()
    return out


def bar_digest_refusal(pinned: dict[str, str], folder: Path, from_year: int, to_year: int) -> str | None:
    """Why the yearly files a run reads from `folder` (`from_year`..`to_year`, the research window) are not exactly the
    `pinned` ones, naming each file and both digests; None when they are. A file pinned but not read (another
    --from-year), read but not pinned, missing, or with another digest (a re-pull) is refused."""
    now = bar_file_digests(folder, from_year, to_year)
    out = []
    for name in sorted(set(pinned) | set(now)):
        if name not in now:
            where = "missing from the folder" if not (Path(folder) / name).exists() else \
                f"not read by this run (years {from_year}-{to_year})"
            out.append(f"{name}: pinned sha256 {pinned[name]}, {where}")
        elif name not in pinned:
            out.append(f"{name}: sha256 {now[name]} read by this run but not pinned")
        elif now[name] != pinned[name]:
            out.append(f"{name}: pinned sha256 {pinned[name]}, now {now[name]}")
    return "; ".join(out) or None


def read_bar_files(folder: Path, from_year: int, to_year: int) -> pd.DataFrame:
    """The yearly 1m files (xauusd_1m_dukascopy_<year>.parquet, release data-v1) for `from_year`..`to_year` as one
    frame in time order (BAR_COLUMNS, timestamps UTC), de-duplicated on ts_utc; bars with an error-level data-quality
    flag are dropped (quarantined at ingest, never used). FileNotFoundError when no file matches. The one bar loader
    research shares (scripts/research_pass.py, scripts/count_owed_events.py)."""
    frames = []
    for f in sorted(Path(folder).glob(BAR_FILE_GLOB)):
        year = int(f.stem.rsplit("_", 1)[1])
        if from_year <= year <= to_year:
            frames.append(pd.read_parquet(f))
    if not frames:
        raise FileNotFoundError(f"no xauusd_1m_dukascopy_<year>.parquet files for {from_year}-{to_year} in {folder}")
    b = pd.concat(frames, ignore_index=True)
    b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
    b["visible_at"] = pd.to_datetime(b["visible_at"], utc=True)
    flags = b["dq_flag"].fillna("").astype(str) if "dq_flag" in b else pd.Series("", index=b.index)
    b = b[~flags.str.startswith("error")]
    return b.drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)[BAR_COLUMNS]


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


# ------------------------------------------------------------------ positioning (data-positioning workflow, release positioning-v1)
POSITIONING_TAG = "positioning-v1"
POSITIONING_COLUMNS = ("series", "value_date", "value", "vintage", "available_utc", "source")


def read_positioning_files(path: Path) -> pd.DataFrame:
    """The positioning release as one long frame (goldbot/data/positioning.py COLUMNS): `path` is a Parquet file or a
    folder of them. Empty when nothing is there; a file without the point-in-time columns is refused."""
    files = [path] if path.is_file() else sorted(path.glob("*.parquet")) if path.is_dir() else []
    if not files:
        return pd.DataFrame(columns=list(POSITIONING_COLUMNS))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    missing = [c for c in POSITIONING_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"positioning release {path} lacks {missing}")
    df["available_utc"] = pd.to_datetime(df["available_utc"], utc=True)
    df["ts_utc"] = df["available_utc"]
    return (df.drop_duplicates(["series", "value_date", "vintage"], keep="last").sort_values("available_utc")
            .reset_index(drop=True))


def load_positioning_into_store(store: Store, df: pd.DataFrame) -> int:
    """Append to the `positioning` table, partitioned by each row's source (cftc, spdr); de-duplicated on series,
    value_date, vintage."""
    return sum(store.append("positioning", g.drop(columns=["source"]), source=str(src))
               for src, g in df.groupby("source")) if len(df) else 0


def store_positioning(store: Store) -> pd.DataFrame:
    """The positioning rows the store holds (what research passes as ctx["positioning"]), with their source."""
    df = store.read("positioning")
    if df.empty:
        return df
    df["available_utc"] = pd.to_datetime(df["available_utc"], utc=True)
    return df.drop(columns=[c for c in ("symbol", "year", "month") if c in df.columns])


def sync_release_positioning(store: Store, *, raw_dir: Path = Path("raw-positioning"), token: str | None = None) -> int:
    """Download the positioning release and load it into the store. 0 when the release does not exist yet (the
    data-positioning workflow has not run); other network errors propagate."""
    token = token or os.environ.get("GH_TOKEN")
    try:
        assets = list_assets(token, tag=POSITIONING_TAG)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 0
        raise
    files = [download(a, raw_dir, token) for a in assets]
    return sum(load_positioning_into_store(store, read_positioning_files(f)) for f in files)


# ------------------------------------------------------------------ US calendar, tiers 1 and 2 (data-us-calendar workflow, release calendar-v1)
CALENDAR_TAG = "calendar-v1"
US_CALENDAR_COLUMNS = ("event_id", "event_type", "tier", "event_utc", "status", "available_utc", "source")


def read_us_calendar_files(path: Path) -> pd.DataFrame:
    """The US calendar release, tiers 1 and 2 (goldbot/data/us_calendar.py COLUMNS) as one frame, for ctx["us_calendar"]:
    `path` is a Parquet file or a folder of them. Empty when nothing is there; a file without the point-in-time
    columns is refused. Every row is kept (cancellations and late stamps are history, not duplicates)."""
    files = [path] if path.is_file() else sorted(path.glob("*.parquet")) if path.is_dir() else []
    if not files:
        return pd.DataFrame(columns=list(US_CALENDAR_COLUMNS))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    missing = [c for c in US_CALENDAR_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"us calendar release {path} lacks {missing}")
    df["available_utc"] = pd.to_datetime(df["available_utc"], utc=True)
    df["event_utc"] = pd.to_datetime(df["event_utc"], utc=True)
    df["ts_utc"] = df["available_utc"]
    return (df.drop_duplicates(["event_id", "status", "available_utc"], keep="last")
            .sort_values(["available_utc", "event_id"], kind="stable").reset_index(drop=True))


def sync_release_us_calendar(*, raw_dir: Path = Path("raw-calendar"), token: str | None = None) -> pd.DataFrame:
    """Download release calendar-v1 into `raw_dir` and return its rows (read_us_calendar_files). Empty when the release
    does not exist yet (the data-us-calendar workflow has not run); other network errors propagate."""
    token = token or os.environ.get("GH_TOKEN")
    try:
        assets = list_assets(token, tag=CALENDAR_TAG)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return read_us_calendar_files(raw_dir / "__none__")
        raise
    for a in assets:
        download(a, raw_dir, token)
    return read_us_calendar_files(raw_dir)


def sync_release(store: Store, *, token: str | None = None) -> dict[str, int]:
    """Bars from data-v1 and macro series from macro-v1 (the Saturday retrain's refresh). A macro failure is logged
    and leaves the bar refresh standing."""
    # downloads go inside the store, never the working directory: the services run under ProtectSystem=strict with only
    # state/, logs/, data/ and models/ writable, so a cwd-relative raw/ fails the Saturday refresh ("Read-only file system")
    counts = sync_release_bars(store, token=token, raw_dir=store.root / "_raw" / "bars")
    try:
        counts["macro"] = sync_release_macro(store, token=token, raw_dir=store.root / "_raw" / "macro")
    except Exception as exc:     # macro is optional input; bars are what the retrain needs
        log.warning("macro release sync failed: %s", exc)
    return counts
