"""rules-v1 headline scorer (BACKLOG 231-234, R144): the free, deterministic fallback when no LLM client is configured.

The contract under test: the rules score a labelled set with sensible shock precision; a collector without a client
scores every prefiltered headline (`scorer` = rules-v1) so the shock blackout can fire; an LLM score takes precedence
and the rule score is kept beside it; a rule shock blocks entries and never an exit; the news features read rule rows
flagged; health fails when headlines arrive but none is scored for 24 h.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from goldbot.data import news_features as nf
from goldbot.data.news import parse_feed, shock_window
from goldbot.data.news_collector import NewsCollector
from goldbot.data.news_rules import SCORER, RuleScore, score_rules, score_text, shock_confidence
from goldbot.data.store import Store
from tests.test_news import NOW, RSS, FakeClient, _collector

LABELLED = yaml.safe_load((Path(__file__).parent / "fixtures" / "news_rules" / "labelled.yaml").read_text())
WEIGHTS = {"forexlive": 0.9, "fed_press": 1.0, "bls": 1.0}


def _scores() -> list[tuple[dict, RuleScore]]:
    df = pd.DataFrame([{"item_id": str(i), "source": r["source"], "title": r["title"], "summary": ""}
                       for i, r in enumerate(LABELLED)])
    return list(zip(LABELLED, score_rules(df, WEIGHTS, 0.8)))


# ------------------------------------------------------------------------------------------------ the classifier
def test_rules_flag_shocks_on_the_labelled_set_with_high_precision_and_recall():
    pairs = _scores()
    tp = sum(r["shock"] and s.shock for r, s in pairs)
    fp = [r["title"] for r, s in pairs if s.shock and not r["shock"]]
    fn = [r["title"] for r, s in pairs if r["shock"] and not s.shock]
    assert tp / max(tp + len(fp), 1) >= 0.9, fp           # a false shock costs entries; keep it rare
    assert tp / max(tp + len(fn), 1) >= 0.8, fn
    assert all(s.relevance >= 0.7 for r, s in pairs if s.shock)   # a rule shock clears the blackout's relevance bar


def test_rules_read_rates_and_dollar_direction_on_the_labelled_set():
    pairs = _scores()
    for field in ("rates", "dollar"):
        lab = [(r[field], getattr(s, field), r["title"]) for r, s in pairs if r[field] is not None]
        wrong = [t for want, got, t in lab if want != got]
        assert len(lab) >= 8 and 1 - len(wrong) / len(lab) >= 0.85, (field, wrong)


def test_a_negation_cancels_a_shock_and_flips_a_rate_direction():
    assert score_text("a", "Iran fires missiles at Israel").shock
    assert not score_text("a", "Iran denies it fired missiles at Israel").shock
    assert score_text("a", "Fed signals a rate cut").rates == "dovish"
    assert score_text("a", "Fed rules out a rate cut").rates == "hawkish"


def test_a_hedged_or_low_trust_shock_is_below_the_confidence_bar():
    assert shock_confidence("Iran fires missiles at Israel") >= 0.9
    assert shock_confidence("Iran could fire missiles at Israel") < 0.8
    assert shock_confidence("Did Iran fire missiles at Israel?") < 0.8
    assert shock_confidence("Iran fires missiles at Israel", source_weight=0.5) < 0.8
    assert not score_text("a", "Iran fires missiles at Israel", source_weight=0.5).shock


def test_a_scheduled_release_is_never_a_shock_however_large_the_surprise():
    for t in ("US CPI far hotter than expected at 6.1%", "Federal Reserve issues FOMC statement",
              "Non-farm payrolls -300K, much weaker than expected"):
        assert not score_text("a", t).shock, t


def test_rules_are_deterministic():
    a = [s.model_dump() for _, s in _scores()]
    b = [s.model_dump() for _, s in _scores()]
    assert a == b


# ------------------------------------------------------------------------------------------------ the collector
def test_without_a_client_every_prefiltered_headline_is_scored_by_the_rules(tmp_path):
    c = _collector(tmp_path, None)
    out = c.poll(NOW)
    assert out["llm_skipped"] == "no client" and out["scored"] == 3 and out["rule_scored"] == 3
    stored = Store(tmp_path / "data").read("news").set_index("title")
    iran = stored.loc["Iran fires missiles at Israel, gold jumps"]
    assert iran["scorer"] == SCORER and bool(iran["shock"]) and iran["relevance"] >= 0.7
    assert bool(iran["rule_shock"]) and iran["rule_shock_confidence"] >= 0.8
    assert stored.loc["Fed's Waller: more cuts likely this year", "rates"] == "dovish"
    assert stored.loc["Local football club signs striker", "scorer"] == ""          # not prefiltered: unscored
    assert pd.isna(stored.loc["Local football club signs striker", "relevance"])
    assert (pd.to_datetime(stored["written_utc"], utc=True) >= NOW).all()           # R139 write stamp on rule rows


def test_an_exhausted_llm_budget_falls_back_to_the_rules(tmp_path):
    out = _collector(tmp_path, FakeClient(), cap=0.0).poll(NOW)
    assert out["llm_skipped"] == "news budget used" and out["rule_scored"] == 3 and out["llm_scored"] == 0


def test_an_llm_score_takes_precedence_and_the_rule_score_is_kept(tmp_path):
    c = _collector(tmp_path, FakeClient(cost_tokens=1000))        # the fake LLM calls nothing a shock
    out = c.poll(NOW)
    assert out["llm_scored"] == 3 and out["rule_scored"] == 0
    stored = Store(tmp_path / "data").read("news").set_index("title")
    iran = stored.loc["Iran fires missiles at Israel, gold jumps"]
    assert iran["scorer"] == "llm" and not bool(iran["shock"]) and iran["relevance"] == pytest.approx(0.4)
    assert bool(iran["rule_shock"]) and iran["rule_relevance"] >= 0.7            # both kept
    assert shock_window(Store(tmp_path / "data").read("news"), NOW + pd.Timedelta(minutes=5), 30, 0.7) is None


def test_the_collector_reads_its_confidence_bar_and_source_weights(tmp_path):
    c = _collector(tmp_path, None)
    c.rule_min_confidence = 0.99
    c.poll(NOW)
    assert not Store(tmp_path / "data").read("news")["shock"].astype(bool).any()
    c2 = _collector(tmp_path / "b", None)
    c2.source_weights = {"forexlive": 0.3}
    c2.poll(NOW)
    assert not Store(tmp_path / "b" / "data").read("news")["shock"].astype(bool).any()


# ------------------------------------------------------------------------------------------------ the blackout
def test_a_rule_shock_blocks_entries_and_never_blocks_an_exit(tmp_path):
    from goldbot.execution.broker import Tick
    from tests.test_exit_policies import SESSION, _engine, _enter, _forbid_approvals
    _collector(tmp_path, None).poll(NOW)
    eng, pb = _engine(tmp_path, SESSION, news_blackout=True, data_root=str(tmp_path / "data"))
    _forbid_approvals(eng)
    at = NOW + pd.Timedelta(minutes=10)
    pid = _enter(eng, pb, SESSION, at - pd.Timedelta(minutes=5), 2000.0)
    tick = Tick(ts_utc=at, bid=2000.0, ask=2000.0)
    pb.on_tick(tick)
    eng._refresh_account(tick)
    assert eng.state.in_blackout and eng.state.blackout_kind == "news_shock"
    assert "news_blackout" in eng.gate.account_reasons(eng.state)                     # entries refused
    eng._blackout_close(pd.DataFrame(), at, {})                                     # a shock never force-closes
    assert pid in eng.open
    for _ in range(SESSION.label_spec.max_bars + 1):                               # the time exit still runs
        eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and eng.decisions[-1]["action"] == "time_exit"


# ------------------------------------------------------------------------------------------------ the features
def test_news_features_read_rule_rows_and_flag_their_share():
    t0 = pd.Timestamp("2026-11-02 00:00", tz="UTC")
    rows = []
    for k, scorer in enumerate(["llm", SCORER, SCORER, ""]):
        rec = t0 + pd.Timedelta(minutes=30 * k)
        rows.append({"item_id": f"i{k}", "ts_utc": rec, "received_utc": rec, "written_utc": rec + pd.Timedelta(minutes=1),
                     "source": "rss", "title": "Gold rises as Fed signals cuts", "summary": "", "scored": True,
                     "relevance": 0.8, "rates": "dovish", "risk": "neutral", "dollar": "neutral", "surprise": "none",
                     "shock": False, "scorer": scorer})
    h = nf.headline_features(pd.DataFrame(rows))
    assert h["rule_scored"].tolist() == [False, True, True, False]                 # "" = a pre-rules LLM row
    assert h["relevant"].all()
    g = nf.news_grid(pd.DataFrame(rows), end=t0 + pd.Timedelta(hours=2)).set_index("available_utc")
    assert g.loc[t0 + pd.Timedelta(hours=2), "news_rules_share_24h"] == pytest.approx(0.5)
    assert g.loc[t0 + pd.Timedelta(hours=2), "news_count_4h"] == 4


# ------------------------------------------------------------------------------------------------ health
def test_poll_records_when_headlines_last_arrived_and_were_scored(tmp_path):
    c = _collector(tmp_path, None)
    c.poll(NOW)
    st = json.loads((tmp_path / "news_scoring.json").read_text())
    assert st["last_prefiltered_utc"] == NOW.isoformat() and st["last_scored_utc"] == NOW.isoformat()
    assert st["since_utc"] == NOW.isoformat() and st["last_poll"]["rule_scored"] == 3


def test_health_fails_when_headlines_arrive_but_none_is_scored_for_a_day(tmp_path):
    from goldbot.ops import health
    from tests.test_health import NOW as HNOW
    from tests.test_health import make_ctx, write
    fresh = (HNOW - pd.Timedelta(minutes=4)).isoformat()
    write(tmp_path / "news_feeds.json", {"a": {"ok": True, "items": 3, "ts": fresh}})
    assert health.check_news(make_ctx(tmp_path)).status == "ok"                                   # no record yet: not judged
    rec = tmp_path / "news_scoring.json"
    write(rec, {"since_utc": (HNOW - pd.Timedelta(days=3)).isoformat(), "last_prefiltered_utc": fresh,
                "last_scored_utc": None})
    c = health.check_news(make_ctx(tmp_path))
    assert c.status == "fail" and "0 headlines scored in 24 h" in c.reason
    write(rec, {"since_utc": (HNOW - pd.Timedelta(days=3)).isoformat(), "last_prefiltered_utc": fresh,
                "last_scored_utc": (HNOW - pd.Timedelta(hours=25)).isoformat()})
    assert health.check_news(make_ctx(tmp_path)).status == "fail"
    write(rec, {"since_utc": (HNOW - pd.Timedelta(hours=2)).isoformat(), "last_prefiltered_utc": fresh,
                "last_scored_utc": None})
    assert health.check_news(make_ctx(tmp_path)).status == "ok"                                   # under a day of record
    write(rec, {"since_utc": (HNOW - pd.Timedelta(days=3)).isoformat(),
                "last_prefiltered_utc": (HNOW - pd.Timedelta(hours=30)).isoformat(), "last_scored_utc": None})
    assert health.check_news(make_ctx(tmp_path)).status == "ok"                                   # nothing arrived: nothing to score
    write(rec, {"since_utc": (HNOW - pd.Timedelta(days=3)).isoformat(), "last_prefiltered_utc": fresh,
                "last_scored_utc": fresh})
    assert health.check_news(make_ctx(tmp_path)).status == "ok"


def test_parse_feed_starts_rows_unscored_with_empty_rule_columns():
    df = parse_feed(RSS, "forexlive", NOW)
    assert (df["scorer"] == "").all() and df["rule_relevance"].isna().all() and not df["rule_shock"].any()


# ------------------------------------------------------------------------------------------------ review round 1
def test_ongoing_conflict_and_market_reaction_framing_is_not_a_shock():
    """Safety review HIGH: a running war's casualty updates and market-reaction lines are not new events."""
    for t in ("Israeli airstrikes kill 12 in Gaza", "Markets shrug off airstrikes",
              "Gold steady as missile strikes continue", "Airstrikes continue for a third day",
              "Death toll from airstrikes rises to 40", "Stocks rise amid ongoing airstrikes"):
        assert not score_text("a", t, source_weight=0.9).shock, t
    assert score_text("a", "Israel launches airstrikes on Iran", source_weight=0.9).shock


