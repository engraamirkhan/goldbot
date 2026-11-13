"""LLM news features (R139, plan item 8): point-in-time columns from the headline collector's own forward history.

The contract under test: a headline reaches a bar only through `asof_join` on `available_utc` = the collector's
write time (`written_utc`; for rows from before that stamp, the next poll's `received_utc`, which the sequential
collector loop proves came after the write), never the publication time; a headline the collector first saw long after it
was published (a backfill: the first poll of a feed, a press-release archive) never counts; a collector outage reads
NaN, not "no news".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data import news_features as nf
from goldbot.features import news_llm, registry

T0 = pd.Timestamp("2026-11-02 00:00", tz="UTC")
WRITE = pd.Timedelta(minutes=2)             # the poll's write, after scoring


def _row(i: int, received: pd.Timestamp, *, published: pd.Timestamp | None = None, title: str = "Fed holds rates",
         relevance: float = 0.8, rates: str = "dovish", risk: str = "risk_off", dollar: str = "negative",
         surprise: str = "none", shock: bool = False, source: str = "rss",
         written: pd.Timestamp | None = None) -> dict:
    return {"item_id": f"id{i}", "ts_utc": published if published is not None else received, "received_utc": received,
            "written_utc": written if written is not None else received + WRITE,
            "source": source, "title": title, "summary": "", "link": f"https://x/{i}", "scored": not np.isnan(relevance),
            "relevance": relevance, "rates": rates if not np.isnan(relevance) else "",
            "risk": risk if not np.isnan(relevance) else "", "dollar": dollar if not np.isnan(relevance) else "",
            "surprise": surprise if not np.isnan(relevance) else "", "shock": shock}


def _news(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df["received_utc"] = pd.to_datetime(df["received_utc"], utc=True)
    df["written_utc"] = pd.to_datetime(df["written_utc"], utc=True)
    return df


def _background(days: int = 10, every: str = "30min") -> list[dict]:
    """An always-polling collector: one irrelevant headline every 30 minutes (keeps the outage rule quiet)."""
    stamps = pd.date_range(T0, T0 + pd.Timedelta(days=days), freq=every)
    return [_row(10_000 + k, t, title="Company earnings call", relevance=0.05, rates="neutral", risk="neutral",
                 dollar="neutral") for k, t in enumerate(stamps)]


def _bars(start: pd.Timestamp, n: int, tf: str = "1h") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=tf)
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(tf), "open": 2000.0, "high": 2001.0,
                         "low": 1999.0, "close": 2000.0, "spread": 0.2, "tick_count": 100.0})


def test_headline_sentiment_is_gold_signed_and_relevance_weighted():
    h = nf.headline_features(_news([
        _row(1, T0, rates="dovish", risk="risk_off", dollar="negative", relevance=1.0),
        _row(2, T0, rates="hawkish", risk="risk_on", dollar="positive", relevance=0.5),
        _row(3, T0, rates="neutral", risk="neutral", dollar="neutral", relevance=0.9)]))
    s = h.set_index("item_id")["sentiment"]
    assert s["id1"] == pytest.approx(1.0)
    assert s["id2"] == pytest.approx(-0.5)
    assert s["id3"] == pytest.approx(0.0)


def test_surprise_is_gold_signed_a_us_data_beat_is_bearish_for_gold():
    h = nf.headline_features(_news([
        _row(1, T0, title="US payrolls beat forecasts", surprise="beat"),
        _row(2, T0, title="US CPI misses", surprise="miss"),
        _row(3, T0, surprise="inline")])).set_index("item_id")["surprise"]
    assert (h["id1"], h["id2"], h["id3"]) == (-1.0, 1.0, 0.0)


def test_event_types_from_the_llm_fields_and_the_headline_text():
    h = nf.headline_features(_news([
        _row(1, T0, title="Powell signals a pause at the FOMC"),
        _row(2, T0, title="Missile strike raises tension in the Gulf", rates="neutral", dollar="neutral", shock=True),
        _row(3, T0, title="Gold ETF holdings see record inflows", rates="neutral", dollar="neutral"),
        _row(4, T0, title="PBOC adds gold to reserves for a tenth month", rates="neutral", dollar="neutral"),
        _row(5, T0, title="Dollar index slides after jobs report", rates="neutral", dollar="negative")])
    ).set_index("item_id")
    assert h.loc["id1", "evt_fed"] and not h.loc["id1", "evt_geo"]
    assert h.loc["id2", "evt_geo"] and not h.loc["id2", "evt_fed"]
    assert h.loc["id3", "evt_etf"]
    assert h.loc["id4", "evt_cb"]
    assert h.loc["id5", "evt_usd"]


def test_available_utc_is_the_write_time_never_the_publication_time():
    pub = T0 + pd.Timedelta(minutes=10)
    rec = T0 + pd.Timedelta(minutes=40)
    h = nf.headline_features(_news([_row(1, rec, published=pub, written=rec + pd.Timedelta(minutes=7))]))
    assert h["available_utc"].iloc[0] == rec + pd.Timedelta(minutes=7)


def test_rows_without_a_write_stamp_are_available_at_the_next_poll_and_the_last_poll_waits():
    """Legacy rows (collected before `written_utc`): the next poll's receive stamp bounds the write (sequential loop)."""
    rows = [_row(1, T0), _row(2, T0), _row(3, T0 + pd.Timedelta(minutes=5)), _row(4, T0 + pd.Timedelta(minutes=10))]
    df = _news(rows).drop(columns=["written_utc"])
    h = nf.headline_features(df).set_index("item_id")["available_utc"]
    assert h["id1"] == h["id2"] == T0 + pd.Timedelta(minutes=5)
    assert h["id3"] == T0 + pd.Timedelta(minutes=10)
    assert "id4" not in h.index                                   # no later poll yet: its write time is unproven
    mixed = _news(rows)
    mixed.loc[0, "written_utc"] = pd.NaT
    m = nf.headline_features(mixed).set_index("item_id")["available_utc"]
    assert m["id1"] == T0 + pd.Timedelta(minutes=5) and m["id2"] == T0 + WRITE


