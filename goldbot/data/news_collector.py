"""One polling round of the headline collector (`python -m goldbot.ops.run news` loops it every poll_seconds).

Fetch every configured feed (a failing feed is recorded in state/news_feeds.json and skipped, never fatal), keep the
items not stored yet, score the prefiltered ones in batches while today's news budget and the agents' monthly budget
allow, and append everything to the store's `news` table (unscored items keep relevance NaN).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from goldbot.agents.runner import Client, SpendLedger, cost_of
from goldbot.data.news import BATCH, apply_scores, parse_feed, score_batch, worth_scoring
from goldbot.data.store import Store


class NewsCollector:
    def __init__(self, store: Store, state_dir: str | Path, feeds: dict[str, str], fetcher: Callable[[str], str],
                 client: Client | None, ledger: SpendLedger | None, model: str, daily_cap_usd: float):
        self.store, self.state = store, Path(state_dir)
        self.feeds, self.fetcher, self.client, self.ledger = feeds, fetcher, client, ledger
        self.model, self.daily_cap = model, daily_cap_usd
        self.spend_path = self.state / "news_spend.json"

    def _spent_today(self, now: pd.Timestamp) -> float:
        try:
            return float(json.loads(self.spend_path.read_text()).get(f"{now:%Y-%m-%d}", 0.0)) if self.spend_path.exists() else 0.0
        except ValueError:
            return self.daily_cap                       # unreadable: assume the budget is used (fail closed on spend)

    def _add_spend(self, now: pd.Timestamp, usd: float) -> None:
        key = f"{now:%Y-%m-%d}"
        self.spend_path.parent.mkdir(parents=True, exist_ok=True)
        self.spend_path.write_text(json.dumps({key: round(self._spent_today(now) + usd, 6)}))
        if self.ledger is not None:
            self.ledger.add(now, usd)                   # counts against the staff agents' monthly cap

    def poll(self, now: pd.Timestamp) -> dict[str, Any]:
        frames, health = [], {}
        for name, url in self.feeds.items():
            try:
                df = parse_feed(self.fetcher(url), name, now)
                frames.append(df)
                health[name] = {"ok": True, "items": len(df), "ts": now.isoformat()}
            except Exception as exc:                     # a broken feed must not stop the others
                health[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200], "ts": now.isoformat()}
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "news_feeds.json").write_text(json.dumps(health, indent=1))
        out: dict[str, Any] = {"feeds_ok": sum(h["ok"] for h in health.values()), "feeds": len(self.feeds),
                               "new": 0, "scored": 0, "cost_usd": 0.0}
        if not frames:
            return out
        items = pd.concat(frames, ignore_index=True).drop_duplicates("item_id")
        # from the oldest item in the feeds, not a fixed window: press-release feeds keep items for weeks
        seen = self.store.read("news", start=items["ts_utc"].min(), columns=["item_id"])
        if not seen.empty:
            items = items[~items["item_id"].isin(set(seen["item_id"]))]
        if items.empty:
            return out
        todo = items[worth_scoring(items)].sort_values("ts_utc", ascending=False)   # newest first
        scored: list = []
        for start in range(0, len(todo), BATCH):
            room = min(self.daily_cap - self._spent_today(now), self.ledger.remaining(now) if self.ledger else 0.0)
            if self.client is None or room < 0.05:
                out["unscored_reason"] = "no client" if self.client is None else "news budget used"
                break
            try:
                s, usage = score_batch(self.client, self.model, todo.iloc[start:start + BATCH])
            except Exception as exc:                     # API trouble: keep these unstored, try again next round
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
                items = items[~items["item_id"].isin(set(todo["item_id"].iloc[start:]))]
                break
            _, usd = cost_of(usage)
            self._add_spend(now, usd)
            out["cost_usd"] += usd
            scored += s
        items = apply_scores(items, scored)
        out["new"], out["scored"] = len(items), int(items["scored"].sum())
        self.store.append("news", items, source="rss")
        return out
