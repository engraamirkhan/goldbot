"""Economic calendar (design: "Forex Factory's free weekly feed archived daily by us").

The VPS fetches the week's feed once a day (scheduler job `calendar_archive`), keeps every row with the time it was
received, and the engines read upcoming events from the store. Tier 1 is the design's blackout list for gold: US CPI,
NFP, FOMC (statement, rate decision, press conference) and PCE; the RiskGate blocks entries from `before_min` before
to `after_min` after each tier-1 event. Tier 2 is any other high-impact USD event, tier 3 everything else.

The scheduled time is known in advance, so using it for "minutes to the next event" is not lookahead; anything about
the outcome (actual vs forecast) must join on `received_utc`.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from typing import Any

import pandas as pd

FF_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# settings.yaml risk.blackout.events names -> title patterns in the feed (USD events only)
TIER1_PATTERNS = {
    "CPI": r"\bCPI\b",
    "NFP": r"Non-Farm Employment Change|\bNFP\b",
    "FOMC": r"\bFOMC (?:Statement|Press Conference)\b|Federal Funds Rate",   # design: statement and presser
    "PCE": r"\bPCE\b",
}
COLUMNS = ["event_id", "ts_utc", "country", "title", "impact", "tier", "forecast", "previous", "received_utc"]


def tier_of(country: str, title: str, impact: str, tier1_events: list[str]) -> int:
    if country.upper() == "USD":
        for name in tier1_events:
            pat = TIER1_PATTERNS.get(name.upper())
            if pat and re.search(pat, title, flags=re.IGNORECASE):
                return 1
        if impact.lower() == "high":
            return 2
    return 3


def parse_ff_week(payload: str | list[dict[str, Any]], received_utc: pd.Timestamp,
                  tier1_events: list[str]) -> pd.DataFrame:
    """Rows of the Forex Factory weekly JSON -> calendar_events rows. Bad rows are skipped, not guessed."""
    items = json.loads(payload) if isinstance(payload, str) else payload
    rows = []
    for it in items:
        try:
            ts = pd.Timestamp(it["date"])
            if ts.tzinfo is None:
                continue                       # the feed carries an offset; a naive time cannot be placed in UTC
            ts = ts.tz_convert("UTC")
            country, title, impact = str(it["country"]), str(it["title"]).strip(), str(it.get("impact", ""))
        except (KeyError, ValueError, TypeError):
            continue
        eid = hashlib.sha1(f"{country}|{title}|{ts.isoformat()}".encode()).hexdigest()[:16]
        rows.append({"event_id": eid, "ts_utc": ts, "country": country, "title": title, "impact": impact,
                     "tier": tier_of(country, title, impact, tier1_events), "forecast": str(it.get("forecast") or ""),
                     "previous": str(it.get("previous") or ""), "received_utc": received_utc})
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        df["received_utc"] = pd.to_datetime(df["received_utc"], utc=True)
    return df


def fetch_ff_week(url: str = FF_THIS_WEEK, timeout: float = 20.0) -> str:   # pragma: no cover - network (VPS only)
    req = urllib.request.Request(url, headers={"User-Agent": "goldbot-calendar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def blackout_window(events: pd.DataFrame, now: pd.Timestamp, before_min: int, after_min: int) -> dict[str, Any] | None:
    """The tier-1 event whose window [ts - before, ts + after] contains `now`, or None."""
    if events.empty:
        return None
    ev = events[events["tier"] == 1]
    ts = pd.DatetimeIndex(pd.to_datetime(ev["ts_utc"], utc=True))
    inside = (ts - pd.Timedelta(minutes=before_min) <= now) & (now <= ts + pd.Timedelta(minutes=after_min))
    if not inside.any():
        return None
    row = ev[inside].iloc[0]
    return {"title": str(row["title"]), "ts_utc": pd.Timestamp(row["ts_utc"]).isoformat()}
