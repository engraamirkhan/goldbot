"""Bounded spawning (docs/TRADER_LIFECYCLE.md section 3): the gap detectors, the founder caps, on-demand staff runs
inside the budget, and the rule that nothing here creates a live agent or bypasses the promotion gates."""
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.agents.runner import AgentRunner, SpendLedger
from goldbot.agents.tools import ReadOnlyTools
from goldbot.config import GapSettings, load_settings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.ops import gap_watch as G
from goldbot.research import population as P
from goldbot.research.population import Member, Population, SpawnRefused, founder_base
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import TIMEFRAME_KEY, AgentIdentity

NOW = pd.Timestamp("2026-10-10 23:55", tz="UTC")
WF: set[str] = set(load_settings().walkforward)        # 15m, 1h, 4h: the timeframes the Saturday retrain trains


# ---------------------------------------------------------------------------------------------- helpers
class FakeClient:
    def __init__(self, n: int = 10):
        self.requests: list[dict[str, Any]] = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return NS(content=[NS(type="text", text="Report.")], stop_reason="end_turn", usage=usage, stop_details=None)


def _runner(tmp: Path, cap: float = 40.0, spent: float = 0.0) -> tuple[AgentRunner, FakeClient]:
    ledger = SpendLedger(tmp / "spend.json", monthly_cap_usd=cap)
    if spent:
        ledger.add(NOW, spent)
    client = FakeClient()
    return AgentRunner(client, ReadOnlyTools(tmp, Store(tmp / "data"), now=lambda: NOW), ledger, tmp), client


def _pop(tmp: Path) -> Population:
    pop = Population(tmp / "population.json")
    pop.ensure_founders(NOW - pd.Timedelta(days=60))
    return pop


def _run(tmp: Path, pop: Population, *, settings: GapSettings | None = None, drift: dict | None = None,
         dq: pd.DataFrame | None = None, vol: pd.Series | None = None, windows: list | None = None,
         plan: dict | None = None, trials: list | None = None, stages: dict | None = None,
         runner: AgentRunner | None = None, now: pd.Timestamp = NOW) -> G.GapReport:
    return G.run_gap_watch(now=now, settings=settings or GapSettings(), population=pop, state_dir=tmp,
                           walkforward_tfs=WF, drift=drift or {}, dq=dq if dq is not None else pd.DataFrame(),
                           daily_vol=vol if vol is not None else pd.Series(dtype=float), champion_windows=windows or [],
                           plan=plan, trials=trials or [], engine_stages=stages or {}, runner=runner)


def _dq(days: list[int], severity: str = "error") -> pd.DataFrame:
    ts = [NOW - pd.Timedelta(days=d, hours=1) for d in days]
    return pd.DataFrame({"ts_utc": ts, "check": ["bid_gt_ask"] * len(ts), "severity": [severity] * len(ts)})


def _passed_trial(family: str, n: int, **overrides) -> dict:
    cfg = {**SPECIALISTS[family].default_config, **overrides}
    return {"trial": n, "family": family, "config": cfg, "status": "evaluated", "results": {"gates": {"passed": True}}}


