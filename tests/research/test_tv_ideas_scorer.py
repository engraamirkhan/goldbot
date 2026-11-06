"""The TradingView author-ideas shadow scorer (goldbot/research/tv_ideas.py; prereg-2026-10-28-tv-author-ideas.md,
amendment T-1). Section 6 owes tests on synthetic bars for: a same-bar stop-first touch, `no_fill`, `invalidated`, swap
nights, the holdout refusal, and edited live ideas scored on their first snapshot. Every idea, author and text here is
invented (the real ones are third-party content and never enter the repo)."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import tv_ideas as ti
from goldbot.data.timeutil import server_wall
from goldbot.db.connection import Database
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.research import tv_ideas as tva
from tests.tv_ideas_fake import make_idea

MID = 4000.0
HALF_SPREAD = 0.10            # bid = mid - 0.10, ask = mid + 0.10: a 0.20 bar spread
WIGGLE = 5.0                  # every minute's high/low is mid +- 5, so a flat day's range (and ATR14) is 10
ATR = 2 * WIGGLE
EXTRA = 0.23                  # $/oz round trip beyond the spread
SWAP = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=0.0, triple_weekday=2, server_tz="Europe/Athens",
                contract_oz=100.0)                                    # long pays $0.60/oz a night, short nothing
COSTS = tva.ScoreCosts(extra_usd=EXTRA, swap=SWAP, source="test priors")
HOLDOUT = (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC"))
T0 = pd.Timestamp("2026-11-02 10:00", tz="UTC")                       # a Monday


def flat_bars(start: str = "2026-10-01", end: str = "2026-11-21") -> pd.DataFrame:
    """1m bid/ask minutes outside the weekly close (Friday 23:55 to Monday 01:05 on NY+7, gold's clock), mid flat at
    MID, each minute's high/low at mid +- WIGGLE."""
    ts = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    wall = server_wall(ts, "NY+7")
    tod = wall - wall.normalize()
    closed = (((wall.dayofweek == 4) & (tod >= pd.Timedelta(hours=23, minutes=55))) | (wall.dayofweek >= 5)
              | ((wall.dayofweek == 0) & (tod < pd.Timedelta(hours=1, minutes=5))))
    ts = ts[~closed]
    mid = np.full(len(ts), MID)
    out: dict[str, object] = {"ts_utc": ts}
    for side, sgn in (("bid", -1), ("ask", 1)):
        out[f"{side}_open"] = mid + sgn * HALF_SPREAD
        out[f"{side}_close"] = mid + sgn * HALF_SPREAD
        out[f"{side}_high"] = mid + WIGGLE + sgn * HALF_SPREAD
        out[f"{side}_low"] = mid - WIGGLE + sgn * HALF_SPREAD
    return pd.DataFrame(out)


def set_minute(bars: pd.DataFrame, at: pd.Timestamp, *, low: float | None = None, high: float | None = None,
               open_: float | None = None) -> None:
    """Move one minute's mid low / high / open (bid and ask follow at +- HALF_SPREAD)."""
    i = int(np.flatnonzero(pd.DatetimeIndex(bars["ts_utc"]) == at)[0])
    for side, sgn in (("bid", -1), ("ask", 1)):
        if low is not None:
            bars.loc[i, f"{side}_low"] = low + sgn * HALF_SPREAD
        if high is not None:
            bars.loc[i, f"{side}_high"] = high + sgn * HALF_SPREAD
        if open_ is not None:
            bars.loc[i, f"{side}_open"] = open_ + sgn * HALF_SPREAD


def minutes(bars: pd.DataFrame) -> tva.Minutes:
    return tva.Minutes.from_frame(bars)


# ------------------------------------------------------------------------------------------------- the direction read
def test_a_flat_market_costs_each_side_its_spread_extra_and_its_own_swap():
    """Baseline mirror (prereg 2): long and short at the same entry, each net of its own costs, swap included. A D idea
    held 5 trading days from Monday crosses 5 server rollovers, Wednesday's triple: 7 nights x $0.60 on the long."""
    m = minutes(flat_bars())
    long_ = tva.direction_read(m, T0, side=1, atr=ATR, bucket="D", costs=COSTS)
    short = tva.direction_read(m, T0, side=-1, atr=ATR, bucket="D", costs=COSTS)
    assert long_ is not None and short is not None
    assert long_.resolved and short.resolved and long_.exit == "time"
    assert short.r == pytest.approx(-(2 * HALF_SPREAD + EXTRA) / ATR)
    assert long_.r == pytest.approx(-(2 * HALF_SPREAD + EXTRA + 7 * 0.60) / ATR)
    assert long_.nights == 7


def test_the_catastrophic_stop_at_2r_ends_the_direction_read():
    bars = flat_bars()
    set_minute(bars, T0 + pd.Timedelta(hours=3), low=MID - 2.5 * ATR)
    out = tva.direction_read(minutes(bars), T0, side=1, atr=ATR, bucket="D", costs=COSTS)
    assert out is not None and out.exit == "stop" and out.resolved
    assert out.r == pytest.approx((-2 * ATR - EXTRA) / ATR)            # exit at the stop price: fill - 2R
    assert out.exit_utc == T0 + pd.Timedelta(hours=3)


def test_the_direction_read_stays_pending_until_a_bar_past_its_horizon_exists():
    bars = flat_bars(end="2026-11-03")                                  # Monday's idea, I horizon ends Tuesday 10:00
    out = tva.direction_read(minutes(bars), T0, side=1, atr=ATR, bucket="I", costs=COSTS)
    assert out is not None and not out.resolved
    done = tva.direction_read(minutes(flat_bars(end="2026-11-04")), T0, side=1, atr=ATR, bucket="I", costs=COSTS)
    assert done is not None and done.resolved


def test_the_1_5x_cost_stress_adds_half_the_spread_and_extra_again():
    m = minutes(flat_bars())
    out = tva.direction_read(m, T0, side=-1, atr=ATR, bucket="I", costs=COSTS)
    assert out is not None
    assert out.r_stress == pytest.approx(out.r - 0.5 * (2 * HALF_SPREAD + EXTRA) / ATR)


# ------------------------------------------------------------------------------------------------- the levels read
def test_a_bar_that_touches_stop_and_target_is_a_stop():
    """Prereg 1.3(a): "If both are touched inside one 1m bar, the stop is assumed first"."""
    bars = flat_bars()
    set_minute(bars, T0 + pd.Timedelta(minutes=30), low=MID - 9, high=MID + 9)
    call = tva.Call(label="long", entry=MID, stop=MID - 8, target=MID + 8)
    out = tva.levels_read(minutes(bars), T0, call, atr=ATR, bucket="I", costs=COSTS)
    assert out.status == "stop"
    fill = MID + HALF_SPREAD
    assert out.r == pytest.approx(((MID - 8) - fill - EXTRA) / (fill - (MID - 8)))


def test_a_limit_the_market_never_reaches_is_no_fill_and_scores_zero():
    """Entry 7 below the price (beyond 0.25 ATR): a limit, valid 1 trading day for an I idea; bars never go 5 lower."""
    call = tva.Call(label="long", entry=MID - 7, stop=MID - 15, target=MID + 6)
    out = tva.levels_read(minutes(flat_bars()), T0, call, atr=ATR, bucket="I", costs=COSTS)
    assert out.status == "no_fill" and out.r == 0.0 and out.fill_utc is None


def test_a_limit_whose_target_trades_first_is_invalidated():
    bars = flat_bars()
    set_minute(bars, T0 + pd.Timedelta(hours=2), high=MID + 7)
    call = tva.Call(label="long", entry=MID - 7, stop=MID - 15, target=MID + 6)
    out = tva.levels_read(minutes(bars), T0, call, atr=ATR, bucket="I", costs=COSTS)
    assert out.status == "invalidated" and out.r == 0.0


def test_a_limit_that_fills_then_reaches_its_target_wins_net_of_costs():
    bars = flat_bars()
    set_minute(bars, T0 + pd.Timedelta(hours=1), low=MID - 8)
    set_minute(bars, T0 + pd.Timedelta(hours=2), high=MID + 7)
    call = tva.Call(label="long", entry=MID - 7, stop=MID - 15, target=MID + 6)
    out = tva.levels_read(minutes(bars), T0, call, atr=ATR, bucket="I", costs=COSTS)
    assert out.status == "target"
    assert out.fill_utc == T0 + pd.Timedelta(hours=1)
    assert out.r == pytest.approx(((MID + 6) - (MID - 7) - EXTRA) / 8)


def test_a_stop_on_the_wrong_side_demotes_the_idea_to_direction_only():
    call = tva.Call(label="long", entry=MID, stop=MID + 5, target=MID + 10)
    out = tva.levels_read(minutes(flat_bars()), T0, call, atr=ATR, bucket="I", costs=COSTS)
    assert out.status == "demoted" and out.r is None


# ------------------------------------------------------------------------------------------------- point in time
def test_atr_is_the_prior_daily_close_s_not_the_entry_day_s():
    """R = ATR14 on daily bars as of the prior daily close: a wild range later on the entry day changes nothing."""
    bars = flat_bars()
    set_minute(bars, T0 + pd.Timedelta(hours=4), low=MID - 400, high=MID + 400)
    atr = tva.daily_atr(bars, "Europe/Athens")
    ideas = pd.DataFrame({"ts_utc": [T0]})
    got = tva.atr_at(ideas, atr)
    assert got[0] == pytest.approx(ATR)


# ------------------------------------------------------------------------------------------------- the stored ideas
def _db(tmp_path: Path) -> ti.IdeasDb:
    return ti.IdeasDb(tmp_path / "tv_ideas.db")


def _record(db: ti.IdeasDb, i: int, *, author: str = "SynthA", published: pd.Timestamp, seen: pd.Timestamp,
            live_since: pd.Timestamp | None, ticker: str = "XAUUSD", exchange: str = "FX", text: str = "UP",
            interval: str = "60", updates: list | None = None) -> int:
    det = make_idea(i, author=author, exchange=exchange, ticker=ticker, created=published.timestamp(),
                    interval=interval, description=text, updates=updates)
    idea, ups = ti.idea_from_detail(det)
    db.record(idea, ups, seen, live_since=live_since, latency_s=900)
    return idea.idea_id


def _synth_extractor(title: str, description: str) -> tva.Call:
    """Test-only frozen extractor: the word UP or DOWN in the text."""
    if "UP" in description:
        return tva.Call(label="long")
    if "DOWN" in description:
        return tva.Call(label="short")
    return tva.Call(label="abstain")


ADMITTED = pd.Timestamp("2026-10-01", tz="UTC")
SYNTH = {"syntha": tva.Extractor(name="synth-updown-v1", fn=_synth_extractor, admitted_utc=ADMITTED)}
LIVE_SINCE = pd.Timestamp("2026-10-30", tz="UTC")


def _score(tmp_path: Path, bars: pd.DataFrame, *, extractors=SYNTH, as_of=pd.Timestamp("2026-11-21", tz="UTC")):
    ideas = tva.read_ideas(tmp_path / "tv_ideas.db", authors=["SynthA", "SynthB"])
    return {r.idea_id: r for r in tva.score_ideas(ideas, bars, costs=COSTS, holdout=HOLDOUT, as_of=as_of,
                                                  extractors=extractors)}


def test_an_edited_live_idea_is_scored_on_its_first_snapshot_and_stays_in_the_decision_read(tmp_path):
    db = _db(tmp_path)
    i = _record(db, 1, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE, text="UP")
    _record(db, 1, published=T0 - pd.Timedelta(minutes=5), seen=T0 + pd.Timedelta(hours=6), live_since=LIVE_SINCE,
            text="DOWN")                                              # the author rewrote it to the other side
    row = _score(tmp_path, flat_bars())[i]
    assert row.label == "long" and row.edited and row.decision_eligible       # T-2 (7): edited live ideas stay in
    assert row.r_side == row.r_long


def test_an_author_s_trade_update_is_not_an_edit_and_leaves_the_idea_decision_eligible(tmp_path):
    """Only a changed text is an edit (prereg 1.5). The collector's `edited` flag also counts the author's later notes
    ("target reached"), which would drop winners from the decision read: selection on the outcome."""
    db = _db(tmp_path)
    up = [{"id": 77, "ts": (T0 + pd.Timedelta(hours=3)).timestamp(), "label": "Target reached"}]
    i = _record(db, 2, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE, text="UP")
    _record(db, 2, published=T0 - pd.Timedelta(minutes=5), seen=T0 + pd.Timedelta(hours=4), live_since=LIVE_SINCE,
            text="UP", updates=up)
    row = _score(tmp_path, flat_bars())[i]
    assert not row.edited and row.decision_eligible


def test_an_idea_published_in_the_holdout_is_refused(tmp_path):
    db = _db(tmp_path)
    pub = pd.Timestamp("2026-09-28 10:00", tz="UTC")
    i = _record(db, 3, published=pub, seen=pub + pd.Timedelta(minutes=8), live_since=pd.Timestamp("2026-09-01",
                                                                                                    tz="UTC"))
    row = _score(tmp_path, flat_bars(start="2026-09-01"))[i]
    assert row.status == "holdout" and row.r_long is None and row.r_side is None and not row.decision_eligible


def test_without_an_admitted_extractor_an_idea_is_unlabelled_but_its_baselines_are_scored(tmp_path):
    """T-1: no parser passed its audit, so no idea has a side. The long and short reads (drift and random baselines at
    the author's timestamps) need no side; the decision read waits for an admitted extractor."""
    db = _db(tmp_path)
    i = _record(db, 4, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE)
    row = _score(tmp_path, flat_bars(), extractors={})[i]
    assert row.label == "unlabelled" and row.extractor is None and row.r_side is None
    assert row.status == "scored" and row.r_long is not None and row.r_short is not None
    assert not row.decision_eligible
    assert tva.ADMITTED_EXTRACTORS == {}                     # nothing admitted until an amendment and its audit pass


def test_the_entry_is_the_first_minute_at_or_after_available_utc_not_the_publish_time(tmp_path):
    db = _db(tmp_path)
    seen = T0 + pd.Timedelta(minutes=7, seconds=20)
    i = _record(db, 5, published=T0, seen=seen, live_since=LIVE_SINCE)
    row = _score(tmp_path, flat_bars())[i]
    assert row.entry_utc == T0 + pd.Timedelta(minutes=8)
    assert row.r_long_pub is not None                        # the capture-loss read at publish + 60 s (descriptive)


def test_back_catalogue_and_non_gold_ideas_are_counted_not_scored(tmp_path):
    db = _db(tmp_path)
    back = _record(db, 6, published=T0 - pd.Timedelta(days=4), seen=T0, live_since=None)
    btc = _record(db, 7, published=T0, seen=T0 + pd.Timedelta(minutes=5), live_since=LIVE_SINCE, ticker="BTCUSD",
                  exchange="BITSTAMP")
    rows = _score(tmp_path, flat_bars())
    assert rows[back].status == "backfilled" and rows[back].r_long is None
    assert rows[btc].status == "not_gold" and rows[btc].symbol_class == "other"


def test_a_futures_idea_is_scored_direction_only_and_its_levels_are_discarded(tmp_path):
    db = _db(tmp_path)

    def levels_extractor(title: str, description: str) -> tva.Call:
        return tva.Call(label="long", entry=MID, stop=MID - 8, target=MID + 8)

    i = _record(db, 8, published=T0, seen=T0 + pd.Timedelta(minutes=5), live_since=LIVE_SINCE, ticker="GC1!",
                exchange="COMEX")
    row = _score(tmp_path, flat_bars(), extractors={"syntha": tva.Extractor(name="x", fn=levels_extractor,
                                                                                    admitted_utc=ADMITTED)})[i]
    assert row.symbol_class == "futures" and row.levels_status is None and row.r_side is not None


def test_the_timeframe_buckets_follow_the_prereg():
    assert [tva.bucket_of(tf) for tf in ("30S", "15", "60", "240", "480", "1D", "2D", "1W", "1M", None)] == \
        ["I", "I", "I", "I", "D", "D", "D", "W", "W", "D"]


def test_the_extractor_hash_pins_its_source():
    e = SYNTH["syntha"]
    assert len(e.sha) == 16 and e.sha == tva.Extractor(name="other", fn=_synth_extractor, admitted_utc=ADMITTED).sha
    later = tva.Extractor(name="other", fn=_synth_extractor, admitted_utc=ADMITTED + pd.Timedelta(days=1))
    assert later.sha != e.sha                                     # the admission time is part of the frozen record


# ------------------------------------------------------------------------------------------------- the job
def test_the_job_reads_tv_ideas_read_only_and_stores_scores_once_final(tmp_path):
    db = _db(tmp_path)
    i = _record(db, 9, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE)
    path = tmp_path / "tv_ideas.db"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    aux = Database.open(tmp_path, "aux")
    ideas = tva.read_ideas(path, authors=["SynthA"])
    rows = tva.score_ideas(ideas, flat_bars(), costs=COSTS, holdout=HOLDOUT,
                           as_of=pd.Timestamp("2026-11-21", tz="UTC"), extractors=SYNTH)
    assert tva.save_scores(aux, rows, scored_utc=pd.Timestamp("2026-11-21", tz="UTC")) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with aux.connection() as conn:
        got = conn.execute("SELECT status, label, r_side FROM tva_scores WHERE idea_id = ?", (i,)).fetchone()
    assert got[0] == "scored" and got[1] == "long" and got[2] is not None
    # a final row is never rescored: the second night writes nothing
    assert tva.save_scores(aux, rows, scored_utc=pd.Timestamp("2026-11-22", tz="UTC")) == 0
    with pytest.raises(sqlite3.OperationalError):
        conn = tva._read_only(path)
        try:
            conn.execute("CREATE TABLE x (a)")
        finally:
            conn.close()


def test_a_pending_row_is_rewritten_until_it_resolves(tmp_path):
    db = _db(tmp_path)
    i = _record(db, 10, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE)
    aux = Database.open(tmp_path, "aux")
    ideas = tva.read_ideas(tmp_path / "tv_ideas.db", authors=["SynthA"])
    early = tva.score_ideas(ideas, flat_bars(end="2026-11-03"), costs=COSTS, holdout=HOLDOUT,
                            as_of=pd.Timestamp("2026-11-03", tz="UTC"), extractors=SYNTH)
    assert [r.status for r in early] == ["pending"]
    assert tva.save_scores(aux, early, scored_utc=pd.Timestamp("2026-11-03", tz="UTC")) == 1
    late = tva.score_ideas(ideas, flat_bars(), costs=COSTS, holdout=HOLDOUT,
                           as_of=pd.Timestamp("2026-11-21", tz="UTC"), extractors=SYNTH)
    assert tva.save_scores(aux, late, scored_utc=pd.Timestamp("2026-11-21", tz="UTC")) == 1
    with aux.connection() as conn:
        assert conn.execute("SELECT status FROM tva_scores WHERE idea_id = ?", (i,)).fetchone()[0] == "scored"


def test_the_summary_reports_progress_toward_the_forward_read_and_no_skill_statistic(tmp_path):
    db = _db(tmp_path)
    _record(db, 11, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE)
    _record(db, 12, author="SynthB", published=T0, seen=T0 + pd.Timedelta(minutes=3), live_since=LIVE_SINCE)
    ideas = tva.read_ideas(tmp_path / "tv_ideas.db", authors=["SynthA", "SynthB"])
    rows = tva.score_ideas(ideas, flat_bars(), costs=COSTS, holdout=HOLDOUT,
                           as_of=pd.Timestamp("2026-11-21", tz="UTC"), extractors=SYNTH)
    s = tva.summarise(rows)
    assert s["SynthA"]["eligible_resolved"] == 1 and s["SynthA"]["publish_days"] == 1
    assert s["SynthB"]["unlabelled"] == 1 and s["SynthB"]["eligible_resolved"] == 0
    assert not any("r_side" in k or "mean" in k for a in s.values() for k in a)


def test_tva_scores_is_a_scheduled_job_after_the_nightly_bars():
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS, tva_scores
    s = load_settings()
    assert JOBS["tva_scores"] is tva_scores
    sch = s.scheduler.tva_scores
    assert sch.kind == "daily" and sch.at > s.scheduler.feed_reconcile.at and sch.at > s.scheduler.nightly_costs.at


def test_the_job_skips_without_a_tv_ideas_file(tmp_path):
    from goldbot.config import load_settings
    out = tva.run_job(load_settings(), tmp_path, store=None, accounts=[], slot=pd.Timestamp("2026-11-21", tz="UTC"))
    assert "skipped" in out


class _Store:
    """The brain store as account_bars_1m reads it: the canonical broker's live minutes only."""

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars

    def read(self, table: str, *, source: str, symbol: str, start=None, end=None, columns=None) -> pd.DataFrame:
        if table != "bars_1m_live" or source != "icm":
            return pd.DataFrame()
        b = self.bars
        return b[(b["ts_utc"] >= start) & (b["ts_utc"] < end)] if start is not None else b


def test_the_job_scores_on_the_canonical_broker_s_minutes_and_reports_counts_only(tmp_path):
    from types import SimpleNamespace

    from goldbot.config import load_settings
    settings = load_settings()
    author = settings.tv_ideas.authors[0]
    db = _db(tmp_path)
    i = _record(db, 13, author=author, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE,
                text="UP")
    accounts = [SimpleNamespace(account_id="other", broker="vantage", symbol="XAUUSD+"),
                SimpleNamespace(account_id="acc-a", broker="icm", symbol="XAUUSD")]
    slot = pd.Timestamp("2026-11-21", tz="UTC")
    out = tva.run_job(settings, tmp_path, store=_Store(flat_bars()), accounts=accounts, slot=slot, costs=COSTS)
    assert out["bars_account"] == "acc-a" and out["written"] == 1 and out["extractors"] == []
    assert out["authors"][author]["unlabelled"] == 1 and out["authors"][author]["scored"] == 1
    assert "UP" not in repr(out)                                       # no idea text leaves the file
    again = tva.run_job(settings, tmp_path, store=_Store(flat_bars()), accounts=accounts, slot=slot, costs=COSTS)
    assert again["rescored"] == 0 and again["written"] == 0
    rows = tva.stored_rows(Database.open(tmp_path, "aux"))
    assert [r.idea_id for r in rows] == [i] and rows[0].r_long is not None


def _always_short(title: str, description: str) -> tva.Call:
    return tva.Call(label="short")


def test_an_unlabelled_final_row_takes_a_later_admitted_extractor_s_label_and_a_labelled_one_never_changes(tmp_path):
    db = _db(tmp_path)
    i = _record(db, 14, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE, text="UP")
    aux = Database.open(tmp_path, "aux")
    ideas = tva.read_ideas(tmp_path / "tv_ideas.db", authors=["SynthA"])
    at = pd.Timestamp("2026-11-21", tz="UTC")

    def score(extractors: dict[str, tva.Extractor]) -> list[tva.ScoreRow]:
        return tva.score_ideas(ideas, flat_bars(), costs=COSTS, holdout=HOLDOUT, as_of=at, extractors=extractors)

    assert tva.save_scores(aux, score({}), scored_utc=at) == 1
    assert tva.save_scores(aux, score(SYNTH), scored_utc=at) == 1                  # unlabelled -> long
    flipped = {"syntha": tva.Extractor(name="flip", fn=_always_short, admitted_utc=ADMITTED)}
    assert tva.save_scores(aux, score(flipped), scored_utc=at) == 0                # a frozen label is never rewritten
    row = {r.idea_id: r for r in tva.stored_rows(aux)}[i]
    assert row.label == "long" and row.extractor == "synth-updown-v1" and row.decision_eligible


def test_ideas_before_the_extractor_s_admission_are_scored_but_never_decision_eligible(tmp_path):
    """T-2 (6): the forward universe starts at the dated amendment that admits the author's extractor."""
    db = _db(tmp_path)
    before = _record(db, 15, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE)
    after = _record(db, 16, published=T0 + pd.Timedelta(hours=1), seen=T0 + pd.Timedelta(hours=1, minutes=4),
                    live_since=LIVE_SINCE)
    late = {"syntha": tva.Extractor(name="synth-updown-v1", fn=_synth_extractor,
                                    admitted_utc=T0 + pd.Timedelta(minutes=30))}
    rows = _score(tmp_path, flat_bars(), extractors=late)
    assert rows[before].label == "long" and rows[before].status == "scored" and not rows[before].decision_eligible
    assert rows[after].decision_eligible


def _drop(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    t = pd.DatetimeIndex(bars["ts_utc"])
    return bars[~((t >= start) & (t < end))].reset_index(drop=True)


def test_a_hole_in_the_minutes_mid_hold_is_no_bars_until_filled(tmp_path):
    """T-2 (2): a hole over 15 minutes outside the daily break and the weekly close makes the idea `no_bars` (not
    final: rescored nightly), and the filled minutes score it."""
    db = _db(tmp_path)
    i = _record(db, 17, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE, interval="1D")
    hole = _drop(flat_bars(), T0 + pd.Timedelta(days=1, hours=2), T0 + pd.Timedelta(days=1, hours=2, minutes=30))
    row = _score(tmp_path, hole)[i]
    assert row.status == "no_bars" and not row.final and row.r_side is None
    short = _drop(flat_bars(), T0 + pd.Timedelta(days=1, hours=2), T0 + pd.Timedelta(days=1, hours=2, minutes=10))
    assert _score(tmp_path, short)[i].status == "scored"            # 10 minutes missing: within tolerance
    assert _score(tmp_path, flat_bars())[i].status == "scored"


def test_the_daily_break_and_the_weekly_close_are_not_holes_even_when_the_us_and_eu_clocks_differ():
    """The break is 17:00 New York (NY+7 midnight); 2026-10-26..30 it sat at 22:58-00:02 Athens, an hour off the
    configured broker clock, and must still read as the break."""
    bars = flat_bars()
    wall = server_wall(pd.DatetimeIndex(bars["ts_utc"]), "NY+7")
    tod = wall - wall.normalize()
    in_break = (tod >= pd.Timedelta(hours=23, minutes=58)) | (tod < pd.Timedelta(hours=1, minutes=2))
    m = tva.Minutes.from_frame(bars[~in_break])
    assert not m.holes(tva.BREAK_CLOCK).any()
    assert m.holes("Europe/Athens").any()                     # the configured broker clock would hole that week
    late =(tod >= pd.Timedelta(hours=12)) & (tod < pd.Timedelta(hours=12, minutes=30)) & \
        (pd.DatetimeIndex(bars["ts_utc"]).normalize() == pd.Timestamp("2026-10-28", tz="UTC"))
    assert tva.Minutes.from_frame(bars[~(in_break | late)]).holes(tva.BREAK_CLOCK).sum() == 1


def test_a_partial_day_is_not_used_for_atr(tmp_path):
    """An ATR day with under 80% of the median day's minutes poisons every ATR whose 15 days include it."""
    thin = pd.Timestamp("2026-10-27 12:00", tz="UTC")                 # a Tuesday, inside the 15 days before T0
    bars = _drop(flat_bars(), thin, thin + pd.Timedelta(hours=8))
    atr = tva.daily_atr(bars, "Europe/Athens")
    got = tva.atr_at(pd.DataFrame({"ts_utc": [T0, T0 + pd.Timedelta(days=21)]}), atr)
    assert np.isnan(got[0]) and got[1] == pytest.approx(ATR)
    db = _db(tmp_path)
    i = _record(db, 18, published=T0 - pd.Timedelta(minutes=5), seen=T0, live_since=LIVE_SINCE)
    assert _score(tmp_path, bars)[i].status == "no_bars"


def test_a_final_row_does_not_change_when_later_bars_are_appended_or_perturbed(tmp_path):
    db = _db(tmp_path)
    ids = [_record(db, 20 + k, published=T0 + pd.Timedelta(hours=k), seen=T0 + pd.Timedelta(hours=k, minutes=6),
                   live_since=LIVE_SINCE, interval=tf, text=txt)
           for k, (tf, txt) in enumerate((("60", "UP"), ("1D", "DOWN"), ("240", "UP"), ("1D", "UP")))]
    at = pd.Timestamp("2026-11-21", tz="UTC")
    first = _score(tmp_path, flat_bars(), as_of=at)
    final = {i: first[i] for i in ids if first[i].final}
    assert len(final) == len(ids)
    cut = max(max(r.entry_utc for r in final.values()) + pd.Timedelta(days=15), at - pd.Timedelta(days=5))
    later = flat_bars(end="2026-12-21")
    after = pd.DatetimeIndex(later["ts_utc"]) >= cut
    walk = np.cumsum(np.random.default_rng(7).normal(0, 2.0, int(after.sum())))
    walk[len(walk) // 2] += 300.0                                     # a spike
    for c in [c for c in later.columns if c != "ts_utc"]:
        later.loc[after, c] = later.loc[after, c] + walk
    again = _score(tmp_path, later, as_of=pd.Timestamp("2026-12-21", tz="UTC"))
    assert {i: again[i].model_dump() for i in final} == {i: r.model_dump() for i, r in final.items()}
