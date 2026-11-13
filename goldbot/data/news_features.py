"""Point-in-time LLM news features from the headline collector's own forward history (R139, plan item 8).

Input: the store's `news` table as the collector wrote it (goldbot/data/news_collector.py: `received_utc` = the poll
that first saw the item, `written_utc` = the collector's clock just before that poll's append, after scoring; the LLM's
fixed score schema is written in that same poll and never rescored). Nothing here calls
a model: the per-headline fields are derived from the scores the collector already paid for (the existing news budget).

Per headline (`headline_features`):
* `available_utc` = `written_utc`, the row's actual write time (quant review 2026-11-13: no assumed fixed lag). A row
  from before that stamp existed takes the next later `received_utc` in the table: the collector polls in one
  sequential loop (goldbot/ops/services/news.py), so the next poll starts only after this one's append, which makes it
  a proven upper bound; a row with no later poll yet is not available. Never the publication time `ts_utc`: a feed
  often lists an item minutes or hours after its stamp.
* backfills never count: an item the collector first saw more than MAX_STALE after its publication (the first poll
  of a feed, a press-release archive listing weeks of items) is dropped, not stamped as fresh news. Rows of any
  store source but the collector's (`rss`) are dropped too: an import is a backfill by construction.
* `sentiment` in [-1, 1], gold-signed (positive = supportive for gold): relevance x the mean of rates (dovish +1,
  hawkish -1), risk (risk_off +1, risk_on -1) and dollar (negative +1, positive -1);
* `surprise`, gold-signed: a data beat is -1 (USD-supportive), a miss +1, inline/none 0;
* `rule_scored`: the score came from the free rules-v1 scorer (`scorer` = rules-v1, goldbot/data/news_rules.py; R144)
  rather than the LLM. Rule rows are read like LLM rows (same schema) and flagged, so research can separate them;
  a row without a `scorer` is from before the rules existed and was LLM-scored;
* event types (relevant headlines only, relevance >= RELEVANT): `evt_fed`, `evt_usd`, `evt_geo` (or the LLM's shock
  flag), `evt_etf`, `evt_cb` (central-bank gold buying), from the LLM fields plus a fixed keyword taxonomy on the
  headline text. Keywords instead of a new LLM field keep one definition over the whole collected history: a schema
  change would split this short series at the deploy date.

Aggregates (`news_grid`) on a GRID (15 min) of availability stamps g, each over the headlines with available_utc in
(g - w, g]: relevant-headline counts and summed sentiment over 1h/4h/24h, the sentiment sums' z-score against the
trailing Z_LOOKBACK of grid values (at least Z_MIN), gold-signed surprise, shock and event counts over 4h, and the share
of prefiltered headlines the daily budget left unscored over 24h (a coverage column), and the share of scored
headlines over 24h whose score came from the rules (`news_rules_share_24h`, the scorer flag). Two masks, both applied before
the z-scores so a masked stamp never enters a trailing mean or std:
* budget cut-off: the daily news cap leaves late-UTC items unscored, which would make counts and sums fall by time of
  day; a window in which more than MAX_UNSCORED of the prefiltered headlines are unscored reads NaN (the 1h/4h/24h
  columns by their own window; the 4h surprise, shock and event counts by the 4h one);
* outage: a grid stamp with nothing available in the trailing OUTAGE reads NaN in every column, never "no news".

The control the discovery screen pre-registers (4-ai-systems.md s.5 rank 1) is `shuffled_control`: the same headlines
with their received times permuted within each UTC day.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.news import PREFILTER
from goldbot.data.timeutil import epoch_ns

SOURCE = "rss"                                   # the collector's store source (news_collector.NewsCollector.poll)
MAX_UNSCORED = 0.2                               # share of prefiltered items left unscored above which a window is NaN
MAX_STALE = pd.Timedelta(hours=2)                # received later than this after publication = a backfill, dropped
RELEVANT = 0.5
GRID = pd.Timedelta(minutes=15)
WINDOWS = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "24h": pd.Timedelta(hours=24)}
Z_LOOKBACK = pd.Timedelta(days=30)
Z_MIN = pd.Timedelta(days=7)
OUTAGE = pd.Timedelta(hours=12)
EVENTS = ("fed", "usd", "geo", "etf", "cb")

COLUMNS: tuple[str, ...] = (
    *(f"news_count_{w}" for w in WINDOWS), *(f"news_sent_{w}" for w in WINDOWS),
    *(f"news_sent_z_{w}" for w in WINDOWS), "news_surprise_4h", "news_shock_4h",
    *(f"news_evt_{e}_4h" for e in EVENTS), "news_unscored_share_24h", "news_rules_share_24h")
RULES_SCORER = "rules-v1"                        # goldbot.data.news_rules.SCORER (not imported: keeps this module light)

_FED = re.compile(r"\b(?:fed|fomc|powell|federal reserve|rate (?:cut|hike)s?|interest rates?|dot plot)\b", re.I)
_USD = re.compile(r"\b(?:dollar|dxy|greenback|treasur(?:y|ies)|yields?|cpi|pce|inflation|payrolls?|nfp|jobs report|"
                  r"unemployment|jobless|gdp|retail sales|ism)\b", re.I)
_GEO = re.compile(r"\b(?:war|missiles?|attacks?|strikes?|sanctions?|invasion|invades?|geopolitic\w*|tensions?|"
                  r"ceasefire|conflict|military|nuclear|troops)\b", re.I)
_ETF = re.compile(r"\b(?:etfs?|gld|spdr|ishares|holdings|inflows?|outflows?)\b", re.I)
_CB = re.compile(r"\b(?:central banks?|pboc|people'?s bank|rbi|reserve bank|national bank|reserves)\b", re.I)
_CB_BUY = re.compile(r"\b(?:buy\w*|bought|purchas\w*|adds?|added|accumulat\w*|reserves?)\b", re.I)
_GOLD = re.compile(r"\b(?:gold|bullion)\b", re.I)


def _utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True)


def _admissible(news: pd.DataFrame) -> pd.DataFrame:
    """The collector's own rows with a receive stamp (any freshness), with `available_utc` (see the module docstring;
    an `available_utc` already on the row, from `ctx_frame`, is kept; NaT = not provably written yet)."""
    df = news
    if "source" in df.columns:
        df = df[df["source"].astype(str) == SOURCE]
    df = df.dropna(subset=["received_utc"]).copy()
    df["received_utc"] = _utc(df["received_utc"])
    df["ts_utc"] = _utc(df["ts_utc"]).fillna(df["received_utc"])
    polls = np.unique(epoch_ns(pd.DatetimeIndex(df["received_utc"])))
    nxt = np.searchsorted(polls, epoch_ns(pd.DatetimeIndex(df["received_utc"])), side="right")
    next_poll = pd.Series(pd.to_datetime(np.where(nxt < len(polls), polls[np.minimum(nxt, len(polls) - 1)], 0),
                                         utc=True), index=df.index).where(nxt < len(polls))
    avail = _utc(df["written_utc"]) if "written_utc" in df.columns else pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    if "available_utc" in df.columns:
        avail = _utc(df["available_utc"]).fillna(avail)
    df["available_utc"] = avail.fillna(next_poll)
    return df


def _fresh(df: pd.DataFrame) -> pd.Series:
    return (df["received_utc"] - df["ts_utc"]) <= MAX_STALE


def headline_features(news: pd.DataFrame) -> pd.DataFrame:
    """One row per admissible headline: item_id, available_utc, scored, relevance, relevant, sentiment, surprise,
    shock, prefiltered and evt_* (see the module docstring). Sorted by available_utc."""
    cols = ["item_id", "available_utc", "scored", "rule_scored", "relevance", "relevant", "sentiment", "surprise",
            "shock", "prefiltered", *(f"evt_{e}" for e in EVENTS)]
    if news is None or news.empty:
        return pd.DataFrame(columns=cols)
    df = _admissible(news)
    df = df[_fresh(df) & df["available_utc"].notna()]
    if df.empty:
        return pd.DataFrame(columns=cols)
    rel = pd.to_numeric(df["relevance"], errors="coerce")
    scored = rel.notna()
    sign = {"dovish": 1.0, "hawkish": -1.0, "risk_off": 1.0, "risk_on": -1.0, "negative": 1.0, "positive": -1.0}

    def comp(c: str) -> pd.Series:
        return df[c].astype(str).map(sign).fillna(0.0) if c in df.columns else pd.Series(0.0, index=df.index)
    sentiment = (rel * (comp("rates") + comp("risk") + comp("dollar")) / 3.0).where(scored)
    surprise = df.get("surprise", pd.Series("", index=df.index)).astype(str).map({"beat": -1.0, "miss": 1.0}) \
        .fillna(0.0).where(scored)
    shock = df.get("shock", pd.Series(False, index=df.index)).fillna(False).astype(bool) & scored
    text = df["title"].fillna("").astype(str) + " " + df.get("summary", pd.Series("", index=df.index)).fillna("").astype(str)
    relevant = scored & (rel >= RELEVANT)
    rates = df.get("rates", pd.Series("", index=df.index)).astype(str)
    dollar = df.get("dollar", pd.Series("", index=df.index)).astype(str)
    title = df["title"].fillna("").astype(str)
    out = pd.DataFrame({
        "item_id": df["item_id"].to_numpy(), "available_utc": df["available_utc"],
        "scored": scored, "rule_scored": scored & df.get("scorer", pd.Series("", index=df.index)).fillna("")
        .astype(str).eq(RULES_SCORER), "relevance": rel, "relevant": relevant, "sentiment": sentiment, "surprise": surprise,
        "shock": shock, "prefiltered": text.str.contains(PREFILTER),
        "evt_fed": relevant & (title.str.contains(_FED) | (rates.isin(["hawkish", "dovish"]) & text.str.contains(_FED))),
        "evt_usd": relevant & (title.str.contains(_USD) | dollar.isin(["positive", "negative"])),
        "evt_geo": relevant & (title.str.contains(_GEO) | shock),
        "evt_etf": relevant & title.str.contains(_ETF) & text.str.contains(_GOLD),
        "evt_cb": relevant & title.str.contains(_CB) & title.str.contains(_CB_BUY) & text.str.contains(_GOLD),
    })
    return out.sort_values("available_utc", kind="mergesort").reset_index(drop=True)[cols]


def _window_sum(at: np.ndarray, grid: np.ndarray, w: int, x: np.ndarray) -> np.ndarray:
    """Sum of x over events with stamp in (g - w, g] for each grid stamp g (stamps sorted, epoch ns)."""
    cs = np.r_[0.0, np.cumsum(x)]
    return cs[np.searchsorted(at, grid, side="right")] - cs[np.searchsorted(at, grid - w, side="right")]


def news_grid(news: pd.DataFrame, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """The aggregate columns (COLUMNS) on GRID stamps from the first received headline to max(last one, `end`), as
    DataFrame(available_utc, *COLUMNS). Each stamp uses only headlines available at or before it."""
    if news is None or news.empty:
        return pd.DataFrame(columns=["available_utc", *COLUMNS])
    adm = _admissible(news)
    if adm.empty:
        return pd.DataFrame(columns=["available_utc", *COLUMNS])
    h = headline_features(adm)
    adm = adm[adm["available_utc"].notna()]
    if adm.empty:
        return pd.DataFrame(columns=["available_utc", *COLUMNS])
    seen = np.sort(epoch_ns(pd.DatetimeIndex(adm["available_utc"])))
    first = pd.Timestamp(seen[0], tz="UTC").ceil(GRID)
    last = pd.Timestamp(seen[-1], tz="UTC")
    if end is not None:
        last = max(last, pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else
                   pd.Timestamp(end).tz_localize("UTC"))
    stamps = pd.date_range(first, last.ceil(GRID), freq=GRID)
    g = epoch_ns(stamps)
    at = epoch_ns(pd.DatetimeIndex(h["available_utc"])) if len(h) else np.array([], dtype=np.int64)
    rel = h["relevant"].to_numpy(dtype=bool) if len(h) else np.array([], dtype=bool)
    out: dict[str, Any] = {"available_utc": stamps}
    for name, w in WINDOWS.items():
        wn = int(w.value)
        out[f"news_count_{name}"] = _window_sum(at, g, wn, rel.astype(float))
        sent = np.where(rel, h["sentiment"].fillna(0.0).to_numpy(dtype=float), 0.0) if len(h) else np.array([])
        out[f"news_sent_{name}"] = _window_sum(at, g, wn, sent)
    w4 = int(WINDOWS["4h"].value)

    def relsum(col: str) -> np.ndarray:
        x = np.where(rel, h[col].fillna(0.0).to_numpy(dtype=float), 0.0) if len(h) else np.array([])
        return _window_sum(at, g, w4, x)
    out["news_surprise_4h"] = relsum("surprise")
    out["news_shock_4h"] = relsum("shock")
    for e in EVENTS:
        out[f"news_evt_{e}_4h"] = relsum(f"evt_{e}")
    pre = h["prefiltered"].to_numpy(dtype=bool) if len(h) else np.array([], dtype=bool)
    uns = pre & ~h["scored"].to_numpy(dtype=bool) if len(h) else np.array([], dtype=bool)
    w24 = int(WINDOWS["24h"].value)
    n_pre = _window_sum(at, g, w24, pre.astype(float))
    with np.errstate(invalid="ignore", divide="ignore"):
        out["news_unscored_share_24h"] = np.where(n_pre > 0, _window_sum(at, g, w24, uns.astype(float)) / n_pre, np.nan)
    n_sc = _window_sum(at, g, w24, h["scored"].to_numpy(dtype=float)) if len(h) else np.zeros(len(g))
    n_rule = _window_sum(at, g, w24, h["rule_scored"].to_numpy(dtype=float)) if len(h) else np.zeros(len(g))
    with np.errstate(invalid="ignore", divide="ignore"):
        out["news_rules_share_24h"] = np.where(n_sc > 0, n_rule / n_sc, np.nan)
    frame = pd.DataFrame(out)
    # budget cut-off: a window with too many prefiltered items left unscored is unknown, not quiet
    for name, w in WINDOWS.items():
        n_p = _window_sum(at, g, int(w.value), pre.astype(float))
        with np.errstate(invalid="ignore", divide="ignore"):
            share = np.where(n_p > 0, _window_sum(at, g, int(w.value), uns.astype(float)) / n_p, 0.0)
        cut = share > MAX_UNSCORED
        cols = [f"news_count_{name}", f"news_sent_{name}"]
        if name == "4h":
            cols += ["news_surprise_4h", "news_shock_4h", *(f"news_evt_{e}_4h" for e in EVENTS)]
        frame.loc[cut, cols] = np.nan
    # outage: nothing available (relevant or not, fresh or not) in the trailing OUTAGE -> unknown, not quiet
    last_seen = np.searchsorted(seen, g, side="right") - 1
    gap = g - seen[np.clip(last_seen, 0, None)]
    down = (last_seen < 0) | (gap > int(OUTAGE.value))
    frame.loc[down, [c for c in COLUMNS if c in frame.columns]] = np.nan
    # z-scores last, on the masked sums: a masked stamp never enters a trailing mean or std
    n_look, n_min = int(Z_LOOKBACK / GRID), int(Z_MIN / GRID)
    for name in WINDOWS:
        s = frame[f"news_sent_{name}"]
        r = s.rolling(n_look, min_periods=n_min)
        sd = r.std()
        frame[f"news_sent_z_{name}"] = ((s - r.mean()) / sd.where(sd > 0)).to_numpy()
    return frame[["available_utc", *COLUMNS]]


def shuffled_control(news: pd.DataFrame, seed: int) -> pd.DataFrame:
    """The pre-registered control: every headline's received time permuted within its UTC day (publication times
    move with it, so each item's receive lag and the backfill rule are unchanged). Same rows, same scores."""
    df = _admissible(news).reset_index(drop=True)
    lag = df["received_utc"] - df["ts_utc"]
    write = df["available_utc"] - df["received_utc"]
    rng = np.random.default_rng(seed)
    rec = df["received_utc"].to_numpy().copy()
    for _, idx in df.groupby(df["received_utc"].dt.floor("D")).indices.items():
        rec[idx] = rec[rng.permutation(idx)]
    df["received_utc"] = pd.to_datetime(rec, utc=True)
    df["ts_utc"] = df["received_utc"] - lag
    df["available_utc"] = df["received_utc"] + write
    if "written_utc" in df.columns:
        df["written_utc"] = df["available_utc"]
    return df


