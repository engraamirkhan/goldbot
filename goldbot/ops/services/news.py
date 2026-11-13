"""The headline collector service (`python -m goldbot.ops.run news`). Moved verbatim from goldbot/ops/run.py, which
re-exports it."""
from __future__ import annotations

from goldbot.ops.services._base import log


def run_news() -> None:
    import time
    from pathlib import Path

    import pandas as pd

    from goldbot.agents.runner import SpendLedger
    from goldbot.config import load_settings
    from goldbot.data.news import fetch
    from goldbot.data.news_collector import NewsCollector
    from goldbot.data.store import Store
    from goldbot.ops import accounts
    from goldbot.ops.health import start_heartbeat
    settings = load_settings()
    start_heartbeat("state", "news")              # a thread: the poll sleeps poll_seconds (300 s) between rounds
    key = accounts.get_secret("anthropic-api-key")
    client = None
    if key:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
    else:
        log.warning("no anthropic-api-key in the keyring: headlines are scored by the free rules-v1 scorer only")
    collector = NewsCollector(Store(settings.data_root), "state", settings.news.feeds, fetch, client,
                              SpendLedger(Path("state") / "agent_spend.json", settings.agents.monthly_cap_usd),
                              settings.news.model, settings.news.daily_cap_usd,
                              rule_min_confidence=settings.news.rule_shock_min_confidence,
                              source_weights=settings.news.source_weights,
                              rule_dedupe_hours=settings.news.rule_shock_dedupe_hours,
                              blackout_min=settings.news.shock_blackout_min,
                              shock_min_relevance=settings.news.shock_min_relevance)
    while True:
        try:
            log.info("news: %s", collector.poll(pd.Timestamp.now("UTC")))
        except Exception:
            log.exception("news poll")
        time.sleep(settings.news.poll_seconds)