def test_backfilled_headlines_never_count():
    """First poll of a feed: items published weeks before the collector saw them are dropped, not stamped as new."""
    rec = T0 + pd.Timedelta(days=3)
    h = nf.headline_features(_news([
        _row(1, rec, published=rec - pd.Timedelta(days=20)),
        _row(2, rec, published=rec - nf.MAX_STALE - pd.Timedelta(minutes=1)),
        _row(3, rec, published=rec - pd.Timedelta(minutes=30))]))
    assert list(h["item_id"]) == ["id3"]


def test_only_the_collector_source_counts():
    h = nf.headline_features(_news([_row(1, T0), _row(2, T0, source="import")]))
    assert list(h["item_id"]) == ["id1"]


def test_grid_counts_and_sentiment_windows():
    rows = _background() + [_row(1, T0 + pd.Timedelta(hours=5, minutes=3)),
                            _row(2, T0 + pd.Timedelta(hours=7), rates="hawkish", risk="risk_on", dollar="positive")]
    g = nf.news_grid(_news(rows)).set_index("available_utc")
    at = T0 + pd.Timedelta(hours=7, minutes=15)
    assert g.loc[at, "news_count_1h"] == 1           # only id2 in (6:15, 7:15]
    assert g.loc[at, "news_count_4h"] == 2
    assert g.loc[at, "news_sent_4h"] == pytest.approx(0.8 - 0.8)
    assert g.loc[at, "news_sent_1h"] == pytest.approx(-0.8)
    assert g.loc[at, "news_evt_fed_4h"] == 2


def test_collector_outage_reads_nan_not_quiet():
    rows = [r for r in _background(days=3) if not (T0 + pd.Timedelta(hours=20) < r["received_utc"]
                                                   <= T0 + pd.Timedelta(hours=40))]
    g = nf.news_grid(_news(rows)).set_index("available_utc")
    assert np.isnan(g.loc[T0 + pd.Timedelta(hours=36), "news_count_1h"])
    assert g.loc[T0 + pd.Timedelta(hours=10), "news_count_1h"] == 0