def coverage(news: pd.DataFrame) -> dict[str, Any]:
    """How much forward history the collector holds: first/last receive stamps, span in days, rows kept and backfilled
    rows dropped, and scored rows (run on the brain's store; the Mac holds no news table)."""
    adm = _admissible(news) if news is not None and not news.empty else pd.DataFrame()
    if adm.empty:
        return {"rows": 0, "days": 0.0, "first_received_utc": None, "last_received_utc": None,
                "backfilled_dropped": 0, "scored": 0}
    fresh = _fresh(adm)
    a, b = adm["received_utc"].min(), adm["received_utc"].max()
    return {"rows": int(fresh.sum()), "days": round((b - a) / pd.Timedelta(days=1), 3),
            "first_received_utc": a.isoformat(), "last_received_utc": b.isoformat(),
            "backfilled_dropped": int((~fresh).sum()),
            "scored": int(pd.to_numeric(adm.loc[fresh, "relevance"], errors="coerce").notna().sum())}


def ctx_frame(news: pd.DataFrame) -> pd.DataFrame:
    """The collector's rows for ctx["news"], stamped `available_utc` (the write time, see the module docstring), so a
    caller that cuts ctx tables by availability (pipeline.lookahead_check) cuts this one exactly where the features
    do. Rows not provably written yet are left out."""
    df = _admissible(news)
    return df[df["available_utc"].notna()].reset_index(drop=True)
