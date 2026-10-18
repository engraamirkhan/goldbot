"""`GET /api/tradingview`: is every indicator alert still arriving? Read-only, built from the `tv_indicators` dataset.

Per indicator x timeframe: the last bar received, alerts (bars) in the last 24 h, the plots seen, and the gaps: bar
slots missing between consecutive received bars in the last 24 h (a market-closed stretch counts as missing slots; the
screen says so). Chart-export (CSV) history is counted apart (`csv_bars`, `csv_rows`) with its repaint status:
"unvalidated" until goldbot/research/tv_repaint.py has flagged every CSV row of that indicator x timeframe; it is never
counted as a live alert. Nothing here writes, and no TradingView value is shown, only the counts, so an invite-only
indicator's output is not exposed to a viewer.

The direct pull (ADR 0030, goldbot/data/tv_pull.py) writes the same dataset with `origin` "pull_live" (received within
`live_max_lag_s` of the close) or "pull_history" (backfill): counted per indicator apart from the alerts
(`pull_live_24h`, `pull_history_bars`). Its "chart" series is the bars, not an indicator, and is not listed. The
repaint status covers every history origin (chart export and pull_history). The sign-in comes from state/tv_auth.db
through goldbot/ops/tv_pull_health.py (`signin_state`); tv.db (the webhook inbox and the pull log) is never opened, and
no cookie, layout or user id is read.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from goldbot.api.schema import TvIndicatorStatus, TvStatusView
from goldbot.data.store import Store
from goldbot.ops.tv_pull_health import fix_command, signin_state
from goldbot.webhook.app import tf_seconds
from goldbot.webhook.inbox import TABLE

WINDOW = pd.Timedelta(hours=24)
HISTORY = pd.Timedelta(days=30)
Status = Literal["none", "unvalidated", "validated"]
Key = tuple[str, str, str]
# goldbot/data/tv_pull.py ORIGIN_LIVE, ORIGIN_HISTORY and CHART, not imported so that the API never loads the pull's
# network module (tests/test_tv_status_link.py pins them)
PULL_LIVE, PULL_HISTORY, CHART = "pull_live", "pull_history", "chart"
HISTORY_ORIGINS = ("csv", PULL_HISTORY)
History = dict[str, tuple[int, int, bool]]      # history origin -> (bars, plot rows, every row repaint-validated)


def _gaps(bars: list[pd.Timestamp], sec: int) -> int:
    if sec <= 0 or len(bars) < 2:
        return 0
    steps = [(b - a).total_seconds() / sec for a, b in zip(bars, bars[1:])]
    return int(sum(max(round(s) - 1, 0) for s in steps))


def _history(store: Store, cols: set[str]) -> dict[Key, History]:
    """(source, symbol, tf) -> its history per origin, over every row ever imported or backfilled."""
    if "origin" not in cols:
        return {}
    ok = "BOOL_AND(COALESCE(repaint_validated, false))" if "repaint_validated" in cols else "false"
    origins = ", ".join(f"'{o}'" for o in HISTORY_ORIGINS)
    df = store.sql(
        f"SELECT source, symbol, tf, origin, COUNT(DISTINCT bar_time) AS bars, COUNT(*) AS n, {ok} AS ok "
        f"FROM tv_indicators WHERE origin IN ({origins}) GROUP BY source, symbol, tf, origin")
    out: dict[Key, History] = {}
    for src, sym, tf, origin, bars, n, valid in df.itertuples(index=False, name=None):
        out.setdefault((str(src), str(sym), str(tf)), {})[str(origin)] = (int(bars), int(n), bool(valid))
    return out


def _pull_live(store: Store, cols: set[str], now: pd.Timestamp) -> dict[Key, int]:
    """(source, symbol, tf) -> closed bars the pull received live in the last 24 h."""
    if "origin" not in cols:
        return {}
    since = (now - WINDOW).to_pydatetime().isoformat()
    df = store.sql(
        "SELECT source, symbol, tf, COUNT(DISTINCT bar_time) AS bars FROM tv_indicators "
        f"WHERE origin = '{PULL_LIVE}' AND bar_time >= TIMESTAMPTZ '{since}' GROUP BY source, symbol, tf")
    return {(str(src), str(sym), str(tf)): int(b) for src, sym, tf, b in df.itertuples(index=False, name=None)}


def _repaint(hist: History) -> Status:
    if not hist:
        return "none"
    return "validated" if all(valid for _, _, valid in hist.values()) else "unvalidated"


def _row(key: Key, hist: History, pull_live: int, *, last_bar_time: str = "", alerts_24h: int = 0,
         plots_seen: list[str] | None = None, gaps_24h: int = 0) -> TvIndicatorStatus:
    indicator, _, version = key[0].partition("@")
    csv_bars, csv_rows, _ = hist.get("csv", (0, 0, False))
    return TvIndicatorStatus(
        indicator=indicator, version=version, symbol=key[1], tf=key[2], last_bar_time=last_bar_time,
        alerts_24h=alerts_24h, plots_seen=plots_seen or [], gaps_24h=gaps_24h, csv_bars=csv_bars, csv_rows=csv_rows,
        repaint_status=_repaint(hist), pull_live_24h=pull_live,
        pull_history_bars=hist.get(PULL_HISTORY, (0, 0, False))[0])


def _view(now: pd.Timestamp, rows: list[TvIndicatorStatus], state_dir: Path | None,
          cookie_keys: tuple[str, str] | None) -> TvStatusView:
    st = signin_state(state_dir, now) if state_dir is not None else None
    signin = st.signin if st is not None else "not_set_up"
    fix = (fix_command(*cookie_keys) if cookie_keys else fix_command()) if signin in ("expired", "unknown") else None
    last = st.last_ok_utc if st is not None else None
    return TvStatusView(
        now=now.isoformat(), indicators=rows, signin=signin,
        last_pull_utc=last.to_pydatetime() if last is not None else None, stale=bool(st is not None and st.stale),
        fix_command=fix, live_bars_24h=sum(r.alerts_24h + r.pull_live_24h for r in rows),
        history_bars=sum(r.csv_bars + r.pull_history_bars for r in rows))


def tv_status(store: Store, now: pd.Timestamp, state_dir: str | Path | None = None,
              cookie_keys: tuple[str, str] | None = None) -> TvStatusView:
    """`state_dir`: where tv_auth.db lives (None: the sign-in is not read). `cookie_keys`: settings tv_pull
    sessionid_key and sessionid_sign_key, for the fix command (the defaults otherwise)."""
    now = now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC")
    sdir = Path(state_dir) if state_dir is not None else None
    tdir = store.table_dir(TABLE)
    if not tdir.exists() or not any(tdir.rglob("*.parquet")):
        return _view(now, [], sdir, cookie_keys)
    cols = set(store.sql("DESCRIBE SELECT * FROM tv_indicators")["column_name"])
    hist = _history(store, cols)
    pull = _pull_live(store, cols, now)
    since = (now - HISTORY).to_pydatetime()
    live_only = "origin IS NULL AND " if "origin" in cols else ""
    df = store.sql(
        "SELECT source, symbol, tf, bar_time, plot, intrabar FROM tv_indicators "
        f"WHERE {live_only}ts_utc >= TIMESTAMPTZ '{since.isoformat()}'")
    rows: list[TvIndicatorStatus] = []
    seen: set[Key] = set()
    if not df.empty:
        df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True)
        for (source, symbol, tf), g in df[~df["intrabar"].astype(bool)].groupby(["source", "symbol", "tf"]):
            key = (str(source), str(symbol), str(tf))
            seen.add(key)
            bars = sorted(g["bar_time"].unique())
            recent = [b for b in bars if b >= now - WINDOW]
            rows.append(_row(key, hist.get(key, {}), pull.get(key, 0),
                             last_bar_time=pd.Timestamp(bars[-1]).isoformat(), alerts_24h=len(recent),
                             plots_seen=sorted(p for p in g["plot"].unique() if p),
                             gaps_24h=_gaps(recent, tf_seconds(key[2]))))
    for key in sorted((set(hist) | set(pull)) - seen):
        if key[0].partition("@")[0] != CHART:
            rows.append(_row(key, hist.get(key, {}), pull.get(key, 0)))
    rows.sort(key=lambda r: (r.indicator, r.tf))
    return _view(now, rows, sdir, cookie_keys)
