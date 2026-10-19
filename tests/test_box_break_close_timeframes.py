"""`box_break_close` on 30m (shadow roster amendment 2026-10-19b): the timeframe is known end to end (tf_seconds, the
store's bar table, the release resample), the agent runs on it with its pre-registered time exit, the carried-state
decision equals the full-history rule on 30m bars, and every roster timeframe closes on the engine's base clock (why
5m is not in the roster: the engine decides and advances shadow trades only at 15m closes)."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data import release
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.store import TABLES
from goldbot.data.synthetic import synthetic_ticks
from goldbot.db.connection import Database
from goldbot.engine.replica_context import ReplicaShadow
from goldbot.engine.runner import EngineConfig
from goldbot.research.population import SHADOW_ROSTER, Population
from goldbot.specialists.box_break_close import ROSTER_TIMEFRAMES, BoxBreakCloseSpecialist
from tests.test_box_break_close import History, _walk


@pytest.fixture(scope="module")
def series_30m() -> pd.DataFrame:
    b1 = ticks_to_1m(synthetic_ticks("2010-01-04", "2010-06-30", ticks_per_minute=2, seed=7))
    return mid(resample_bars(b1, "30m").reset_index(drop=True))


def test_30m_is_a_known_timeframe_with_a_bar_table_and_a_release_resample():
    assert tf_seconds("30m") == 1800
    assert "bars_30m" in TABLES
    assert "30m" in release.HIGHER_TFS


def test_30m_bars_floor_on_the_half_hour(series_30m):
    ts = pd.to_datetime(series_30m["ts_utc"], utc=True)
    assert ((ts.dt.minute % 30) == 0).all() and (ts.diff().dropna() >= pd.Timedelta(minutes=30)).all()


def test_the_agent_runs_on_30m_shadow_only_with_a_2r_target_and_a_36_hour_time_exit():
    a = BoxBreakCloseSpecialist(timeframe="30m")
    assert a.timeframe == "30m" and a.shadow_only and a.frozen_config
    assert a.label_spec.target_atr == 2 * a.label_spec.stop_atr
    assert a.label_spec.max_bars * tf_seconds("30m") == 36 * 3600


def test_the_roster_holds_box_break_close_on_15m_30m_1h_and_4h_and_founds_each_in_shadow(tmp_path):
    assert ROSTER_TIMEFRAMES == ("15m", "30m", "1h", "4h")
    assert [tf for fam, tf in SHADOW_ROSTER if fam == "box_break_close"] == list(ROSTER_TIMEFRAMES)
    pop = Population(tmp_path / "population.json")
    pop.ensure_founders(pd.Timestamp("2026-10-19", tz="UTC"))
    tfs = sorted(m.specialist().timeframe for m in pop.members.values() if m.family == "box_break_close")
    assert tfs == sorted(ROSTER_TIMEFRAMES)


def test_every_roster_timeframe_closes_on_the_engine_base_clock():
    """The engine decides each timeframe only at base-clock closes and advances its shadow trades one bar per such
    close: a timeframe shorter than (or not a multiple of) the base clock would skip bars and misstate its outcomes."""
    base = tf_seconds(EngineConfig.model_fields["decision_tf"].default)
    assert all(tf_seconds(tf) % base == 0 for _, tf in SHADOW_ROSTER)


def test_the_30m_live_decision_from_the_carried_state_equals_the_full_history_rule(tmp_path, series_30m):
    agent = BoxBreakCloseSpecialist(timeframe="30m")
    full = agent.candidates(series_30m, pd.DataFrame())
    truth = dict(zip(full["idx"].astype(int), full["side"].astype(int)))
    ends = range(1500, len(series_30m))
    want = [truth.get(e, 0) for e in ends]
    assert sum(1 for w in want if w) >= 3, "the window must hold signals or this proves nothing"
    shadow = ReplicaShadow(Database.open(tmp_path, "aux"), history=History(series_30m), warm_wait_s=120)
    assert _walk(shadow, series_30m, agent, ends)[2:] == want[2:]
