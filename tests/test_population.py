"""The population tournament's rules (design: A competing population of trading agents)."""
import random

import numpy as np
import pandas as pd
import pytest

from goldbot.api.schema import AgentRow
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.research import population as P
from goldbot.research.population import Member, Population, mutate_config, score_agent
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import AgentIdentity

NOW = pd.Timestamp("2026-10-03 12:00", tz="UTC")
FOUNDER = AgentIdentity(family="session_open", config=dict(SPECIALISTS["session_open"].default_config)).agent_id


def _add_trades(book: ShadowBook, agent_id: str, rets: list[float], p: float = 0.6, start: pd.Timestamp | None = None) -> None:
    version = f"{agent_id}-v1"
    t0 = start or NOW - pd.Timedelta(days=len(rets))
    book.track(version, t0)
    for i, r in enumerate(rets):
        ts = t0 + pd.Timedelta(hours=8 * i)
        entry = 2400.0
        t = ShadowTrade(version=version, agent_id=agent_id, side=1, entry_ts=ts, entry=entry, stop=entry - 4.0,
                        target=entry + 6.0, max_bars=16, p=p, bars_held=3, exit_ts=ts + pd.Timedelta(hours=1),
                        exit=entry * (1 + r), barrier="target" if r > 0 else "stop", ret=r)
        book.books[version].closed.append(t)


def _member(pop: Population, agent_id: str, status: str = "shadow", generation: int = 1, **cfg) -> Member:
    config = {**SPECIALISTS["session_open"].default_config, **cfg}
    m = Member(agent_id=agent_id, family="session_open", config=config, generation=generation, status=status,  # type: ignore[arg-type]
               created_utc=NOW - pd.Timedelta(days=200), status_since_utc=NOW - pd.Timedelta(days=200))
    pop.members[agent_id] = m
    return m


