"""TradingView author ideas collector (goldbot/data/tv_ideas.py). Synthetic fixtures only (tests/tv_ideas_fake.py):
real idea text is third-party content and never enters the repo."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.config import TvIdeasSettings
from goldbot.data import tv_ideas as ti
from goldbot.data.store import asof_join
from goldbot.data.tv_wire import ReadOnlyViolation
from tests.tv_ideas_fake import FakeIdeasApi, make_idea

T0 = pd.Timestamp("2026-10-27 08:00", tz="UTC")          # a Tuesday
DAY = 86400.0


def _cfg(**kw: Any) -> TvIdeasSettings:
    base: dict[str, Any] = {"authors": ["SynthAuthor"], "min_interval_s": 1.0, "backfill_min_interval_s": 1.0}
    base.update(kw)
    return TvIdeasSettings(**base)


def _client(api: FakeIdeasApi, budget: int = 100_000) -> ti.IdeasClient:
    return ti.IdeasClient.build(api, min_interval_s=1.0, budget=budget, sleep=lambda s: None, monotonic=lambda: 0.0)


def _db(tmp_path: Path) -> ti.IdeasDb:
    return ti.IdeasDb(tmp_path / "tv_ideas.db")


def _rows(db: ti.IdeasDb, sql: str, *args: Any) -> list[sqlite3.Row]:
    con = sqlite3.connect(db.path)
    con.row_factory = sqlite3.Row
    try:
        return list(con.execute(sql, args))
    finally:
        con.close()


# ------------------------------------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,entry,stop,target", [
    ("Buy 2350 - 2355\nSL 2340\nTP1 2370 TP2 2385", [2350.0, 2355.0], [2340.0], [2370.0, 2385.0]),
    ("Entry: $4,035.50 stop loss: 4,020 targets: 4,060 / 4,080", [4035.5], [4020.0], [4060.0, 4080.0]),
    ("Sell limit 1.2650, invalidation 1.2710, take profit 1.2550", [1.265], [1.271], [1.255]),
    ("We expect a move toward 2400 on the 4H; RSI 70 and 15 pips", [], [], []),
    ("[b]Target:[/b] 3,350 and 3,380 (2%)", [], [], [3350.0, 3380.0]),
])
def test_parse_levels(text: str, entry: list[float], stop: list[float], target: list[float]):
    lv = ti.parse_levels(text)
    assert (lv.entry, lv.stop, lv.target) == (entry, stop, target)


def test_parse_levels_ranges_trailing_keywords_and_support_resistance():
    lv = ti.parse_levels("We want a better entry for the 1970-75 target. Support 2020, Resistance 2040-45 "
                         "after a 100-140pips move; key level 1698-5 support")
    assert lv.target == [1970.0, 1975.0] and lv.entry == []
    assert lv.support == [2020.0, 1698.0, 1695.0] and lv.resistance == [2040.0, 2045.0]
    assert not ti.parse_levels("Support 2020 and resistance 2040").any       # S/R alone is not entry/stop/target
    lv = ti.parse_levels("Price trades between 4843 Goldturn support and 5412 Goldturn resistance, the 2051 "
                         "Bullish target")
    assert (lv.support, lv.resistance, lv.target) == ([4843.0], [5412.0], [2051.0])


def test_detail_parses_to_an_idea_with_utc_times_direction_and_snapshot():
    d = make_idea(1, created=1_793_042_561.0, direction=2, interval="1D", title="Synthetic short",
                  description="Sell 2400 SL 2410 TP 2380", updates=[{"id": 5, "ts": 1_793_050_000.0}])
    idea, ups = ti.idea_from_detail(json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith("_")})))
    assert idea.idea_id == 10_000_001 and idea.uuid == "u0000001" and idea.author == "SynthAuthor"
    assert idea.direction == "short" and idea.tf == "1D" and idea.interval == "1D"
    assert idea.published_utc == pd.Timestamp(1_793_042_561, unit="s", tz="UTC")
    assert idea.updated_utc == pd.Timestamp(1_793_050_000, unit="s", tz="UTC")
    assert idea.snapshot_url and idea.snapshot_url.endswith("_big.png")
    assert idea.chart_url.startswith("https://www.tradingview.com/chart/")
    assert idea.levels.entry == [2400.0] and idea.levels.stop == [2410.0] and idea.levels.target == [2380.0]
    assert [u.update_id for u in ups] == [5] and ups[0].created_utc.tzinfo is not None


@pytest.mark.parametrize("code,label", [(1, "long"), (2, "short"), (0, "neutral"), (7, "unknown")])
def test_direction_codes(code: int, label: str):
    d = {k: v for k, v in make_idea(1, direction=code).items() if not k.startswith("_")}
    assert ti.idea_from_detail(d)[0].direction == label


# ------------------------------------------------------------------------------------------------ read-only
def test_requests_carry_no_cookie_and_stay_on_the_ideas_api(tmp_path: Path):
    api = FakeIdeasApi([make_idea(1, created=T0.timestamp() - 600)])
    ti.poll_once(_cfg(), _db(tmp_path), _client(api), now=lambda: T0)
    assert api.calls and all("Cookie" not in h for _, h in api.calls)
    assert all(u.startswith("https://www.tradingview.com/api/v1/ideas/") for u, _ in api.calls)
    with pytest.raises(ReadOnlyViolation):
        _client(api).get_json("https://www.tradingview.com/accounts/signin/")


# ------------------------------------------------------------------------------------------------ live poll
def test_live_poll_point_in_time_and_first_release_is_kept(tmp_path: Path):
    db = _db(tmp_path)
    old = make_idea(1, created=T0.timestamp() - 3 * DAY, description="Buy 2350 SL 2340 TP 2370")
    api = FakeIdeasApi([old])
    rep = ti.poll_once(_cfg(), db, _client(api), now=lambda: T0)
    assert rep.ok and rep.new_ideas == 1
    r = _rows(db, "SELECT * FROM tv_ideas")[0]
    # published before our first poll: back catalogue, available at the author's claim + the latency
    assert r["backfilled"] == 1
    assert pd.Timestamp(r["available_utc"]) == pd.Timestamp(old["created_timestamp"], unit="s", tz="UTC") + \
        pd.Timedelta(seconds=900)

    # a new idea after the first poll: live, available when we first held its full text
    t1 = T0 + pd.Timedelta(minutes=12)
    new = make_idea(2, created=t1.timestamp() - 300, direction=1, description="Long from 2360")
    api.ideas.append(new)
    rep = ti.poll_once(_cfg(), db, _client(api), now=lambda: t1)
    assert rep.new_ideas == 1
    r2 = _rows(db, "SELECT * FROM tv_ideas WHERE idea_id = ?", new["id"])[0]
    assert r2["backfilled"] == 0 and pd.Timestamp(r2["available_utc"]) == t1
    assert pd.Timestamp(r2["first_seen_utc"]) == t1 and r2["edited"] == 0

    # the author edits the text and appends an update: a new version and an update row, the first release untouched
    t2 = t1 + pd.Timedelta(minutes=12)
    api.ideas[1] = make_idea(2, created=new["created_timestamp"], direction=1, description="Long from 2365 now",
                             updates=[{"id": 77, "ts": t2.timestamp() - 60, "value": "closed", "label": "Target hit"}])
    rep = ti.poll_once(_cfg(), db, _client(api), now=lambda: t2)
    assert rep.new_updates == 1 and rep.new_versions == 1 and rep.new_ideas == 0
    r2b = _rows(db, "SELECT * FROM tv_ideas WHERE idea_id = ?", new["id"])[0]
    assert r2b["description"] == "Long from 2360" and r2b["available_utc"] == r2["available_utc"]
    assert r2b["edited"] == 1
    vers = _rows(db, "SELECT * FROM tv_idea_versions WHERE idea_id = ? ORDER BY seen_utc", new["id"])
    assert [v["description"] for v in vers] == ["Long from 2360", "Long from 2365 now"]
    up = _rows(db, "SELECT * FROM tv_idea_updates WHERE update_id = 77")[0]
    assert up["backfilled"] == 0 and pd.Timestamp(up["available_utc"]) == t2 and up["label"] == "Target hit"

    # an unchanged poll writes no idea, version or update
    rep = ti.poll_once(_cfg(), db, _client(api), now=lambda: t2 + pd.Timedelta(minutes=12))
    assert (rep.new_ideas, rep.new_updates, rep.new_versions) == (0, 0, 0)
    assert len(_rows(db, "SELECT * FROM tv_ideas")) == 2


def test_other_authors_in_a_listing_are_ignored(tmp_path: Path):
    db = _db(tmp_path)
    api = FakeIdeasApi([make_idea(1, created=T0.timestamp() - 60)])
    real = api._listing

    def leaky(q: dict[str, str]) -> dict[str, Any]:
        out = real(q)
        out["results"] = out["results"] + [dict(out["results"][0], id=99, user={"username": "SomeoneElse"})]
        return out

    api._listing = leaky  # type: ignore[method-assign]
    ti.poll_once(_cfg(), db, _client(api), now=lambda: T0)
    assert [r["idea_id"] for r in _rows(db, "SELECT idea_id FROM tv_ideas")] == [10_000_001]


def test_failed_detail_is_retried_and_keeps_the_first_sighting(tmp_path: Path):
    db = _db(tmp_path)
    t_first = T0
    ti.poll_once(_cfg(), db, _client(FakeIdeasApi([])), now=lambda: T0 - pd.Timedelta(hours=1))   # first poll
    api = FakeIdeasApi([make_idea(1, created=t_first.timestamp() - 60)])
    calls = {"n": 0}
    real_get = api.get

    def flaky(url: str, headers: Any) -> str:
        if "/api/v1/ideas/u" in url and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("503")
        return real_get(url, headers)

    api.get = flaky  # type: ignore[method-assign]
    rep = ti.poll_once(_cfg(), db, _client(api), now=lambda: t_first)
    assert not rep.ok and rep.new_ideas == 0
    t2 = t_first + pd.Timedelta(minutes=12)
    rep = ti.poll_once(_cfg(), db, _client(api), now=lambda: t2)
    assert rep.ok and rep.new_ideas == 1
    r = _rows(db, "SELECT * FROM tv_ideas")[0]
    assert pd.Timestamp(r["first_seen_utc"]) == t_first and pd.Timestamp(r["available_utc"]) == t2


def test_daily_request_budget_skips_the_poll(tmp_path: Path):
    db = _db(tmp_path)
    api = FakeIdeasApi([make_idea(i, created=T0.timestamp() - i) for i in range(1, 4)])
    ti.poll_once(_cfg(max_requests_per_day=2), db, _client(api), now=lambda: T0)
    n = len(api.calls)
    rep = ti.poll_once(_cfg(max_requests_per_day=2), db, _client(api), now=lambda: T0 + pd.Timedelta(minutes=12))
    assert not rep.ok and "budget" in rep.reason and len(api.calls) == n


def test_follow_backs_off_doubling_after_failures(tmp_path: Path):
    waits: list[float] = []
    reports = iter([ti.RunReport(kind="poll", ok=False, reason="x")] * 3 + [ti.RunReport(kind="poll", ok=True)])
    ti.follow(lambda: next(reports), _cfg(backoff_base_s=300, backoff_max_s=2000, poll_jitter_s=60),
              sleep=waits.append, max_passes=4, rand=lambda: 0.5)
    # never faster than the poll interval (720 s), doubling to the cap; a good pass waits interval + jitter
    assert waits == [720, 720, 1200, 750]


# ------------------------------------------------------------------------------------------------ backfill
def _catalogue(n_fx: int = 1600, n_oanda: int = 600, n_tvc: int = 200) -> list[dict[str, Any]]:
    """More than the listing serves to one query (1000): only slicing reaches the oldest ideas."""
    ideas: list[dict[str, Any]] = []
    i = 0
    for exch, n in (("FX", n_fx), ("OANDA", n_oanda), ("TVC", n_tvc)):
        for _ in range(n):
            i += 1
            word = f"theme{i % 5}"
            ideas.append(make_idea(i, exchange=exch, created=1_600_000_000.0 + i * 3600, likes=i % 97,
                                   title=f"Gold {word} plan", description=f"Gold {word} text {i}. Buy {2000 + i % 300}"
                                   f" SL {1990 + i % 300}",
                                   updates=[{"id": 900_000 + i, "ts": 1_600_000_000.0 + i * 3600 + 7200}]
                                   if i % 4 == 0 else None))
    return ideas


def test_backfill_slices_past_the_listing_cap_and_flags_rows(tmp_path: Path):
    db = _db(tmp_path)
    cat = _catalogue()
    api = FakeIdeasApi(cat)
    rep = ti.backfill(_cfg(), db, _client(api), authors=["SynthAuthor"], now=lambda: T0)
    assert rep.listing["SynthAuthor"].total == 2400 and rep.listing["SynthAuthor"].collected == 2400
    assert rep.details_fetched == 2400 and rep.ok
    rows = _rows(db, "SELECT * FROM tv_ideas")
    assert len(rows) == 2400 and all(r["backfilled"] == 1 for r in rows)
    one = next(r for r in rows if r["idea_id"] == cat[0]["id"])
    assert pd.Timestamp(one["available_utc"]) == pd.Timestamp(cat[0]["created_timestamp"], unit="s", tz="UTC") + \
        pd.Timedelta(seconds=900)
    assert sum(r["edited"] for r in rows) == 600
    ups = _rows(db, "SELECT * FROM tv_idea_updates")
    assert len(ups) == 600 and all(u["backfilled"] == 1 for u in ups)
    # resumable: a second run fetches no detail again
    n = len(api.calls)
    rep2 = ti.backfill(_cfg(), db, _client(api), authors=["SynthAuthor"], now=lambda: T0, listing=False)
    assert rep2.details_fetched == 0 and len(api.calls) == n


def test_backfill_stops_at_its_budget_and_reports_it(tmp_path: Path):
    db = _db(tmp_path)
    rep = ti.backfill(_cfg(), db, _client(FakeIdeasApi(_catalogue()), budget=15), authors=["SynthAuthor"],
                      now=lambda: T0)
    assert not rep.ok and "budget" in rep.reason
    assert rep.listing["SynthAuthor"].collected < 2400


# ------------------------------------------------------------------------------------------------ point in time
def test_asof_join_never_sees_an_idea_or_update_before_it_was_available(tmp_path: Path):
    """Seed a future release: an idea first held at t1 must not reach a bar at t1 - 1 min."""
    db = _db(tmp_path)
    api = FakeIdeasApi([])
    ti.poll_once(_cfg(), db, _client(api), now=lambda: T0)                       # first poll: nothing yet
    t1 = T0 + pd.Timedelta(minutes=30)
    api.ideas.append(make_idea(1, created=(T0 + pd.Timedelta(minutes=5)).timestamp(), direction=1))
    ti.poll_once(_cfg(), db, _client(api), now=lambda: t1)
    frame = ti.ideas_frame(db.path)
    assert list(frame["available_utc"]) == [t1]
    bars = pd.DataFrame({"ts_utc": [t1 - pd.Timedelta(minutes=1), t1, t1 + pd.Timedelta(minutes=15)]})
    out = asof_join(bars, frame[["available_utc", "direction"]])
    assert out["direction"].isna().tolist() == [True, False, False]
    # published 25 min before we held it: the published time is never the join key
    assert frame["published_utc"].iloc[0] < frame["available_utc"].iloc[0]


# ------------------------------------------------------------------------------------------------ health
def _ctx(tmp_path: Path, now: pd.Timestamp) -> Any:
    return SimpleNamespace(state_dir=tmp_path, now=now, settings=None)


def test_health_ok_before_first_poll_warn_when_stale_on_a_weekday_only(tmp_path: Path):
    assert ti.check_tv_ideas(_ctx(tmp_path, T0)).status == "ok"
    db = _db(tmp_path)
    ti.poll_once(_cfg(), db, _client(FakeIdeasApi([])), now=lambda: T0)
    c = ti.check_tv_ideas(_ctx(tmp_path, T0 + pd.Timedelta(minutes=30)))
    assert c.name == "tv_ideas" and c.status == "ok"
    c = ti.check_tv_ideas(_ctx(tmp_path, T0 + pd.Timedelta(hours=3)))           # Tuesday
    assert c.status == "warn" and "3" in c.reason
    sat = pd.Timestamp("2026-10-31 12:00", tz="UTC")
    assert ti.check_tv_ideas(_ctx(tmp_path, sat)).status == "ok"


def test_health_warns_on_an_unreadable_file(tmp_path: Path):
    (tmp_path / "tv_ideas.db").write_bytes(b"not sqlite" * 100)
    assert ti.check_tv_ideas(_ctx(tmp_path, T0)).status == "warn"


# ------------------------------------------------------------------------------------------------ summary
def test_summary_counts_shares(tmp_path: Path):
    db = _db(tmp_path)
    api = FakeIdeasApi([
        make_idea(1, created=1_700_000_000.0, direction=1, description="Buy 2000 SL 1990 TP 2020"),
        make_idea(2, created=1_700_100_000.0, direction=0, description="No levels here",
                  updates=[{"id": 3, "ts": 1_700_200_000.0}]),
        make_idea(3, created=1_700_300_000.0, direction=2, ticker="EURUSD", description="Sell 1.10"),
    ])
    ti.backfill(_cfg(), db, _client(api), authors=["SynthAuthor"], now=lambda: T0)
    s = ti.summary(db.path)
    a = s["authors"]["SynthAuthor"]
    assert a["ideas"] == 3 and a["symbols"] == {"XAUUSD": 2, "EURUSD": 1}
    assert a["share_direction"] == pytest.approx(2 / 3) and a["share_edited"] == pytest.approx(1 / 3)
    assert a["share_any_level"] == pytest.approx(2 / 3) and a["share_entry_stop_target"] == pytest.approx(1 / 3)
    assert a["first_published"].startswith("2023-11-14") and a["n_updates"] == 1


def test_reparse_recomputes_derived_levels_and_never_touches_the_text(tmp_path: Path):
    db = _db(tmp_path)
    api = FakeIdeasApi([make_idea(1, created=1_700_000_000.0, description="Buy 2000 SL 1990 TP 2020")])
    ti.backfill(_cfg(), db, _client(api), authors=["SynthAuthor"], now=lambda: T0)
    con = sqlite3.connect(db.path)
    con.execute("UPDATE tv_ideas SET levels = '{}', has_entry = 0")           # an older parser's output
    con.commit()
    con.close()
    before = _rows(db, "SELECT description, available_utc FROM tv_ideas")[0]
    assert db.reparse() == 1
    r = _rows(db, "SELECT * FROM tv_ideas")[0]
    assert r["has_entry"] == 1 and json.loads(r["levels"])["entry"] == [2000.0]
    assert (r["description"], r["available_utc"]) == (before["description"], before["available_utc"])


def test_backfill_gives_up_on_ideas_the_listing_counts_but_never_serves(tmp_path: Path):
    """TradingView's count can include hidden or removed ideas: the slicing stops after a word round finds nothing
    new, reports the gap, and still reads every detail it has."""
    api = FakeIdeasApi(_catalogue(n_fx=1300, n_oanda=0, n_tvc=0))
    real = api._listing

    def counts_a_ghost(q: dict[str, str]) -> dict[str, Any]:
        out = real(q)
        if q.get("sort") == "recent_extended" and not ({"exchange", "q", "type", "is_video", "symbol"} & set(q)):
            out["count"] += 1
        return out

    api._listing = counts_a_ghost  # type: ignore[method-assign]
    db = _db(tmp_path)
    rep = ti.backfill(_cfg(), db, _client(api), authors=["SynthAuthor"], now=lambda: T0)
    lst = rep.listing["SynthAuthor"]
    assert (lst.total, lst.collected) == (1301, 1300) and lst.requests < 200
    assert rep.ok and "incomplete" in rep.reason and rep.details_fetched == 1300