def test_unscored_share_counts_prefiltered_items_the_budget_left_unscored():
    rows = _background(days=2) + [_row(1, T0 + pd.Timedelta(hours=3), title="Gold rallies", relevance=np.nan),
                                  _row(2, T0 + pd.Timedelta(hours=3), title="Gold dips")]
    g = nf.news_grid(_news(rows)).set_index("available_utc")
    at = T0 + pd.Timedelta(hours=4)
    assert g.loc[at, "news_unscored_share_24h"] == pytest.approx(0.5)
    # half the prefiltered headlines unscored (> MAX_UNSCORED): the budget cut-off would bias the counts, so NaN
    for c in ("news_count_1h", "news_count_4h", "news_count_24h", "news_sent_24h", "news_evt_fed_4h"):
        assert np.isnan(g.loc[at, c]), c
    assert g.loc[T0 + pd.Timedelta(hours=2), "news_count_4h"] == 0      # before the unscored item: a known quiet


def test_outage_is_masked_before_the_z_scores():
    """An outage's stamps never enter the z-score's trailing mean and std."""
    rows = _background(days=20)
    rng = np.random.default_rng(5)
    hits = [_row(i, T0 + pd.Timedelta(minutes=int(m))) for i, m in enumerate(sorted(rng.integers(0, 20 * 1440, 600)))]
    gap = (T0 + pd.Timedelta(days=9), T0 + pd.Timedelta(days=11))
    keep = [r for r in rows + hits if not (gap[0] < r["received_utc"] <= gap[1])]
    g = nf.news_grid(_news(keep))
    down = g["news_count_4h"].isna()
    assert down.any()
    assert g.loc[down, "news_sent_z_4h"].isna().all()
    s = g["news_sent_4h"]
    n_look, n_min = int(nf.Z_LOOKBACK / nf.GRID), int(nf.Z_MIN / nf.GRID)
    r = s.rolling(n_look, min_periods=n_min)
    pd.testing.assert_series_equal(g["news_sent_z_4h"], (s - r.mean()) / r.std(), check_names=False)


def test_sentiment_z_needs_its_warm_up():
    g = nf.news_grid(_news(_background(days=10)))
    early = g[g["available_utc"] < T0 + nf.Z_MIN]
    assert early["news_sent_z_4h"].isna().all()


def _enable():
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    name = news_llm.enable()
    return name, feats, signed


def _restore(feats: dict, signed: dict) -> None:
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def test_feature_is_truncation_invariant_a_future_headline_changes_no_earlier_bar():
    """The as-of contract: seed headlines after the bars' closes; every earlier bar must be bit-identical."""
    name, feats, signed = _enable()
    try:
        base = _background(days=10) + [_row(i, T0 + pd.Timedelta(hours=6 * i, minutes=7)) for i in range(1, 30)]
        bars = _bars(T0 + pd.Timedelta(days=2), 96)
        cut = bars["visible_at"].iloc[47]
        future = [_row(900 + k, cut + pd.Timedelta(minutes=1 + k), relevance=1.0, rates="hawkish", risk="risk_on",
                       dollar="positive", shock=True, surprise="beat") for k in range(20)]
        a = registry.build_features(bars, [name], ctx={"news": _news(base)})
        b = registry.build_features(bars, [name], ctx={"news": _news(base + future)})
        early = bars["visible_at"] <= cut
        pd.testing.assert_frame_equal(a[early], b[early])
        assert not a[~early].equals(b[~early])                    # the seeded release is seen once it is available
    finally:
        _restore(feats, signed)


def test_a_headline_published_before_the_bar_but_received_after_is_not_seen():
    name, feats, signed = _enable()
    try:
        bars = _bars(T0 + pd.Timedelta(days=2), 4)
        close = bars["visible_at"].iloc[0]
        late = _row(1, close + pd.Timedelta(minutes=20), published=close - pd.Timedelta(minutes=30), relevance=1.0)
        x = registry.build_features(bars, [name], ctx={"news": _news(_background(days=4) + [late])})
        assert x["news_count_1h"].iloc[0] == 0
        assert x["news_count_1h"].iloc[1] == 1
    finally:
        _restore(feats, signed)