def _good(n: int, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(np.where(rng.random(n) < 0.6, 0.0025, -0.0016))      # ~60% winners, positive expectancy


def _bad(n: int, seed: int = 1) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(np.where(rng.random(n) < 0.3, 0.0025, -0.0016))      # ~30% winners, negative expectancy


def test_founders_are_seeded_and_unranked_agents_are_not_promoted(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    _add_trades(book, FOUNDER, _good(40))                               # below the 60-trade ranking threshold
    out = pop.tournament(book, NOW)
    assert FOUNDER in pop.members and pop.members[FOUNDER].notes == ["founder"]
    assert out["promoted"] == [] and pop.members[FOUNDER].status == "shadow"
    assert pop.members[FOUNDER].stats["n"] == 40


def test_strong_shadow_record_is_promoted_and_a_weak_one_is_not(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    pop.ensure_founders(NOW)
    _member(pop, "strong")
    _member(pop, "flat")
    _add_trades(book, "strong", _good(90))
    _add_trades(book, "flat", list(np.tile([0.002, -0.002], 45)))
    out = pop.tournament(book, NOW)
    assert "strong" in out["promoted"] and pop.members["strong"].status == "live"
    assert pop.members["flat"].status == "shadow"
    assert pop.members["strong"].capital_weight == pytest.approx(1.0)   # only live agent in its family


def test_retirement_by_confidence_bound_and_the_six_month_shadow_tail(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    _member(pop, "loser", status="live")
    _add_trades(book, "loser", _bad(120))
    out = pop.tournament(book, NOW)
    m = pop.members["loser"]
    assert out["retired"] == ["loser"] and m.status == "retired" and m.capital_weight == 0.0
    assert m.in_shadow_book                                            # keeps shadow-trading after retirement
    later = pop.tournament(book, NOW + pd.Timedelta(days=P.RETIRED_SHADOW_DAYS + 1))
    assert "loser" in later["expired"] and not pop.members["loser"].in_shadow_book
    assert all(r["agent_id"] != "loser" for r in pop.league())


def test_two_bottom_quartile_months_retire_an_agent(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    for i in range(4):
        _member(pop, f"g{i}")
        _add_trades(book, f"g{i}", _good(70, seed=i) if i < 3 else list(np.tile([0.0025, -0.0016, -0.0016], 24)), )
    pop.members["g3"].monthly_quartile["2026-09"] = 3                 # already bottom last month
    out = pop.tournament(book, NOW)
    assert pop.members["g3"].monthly_quartile["2026-10"] == 3
    assert "g3" in out["retired"]
    assert pop.members["g0"].status != "retired"


def test_winners_are_cloned_into_mutated_shadow_children(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    w = _member(pop, "winner", status="live", generation=1)
    _add_trades(book, "winner", _good(120))
    out = pop.tournament(book, NOW, seed="fixed")
    kids = [pop.members[c] for c in out["cloned"]]
    assert 2 <= len(kids) <= 3
    for k in kids:
        assert k.parent_id == "winner" and k.generation == 2 and k.status == "shadow" and k.capital_weight == 0.0
        assert k.config != w.config and set(k.config) == set(w.config)
    # a winner with MAX_LIVE_CHILDREN children alive is not cloned again
    for i in range(P.MAX_LIVE_CHILDREN - len(kids)):
        _member(pop, f"kid-extra-{i}", generation=2).parent_id = "winner"
    again = pop.tournament(book, NOW + pd.Timedelta(days=7), seed="fixed")
    assert [c for c in again["cloned"] if pop.members[c].parent_id == "winner"] == []


def test_shadow_and_live_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "SHADOW_CAP", 3)
    monkeypatch.setattr(P, "LIVE_CAP", 1)
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    _member(pop, "winner", status="live")
    _add_trades(book, "winner", _good(120))
    for i in range(2):
        _member(pop, f"s{i}")
        _add_trades(book, f"s{i}", _good(90, seed=10 + i))
    out = pop.tournament(book, NOW)
    assert out["promoted"] == []                                       # the single live slot is taken
    assert len(pop.active("shadow")) <= 3


def test_capital_splits_by_fitness_within_a_family(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    _member(pop, "a", status="live")
    _member(pop, "b", status="live")
    _add_trades(book, "a", _good(100, seed=3))
    _add_trades(book, "b", _good(100, seed=4))
    pop.tournament(book, NOW)
    live = pop.active("live")
    assert sum(m.capital_weight for m in live) == pytest.approx(1.0)
    assert all(m.capital_weight > 0 for m in live)


def test_diversity_penalty_zeroes_a_copy_of_a_funded_agent():
    rets = _good(100)
    idx = pd.date_range("2026-01-01", periods=100, freq="8h", tz="UTC")
    t = pd.DataFrame({"ret": rets, "r": np.array(rets) / (4 / 2400), "p": 0.6,
                      "target_hit": (np.array(rets) > 0).astype(int), "exit_ts": idx})
    alone = score_agent(t, corr_funded=0.0, n_population=5)
    copy = score_agent(t, corr_funded=0.98, n_population=5)
    assert alone.fitness > 0 and copy.fitness < 0.05 * alone.fitness


def test_mutations_always_change_the_config_and_keep_types():
    base = dict(SPECIALISTS["session_open"].default_config)
    rng = random.Random(0)
    for _ in range(50):
        child = mutate_config(base, rng)
        assert child != base and set(child) == set(base)
        assert all(type(child[k]) is type(base[k]) for k in base)
    with pytest.raises(ValueError):
        AgentIdentity(family="session_open", config=base).mutate({})


def test_league_rows_match_the_api_contract_and_population_persists(tmp_path):
    pop, book = Population(tmp_path / "p.json"), ShadowBook(tmp_path)
    _member(pop, "strong")
    _add_trades(book, "strong", _good(90))
    pop.tournament(book, NOW)
    pop.save(tmp_path / "agents.json")
    rows = [AgentRow.model_validate(r) for r in pop.league()]
    assert {r.agent_id for r in rows} >= {"strong", FOUNDER}
    again = Population(tmp_path / "p.json")
    assert again.members["strong"].status == pop.members["strong"].status
    assert again.members["strong"].created_utc == pop.members["strong"].created_utc
