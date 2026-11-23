"""The KoG weekly forward shadow scorer (goldbot/research/kog_weekly_shadow.py; prereg-2026-11-23-kog-weekly-redbox.md
sections 3, 4 and 7.1). Synthetic bars, text and levels only: no author text or author level lives in the repo."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.timeutil import server_wall
from goldbot.db.connection import Database
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.research import kog_weekly_shadow as kws
from goldbot.research import tv_ideas as tva

MID = 2000.0
HALF = 0.10                      # bid = mid - 0.10, ask = mid + 0.10
WIG = 0.5                        # each minute's high / low = mid +- 0.5
EXTRA = 0.144                    # C4 round trip beyond the spread
SWAP = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=40.0, triple_weekday=2, server_tz="Europe/Athens",
                contract_oz=100.0)   # long pays $0.60/oz a night; the short's +$0.40 credit is never taken
COSTS = tva.ScoreCosts(extra_usd=EXTRA, swap=SWAP, source="test priors")
PUB = pd.Timestamp("2026-11-22 18:00", tz="UTC")                     # a Sunday
AVAIL = PUB + pd.Timedelta(minutes=5)
WINDOW_END = pd.Timestamp("2026-11-27 21:55", tz="UTC")              # Friday 23:55 server (NY+7 = UTC+2)
HORIZON = WINDOW_END + pd.Timedelta(days=7)
NOW = pd.Timestamp("2026-12-06", tz="UTC")
SIG_BAR = pd.Timestamp("2026-11-24 10:00", tz="UTC")                 # Tuesday; server hour 12
SINCE = pd.Timestamp("2026-11-22", tz="UTC")

BLOCK = """Weekly words.

RED BOX TARGETS:

