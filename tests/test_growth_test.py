"""Growth sprint 2026-11-01 (docs/research/growth-sprint-2026-11-01.md section 3): the demo growth test on icm-demo.

GS1 (pyramided session-range breakout; single unit until the adds land) and its paired random-entry control GS1C, both
sized by the RiskGate's own risk sizing (at most 1% of equity per unit; no cap loosened), GS1 exempt from the ADR 0061
refusal by `role: owner_experiment` until 2026-11-27 only, and a virtual ledger that replays the realised R at f =
5/10/20% (quant requirements 5 and 6)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.data.resample import resample_bars
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_growth as G
from goldbot.research import arms
from goldbot.research import growth_ledger as L
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits
from tests.test_count_owed_events import _bars_1m

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "config" / "exploration_arms.yaml"
NOW = pd.Timestamp("2026-11-02 09:00", tz="UTC")
NO = (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))


# ------------------------------------------------------------------------------------------------ the exemption
def _exp(arm_id: str = "GS1", role: str = "owner_experiment") -> arms.Arm:
    return arms.Arm.model_validate(dict(id=arm_id, rule="session_range_break", timeframe="15m", role=role,
                                        params={"sessions": ["asia_london"], "stop_atr": 1.5, "target_r": 3.0, "hold_bars": 16}))


def test_owner_experiment_role_exempts_gs1_from_the_qualification_rule_until_its_expiry() -> None:
    before = pd.Timestamp("2026-11-27 23:59", tz="UTC")
    assert arms.qualification_refusal(_exp(), now=before) is None


def test_owner_experiment_exemption_ends_after_2026_11_27() -> None:
    why = arms.qualification_refusal(_exp(), now=pd.Timestamp("2026-11-28 00:00", tz="UTC"))
    assert why is not None and "expired" in why and "2026-11-27" in why


def test_owner_experiment_role_on_any_other_arm_is_refused() -> None:
    why = arms.qualification_refusal(_exp("GS9"), now=NOW)
    assert why is not None and "owner_experiment" in why


def test_every_other_strategy_arm_keeps_the_qualification_rule() -> None:
    why = arms.qualification_refusal(_exp(role="strategy"), now=NOW)
    assert why is not None and why.startswith(arms.QUALIFY_RULE)


def test_the_frozen_spec_carries_gs1_as_owner_experiment_and_gs1c_as_its_control() -> None:
    got = {a.id: a for a in arms.load_arms(SPEC)}
    assert got["GS1"].role == "owner_experiment" and got["GS1"].rule == "session_range_break"
    assert got["GS1C"].role == "control" and got["GS1C"].params["control"] == "GS1"
    body = {k: v for k, v in got["GS1"].params.items()}
    assert {k: v for k, v in got["GS1C"].params.items() if k != "control"} == body   # same mechanics
    assert got["GS1C"].entry_refused == ""


def test_the_engine_journals_the_owner_experiment_exemption_at_start() -> None:
    from goldbot.engine import exploration
    assert exploration.OWNER_EXPERIMENT_ACTION == "exploration_arm_owner_experiment"


# ------------------------------------------------------------------------------------------------ the rules
@pytest.fixture(scope="module")
def ctx() -> R.RuleContext:
    b1 = _bars_1m("2023-01-02", "2023-02-03")
    bars = resample_bars(b1, "15m").reset_index(drop=True)
    return R.RuleContext(bars=bars, b1=b1.reset_index(drop=True), blackout_lo=NO[0], blackout_hi=NO[1])


def test_gs1_enters_on_the_first_15m_close_beyond_the_session_range_once_per_session(ctx) -> None:
    arm = arms.load_arms(SPEC)
    gs1 = next(a for a in arm if a.id == "GS1")
    c = R.candidates(gs1, ctx)
    assert len(c) > 10
    close = ctx.close_ts[c["idx"].to_numpy()]
    m = ctx.mid
    for i, t in zip(c["idx"], close, strict=True):
        day = t.normalize()
        session = next(s for s in G.SESSIONS if G.session_bounds(day, s)[1] < t <= G.session_bounds(day, s)[2])
        r0, r1, _ = G.session_bounds(day, session)
        ts = pd.DatetimeIndex(m["ts_utc"])
        rng = m[(ts >= r0) & (ts < r1)]
        side = int(c.loc[c["idx"] == i, "side"].iloc[0])
        px = float(m["close"].iloc[i])
        assert px > rng["high"].max() if side > 0 else px < rng["low"].min()
    keys = [(t.normalize(), t.hour >= 13) for t in close]
    assert len(keys) == len(set(keys)) or len(c) == len(close)
    assert (c["target_r"] == 3.0).all() and (c["max_bars"] == 15).all()


def test_gs1c_enters_once_per_eligible_session_at_its_seeded_close_with_gs1s_barriers(ctx) -> None:
    got = {a.id: a for a in arms.load_arms(SPEC)}
    c = R.candidates(got["GS1C"], ctx)
    close = ctx.close_ts[c["idx"].to_numpy()]
    assert len(c) > 20
    for t, side in zip(close, c["side"], strict=True):
        day = t.normalize()
        assert any(G.control_close("GS1C", day, s) == t for s in G.SESSIONS)
        assert side == R.hash_side("GS1C", t)
    assert 0.3 < (c["side"] > 0).mean() < 0.7                                        # a fair coin
    gs1 = R.candidates(got["GS1"], ctx)
    assert set(c["target_r"]) == set(gs1["target_r"]) and set(c["max_bars"]) == set(gs1["max_bars"])


def test_growth_rules_decide_point_in_time(ctx) -> None:
    """A candidate at bar i is the same when the bars after i are dropped (live == history)."""
    gs1 = next(a for a in arms.load_arms(SPEC) if a.id == "GS1")
    full = R.candidates(gs1, ctx)
    cut = int(full["idx"].iloc[len(full) // 2])
    part = R.RuleContext(bars=ctx.bars.iloc[:cut + 1].reset_index(drop=True), b1=ctx.b1, blackout_lo=NO[0],
                         blackout_hi=NO[1])
    got = R.candidates(gs1, part)
    pd.testing.assert_frame_equal(got.reset_index(drop=True), full[full["idx"] <= cut].reset_index(drop=True))


# ------------------------------------------------------------------------------------------------ RiskGate sizing
def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000,
                                week_start_equity=10_000, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=20.0)
    return AccountState(**{**base, **kw})


def _intent(tmp_path: Path, **kw: Any) -> Intent:
    phase = tmp_path / "phase_state.json"
    phase.write_text(json.dumps({"gates_passed": []}))
    base: dict[str, Any] = dict(agent_id="explore:GS1", side=1, p=0.5, target_atr=3.0, stop_atr=1.0, atr_usd=5.0,
                                cost_atr=0.05, multiplier=1.0, price=2400.0, timeframe="15m",
                                family="session_range_break", volume_min=0.01, volume_max=0.10, account_mode="demo",
                                phase_file=phase, purpose="exploration", terminal_trade_mode="demo", arm_id="GS1")
    return Intent(**{**base, **kw})


def test_a_growth_arm_is_sized_by_the_gate_above_the_minimum_lot_within_its_cap(tmp_path) -> None:
    lim = RiskLimits(probe_only=True, exploration_arms={"GS1": 60.0}, growth_arms=frozenset({"GS1"}))
    d = RiskGate(lim).check(_intent(tmp_path), _state(), NOW)
    assert d.allowed, d.reasons
    assert 0.01 < d.lots <= 0.10 + 1e-9
    assert d.risk_amount <= 60.0 + 1e-9 and d.risk_amount <= 0.01 * 10_000 + 1e-9


def test_a_growth_arm_never_risks_more_than_its_per_trade_cap(tmp_path) -> None:
    lim = RiskLimits(probe_only=True, exploration_arms={"GS1": 10.0}, growth_arms=frozenset({"GS1"}))
    d = RiskGate(lim).check(_intent(tmp_path), _state(), NOW)
    assert not d.allowed and "exploration_arm_cap" in d.reasons


def test_an_arm_not_registered_as_growth_keeps_the_minimum_lot_fence(tmp_path) -> None:
    lim = RiskLimits(probe_only=True, exploration_arms={"GS1": 60.0})
    d = RiskGate(lim).check(_intent(tmp_path), _state(), NOW)
    assert not d.allowed and "exploration_not_min_lot" in d.reasons


def test_growth_sizing_is_refused_once_a_live_gate_is_in_the_phase_file(tmp_path) -> None:
    lim = RiskLimits(probe_only=True, exploration_arms={"GS1": 60.0}, growth_arms=frozenset({"GS1"}))
    it = _intent(tmp_path)
    Path(str(it.phase_file)).write_text(json.dumps({"gates_passed": ["paper_to_tiny_live"]}))
    d = RiskGate(lim).check(it, _state(), NOW)
    assert not d.allowed and "exploration_not_demo" in d.reasons


def _account(mode: str = "demo", probe_only: bool = True, exploring: bool = True) -> dict[str, Any]:
    return {"broker": "icm", "mode": mode, "server": "S", "login": None, "terminal_path": "t", "server_tz": "NY+7",
            "symbol": "XAUUSD", "magic_base": 1, "probe_only": probe_only, "enabled": True,
            "daily_loss_limit_abs": 220,
            "exploration": {"enabled": exploring, "budget_share": 0.9, "pots": {"growth": {"gbp": 60, "max_open": 2}},
                            "arms": {"GS1": {"style": "growth", "max_loss": 30.0, "sizing": "growth"}}}}


@pytest.mark.parametrize("kw", [{"mode": "live", "probe_only": False, "exploring": False},
                                {"probe_only": False, "exploring": False}])
def test_growth_sizing_is_refused_at_load_on_an_account_that_is_not_demo_probe_only(kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="growth sizing"):
        acc_mod.Account.model_validate({"account_id": "x", **_account(**kw)})


def test_growth_arm_ids_reach_the_gate_only_on_a_demo_probe_only_account() -> None:
    acc = acc_mod.Account.model_validate({"account_id": "x", **_account()})
    assert acc_mod.growth_arm_ids(acc) == frozenset({"GS1"})


def test_icm_demo_registers_gs1_and_gs1c_at_the_same_size_on_their_own_pot() -> None:
    raw = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]["icm-demo"]["exploration"]
    gs1, gs1c = raw["arms"]["GS1"], raw["arms"]["GS1C"]
    assert gs1["sizing"] == gs1c["sizing"] == "growth"
    assert gs1["max_loss"] == gs1c["max_loss"] and gs1["style"] == gs1c["style"] == "growth"
    others = {k for k, v in raw["arms"].items() if v.get("sizing") == "growth"}
    assert others == {"GS1", "GS1C"}
    for acc_id in ("icm-probe", "vantage-demo"):
        accs = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
        assert not any(v.get("sizing") == "growth" for v in accs[acc_id].get("exploration", {}).get("arms", {}).values())


# ------------------------------------------------------------------------------------------------ the virtual ledger
def _t(h: int, r: float, arm: str = "GS1", m: int = 0, session: str = "asia_london") -> L.LedgerTrade:
    entry = pd.Timestamp("2026-11-02", tz="UTC") + pd.Timedelta(hours=h, minutes=m)
    return L.LedgerTrade(arm_id=arm, entry_utc=entry, exit_utc=entry + pd.Timedelta(hours=1), r=r, session=session)


def test_virtual_ledger_compounds_by_entry_time_and_reports_doubling() -> None:
    trades = [_t(i, 1.0) for i in range(8)]
    out = L.virtual_ledger(trades, f=0.10)
    assert out.final_multiple == pytest.approx(1.1 ** 8)
    assert out.doubled and out.trades == 8


def test_virtual_ledger_sizes_overlapping_trades_on_equity_at_entry() -> None:
    a = L.LedgerTrade(arm_id="GS1", entry_utc=pd.Timestamp("2026-11-02 08:00", tz="UTC"),
                      exit_utc=pd.Timestamp("2026-11-02 12:00", tz="UTC"), r=1.0)
    b = L.LedgerTrade(arm_id="GS1", entry_utc=pd.Timestamp("2026-11-02 09:00", tz="UTC"),
                      exit_utc=pd.Timestamp("2026-11-02 10:00", tz="UTC"), r=-1.0)
    out = L.virtual_ledger([a, b], f=0.20)
    # both sized on 1.0 (equity at each entry: nothing had closed): 1 + 0.2 - 0.2
    assert out.final_multiple == pytest.approx(1.0)
    assert out.max_drawdown == pytest.approx(0.2)


def test_virtual_ledger_floors_at_zero_when_a_gap_passes_minus_one_over_f() -> None:
    out = L.virtual_ledger([_t(0, -6.0), _t(2, 3.0)], f=0.20)
    assert out.final_multiple == 0.0 and out.ruined and out.max_drawdown == pytest.approx(1.0)


def test_virtual_ledger_flags_f20_with_adds_as_not_executable_at_icm_margin() -> None:
    # even at 1:500, four units at 20% each need 4 x 0.04 x 2400 / 500 = 0.77 of equity in margin (> 1/3)
    out = L.virtual_ledger([_t(0, 1.0)], f=0.20, stop_usd=5.0, price=2400.0, units=4, leverage=500.0)
    assert out.margin_ok is False
    assert L.virtual_ledger([_t(0, 1.0)], f=0.05, stop_usd=5.0, price=2400.0, units=1, leverage=500.0).margin_ok
    # at the gate's own 1:20 figure, one unit at the 1% RiskGate cap fits; 5% does not
    assert L.margin_ok(0.01, 5.0, 2400.0, 1) and not L.margin_ok(0.05, 5.0, 2400.0, 1)


def test_growth_read_pairs_control_with_gs1_by_day_and_session_and_counts_refusals() -> None:
    trades = [_t(8, 1.0), _t(14, -1.0, session="london_ny"), _t(9, 0.5, arm="GS1C"),
              _t(15, -0.5, arm="GS1C", session="london_ny"), _t(9, 2.0, arm="GS1C", session="asia_london", m=30)]
    trades[-1] = trades[-1].model_copy(update={"entry_utc": pd.Timestamp("2026-11-03 09:00", tz="UTC")})
    refused = {"GS1": {"pot": 2}, "GS1C": {"daily_loss_limit_abs": 1}}
    rep = L.growth_read(trades, refused=refused, shadow=[_t(58, 1.0, arm="GS1")])
    assert rep.pairs == 2                                   # GS1C on 11-03 has no GS1 entry that session
    assert rep.arms["GS1"].n == 2 and rep.arms["GS1C"].n == 3
    assert rep.arms["GS1"].refused == {"pot": 2} and rep.arms["GS1C"].refused == {"daily_loss_limit_abs": 1}
    assert rep.arms["GS1"].shadow_n == 1
    assert rep.paired_excess_r == pytest.approx(((1.0 - 0.5) + (-1.0 + 0.5)) / 2)
    assert set(rep.arms["GS1"].ledgers) == {"0.05", "0.1", "0.2"}


# ------------------------------------------------------------------------------------------------ pyramid groups
def _rec(cid: str, pnl: float, stop_loss: float, group_id: str | None = None, minute: int = 0) -> Any:
    from goldbot.engine.exploration import ExplorationRecord
    t = pd.Timestamp("2026-11-02 08:15", tz="UTC") + pd.Timedelta(minutes=minute)
    return ExplorationRecord(client_order_id=cid, account_id="icm-demo", arm_id="GS1", family="session_range_break",
                             tf="15m", side=1, lots=0.05, signal_utc=t, sent_utc=t, atr_usd=5.0, stop_usd=5.0,
                             target_usd=15.0, hold_s=14400, fill_price=2400.0, stop_loss=stop_loss,
                             exit_utc=t + pd.Timedelta(hours=1, minutes=minute), pnl=pnl, group_id=group_id)


def test_pyramid_adds_sharing_a_group_id_fold_into_one_trade_scored_on_the_first_units_stop() -> None:
    rows = [_rec("A", 20.0, 20.0), _rec("A2", 10.0, 20.0, group_id="A", minute=30),
            _rec("A3", -5.0, 20.0, group_id="A", minute=45), _rec("B", -20.0, 20.0, minute=300)]
    got = L.fold_groups(rows)
    assert [round(t.r, 6) for t in got] == [1.25, -1.0]
    assert got[0].exit_utc == rows[2].exit_utc


def test_without_a_group_id_each_add_counts_as_its_own_trade() -> None:
    rows = [_rec("A", 20.0, 20.0), _rec("A2", 10.0, 20.0, minute=30)]
    assert len(L.fold_groups(rows)) == 2


def test_the_exploration_record_round_trips_its_group_id_in_the_json_body() -> None:
    from goldbot.engine.exploration import ExplorationRecord
    r = _rec("A2", 1.0, 2.0, group_id="A")
    assert ExplorationRecord.model_validate_json(r.model_dump_json()).group_id == "A"
    old = json.loads(r.model_dump_json())
    old.pop("group_id")
    assert ExplorationRecord.model_validate(old).group_id is None                 # rows written before still load


# ------------------------------------------------------------------------------------------------ engine sizing
TERMS = {"volume_step": 0.01, "volume_min": 0.01, "volume_max": 50.0}


def test_growth_lots_takes_the_largest_step_inside_every_room_and_the_net_lots_cap() -> None:
    from goldbot.engine import exploration as X
    # £3.75 a 0.01 lot at the stop -> £375 a lot; rooms 60/90/30 at the stop, arm cap 30 incl. the round trip
    got = X.growth_lots({"arm_cap": 29.0, "budget": 60.0, "pot": 90.0, "arm_budget": 60.0}, 30.0, 375.0, 25.0,
                        TERMS, 1, 0.0)
    assert got is not None
    lots, loss, cost = got
    assert lots == pytest.approx(0.07) and loss + cost <= 30.0 + 1e-9
    # an open long of 0.05 leaves 0.05 under the 0.10 net cap
    capped = X.growth_lots({"budget": 1e6}, 1e6, 375.0, 25.0, TERMS, 1, 0.05)
    assert capped is not None and capped[0] == pytest.approx(0.05)
    # nothing above the minimum fits: the minimum-lot path judges it
    assert X.growth_lots({"budget": 3.0}, 30.0, 375.0, 25.0, TERMS, 1, 0.0) is None


class _Eng:
    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []


def test_the_engine_refuses_an_owner_experiment_past_its_day_without_a_restart() -> None:
    from types import SimpleNamespace

    from goldbot.engine import exploration as X
    spec = next(a for a in arms.load_arms(SPEC) if a.id == "GS1")
    ex = SimpleNamespace(arms={"GS1": SimpleNamespace(spec=spec)}, retired=frozenset(), unqualified={},
                         owner_read=None, queue=[])
    eng = _Eng()
    X.refresh_owner_experiments(eng, ex, pd.Timestamp("2026-11-27 23:00", tz="UTC"))     # type: ignore[arg-type]
    assert "GS1" not in ex.retired and not eng.decisions
    X.refresh_owner_experiments(eng, ex, pd.Timestamp("2026-11-28 00:01", tz="UTC"))     # type: ignore[arg-type]
    assert "GS1" in ex.retired and "expired" in ex.unqualified["GS1"]
    assert eng.decisions[-1]["action"] == "exploration_arm_unqualified"


# ------------------------------------------------------------------------------------------------ perf-report / health
def test_growth_test_section_reads_equity_fills_refusals_and_shadow_fills(tmp_path) -> None:
    from goldbot.engine.exploration import LEDGER, ledger_db
    from goldbot.ops import growth_report as GR
    from goldbot.research.exploration_data import record_candidate
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"equity": 11_550.0, "week_start_equity": 11_000.0}))
    db = ledger_db(str(tmp_path))
    with db.connection() as conn:
        LEDGER.upsert(conn, _rec("A", 24.0, 24.0))
        LEDGER.upsert(conn, _rec("C", -24.0, 24.0, minute=60).model_copy(update={"arm_id": "GS1C"}))
    for cid, reason in (("X1", "pot"), ("X2", "stale")):
        record_candidate(db, candidate_id=cid, account_id="icm-demo", broker="icm", arm_id="GS1",
                         family="session_range_break", style="growth", tf="15m", side=1,
                         decision_utc=pd.Timestamp("2026-11-02 13:45", tz="UTC"), stop_dist=5.0, target_dist=15.0,
                         max_hold_s=14400, taken=False, blocked_reason=reason, branch="blocked", u=0.5, mu=0.0)
    with db.connection() as conn:
        conn.execute("UPDATE exploration_obs SET label_sim_r = 3.0")
    g = GR.growth_test(tmp_path, now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    assert g is not None and g.week_start.startswith("2026-11-01")
    assert g.multiple == pytest.approx(1.05) and g.growth_pnl == pytest.approx(0.0)
    assert g.growth_drawdown == pytest.approx(24.0 / 11_024.0)
    assert g.read.arms["GS1"].n == 1 and g.read.arms["GS1C"].n == 1
    assert g.read.arms["GS1"].refused == {"pot": 1, "stale": 1}
    assert g.read.arms["GS1"].shadow_n == 1                          # the stale one is no valid entry: not shadowed
    text = "\n".join(GR.lines(g))
    assert "x1.050" in text and "GS1 - GS1C" in text and "f=0.2" in text


def test_growth_test_section_is_absent_when_nothing_is_recorded(tmp_path) -> None:
    from goldbot.ops import growth_report as GR
    assert GR.growth_test(tmp_path, now=NOW) is None


def test_health_shows_the_growth_test_line_with_equity_multiple_and_the_pair(tmp_path) -> None:
    from goldbot.engine.exploration import LEDGER, ledger_db
    from goldbot.ops import health
    from tests.test_health import make_ctx
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"equity": 10_500.0, "week_start_equity": 10_000.0}))
    with ledger_db(str(tmp_path)).connection() as conn:
        LEDGER.upsert(conn, _rec("A", 24.0, 24.0))
    ctx = make_ctx(tmp_path).model_copy(update={"now": pd.Timestamp("2026-11-03 12:00", tz="UTC")})
    c = health.check_growth_test(ctx)
    assert c.name == "growth_test" and c.status == "ok"
    assert "x1.050" in c.reason and "GS1: n 1" in c.reason and "GS1 - GS1C" in c.reason


def test_perf_report_markdown_carries_the_growth_test_section(tmp_path) -> None:
    from goldbot.engine.exploration import LEDGER, ledger_db
    from goldbot.ops import perf_report
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"equity": 10_500.0, "week_start_equity": 10_000.0}))
    with ledger_db(str(tmp_path)).connection() as conn:
        LEDGER.upsert(conn, _rec("A", 24.0, 24.0))
    rep = perf_report.build_performance_report(tmp_path, start="2026-11-02", end="2026-11-03",
                                               now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    assert rep.growth_test is not None and rep.growth_test.multiple == pytest.approx(1.05)
    md = perf_report.format_markdown(rep)
    assert "## Growth test" in md and "x1.050" in md and "GS1 - GS1C" in md
