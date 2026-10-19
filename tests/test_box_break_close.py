"""`box_break_close` (owner's box strategy, shadow roster 2026-10-19) and rule models (ADR 0038): the live decision from
the carried replica state equals the full-history rule; a rule model is never a calibrated probability, never sizes a
live entry, and is registered once; the roster founders exist and are never cloned."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.db.connection import Database
from goldbot.engine.replica_context import ReplicaShadow
from goldbot.engine.shadow import score
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import SHADOW_ROSTER, Population
from goldbot.research.rule_model import RULE_FIRED, RuleModel, is_uncalibrated
from goldbot.specialists.box_break_close import BoxBreakCloseSpecialist

TAIL = 800


@pytest.fixture(scope="module")
def series() -> pd.DataFrame:
    b1 = ticks_to_1m(synthetic_ticks("2010-01-04", "2010-04-30", ticks_per_minute=2, seed=7))
    return mid(resample_bars(b1, "15m").reset_index(drop=True))


class History:
    """A store stand-in: the series' bars before a time."""

    def __init__(self, series: pd.DataFrame):
        self.series, self.ts = series, pd.to_datetime(series["ts_utc"], utc=True)

    def before(self, tf: str, first: pd.Timestamp, n: int | None = None) -> pd.DataFrame:
        b = self.series[(self.ts < first).to_numpy()].reset_index(drop=True)
        return (b.tail(n) if n is not None else b).reset_index(drop=True)

    def count(self, tf: str, first: pd.Timestamp) -> int:
        return int((self.ts < first).sum())


def _frame(series: pd.DataFrame, end: int, tail: int = TAIL):
    m = series.iloc[max(0, end + 1 - tail):end + 1].reset_index(drop=True)
    return SimpleNamespace(m=m, X=pd.DataFrame(index=m.index))


def _walk(shadow: ReplicaShadow, series, agent, ends, tail: int = TAIL) -> list[int]:
    out = []
    for e in ends:
        c = shadow.candidates(agent, _frame(series, e, tail))
        last = min(tail, e + 1) - 1
        out.append(int(c["side"].iloc[-1]) if len(c) and int(c["idx"].iloc[-1]) == last else 0)
    return out


def test_the_live_decision_from_the_carried_state_equals_the_full_history_rule_on_every_bar(tmp_path, series):
    agent = BoxBreakCloseSpecialist()
    full = agent.candidates(series, pd.DataFrame())
    truth = dict(zip(full["idx"].astype(int), full["side"].astype(int)))
    ends = range(1500, len(series))
    want = [truth.get(e, 0) for e in ends]
    assert sum(1 for w in want if w) >= 3, "the window must hold signals or this proves nothing"
    shadow = ReplicaShadow(Database.open(tmp_path, "aux"), history=History(series))
    got = _walk(shadow, series, agent, ends)
    assert got[2:] == want[2:]           # the bar right after the bootstrap has no state one bar back: none, not wrong


def test_a_tail_shorter_than_the_replica_context_is_extended_from_the_store_and_decides_the_same(tmp_path, series):
    agent = BoxBreakCloseSpecialist(timeframe="4h")         # the engine holds ~240 4h bars, the replica needs 313
    full = agent.candidates(series, pd.DataFrame())
    truth = dict(zip(full["idx"].astype(int), full["side"].astype(int)))
    ends = range(1500, len(series))
    shadow = ReplicaShadow(Database.open(tmp_path, "aux"), history=History(series))
    got = _walk(shadow, series, agent, ends, tail=240)
    assert got[2:] == [truth.get(e, 0) for e in ends][2:]


def test_appending_later_bars_never_changes_a_past_decision(series):
    agent = BoxBreakCloseSpecialist()
    short = agent.candidates(series.iloc[:2000], pd.DataFrame())
    full = agent.candidates(series, pd.DataFrame())
    assert short.to_dict("list") == full[full["idx"] < 2000].reset_index(drop=True).to_dict("list")


def test_the_agent_is_shadow_only_with_a_fixed_two_r_target_per_timeframe():
    for tf, bars in (("15m", 96), ("1h", 48), ("4h", 24)):
        a = BoxBreakCloseSpecialist(timeframe=tf)
        assert a.shadow_only and a.timeframe == tf
        assert a.label_spec.target_atr == 2 * a.label_spec.stop_atr and a.label_spec.max_bars == bars


def test_a_rule_model_says_fired_is_not_calibrated_and_keeps_no_raw_score():
    m = RuleModel(rule="box_break_close")
    assert is_uncalibrated(m) and not is_uncalibrated(SimpleNamespace())
    p, raw = score(m, pd.DataFrame({"side": [1]}))
    assert p == RULE_FIRED and raw is None                  # the weekly recalibration never refits it


def test_a_rule_challenger_is_registered_once_loads_and_has_no_backtest(tmp_path):
    reg = ModelRegistry(tmp_path)
    e = reg.ensure_rule_challenger(family="box_break_close", agent_id="a1")
    assert e is not None and e.status == "challenger" and e.backtest == {}
    assert reg.ensure_rule_challenger(family="box_break_close", agent_id="a1") is None
    loaded = ModelRegistry(tmp_path).shadow_models()
    assert isinstance(loaded[e.version][1], RuleModel)


def test_the_roster_founders_exist_in_shadow_with_zero_capital(tmp_path):
    pop = Population(tmp_path / "population.json")
    pop.ensure_founders(pd.Timestamp("2026-10-19", tz="UTC"))
    roster = [m for m in pop.members.values() if m.family == "box_break_close"]
    assert sorted(m.specialist().timeframe for m in roster) == sorted(tf for _, tf in SHADOW_ROSTER)
    assert all(m.status == "shadow" and m.capital_weight == 0 for m in roster)