BREAK ABOVE {a} for {ta}
BREAK BELOW {b} for {tb}
"""


def text(a: str = "2010", ta: str = "2020 and 2030", b: str = "1990", tb: str = "1980 and 1970") -> str:
    return BLOCK.format(a=a, ta=ta, b=b, tb=tb)


def post(i: int = 7, description: str | None = None, *, published: pd.Timestamp = PUB,
         available: pd.Timestamp = AVAIL, title: str = "THE KOG REPORT", author: str = "KnightsofGold") -> dict:
    return {"idea_id": i, "author": author, "title": title, "description": text() if description is None else description,
            "published_utc": published, "available_utc": available, "backfilled": False}


def posts(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows) or [post()])


def bars(start: str = "2026-11-16", end: str = "2026-12-08") -> pd.DataFrame:
    """Flat 1m bid/ask minutes at MID outside the weekly close (Friday 23:55 to Monday 01:05 on NY+7)."""
    ts = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    wall = server_wall(ts, "NY+7")
    tod = wall - wall.normalize()
    closed = (((wall.dayofweek == 4) & (tod >= pd.Timedelta(hours=23, minutes=55))) | (wall.dayofweek >= 5)
              | ((wall.dayofweek == 0) & (tod < pd.Timedelta(hours=1, minutes=5))))
    df = pd.DataFrame({"ts_utc": ts[~closed]})
    set_mid(df, df["ts_utc"].min(), df["ts_utc"].max() + pd.Timedelta(minutes=1), MID)
    return df


def set_mid(b: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp, mid: float, *, low: float | None = None) -> None:
    """Minutes in [t0, t1) at `mid` (open = close = mid, high/low = mid +- WIG, or the given mid low)."""
    sel = (b["ts_utc"] >= t0) & (b["ts_utc"] < t1)
    for side, sgn in (("bid", -1), ("ask", 1)):
        b.loc[sel, f"{side}_open"] = mid + sgn * HALF
        b.loc[sel, f"{side}_close"] = mid + sgn * HALF
        b.loc[sel, f"{side}_high"] = mid + WIG + sgn * HALF
        b.loc[sel, f"{side}_low"] = (mid - WIG if low is None else low) + sgn * HALF


def long_week() -> pd.DataFrame:
    """Tuesday 10:00 UTC the mid jumps to 2012 (1h close above 2010, 15m too), 12:00-13:00 trades 2021 (first target
    2020 on the bid), Wednesday 10:30 one minute wicks to a 1989 low (the stop at 1990); flat 2012 to the end."""
    b = bars()
    end = b["ts_utc"].max() + pd.Timedelta(minutes=1)
    set_mid(b, SIG_BAR, end, 2012.0)
    set_mid(b, SIG_BAR + pd.Timedelta(hours=2), SIG_BAR + pd.Timedelta(hours=3), 2021.0)
    wick = pd.Timestamp("2026-11-25 10:30", tz="UTC")
    set_mid(b, wick, wick + pd.Timedelta(minutes=1), 2012.0, low=1989.0)
    return b


def score(p: pd.DataFrame | None = None, b: pd.DataFrame | None = None, *, now: pd.Timestamp = NOW,
          **kw) -> dict[tuple[int, str, str], kws.ShadowRow]:
    rows = kws.score_shadow(posts() if p is None else p, long_week() if b is None else b, COSTS, since=SINCE,
                            now=now, **kw)
    return {(r.idea_id, r.side, r.tf): r for r in rows}


# ------------------------------------------------------------------------------------------------- section 3
def test_a_long_break_exits_half_at_the_first_target_and_half_at_the_stop_net_of_costs():
    got = score()
    r = got[(7, "long", "1h")]
    assert r.status == "scored" and r.reason is None
    assert r.signal_utc == SIG_BAR + pd.Timedelta(hours=1)
    assert r.fill_utc == SIG_BAR + pd.Timedelta(hours=1, minutes=1)              # signal close + 60 s
    assert r.exit_utc == pd.Timestamp("2026-11-25 10:30", tz="UTC")
    fill, stop = 2012.0 + HALF, 1990.0
    risk = fill - stop
    # half at 2020 (Tuesday, no rollover), half at the stop (one rollover: $0.60); C4 extra once on the full size,
    # the $0.145 stop add-on on the half stopped
    net = 0.5 * (2020.0 - fill) + 0.5 * (stop - fill - 0.60) - EXTRA - 0.145 * 0.5
    assert r.r_net == pytest.approx(net / risk)
    assert r.r_stress == pytest.approx((net - 0.5 * (EXTRA + 0.145 * 0.5)) / risk)
    assert r.n_targets == 2 and r.frac_stopped == pytest.approx(0.5)
    assert r.risk_usd == pytest.approx(risk)                             # a distance, not a level
    m15 = got[(7, "long", "15m")]
    assert m15.signal_utc == SIG_BAR + pd.Timedelta(minutes=15) and m15.r_net == pytest.approx(net / risk)


def test_the_side_that_never_closes_beyond_its_level_is_a_no_signal_row():
    got = score()
    for tf in ("1h", "15m"):
        assert got[(7, "short", tf)].status == "refused" and got[(7, "short", tf)].reason == "no_signal"


def test_a_short_s_swap_is_never_credited_and_the_window_end_exit_is_at_the_ask():
    b = bars()
    end = b["ts_utc"].max() + pd.Timedelta(minutes=1)
    set_mid(b, SIG_BAR, end, 1985.0)                     # closes below 1990, never reaches 1980 or 2010
    r = score(b=b)[(7, "short", "1h")]
    fill = 1985.0 - HALF                                 # sold at the bid
    out = 1985.0 + HALF                                  # bought back at the ask, last minute before Friday 23:55
    assert r.exit_utc == WINDOW_END
    assert r.frac_stopped == 0.0
    assert r.r_net == pytest.approx((fill - out - EXTRA) / (2010.0 - fill))


def test_the_window_ends_at_friday_23_55_server_and_a_post_after_the_close_covers_the_next_week():
    m = tva.Minutes.from_frame(bars())
    assert kws.window_end(m, AVAIL) == WINDOW_END
    assert kws.window_end(m, pd.Timestamp("2026-11-27 22:30", tz="UTC")) == WINDOW_END + pd.Timedelta(days=7)
    assert kws.window_end(m, pd.Timestamp("2026-11-28 12:00", tz="UTC")) == WINDOW_END + pd.Timedelta(days=7)


def test_a_box_under_3_dollars_of_risk_is_refused():
    r = score(posts(post(description=text(b="2009.5", tb="2005"))))[(7, "long", "1h")]
    assert r.status == "refused" and r.reason == "r_below_3"


def test_targets_at_or_behind_the_fill_are_dropped_and_none_left_is_no_targets_left():
    r = score(posts(post(description=text(ta="2011 and 2012"))))[(7, "long", "1h")]
    assert r.reason == "no_targets_left"


def test_an_entry_bar_at_or_after_the_window_end_is_refused():
    """With gold's minutes the 15m signal bar's entry can only fall at the window end across a hole (which makes the
    week `no_bars`), so the rule is tested on the side scorer with a window ending at the entry minute."""
    m = tva.Minutes.from_frame(long_week())
    out = kws.score_side(m, kws.tf_bars(m, "15m"), available=AVAIL, window_end=SIG_BAR + pd.Timedelta(minutes=16),
                         side="long", above=2010.0, below=1990.0, targets=(2020.0, 2030.0), costs=COSTS)
    assert out == "entry_after_window"


def test_a_post_with_one_side_extracted_is_one_side_only_and_counted_at_once():
    p = posts(post(description=text(tb="1980 to 1970 region")))
    got = score(p, now=AVAIL + pd.Timedelta(hours=1))
    assert list(got) == [(7, "-", "-")]
    r = got[(7, "-", "-")]
    assert r.reason == "one_side_only" and r.extractor_abstain == "below:grammar"


def test_a_post_without_the_block_records_the_extractor_s_abstain_reason():
    r = score(posts(post(description="No block here.")))[(7, "-", "-")]
    assert r.status == "refused" and r.reason == "extractor_abstain" and r.extractor_abstain == "no_header"


def test_nothing_is_scored_until_the_week_has_closed_plus_seven_days():
    assert score(now=HORIZON - pd.Timedelta(hours=1)) == {}
    assert len(score(now=HORIZON + pd.Timedelta(hours=1))) == 4


def test_a_hole_in_the_weeks_waits_then_is_no_bars_final():
    b = long_week()
    gap = pd.Timestamp("2026-11-26 10:00", tz="UTC")
    b = b[~((b["ts_utc"] >= gap) & (b["ts_utc"] < gap + pd.Timedelta(hours=1)))].reset_index(drop=True)
    assert score(b=b) == {}                                            # 2 days after the horizon: still waiting
    got = score(b=b, now=HORIZON + pd.Timedelta(days=8))
    assert list(got) == [(7, "-", "-")] and got[(7, "-", "-")].reason == "no_bars"


def test_only_weekly_reports_available_since_the_setting_are_read():
    p = posts(post(1, published=PUB - pd.Timedelta(days=7), available=AVAIL - pd.Timedelta(days=7)),
              post(2, title="THE KOG REPORT - Update"), post(3, author="Goldviewfx"), post(4))
    assert {k[0] for k in score(p)} == {4}


def test_the_skip_set_is_not_rescored():
    assert score(skip={7}) == {}


# ------------------------------------------------------------------------------------------------- controls
def test_control_a_is_200_replicates_seeded_on_the_post_and_replicate():
    r = score()[(7, "long", "1h")]
    reps = r.control_a_r
    assert len(reps) == kws.N_SHIFT == 200
    shift = kws.block_shift(7, 3, 20.0)
    rng = np.random.default_rng(np.random.SeedSequence([zlib.crc32(b"7"), 3]))
    assert shift == pytest.approx(rng.uniform(-20.0, 20.0))
    fired = [x for x in reps if x is not None]
    assert r.control_a_n == len(fired) and r.control_a_mean == pytest.approx(float(np.mean(fired)))
    assert score()[(7, "long", "1h")].control_a_r == reps                       # deterministic


def test_control_c_keeps_or_mirrors_the_event_on_a_seeded_coin():
    r = score()[(7, "long", "1h")]
    assert r.control_c_flipped is not None and r.control_c_r is not None
    if not r.control_c_flipped:
        assert r.control_c_r == pytest.approx(r.r_net)
    else:
        assert r.control_c_r != pytest.approx(r.r_net)


def test_twins_match_tf_side_hour_and_atr_multiples_and_are_drawn_after_exit_excluding_kwr_signal_bars():
    m = tva.Minutes.from_frame(long_week())
    tb = kws.tf_bars(m, "1h")
    exit_ = pd.Timestamp("2026-11-25 10:30", tz="UTC")
    fires = {int((SIG_BAR + pd.Timedelta(hours=26)).value)}                 # pretend a KWR signal on Wed 12:00 close
    cand = kws.twin_candidates(tb, event_open_ns=int(SIG_BAR.value), exit_ns=int(exit_.value), fires=fires)
    closes = pd.DatetimeIndex(tb.close_ns[cand].astype("datetime64[ns]"), tz="UTC")
    assert len(cand) == 3 * 5 - 1          # 3 server hours a weekday over 7 days, less the KWR bar
    assert (closes > exit_).all() and (closes <= exit_ + pd.Timedelta(days=7)).all()
    hours = server_wall(closes - pd.Timedelta(hours=1), "NY+7").hour
    assert set(hours) <= {11, 12, 13}
    assert int((SIG_BAR + pd.Timedelta(hours=26)).value) not in set(tb.close_ns[cand].tolist())
    got = score()
    r = got[(7, "long", "1h")]             # a 1h event has at most 15 eligible bars: all of them, no repeats
    assert r.twin_n == len(r.twin_r) == 15
    assert r.twin_mean_r == pytest.approx(float(np.mean(r.twin_r)))
    assert got[(7, "long", "15m")].twin_n == kws.N_TWINS == 20


def test_a_twin_scales_the_event_s_stop_and_each_target_by_its_own_atr():
    t = kws.twin_geometry(fill=100.0, side=1, event_fill=2012.1, event_stop=1990.0, event_targets=(2020.0, 2030.0),
                          event_atr=2.0, twin_atr=4.0)
    stop, targets = t
    assert stop == pytest.approx(100.0 - 44.2) and targets == pytest.approx((100.0 + 15.8, 100.0 + 35.8))


# ------------------------------------------------------------------------------------------------- storage and job
def test_rows_hold_no_idea_text_and_no_price_level():
    fields = set(kws.ShadowRow.model_fields)
    assert not fields & {"description", "title", "level", "stop", "targets", "fill_price", "above", "below"}
    dump = json.dumps([r.model_dump(mode="json") for r in score().values()])
    assert "Weekly words" not in dump and "RED BOX" not in dump


def test_saving_a_night_twice_writes_each_row_once(tmp_path):
    aux = Database.open(tmp_path, "aux")
    rows = list(score().values())
    assert kws.save_shadow(aux, rows, scored_utc=NOW) == len(rows)
    assert kws.save_shadow(aux, rows, scored_utc=NOW + pd.Timedelta(days=1)) == 0
    stored = kws.stored_rows(aux)
    assert len(stored) == len(rows) and kws.stored_post_ids(aux) == {7}
    back = {(r.idea_id, r.side, r.tf): r for r in stored}[(7, "long", "1h")]
    assert back.control_a_r == score()[(7, "long", "1h")].control_a_r


def test_the_shadow_setting_defaults_to_the_first_forward_sunday():
    from goldbot.config import load_settings
    assert load_settings().tv_ideas.kog_weekly_shadow_from == date(2026, 11, 22)


def _forbidden(name: str) -> bool:
    return name.startswith(("goldbot.engine", "goldbot.execution", "goldbot.risk.gate")) or "RiskGate" in name


@pytest.mark.parametrize("module", ["kog_weekly", "kog_weekly_shadow"])
def test_the_kog_modules_import_nothing_from_the_engine_or_execution_or_riskgate(module):
    src = Path(__file__).resolve().parents[2] / "goldbot" / "research" / f"{module}.py"
    names: list[str] = []
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
    assert not [n for n in names if _forbidden(n)]
    code = (f"import sys, goldbot.research.{module}; "
            "print([m for m in sys.modules if m.startswith(('goldbot.engine', 'goldbot.execution', 'goldbot.risk'))])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


class _Store:
    def __init__(self, b: pd.DataFrame):
        self.b = b
        self.starts: list[pd.Timestamp] = []

    def read(self, table: str, *, source: str, symbol: str, start=None, end=None, columns=None) -> pd.DataFrame:
        if table != "bars_1m_live" or source != "icm":
            return pd.DataFrame()
        self.starts.append(start)
        b = self.b
        return b[(b["ts_utc"] >= start) & (b["ts_utc"] < end)] if start is not None else b


def _job(tmp_path: Path, *extra: tuple[str, str, pd.Timestamp]):
    from types import SimpleNamespace

    from goldbot.config import load_settings
    from goldbot.data import tv_ideas as ti
    from tests.tv_ideas_fake import make_idea
    db = ti.IdeasDb(tmp_path / "tv_ideas.db")
    det = make_idea(1, author="KnightsofGold", created=PUB.timestamp(), title="THE KOG REPORT", description=text())
    idea, ups = ti.idea_from_detail(det)
    db.record(idea, ups, AVAIL, live_since=PUB - pd.Timedelta(days=1), latency_s=900)
    for k, (author, title, published) in enumerate(extra, start=2):
        det = make_idea(k, author=author, created=published.timestamp(), title=title, description=text())
        other, ups = ti.idea_from_detail(det)
        db.record(other, ups, published + pd.Timedelta(minutes=5), live_since=PUB - pd.Timedelta(days=60),
                  latency_s=900)
    accounts = [SimpleNamespace(account_id="acc-a", broker="icm", symbol="XAUUSD")]
    return load_settings(), accounts, idea.idea_id


def test_the_nightly_job_scores_the_shadow_after_the_ideas_and_reruns_write_nothing(tmp_path):
    settings, accounts, iid = _job(tmp_path)
    out = tva.run_job(settings, tmp_path, store=_Store(long_week()), accounts=accounts, slot=NOW, costs=COSTS)
    assert out["kog_weekly_shadow"]["written"] == 4 and out["kog_weekly_shadow"]["posts"] == 1
    assert "Weekly words" not in repr(out) and "RED BOX" not in repr(out)
    again = tva.run_job(settings, tmp_path, store=_Store(long_week()), accounts=accounts, slot=NOW, costs=COSTS)
    assert again["kog_weekly_shadow"]["written"] == 0
    assert {r.idea_id for r in kws.stored_rows(Database.open(tmp_path, "aux"))} == {iid}


def test_a_shadow_failure_is_logged_and_does_not_break_the_ideas_job(tmp_path, monkeypatch, caplog):
    settings, accounts, _ = _job(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(kws, "score_shadow", boom)
    with caplog.at_level("WARNING"):
        out = tva.run_job(settings, tmp_path, store=_Store(long_week()), accounts=accounts, slot=NOW, costs=COSTS)
    assert out["kog_weekly_shadow"] == {"error": "RuntimeError"}
    assert out["written"] >= 1 and "kog_weekly_shadow" in caplog.text and "RuntimeError" in caplog.text
    assert "synthetic" not in caplog.text                    # an exception message could carry a level: never logged


def test_the_shadow_s_bar_load_moves_forward_once_the_kog_weekly_rows_are_stored(tmp_path):
    """Code review: another author's idea, or a KoG post that is not a weekly report, never holds the shadow's bar load
    at an old date: it starts 3 days before the earliest KoG weekly report not stored yet."""
    nxt = PUB + pd.Timedelta(days=7)                                           # Sunday 11-29: not scoreable by NOW
    settings, accounts, _ = _job(tmp_path, ("Goldviewfx", "Some idea", pd.Timestamp("2026-11-23 09:00", tz="UTC")),
                                 ("KnightsofGold", "THE KOG REPORT - Update", PUB + pd.Timedelta(days=1)),
                                 ("KnightsofGold", "THE KOG REPORT", nxt))
    tva.run_job(settings, tmp_path, store=_Store(long_week()), accounts=accounts, slot=NOW, costs=COSTS)
    store = _Store(long_week())
    out = tva.run_job(settings, tmp_path, store=store, accounts=accounts, slot=NOW, costs=COSTS)
    assert out["kog_weekly_shadow"]["written"] == 0
    shadow_start = nxt + pd.Timedelta(minutes=5) - pd.Timedelta(days=3)
    assert shadow_start in store.starts
    assert pd.Timestamp("2026-11-23 09:05", tz="UTC") - pd.Timedelta(days=3) not in store.starts


# ------------------------------------------------------------------------------------------------- quant review round
def test_control_a_replicates_are_stored_on_a_side_that_did_not_fire_too():
    """Quant review: replicates on fired sides only would bias delta-a toward 0. The short never closes below 1990,
    but a block shifted up by more than 10 does fire on the flat 2000 before Tuesday."""
    r = score()[(7, "short", "1h")]
    assert r.status == "refused" and r.reason == "no_signal"
    assert len(r.control_a_r) == kws.N_SHIFT and r.control_a_n is not None and r.control_a_n > 0
    assert r.control_a_mean == pytest.approx(float(np.mean([x for x in r.control_a_r if x is not None])))


def test_scoring_at_the_horizon_and_with_30_more_days_of_minutes_gives_identical_rows():
    b = long_week()
    later = bars(end="2027-01-08")
    full = pd.concat([b, later[later["ts_utc"] > b["ts_utc"].max()]], ignore_index=True)
    early = score(b=full, now=HORIZON + pd.Timedelta(hours=1))
    late = score(b=full, now=HORIZON + pd.Timedelta(days=30))
    assert early.keys() == late.keys() and len(early) == 4
    assert all(early[k].model_dump() == late[k].model_dump() for k in early)


def test_a_break_bar_closing_before_available_utc_never_fires():
    """The 15m bar closing 10:15 is beyond the level, but the post is first seen at 10:20: the first bar that can see
    it closes 10:30."""
    seen = SIG_BAR + pd.Timedelta(minutes=20)
    r = score(posts(post(available=seen)))[(7, "long", "15m")]
    assert r.signal_utc == SIG_BAR + pd.Timedelta(minutes=30)
    assert r.fill_utc == SIG_BAR + pd.Timedelta(minutes=31)
