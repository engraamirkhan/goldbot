"""The replica on the engine's decision frame, shadow only (engine/replica_context.py, quant + trading-safety rules of
2026-10-18): the carried state gives an agent the full-history value on the engine's tail; restart, gap and stale code
are handled by the feed; the agent is never sent to the broker; with no source it proposes nothing."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.db.connection import Database
from goldbot.engine.replica_context import ReplicaShadow
from goldbot.engine.tv_context import TvShadowWatch
from goldbot.execution.broker import Tick
from goldbot.specialists.base import SPECIALISTS
from goldbot.specialists.smc_structure import SmcStructureSpecialist
from goldbot.specialists.tv_event import TvEventSpecialist

TICK = Tick(ts_utc=pd.Timestamp('2010-02-01', tz='UTC'), bid=1.0, ask=1.1)
TAIL = 800        # the engine's feature_window


@pytest.fixture(scope="module")
def series() -> pd.DataFrame:
    b1 = ticks_to_1m(synthetic_ticks("2010-01-04", "2010-03-20", ticks_per_minute=1, seed=11))
    return mid(resample_bars(b1, "15m").reset_index(drop=True))


def _agent(rule: str = "tvr1") -> SmcStructureSpecialist:
    return SmcStructureSpecialist(rule=rule)


class Source:
    def __init__(self, series: pd.DataFrame):
        self.series, self.calls, self.end = series, 0, 0

    def load(self, tf: str) -> pd.DataFrame:
        self.calls += 1
        return self.series.iloc[:self.end + 1].reset_index(drop=True)

    def before(self, tf: str, first: pd.Timestamp) -> int:
        return int((pd.to_datetime(self.series["ts_utc"], utc=True) < first).sum())


def _shadow(tmp_path, src: Source) -> ReplicaShadow:
    return ReplicaShadow(Database.open(tmp_path, "aux"), src.load, src.before)


def _frame(src: Source, end: int):
    src.end = end
    m = src.series.iloc[end + 1 - TAIL:end + 1].reset_index(drop=True)
    return SimpleNamespace(m=m, X=pd.DataFrame(index=m.index))


def _truth(series: pd.DataFrame, agent: SmcStructureSpecialist, end: int) -> bool:
    """Whether the agent fires on bar `end` when run on the whole series from its start (research)."""
    return end in _signals(series, agent)


def _signals(series: pd.DataFrame, agent: SmcStructureSpecialist) -> set[int]:
    """Signal bars on the whole series from its start (prefix-invariant: tests/test_smc_structure.py)."""
    return set(agent.candidates(series, pd.DataFrame())["idx"].astype(int))


def _fires(c: pd.DataFrame, last: int) -> bool:
    return bool(len(c)) and int(c["idx"].iloc[-1]) == last


def _walk(shadow: ReplicaShadow, src: Source, agent, ends) -> list[bool]:
    return [_fires(shadow.candidates(agent, _frame(src, e)), TAIL - 1) for e in ends]


@pytest.mark.parametrize("rule", ["tvr1", "tvr2"])
def test_the_engine_tail_decision_equals_the_full_history_value_on_every_bar(tmp_path, series, rule):
    agent, src = _agent(rule), Source(series)
    shadow = _shadow(tmp_path, src)
    ends = range(2940, 3140)          # holds tvr1's bar 2975 and tvr2's 2982 and 3128
    got = _walk(shadow, src, agent, ends)
    want = [_truth(series, agent, e) for e in ends]
    assert sum(want[agent.state_lag + 1:]) >= 1, "the window must hold signals or this proves nothing"
    # the first state_lag + 1 bars after the bootstrap keep too few snapshots: fail closed, not wrong
    k = agent.state_lag + 1
    assert got[k:] == want[k:]
    assert src.calls == 1


def test_a_restart_resumes_the_carried_state_and_decides_as_the_full_history(tmp_path, series):
    agent, src = _agent(), Source(series)
    ends = list(range(2000, 2400))
    first = _walk(_shadow(tmp_path, src), src, agent, ends[:200])
    again = _shadow(tmp_path, src)                             # a new process on the same aux.db
    second = _walk(again, src, agent, ends[200:])
    assert src.calls == 1                                      # no rebuild after the restart
    assert second == [_truth(series, agent, e) for e in ends[200:]]
    assert len(first) == 200


def test_a_gap_rebuilds_from_the_series_start_and_values_stay_exact(tmp_path, series):
    agent, src = _agent(), Source(series)
    shadow = _shadow(tmp_path, src)
    _walk(shadow, src, agent, range(2000, 2010))
    assert src.calls == 1
    ends = range(2600, 2600 + 300)                             # the engine was down: the snapshot is not in the tail
    got = _walk(shadow, src, agent, ends)
    assert src.calls == 2
    k = agent.state_lag + 1
    assert got[k:] == [_truth(series, agent, e) for e in ends][k:]


def test_a_state_written_by_other_code_is_refused_and_rebuilt(tmp_path, series):
    agent, src = _agent(), Source(series)
    shadow = _shadow(tmp_path, src)
    _walk(shadow, src, agent, range(2000, 2005))
    with shadow.db.write() as conn:
        conn.execute("UPDATE replica_state SET code_version = '0', state = replace(state, '\"code_version\":\"1\"', "
                     "'\"code_version\":\"0\"')")
    ends = range(2005, 2005 + 300)
    got = _walk(_shadow(tmp_path, src), src, agent, ends)
    assert src.calls == 2                                      # the stale state was not used
    k = agent.state_lag + 1
    assert got[k:] == [_truth(series, agent, e) for e in ends][k:]


def test_with_no_source_or_a_failing_source_the_agent_proposes_nothing(tmp_path, series):
    agent, src = _agent(), Source(series)

    def broken(tf: str) -> pd.DataFrame:
        raise OSError("store unreadable")

    shadow = ReplicaShadow(Database.open(tmp_path, "aux"), broken, src.before)
    assert shadow.candidates(agent, _frame(src, 2000)).empty
    watch = TvShadowWatch()                                    # the engine's holder: no replica set
    out = watch.candidates(SimpleNamespace(shadow=None, cfg=SimpleNamespace(tv_max_lag_s=60)), "15m", _frame(src, 2000),
                           [agent], pd.Timestamp("2010-02-01", tz="UTC"), TICK, 1.0, 0.1)
    assert out[agent.agent_id].empty


def test_the_tv_watch_serves_replica_agents_from_the_shadow_and_other_agents_as_before(tmp_path, series):
    agent, src = _agent("tvr2"), Source(series)
    watch = TvShadowWatch()
    watch.replica = _shadow(tmp_path, src)
    eng = SimpleNamespace(shadow=None, cfg=SimpleNamespace(tv_max_lag_s=60))
    fired = 0
    for e in range(2000, 2300):
        out = watch.candidates(eng, "15m", _frame(src, e), [agent], pd.Timestamp("2010-02-01", tz="UTC"), TICK, 1.0, 0.1)
        fired += _fires(out[agent.agent_id], TAIL - 1) and _truth(series, agent, e)
    assert fired > 0


def test_a_replica_reading_agent_is_shadow_only_like_a_tradingview_one():
    assert _agent().shadow_only and _agent("tvr2").shadow_only
    assert not SPECIALISTS["session_open"]().shadow_only
    assert TvEventSpecialist.requires_tv       # its instances are shadow only through the same property


class _AlwaysFires:
    """A replica source that makes the agent fire on every decision bar (side long): only share 0 can stop an entry."""

    def candidates(self, agent, fr) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(fr.m) - 1], "side": [1]})


@pytest.mark.integration
def test_the_engine_never_sends_a_replica_agent_to_the_broker(tmp_path):
    from goldbot.allocator.rules import RuleAllocator
    from goldbot.engine import ConstantModel, Engine, EngineConfig
    from goldbot.execution.broker import Tick
    from goldbot.execution.paper import PaperBroker

    class Full(RuleAllocator):
        def weights(self, r):
            return {**super().weights(r), "smc_structure": 1.0}

    agent = _agent("tvr2")
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path)),
                 PaperBroker(equity=10_000), [agent], {agent.agent_id: ConstantModel(p=0.95)}, allocator=Full())
    eng._tv.replica = _AlwaysFires()      # type: ignore[assignment]
    ticks = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=1, seed=5)
    out = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    assert not [d for d in out if str(d.get("action")).startswith("executed")]
    assert eng.broker.deals_since(pd.Timestamp("2025-01-01", tz="UTC")).empty
    assert eng.shadow is None or not [d for d in eng.decisions if d.get("action") == "order"]
