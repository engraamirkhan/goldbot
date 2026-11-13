"""One polling round of the headline collector (`python -m goldbot.ops.run news` loops it every poll_seconds).

Fetch every configured feed (a failing feed is recorded in state/news_feeds.json and skipped, never fatal), keep the
items not stored yet, score every prefiltered one with the free rules-v1 scorer (goldbot/data/news_rules.py), score them
in LLM batches while a client is configured and today's news budget and the agents' monthly budget allow (an LLM score
takes precedence; the rule score stays in the `rule_*` columns), and append everything to the store's `news` table
(items that are not prefiltered keep relevance NaN). Every row carries
`written_utc`, the collector's clock just before the append (after scoring): the moment the row and its scores became
readable, which the point-in-time news features use as availability (goldbot/data/news_features.py, R139).

`state/news_scoring.json` records when prefiltered headlines last arrived and when one was last scored (any scorer);
the `news` health check fails when headlines arrive but none is scored for a day (R144: the shock blackout is blind).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from goldbot.agents.runner import Client, SpendLedger, cost_of
from goldbot.data.news import (
    BATCH,
    apply_rule_columns,
    apply_scores,
    parse_feed,
    rule_blackout_minutes,
    score_batch,
    worth_scoring,
)
from goldbot.data.news_rules import LLM_SCORER, SCORER, RuleScore, mark_repeats, score_rules
from goldbot.data.store import Store


class NewsCollector:
    def __init__(self, store: Store, state_dir: str | Path, feeds: dict[str, str], fetcher: Callable[[str], str],
                 client: Client | None, ledger: SpendLedger | None, model: str, daily_cap_usd: float,
                 rule_min_confidence: float = 0.8, source_weights: Mapping[str, float] | None = None,
                 rule_dedupe_hours: float = 6.0, blackout_min: int = 30, shock_min_relevance: float = 0.7):
        self.store, self.state = store, Path(state_dir)
        self.feeds, self.fetcher, self.client, self.ledger = feeds, fetcher, client, ledger
        self.model, self.daily_cap = model, daily_cap_usd
        self.rule_min_confidence, self.source_weights = rule_min_confidence, dict(source_weights or {})
        self.rule_lookback = pd.Timedelta(hours=rule_dedupe_hours)
        self.blackout_min, self.shock_min_relevance = blackout_min, shock_min_relevance
        self.spend_path = self.state / "news_spend.json"
        self.scoring_path = self.state / "news_scoring.json"
        self.clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC")

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

    def _record_scoring(self, now: pd.Timestamp, prefiltered: int, out: dict[str, Any]) -> None:
        """When prefiltered headlines last arrived and one was last scored, and the rule-shock blackout minutes of the
        last 14 UTC days, uncapped (the `news` and `news_blackout_cap` health checks read it)."""
        try:
            rec = json.loads(self.scoring_path.read_text()) if self.scoring_path.exists() else {}
        except ValueError:
            rec = {}
        rec.setdefault("since_utc", now.isoformat())
        rec.setdefault("last_prefiltered_utc", None)
        rec.setdefault("last_scored_utc", None)
        if prefiltered:
            rec["last_prefiltered_utc"] = now.isoformat()
        if out["scored"]:
            rec["last_scored_utc"] = now.isoformat()
        if "rule_blackout_min_today" in out:
            days = dict(rec.get("rule_blackout_min") or {})
            days[f"{now:%Y-%m-%d}"] = out["rule_blackout_min_today"]
            rec["rule_blackout_min"] = dict(sorted(days.items())[-14:])
        rec["last_poll"] = {k: out.get(k) for k in ("new", "scored", "llm_scored", "rule_scored", "rule_repeats",
                                                    "llm_skipped", "rules_error")}
        self.scoring_path.write_text(json.dumps(rec, indent=1))

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
                               "new": 0, "scored": 0, "llm_scored": 0, "rule_scored": 0, "cost_usd": 0.0}
        if not frames:
            self._record_scoring(now, 0, out)
            return out
        items = pd.concat(frames, ignore_index=True).drop_duplicates("item_id")
        # from the oldest item in the feeds, not a fixed window: press-release feeds keep items for weeks
        seen = self.store.read("news", start=items["ts_utc"].min(), columns=["item_id"])
        if not seen.empty:
            items = items[~items["item_id"].isin(set(seen["item_id"]))]
        if items.empty:
            self._record_scoring(now, 0, out)
            return out
        todo = items[worth_scoring(items)].sort_values("ts_utc", ascending=False)   # newest first
        rules: list[RuleScore] = []
        repeat_of: dict[str, str] = {}
        day = now.normalize()
        try:                                             # free and deterministic: every prefiltered item, every poll
            rules = score_rules(todo, self.source_weights, self.rule_min_confidence)
            if any(r.shock for r in rules):              # only the first report of an event is a shock
                earlier = self.store.read("news", start=min(day, now - self.rule_lookback) - pd.Timedelta(hours=1))
                marked = mark_repeats(todo, rules, earlier, self.rule_lookback)
                rules = [s for s, _ in marked]
                repeat_of = {s.id: r for s, r in marked if r}
        except Exception as exc:                         # a rules bug leaves the items to the LLM; health fails at once
            out["rules_error"] = f"{type(exc).__name__}: {exc}"[:200]
            rules = []
        scored: list = []
        for start in range(0, len(todo), BATCH):
            room = min(self.daily_cap - self._spent_today(now), self.ledger.remaining(now) if self.ledger else 0.0)
            if self.client is None or room < 0.05:
                out["llm_skipped"] = "no client" if self.client is None else "news budget used"
                break
            try:
                s, usage = score_batch(self.client, self.model, todo.iloc[start:start + BATCH])
            except Exception as exc:                     # API trouble: keep these unstored, try again next round,
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
                # except a rule shock: stored now on its rule score, so an API outage never delays the blackout
                shocks = {r.id for r in rules if r.shock}
                retry = set(todo["item_id"].iloc[start:]) - shocks
                items = items[~items["item_id"].isin(retry)]
                break
            _, usd = cost_of(usage)
            self._add_spend(now, usd)
            out["cost_usd"] += usd
            scored += s
        # rules first, the LLM after: an LLM score takes precedence and the rule score stays in the rule_* columns
        items = apply_scores(apply_rule_columns(items, rules), rules, scorer=SCORER)
        items = apply_scores(items, scored, scorer=LLM_SCORER)
        items["rule_repeat_of"] = items["item_id"].map(repeat_of).fillna("")
        items["llm_active"] = self.client is not None   # a rule score beside an active client = the budget ran out
        out["rule_repeats"] = len(repeat_of)
        out["new"], out["scored"] = len(items), int(items["scored"].sum())
        out["llm_scored"] = int((items["scorer"] == LLM_SCORER).sum())
        out["rule_scored"] = int((items["scorer"] == SCORER).sum())
        items["written_utc"] = max(pd.Timestamp(self.clock()), now)     # after scoring; never before the receipt
        self.store.append("news", items, source="rss")
        out["rule_blackout_min_today"] = rule_blackout_minutes(self.store.read("news", start=day), day,
                                                               self.blackout_min, self.shock_min_relevance)
        self._record_scoring(now, len(todo), out)
        return out