def test_a_repeat_report_of_the_same_event_across_feeds_is_not_a_new_shock():
    from goldbot.data.news_rules import mark_repeats
    df = pd.DataFrame([
        {"item_id": "a", "source": "forexlive", "title": "Iran fires missiles at Israel", "summary": "",
         "received_utc": NOW},
        {"item_id": "b", "source": "reuters", "title": "Iran fires ballistic missiles at Israel, sirens in Tel Aviv",
         "summary": "", "received_utc": NOW + pd.Timedelta(minutes=20)},
        {"item_id": "c", "source": "forexlive", "title": "North Korea fires missiles toward Japan", "summary": "",
         "received_utc": NOW + pd.Timedelta(minutes=40)},
    ])
    scores = score_rules(df, {"forexlive": 0.9, "reuters": 0.9}, 0.8)
    assert all(s.shock for s in scores)
    six = pd.Timedelta(hours=6)
    out = mark_repeats(df, scores, earlier=pd.DataFrame(), lookback=six)
    assert [r for _, r in out] == ["", "a", ""]                     # b repeats a; c is a different event
    assert [s.shock for s, _ in out] == [True, False, True]
    stored = df.iloc[[0]].assign(rule_shock=True)
    assert mark_repeats(df.iloc[[1]], [scores[1]], earlier=stored, lookback=six)[0][1] == "a"   # stored reports count
    old = stored.assign(received_utc=NOW - pd.Timedelta(hours=7))
    assert mark_repeats(df.iloc[[1]], [scores[1]], earlier=old, lookback=six)[0][0].shock      # outside the lookback


