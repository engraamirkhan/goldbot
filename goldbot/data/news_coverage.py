"""News collector coverage readout and outage check (BACKLOG 212; follow-up of R139).

The news features (goldbot/data/news_features.py) are forward-only: their usable history is exactly what the brain's
collector has stored, and the Mac holds no `news` table. The daily `news_coverage` job (goldbot/ops/jobs.py) reads the
store's `news` table on the brain and returns `readout` (news_features.coverage: first/last received, days, kept rows,
backfilled rows dropped, scored rows; plus the scored share), which the scheduler records with the run.

Outages: every gap between consecutive receive stamps (the collector's polls) that holds more than
news_features.OUTAGE of OPEN gold-market time (DEFAULT_SESSIONS, a minute grid) is an outage: a dq_event (check
`news_outage`, severity warning, `span_from`/`span_to` = the two stamps, source `rss`), written once per gap, so a
research window can exclude it. A weekend or daily-break closure is not an outage: the collector may pause while gold
is closed, and news_features reads such a stretch as NaN anyway.

The research copy for item 211 is a local-only export (scripts/news_local.sh pull: sha256-verified, never a release).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.data import news_features as nf
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.store import Store

CHECK = "news_outage"
SOURCE = nf.SOURCE                     # the collector's store source (rss); its default symbol
SYMBOL = "XAUUSD"


def readout(news: pd.DataFrame | None) -> dict[str, Any]:
    """news_features.coverage plus `scored_share` (scored / kept rows; None when there are none)."""
    out: dict[str, Any] = dict(nf.coverage(news if news is not None else pd.DataFrame()))
    out["scored_share"] = round(out["scored"] / out["rows"], 4) if out["rows"] else None
    return out


def _open_hours(a: pd.Timestamp, b: pd.Timestamp, sessions: SessionTable) -> float:
    grid = pd.date_range(a.ceil("1min"), b, freq="1min", inclusive="left")
    if len(grid) == 0:
        return 0.0
    return float(np.asarray(sessions.is_open(grid), dtype=bool).sum()) / 60.0


def outages(news: pd.DataFrame | None, sessions: SessionTable = DEFAULT_SESSIONS) -> list[dict[str, Any]]:
    """Gaps between consecutive receive stamps of the collector's rows holding more than OUTAGE of open market time:
    [{span_from, span_to, open_hours}], oldest first."""
    if news is None or news.empty or "received_utc" not in news.columns:
        return []
    df = news
    if "source" in df.columns:
        df = df[df["source"].astype(str) == SOURCE]
    rec = pd.DatetimeIndex(pd.to_datetime(df["received_utc"], utc=True).dropna().drop_duplicates().sort_values())
    limit = nf.OUTAGE / pd.Timedelta(hours=1)
    out = []
    for a, b in zip(rec[:-1], rec[1:]):
        if b - a <= nf.OUTAGE:
            continue
        hours = _open_hours(a, b, sessions)
        if hours > limit:
            out.append({"span_from": a, "span_to": b, "open_hours": round(hours, 2)})
    return out


def _recorded(store: Store) -> set[tuple[pd.Timestamp, pd.Timestamp]]:
    ev = store.read("dq_events", source=SOURCE, symbol=SYMBOL)
    if ev.empty or "check" not in ev.columns or "span_from" not in ev.columns:
        return set()
    ev = ev[ev["check"] == CHECK]
    return {(pd.Timestamp(a), pd.Timestamp(b)) for a, b in zip(pd.to_datetime(ev["span_from"], utc=True),
                                                                pd.to_datetime(ev["span_to"], utc=True))}


def run(store: Store, now: pd.Timestamp) -> dict[str, Any]:
    """The daily job: readout + outages; each new outage written once to dq_events (warning)."""
    news = store.read("news")
    out = readout(news)
    gaps = outages(news)
    seen = _recorded(store)
    new = [g for g in gaps if (g["span_from"], g["span_to"]) not in seen]
    if new:
        rows = pd.DataFrame([{
            "ts_utc": g["span_from"], "check": CHECK, "severity": "warning",
            "detail": f"news collector: no poll for {g['open_hours']:.1f} h of open market "
                      f"({g['span_from']:%Y-%m-%d %H:%M} .. {g['span_to']:%Y-%m-%d %H:%M} UTC)",
            "reason": CHECK, "span_from": g["span_from"], "span_to": g["span_to"],
            "recorded_utc": pd.Timestamp(now).tz_convert("UTC")} for g in new])
        store.append("dq_events", rows, source=SOURCE, symbol=SYMBOL, dedupe=False)
    out.update(outages=len(gaps), outages_new=len(new),
               outage_hours=round(sum(g["open_hours"] for g in gaps), 2),
               last_outage=(None if not gaps else f"{gaps[-1]['span_from']:%Y-%m-%dT%H:%MZ}"
                                                  f"..{gaps[-1]['span_to']:%Y-%m-%dT%H:%MZ}"))
    return out


__all__ = ["CHECK", "outages", "readout", "run"]