def _vol_series() -> pd.Series:
    """Two years low vol, then a high-vol stretch at the end."""
    idx = pd.date_range(pd.Timestamp("2024-09-01", tz="UTC"), NOW.normalize(), freq="D")
    v = np.full(len(idx), 0.005)
    v[len(idx) // 3: 2 * len(idx) // 3] = 0.008
    v[-60:] = 0.02
    return pd.Series(v, index=idx)


# ---------------------------------------------------------------------------------------------- detectors
def test_uncovered_timeframe_is_a_family_timeframe_with_no_live_or_shadow_agent(tmp_path):
    gaps = G.uncovered_timeframes(_pop(tmp_path))
    ids = {g.gap_id for g in gaps}
    assert ids == {"uncovered_timeframe:breakout:15m", "uncovered_timeframe:mean_reversion:1h",
                   "uncovered_timeframe:tsmom:4h", "uncovered_timeframe:tsmom:1d"}


def test_family_is_dead_when_every_agent_is_retired(tmp_path):
    pop = _pop(tmp_path)
    for m in pop.members.values():
        if m.family == "trend":
            m.status = "retired"
    assert [g.gap_id for g in G.dead_families(pop)] == ["family_dead:trend"]
    assert "uncovered_timeframe:trend:1h" in {g.gap_id for g in G.uncovered_timeframes(pop)}


def test_drift_halts_and_the_system_halt_are_gaps():
    drift = {"halted": {"a1": {"version": "v1", "since": "2026-10-09T23:40:00+00:00", "reasons": ["cusum"]}},
             "system_halt": {"since": "2026-10-09T23:40:00+00:00", "reasons": ["two halted"]}}
    gaps = G.drift_halts(drift)
    assert [g.kind for g in gaps] == ["drift_halt", "system_halt"]
    assert gaps[0].gap_id == "drift_halt:a1:2026-10-09T23:40:00+00:00"
    assert G.drift_halts({}) == []


def test_regime_gap_when_recent_volatility_is_in_a_tercile_no_champion_trained_on():
    vol = _vol_series()
    trained_low = [(pd.Timestamp("2024-09-01", tz="UTC"), pd.Timestamp("2025-06-01", tz="UTC"))]
    gaps, note = G.regime_gap(vol, trained_low, NOW, 20)
    assert [g.gap_id for g in gaps] == ["regime:vol_high:2026-10"] and "high" in note
    trained_all = [(pd.Timestamp("2024-09-01", tz="UTC"), NOW)]
    assert G.regime_gap(vol, trained_all, NOW, 20)[0] == []
    assert G.regime_gap(vol, [], NOW, 20)[0] == []                      # no champion: nothing to compare


def test_daily_realised_vol_from_bars():
    ts = pd.date_range("2026-10-01", periods=48, freq="h", tz="UTC")
    px = 2400 * np.exp(np.cumsum(np.r_[0, np.full(47, 0.001)]))
    bars = pd.DataFrame({"ts_utc": ts, "bid_close": px - 0.1, "ask_close": px + 0.1})
    rv = G.daily_realised_vol(bars)
    assert len(rv) == 2 and rv.iloc[1] == pytest.approx(np.sqrt(24 * 0.001 ** 2), rel=1e-3)


def test_repeated_dq_errors_are_a_gap_and_warnings_or_few_days_are_not():
    assert [g.kind for g in G.dq_repeats(_dq([0, 2, 4]), NOW, 7, 3)] == ["dq_errors"]
    assert G.dq_repeats(_dq([0, 0, 1]), NOW, 7, 3) == []                # two distinct days
    assert G.dq_repeats(_dq([0, 2, 4], severity="warning"), NOW, 7, 3) == []
    assert G.dq_repeats(_dq([0, 2, 9]), NOW, 7, 3) == []                # one is outside the window


def test_research_plan_family_without_a_founder_is_a_gap(tmp_path):
    pop = Population(tmp_path / "p.json")
    plan = {"budget": {"trend": 2, "silver_carry": 1}, "evidence": []}
    gaps = G.unfounded_plan_families(plan, pop)
    assert [g.gap_id for g in gaps] == ["no_founder:silver_carry", "no_founder:trend"]
    rep = _run(tmp_path, pop, plan=plan)
    assert any(a.action == "suggest" and "silver_carry" in a.detail for a in rep.actions)   # never created
    assert "silver_carry" not in {m.family for m in pop.members.values()}
    assert all(m.status == "shadow" and m.capital_weight == 0 for m in pop.members.values())


def test_passed_trial_without_an_agent_is_research_ready(tmp_path):
    pop = _pop(tmp_path)
    t = _passed_trial("trend", 7, target_atr=SPECIALISTS["trend"].default_config["target_atr"] * 1.2)
    failed = {**_passed_trial("trend", 8, target_atr=1.0), "results": {"gates": {"passed": False}}}
    gaps = G.research_ready([t, failed], pop)
    assert [g.gap_id for g in gaps] == ["research_ready:trial7"]


# ---------------------------------------------------------------------------------------------- founders
def test_founder_configs_come_only_from_registered_families_within_bounds(tmp_path):
    pop = _pop(tmp_path)
    base = SPECIALISTS["trend"].default_config
    with pytest.raises(SpawnRefused, match="not a registered family"):
        pop.spawn_founder("silver_carry", {}, NOW, gap_id="g", origin="o")
    with pytest.raises(SpawnRefused, match="outside"):
        pop.spawn_founder("trend", {**base, "target_atr": base["target_atr"] * 3}, NOW, gap_id="g", origin="o")
    with pytest.raises(SpawnRefused, match="not settings"):
        pop.spawn_founder("trend", {**base, "exec": "rm -rf"}, NOW, gap_id="g", origin="o")
    with pytest.raises(SpawnRefused, match="does not run on"):
        pop.spawn_founder("trend", {**base, TIMEFRAME_KEY: "1m"}, NOW, gap_id="g", origin="o")
    with pytest.raises(SpawnRefused, match="lookahead"):
        pop.spawn_founder("trend", {**base, "target_atr": base["target_atr"] * 1.1}, NOW, gap_id="g", origin="o",
                          blocked=frozenset({"trend"}))
    with pytest.raises(SpawnRefused, match="already exists"):
        pop.spawn_founder("trend", dict(base), NOW, gap_id="g", origin="o")
    with pytest.raises(SpawnRefused, match="needs the gap"):
        pop.spawn_founder("trend", {**base, "target_atr": base["target_atr"] * 1.1}, NOW, gap_id="", origin="o")


def test_clones_leave_the_reserved_slots_for_gap_founders(tmp_path, monkeypatch):
    pop = _pop(tmp_path)
    monkeypatch.setattr(P, "SHADOW_CAP", len(pop.active("shadow")) + P.GAP_RESERVED_SLOTS)
    assert not pop.clone_slot_free()                                   # the rest is reserved
    pop.spawn_founder("breakout", founder_base("breakout", "15m"), NOW, gap_id="g", origin="o")
    assert not pop.clone_slot_free()


def _add_trades(book: ShadowBook, agent_id: str, n: int) -> None:
    version = f"{agent_id}-v1"
    t0 = NOW - pd.Timedelta(days=n)
    book.track(version, t0)
    rng = np.random.default_rng(0)
    for i, r in enumerate(np.where(rng.random(n) < 0.6, 0.0025, -0.0016)):
        ts = t0 + pd.Timedelta(hours=8 * i)
        book.books[version].closed.append(ShadowTrade(
            version=version, agent_id=agent_id, side=1, entry_ts=ts, entry=2400.0, stop=2396.0, target=2406.0,
            max_bars=16, p=0.6, bars_held=3, exit_ts=ts + pd.Timedelta(hours=1), exit=2400.0 * (1 + r),
            barrier="target" if r > 0 else "stop", ret=float(r)))


def test_a_gap_founder_reaches_live_only_through_the_research_and_dsr_gates(tmp_path):
    pop, book = _pop(tmp_path), ShadowBook(tmp_path)
    m = pop.spawn_founder("breakout", founder_base("breakout", "15m"), NOW, gap_id="g", origin="o")
    _add_trades(book, m.agent_id, 150)
    out = pop.tournament(book, NOW, research_passed=lambda x: False)
    assert m.agent_id in out["awaiting_research"] and pop.members[m.agent_id].status == "shadow"
    assert pop.members[m.agent_id].capital_weight == 0.0


def test_a_4h_founder_is_spawned_for_an_uncovered_4h_timeframe_and_1d_stays_refused(tmp_path):
    # settings.walkforward has a 4h window (proposal P4), so the retrain can train a 4h agent: gap_watch spawns the
    # family's default on 4h as a zero-capital shadow founder; 1d has no window and is still refused with a suggestion
    pop = _pop(tmp_path)
    rep = _run(tmp_path, pop, settings=GapSettings(founders_per_month=4))
    by_gap = {a.gap_ids[0]: a for a in rep.actions if a.action == "spawn_founder"}
    m = pop.members[by_gap["uncovered_timeframe:tsmom:4h"].target]
    assert m.family == "tsmom" and m.config[TIMEFRAME_KEY] == "4h" and m.specialist().timeframe == "4h"
    assert m.config["max_bars"] == 12                                  # the 1h default's two-day horizon in 4h bars
    assert m.status == "shadow" and m.capital_weight == 0 and m.gap_id == "uncovered_timeframe:tsmom:4h"
    reasons = {r.gap_ids[0]: r.reason for r in rep.refused}
    assert "no walk-forward window for 1d" in reasons["uncovered_timeframe:tsmom:1d"]
    assert "uncovered_timeframe:tsmom:4h" not in reasons
    assert any(a.action == "suggest" and "1d walk-forward window" in a.detail for a in rep.actions)


# ---------------------------------------------------------------------------------------------- the job's logic
def test_gap_watch_spawns_shadow_founders_within_the_monthly_cap_and_writes_gaps_json(tmp_path):
    pop = _pop(tmp_path)
    rep = _run(tmp_path, pop, settings=GapSettings(founders_per_month=1))
    spawned = [a for a in rep.actions if a.action == "spawn_founder"]
    assert len(spawned) == 1 and spawned[0].gap_ids == ["uncovered_timeframe:breakout:15m"]
    reasons = {r.gap_ids[0]: r.reason for r in rep.refused}
    assert "monthly cap" in reasons["uncovered_timeframe:mean_reversion:1h"]
    assert "monthly cap" in reasons["uncovered_timeframe:tsmom:4h"]
    assert "no walk-forward window for 1d" in reasons["uncovered_timeframe:tsmom:1d"]
    saved = json.loads((tmp_path / G.GAPS_FILE).read_text())
    assert {"gaps", "actions", "refused", "already_handled", "limits"} <= set(saved)
    assert saved["limits"]["founders_per_month"] == 1 and saved["limits"]["reserved_shadow_slots"] == 4
    new = pop.members[spawned[0].target]
    assert new.status == "shadow" and new.capital_weight == 0 and new.gap_id == spawned[0].gap_ids[0]
    assert json.loads((tmp_path / G.LEDGER_FILE).read_text().splitlines()[0])["target"] == new.agent_id


def test_passed_research_is_spawned_first_once_per_trial(tmp_path):
    pop = _pop(tmp_path)
    base = SPECIALISTS["trend"].default_config
    t = _passed_trial("trend", 7, target_atr=base["target_atr"] * 1.2)
    rep = _run(tmp_path, pop, trials=[t, dict(t, trial=9)])
    first = [a for a in rep.actions if a.action == "spawn_founder"][0]
    assert first.gap_ids == ["research_ready:trial7"] and pop.members[first.target].origin == "trial 7"
    assert not [g for g in G.research_ready([t], pop)]                 # already has its agent


def test_no_spawn_during_a_system_halt_or_a_drawdown_stage(tmp_path):
    pop = _pop(tmp_path)
    n = len(pop.members)
    rep = _run(tmp_path, pop, drift={"system_halt": {"since": "x", "reasons": ["r"]}})
    assert len(pop.members) == n and rep.spawned == []
    assert any("system halt" in r.reason for r in rep.refused if r.action == "spawn_founder")
    rep = _run(tmp_path, pop, stages={"icm-demo": "size_down"})
    assert len(pop.members) == n and any("drawdown stage" in r.reason for r in rep.refused)


def test_lookahead_blocked_family_gets_no_founder(tmp_path):
    pop = _pop(tmp_path)
    plan = {"budget": {}, "evidence": [{"family": "breakout", "blocked": True}]}
    rep = _run(tmp_path, pop, plan=plan)
    assert any("lookahead" in r.reason for r in rep.refused if r.gap_ids == ["uncovered_timeframe:breakout:15m"])


def test_on_demand_staff_run_once_per_gap_within_the_weekly_cap(tmp_path):
    pop = _pop(tmp_path)
    runner, client = _runner(tmp_path)
    rep = _run(tmp_path, pop, dq=_dq([0, 2, 4]), runner=runner)
    runs = [a for a in rep.actions if a.action == "staff_run"]
    assert [a.target for a in runs] == ["data_steward"] and len(client.requests) == 1
    assert "On-demand run by gap_watch" in client.requests[0]["messages"][0]["content"]
    assert {t["name"] for t in client.requests[0]["tools"]} <= {"read_dq_events", "read_state", "read_decisions"}
    again = _run(tmp_path, pop, dq=_dq([0, 2, 4]), runner=runner, now=NOW + pd.Timedelta(hours=1))
    assert len(client.requests) == 1 and again.already_handled == [runs[0].gap_ids[0]]
    # two more distinct gaps reach the weekly cap of 3; the fourth is refused
    _run(tmp_path, pop, drift={"halted": {"a": {"since": "s1"}}}, runner=runner)
    _run(tmp_path, pop, drift={"halted": {"a": {"since": "s2"}}}, runner=runner)
    rep4 = _run(tmp_path, pop, drift={"halted": {"a": {"since": "s3"}}}, runner=runner)
    assert len(client.requests) == 3
    assert any("weekly cap" in r.reason for r in rep4.refused if r.action == "staff_run")
    later = _run(tmp_path, pop, drift={"halted": {"a": {"since": "s3"}}}, runner=runner, now=NOW + pd.Timedelta(days=8))
    assert [a.target for a in later.actions if a.action == "staff_run"] == ["risk_officer"]


def test_staff_runs_respect_the_monthly_agent_budget(tmp_path):
    runner, client = _runner(tmp_path, cap=40.0, spent=39.5)            # 0.50 left, data steward needs 1.00
    rep = _run(tmp_path, _pop(tmp_path), dq=_dq([0, 2, 4]), runner=runner)
    assert client.requests == [] and any("monthly agent budget" in r.reason for r in rep.refused)
    rep = _run(tmp_path, _pop(tmp_path), dq=_dq([0, 2, 4]), runner=None)
    assert any("anthropic-api-key" in r.reason for r in rep.refused)


def test_a_role_with_write_tools_or_an_unknown_role_is_never_run_on_demand(tmp_path, monkeypatch):
    runner, client = _runner(tmp_path)
    monkeypatch.setitem(G.ON_DEMAND_ROLES, "dq_errors", "research_analyst")       # holds run_trial
    rep = _run(tmp_path, _pop(tmp_path), dq=_dq([0, 2, 4]), runner=runner)
    assert client.requests == [] and any("write tools" in r.reason for r in rep.refused)
    monkeypatch.setitem(G.ON_DEMAND_ROLES, "dq_errors", "trader")
    rep = _run(tmp_path, _pop(tmp_path), dq=_dq([1, 3, 5]), runner=runner)
    assert client.requests == [] and any("new roles go by PR" in r.reason for r in rep.refused)


def test_default_on_demand_roles_exist_and_are_read_only():
    from goldbot.agents.roles import ROLES
    for name in G.ON_DEMAND_ROLES.values():
        assert name in ROLES and not set(ROLES[name].tools) & G.WRITE_TOOLS


def test_regime_and_dead_family_gaps_file_capped_hypotheses_once(tmp_path):
    pop = _pop(tmp_path)
    for m in pop.members.values():
        if m.family in ("trend", "breakout"):
            m.status = "retired"
    windows = [(pd.Timestamp("2024-09-01", tz="UTC"), pd.Timestamp("2025-06-01", tz="UTC"))]
    rep = _run(tmp_path, pop, vol=_vol_series(), windows=windows)
    filed = [a for a in rep.actions if a.action == "file_hypothesis"]
    assert len(filed) == 2 and any(r.action == "file_hypothesis" and "cap" in r.reason for r in rep.refused)
    rows = [json.loads(x) for x in (tmp_path / "hypotheses.jsonl").read_text().splitlines()]
    assert all(r["status"] == "proposed" and r["source"] == "gap_watch" for r in rows)
    again = _run(tmp_path, pop, vol=_vol_series(), windows=windows)
    filed_ids = {r["gap_id"] for r in rows}
    assert "family_dead:trend" in again.already_handled                # still dead, filed once only
    assert not [a for a in again.actions if a.action == "file_hypothesis" and set(a.gap_ids) & filed_ids]


def test_nothing_in_gap_watch_creates_a_live_agent_or_touches_the_gates(tmp_path):
    pop = _pop(tmp_path)
    base = SPECIALISTS["trend"].default_config
    trials = [_passed_trial("trend", i, target_atr=base["target_atr"] * (1 + i / 100)) for i in range(1, 6)]
    runner, _ = _runner(tmp_path)
    before = {a: (m.status, m.capital_weight) for a, m in pop.members.items()}
    for day in range(40):
        _run(tmp_path, pop, trials=trials, dq=_dq([0, 2, 4]), runner=runner, now=NOW + pd.Timedelta(days=day),
             drift={"halted": {"x": {"since": str(day)}}})
    for a, m in pop.members.items():
        if a in before:
            assert (m.status, m.capital_weight) == before[a]
        else:
            assert m.status == "shadow" and m.capital_weight == 0.0 and m.gap_id
    assert len([m for m in pop.members.values() if m.gap_id]) <= P.GAP_RESERVED_SLOTS
    src = Path(G.__file__).read_text()
    # structural: the module never sets a status, never calls the tournament, the registry's promotion or the gate
    for pattern in (r"_set_status", r"\.status\s*=(?!=)", r"status=\"live\"", r"\.tournament\(", r"promote\(",
                    r"RiskGate", r"capital_weight\s*=(?!=)"):
        assert not re.search(pattern, src), pattern


def test_member_without_gap_fields_still_loads(tmp_path):
    m = Member(agent_id="a", family="trend", config={}, created_utc=NOW, status_since_utc=NOW)
    assert m.gap_id is None and m.origin is None
    assert AgentIdentity(family="trend", config={}).agent_id