def test_the_collector_stores_a_repeat_rule_shock_unflagged(tmp_path):
    rss2 = RSS.replace("Iran fires missiles at Israel, gold jumps", "Iran fires more missiles at Israel") \
        .replace("https://x/1", "https://x/9").replace("05:40:00", "05:55:00")
    pages = iter([RSS, rss2])
    c = NewsCollector(Store(tmp_path / "data"), tmp_path, {"fl": "u"}, lambda url: next(pages), None, None, "m", 0.0)
    c.poll(NOW)
    c.poll(NOW + pd.Timedelta(minutes=20))
    news = Store(tmp_path / "data").read("news").set_index("title")
    assert bool(news.loc["Iran fires missiles at Israel, gold jumps", "shock"])
    assert not bool(news.loc["Iran fires more missiles at Israel", "shock"])
    assert not bool(news.loc["Iran fires more missiles at Israel", "rule_shock"])
    assert news.loc["Iran fires more missiles at Israel", "rule_repeat_of"] != ""


def _rule_shocks(times: list[pd.Timestamp], scorer: str = SCORER) -> pd.DataFrame:
    return pd.DataFrame({"title": [f"s{i}" for i in range(len(times))], "received_utc": times,
                         "shock": True, "relevance": 0.9, "scorer": scorer})