def test_no_news_input_gives_the_fixed_column_set_all_nan():
    name, feats, signed = _enable()
    try:
        x = registry.build_features(_bars(T0, 5), [name])
        assert list(x.columns[1:]) == list(news_llm.COLUMNS)
        assert x[list(news_llm.COLUMNS)].isna().all().all()
    finally:
        _restore(feats, signed)


def test_opt_in_and_not_in_the_default_set():
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES
    assert news_llm.NAME in OPT_IN_FEATURES and news_llm.NAME not in DEFAULT_FEATURE_NAMES
    assert len(news_llm.COLUMNS) <= 39


def test_news_is_collect_only_until_twelve_months_of_forward_data():
    assert news_llm.COLLECT_ONLY_MONTHS == 12
    assert "collect-only" in (news_llm.__doc__ or "") and "collect-only" in news_llm.SCREEN_NOTE


def test_shuffled_control_keeps_each_headline_within_its_utc_day():
    rows = _background(days=3)
    ctl = nf.shuffled_control(_news(rows), seed=7)
    orig = _news(rows)
    assert sorted(ctl["received_utc"]) == sorted(orig["received_utc"])
    m = ctl.merge(orig, on="item_id", suffixes=("", "_o"))
    assert (m["received_utc"].dt.floor("D") == m["received_utc_o"].dt.floor("D")).all()
    assert (m["received_utc"] - m["ts_utc"] == m["received_utc_o"] - m["ts_utc_o"]).all()
    assert not (m["received_utc"] == m["received_utc_o"]).all()


def test_coverage_reports_the_forward_history():
    rows = _background(days=3) + [_row(1, T0 + pd.Timedelta(days=1), published=T0 - pd.Timedelta(days=9))]
    c = nf.coverage(_news(rows))
    assert c["first_received_utc"] == T0.isoformat()
    assert c["days"] == pytest.approx(3.0)
    assert c["backfilled_dropped"] == 1


def _research_pass():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "research_pass", Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_research_pass_adds_news_only_when_given_and_stamps_availability():
    from goldbot.research.pipeline import research_feature_names
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        mod = _research_pass()
        assert mod.feature_ctx(None, None) == (None, None)
        ctx, names = mod.feature_ctx(None, None, news=_news(_background(days=1)))
        assert ctx is not None and names == [*research_feature_names(ctx), news_llm.NAME]
        assert (ctx["news"]["available_utc"] == ctx["news"]["written_utc"]).all()
    finally:
        _restore(feats, signed)


@pytest.mark.integration
def test_research_frame_passes_the_pipelines_lookahead_check():
    """The pipeline's own leakage check (history and the news table cut at 70% by available_utc)."""
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import build_decision_frame, lookahead_check
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        name = news_llm.enable()
        bars = resample_bars(ticks_to_1m(synthetic_ticks("2026-11-02", "2026-11-28", ticks_per_minute=2, seed=7)),
                             "1h").reset_index(drop=True)
        rng = np.random.default_rng(3)
        hits = [_row(i, T0 + pd.Timedelta(minutes=int(m)), relevance=float(rng.random()),
                     rates=str(rng.choice(["hawkish", "dovish", "neutral"])), surprise=str(rng.choice(["beat", "miss"])))
                for i, m in enumerate(sorted(rng.integers(0, 26 * 24 * 60, 400)))]
        news = nf.ctx_frame(_news(_background(days=27) + hits))
        out = lookahead_check(bars, None, feature_names=[name], ctx={"news": news})
        assert out["lookahead_columns"] == [] and out["columns_checked"] == len(news_llm.COLUMNS)
        _, full = build_decision_frame(bars, None, [name], {"news": news})
        assert full["news_count_24h"].notna().mean() > 0.8
    finally:
        _restore(feats, signed)
