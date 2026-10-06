"""Headline collector and scorer (design: "Economic news and headlines").

Every few minutes the VPS service `goldbot-news` pulls the configured RSS/Atom feeds, stores each new item with
`published_utc` and `received_utc`, and scores gold-relevant ones with a language model into the design's fixed
schema: relevance to gold (0-1), rates direction (hawkish/dovish), risk (risk-on/off), dollar direction, surprise
(beat/miss/inline) and an unscheduled-shock flag. Features and the RiskGate only ever use what was known at
`received_utc`. A high-relevance unscheduled shock blocks entries for `shock_blackout_min` (engine).

Cost control: a keyword prefilter decides which items are worth scoring, items are scored in batches, the model
runs at low effort, and spend goes into the staff agents' monthly ledger under a daily news cap; items beyond the
cap are stored unscored (relevance NaN) rather than dropped.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Protocol

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

COLUMNS = ["item_id", "ts_utc", "received_utc", "source", "title", "summary", "link", "scored", "relevance",
           "rates", "risk", "dollar", "surprise", "shock"]
PREFILTER = re.compile(
    r"\b(?:gold|xau|bullion|silver|fed|fomc|powell|rate[s]?|yield[s]?|treasur|dollar|usd|dxy|inflation|cpi|pce|"
    r"payroll|nfp|jobs|employment|gdp|recession|tariff|war|missile|attack|sanction|geopolit|central bank|ecb|boj|"
    r"pboc|risk[- ]off|safe[- ]haven|default|crisis|emergency)\b", re.IGNORECASE)
BATCH = 25

Rates = Literal["hawkish", "dovish", "neutral"]
Risk = Literal["risk_on", "risk_off", "neutral"]
Dollar = Literal["positive", "negative", "neutral"]
Surprise = Literal["beat", "miss", "inline", "none"]


class HeadlineScore(FrozenRecord):
    id: str
    relevance: float
    rates: Rates
    risk: Risk
    dollar: Dollar
    surprise: Surprise
    shock: bool


SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "relevance": {"type": "number", "description": "0 to 1: how much this matters for spot gold in the next hours"},
            "rates": {"type": "string", "enum": ["hawkish", "dovish", "neutral"]},
            "risk": {"type": "string", "enum": ["risk_on", "risk_off", "neutral"]},
            "dollar": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "surprise": {"type": "string", "enum": ["beat", "miss", "inline", "none"]},
            "shock": {"type": "boolean", "description": "an unscheduled event: geopolitical escalation, emergency "
                                                        "central-bank action, sudden crisis"},
        },
        "required": ["id", "relevance", "rates", "risk", "dollar", "surprise", "shock"],
        "additionalProperties": False}}},
    "required": ["scores"],
    "additionalProperties": False,
}
SYSTEM = ("You score news headlines for an XAUUSD (spot gold) trading system. For every headline return one score "
          "object with the same id. relevance: 0 = irrelevant to gold, 1 = moves gold now (Fed decisions, US CPI/NFP "
          "surprises, wars, emergency central-bank actions). rates: hawkish if it points to higher US rates for longer, "
          "dovish if lower. risk: risk_off if it raises fear/haven demand. dollar: positive if it supports the USD. "
          "surprise: for a data release, beat/miss/inline against consensus when the headline states it, otherwise "
          "none. shock: true only for unscheduled events; a scheduled release is never a shock. Judge only from the "
          "text given; do not guess facts that are not in it.")


def item_id(source: str, link: str, title: str) -> str:
    return hashlib.sha1(f"{source}|{link or title}".encode()).hexdigest()[:16]


def _text(el: ET.Element | None) -> str:
    return re.sub(r"<[^>]+>", " ", el.text or "").strip() if el is not None else ""


def parse_feed(xml: str, source: str, received_utc: pd.Timestamp) -> pd.DataFrame:
    """RSS 2.0 or Atom -> rows (unscored). Items without a parseable time take `received_utc` (never a later time)."""
    root = ET.fromstring(xml)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    rows = []
    for it in items:
        title = _text(it.find("title")) or _text(it.find(f"{atom}title"))
        if not title:
            continue
        link_el = it.find("link")
        link = (link_el.text or "").strip() if link_el is not None and link_el.text else ""
        if not link:
            al = it.find(f"{atom}link")
            link = al.get("href", "") if al is not None else ""
        summary = _text(it.find("description")) or _text(it.find(f"{atom}summary"))
        raw_ts = _text(it.find("pubDate")) or _text(it.find(f"{atom}published")) or _text(it.find(f"{atom}updated"))
        try:
            ts = pd.Timestamp(parsedate_to_datetime(raw_ts)) if raw_ts and not raw_ts[:4].isdigit() else pd.Timestamp(raw_ts)
            ts = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
        except (TypeError, ValueError):
            ts = received_utc
        rows.append({"item_id": item_id(source, link, title), "ts_utc": min(ts, received_utc), "received_utc": received_utc,
                     "source": source, "title": title[:300], "summary": summary[:600], "link": link, "scored": False,
                     "relevance": np.nan, "rates": "", "risk": "", "dollar": "", "surprise": "", "shock": False})
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        df["received_utc"] = pd.to_datetime(df["received_utc"], utc=True)
    return df


def worth_scoring(df: pd.DataFrame) -> pd.Series:
    return (df["title"] + " " + df["summary"]).str.contains(PREFILTER)


class Client(Protocol):
    @property
    def beta(self) -> Any: ...


def score_batch(client: Client, model: str, items: pd.DataFrame) -> tuple[list[HeadlineScore], Any]:
    """One request for up to BATCH items: structured output constrained to SCORE_SCHEMA. Returns scores for the
    ids it was given (unknown ids are dropped, relevance clipped to [0, 1]) and the response usage."""
    from goldbot.agents.runner import FALLBACK_BETA
    lines = "\n".join(f"{iid}\t{title}" + (f" — {str(summ)[:200]}" if summ else "")
                      for iid, title, summ in zip(items["item_id"], items["title"], items["summary"]))
    resp = client.beta.messages.create(
        model=model, max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content": f"Score these headlines (id<TAB>text):\n{lines}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
        betas=[FALLBACK_BETA], fallbacks="default")
    if resp.stop_reason == "refusal":
        return [], resp.usage
    text = next((b.text for b in resp.content if b.type == "text"), "")
    known = set(items["item_id"])
    out = []
    for s in json.loads(text).get("scores", []) if text else []:
        if s.get("id") in known:
            s["relevance"] = float(min(max(float(s["relevance"]), 0.0), 1.0))
            out.append(HeadlineScore.model_validate(s))
    return out, resp.usage


def apply_scores(df: pd.DataFrame, scores: list[HeadlineScore]) -> pd.DataFrame:
    df = df.copy()
    by_id = {s.id: s for s in scores}
    for i, iid in zip(df.index, df["item_id"]):
        s = by_id.get(iid)
        if s is None:
            continue
        df.loc[i, ["scored", "relevance", "rates", "risk", "dollar", "surprise", "shock"]] = \
            [True, s.relevance, s.rates, s.risk, s.dollar, s.surprise, s.shock]
    return df


def shock_window(news: pd.DataFrame, now: pd.Timestamp, minutes: int, min_relevance: float) -> dict[str, Any] | None:
    """The latest high-relevance unscheduled shock received within `minutes` before `now`, or None."""
    if news.empty or "shock" not in news:
        return None
    rec = pd.to_datetime(news["received_utc"], utc=True)
    hit = news[news["shock"].astype(bool) & (news["relevance"].astype(float) >= min_relevance)
               & (rec <= now) & (rec >= now - pd.Timedelta(minutes=minutes))]
    if hit.empty:
        return None
    row = hit.sort_values("received_utc").iloc[-1]
    return {"title": str(row["title"]), "received_utc": pd.Timestamp(row["received_utc"]).isoformat(), "kind": "news_shock"}


def fetch(url: str, timeout: float = 15.0) -> str:   # pragma: no cover - network (VPS only)
    req = urllib.request.Request(url, headers={"User-Agent": "goldbot-news/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")