def test_rule_shock_blackout_minutes_are_capped_per_utc_day():
    from goldbot.data.news import rule_blackout_minutes
    day = pd.Timestamp("2026-10-12", tz="UTC")
    times = [day + pd.Timedelta(hours=h) for h in range(1, 8)]       # 7 separate shocks x 30 min = 210 min
    news = _rule_shocks(times)
    assert shock_window(news, times[3] + pd.Timedelta(minutes=5), 30, 0.7, rule_cap_min=120) is not None
    assert shock_window(news, times[4] + pd.Timedelta(minutes=5), 30, 0.7, rule_cap_min=120) is None   # cap used
    assert shock_window(news, times[4] + pd.Timedelta(minutes=5), 30, 0.7) is not None                  # no cap
    llm = _rule_shocks(times, scorer="llm")
    assert shock_window(llm, times[6] + pd.Timedelta(minutes=5), 30, 0.7, rule_cap_min=120) is not None  # LLM uncapped
    assert rule_blackout_minutes(news, day, 30, 0.7) == pytest.approx(210)
    nxt = _rule_shocks([day + pd.Timedelta(days=1, hours=1)])
    assert shock_window(pd.concat([news, nxt]), day + pd.Timedelta(days=1, hours=1, minutes=5), 30, 0.7,
                        rule_cap_min=120) is not None                # a new UTC day starts a new cap


def test_overlapping_rule_shock_windows_count_their_union_once():
    from goldbot.data.news import rule_blackout_minutes
    day = pd.Timestamp("2026-10-12", tz="UTC")
    news = _rule_shocks([day + pd.Timedelta(hours=1), day + pd.Timedelta(hours=1, minutes=10)])
    assert rule_blackout_minutes(news, day, 30, 0.7) == pytest.approx(40)


def test_the_engine_and_settings_carry_the_rule_shock_cap():
    from goldbot.config import load_settings
    from goldbot.engine import EngineConfig
    assert EngineConfig(account_id="x", broker_name="icm").shock_rule_cap_min == 120
    assert load_settings().news.rule_shock_cap_min_per_day == 120


def test_collector_logs_rule_blackout_minutes_and_health_alerts_at_the_cap(tmp_path):
    from goldbot.ops import health
    from tests.test_health import NOW as HNOW
    from tests.test_health import make_ctx, write
    _collector(tmp_path, None).poll(NOW)
    st = json.loads((tmp_path / "news_scoring.json").read_text())
    assert st["rule_blackout_min"][f"{NOW:%Y-%m-%d}"] == pytest.approx(30)
    fresh = (HNOW - pd.Timedelta(minutes=4)).isoformat()
    h = tmp_path / "h"
    write(h / "news_feeds.json", {"a": {"ok": True, "items": 3, "ts": fresh}})
    write(h / "news_scoring.json", {"since_utc": fresh, "last_prefiltered_utc": fresh, "last_scored_utc": fresh,
                                    "rule_blackout_min": {f"{HNOW:%Y-%m-%d}": 120.0}})
    cap = health.check_news_blackout_cap(make_ctx(h))
    assert cap.status == "warn" and "120" in cap.reason and health._alerting(cap.name, cap.status)
    write(h / "news_scoring.json", {"since_utc": fresh, "last_prefiltered_utc": fresh, "last_scored_utc": fresh,
                                    "rule_blackout_min": {f"{HNOW:%Y-%m-%d}": 60.0}})
    assert health.check_news_blackout_cap(make_ctx(h)).status == "ok"


def test_health_fails_on_the_first_rules_error(tmp_path):
    from goldbot.ops import health
    from tests.test_health import NOW as HNOW
    from tests.test_health import make_ctx, write
    fresh = (HNOW - pd.Timedelta(minutes=4)).isoformat()
    write(tmp_path / "news_feeds.json", {"a": {"ok": True, "items": 3, "ts": fresh}})
    write(tmp_path / "news_scoring.json", {"since_utc": fresh, "last_prefiltered_utc": fresh, "last_scored_utc": fresh,
                                           "last_poll": {"rules_error": "KeyError: 'x'"}})
    c = health.check_news(make_ctx(tmp_path))
    assert c.status == "fail" and "rules-v1 scorer error" in c.reason


def test_rule_rows_written_while_an_llm_client_was_active_count_as_unscored_for_the_budget_mask():
    t0 = pd.Timestamp("2026-11-02 00:00", tz="UTC")
    rows = []
    for k, (scorer, active) in enumerate([("llm", True), (SCORER, True), (SCORER, True), (SCORER, False)]):
        rec = t0 + pd.Timedelta(minutes=30 * k)
        rows.append({"item_id": f"i{k}", "ts_utc": rec, "received_utc": rec, "written_utc": rec + pd.Timedelta(minutes=1),
                     "source": "rss", "title": "Gold rises as Fed signals cuts", "summary": "", "scored": True,
                     "relevance": 0.8, "rates": "dovish", "risk": "neutral", "dollar": "neutral", "surprise": "none",
                     "shock": False, "scorer": scorer, "llm_active": active})
    g = nf.news_grid(pd.DataFrame(rows), end=t0 + pd.Timedelta(hours=2)).set_index("available_utc")
    at = t0 + pd.Timedelta(hours=2)
    assert g.loc[at, "news_unscored_share_24h"] == pytest.approx(0.5)     # the 2 budget-fallback rule rows
    assert pd.isna(g.loc[at, "news_count_4h"])                              # > MAX_UNSCORED: masked


def test_the_collector_stamps_whether_an_llm_client_was_active(tmp_path):
    _collector(tmp_path, None).poll(NOW)
    assert not Store(tmp_path / "data").read("news")["llm_active"].astype(bool).any()
    _collector(tmp_path / "b", FakeClient(), cap=0.0).poll(NOW)
    assert Store(tmp_path / "b" / "data").read("news")["llm_active"].astype(bool).all()
