"""Demo cost probe (the research director's ruling 2026-10-19; ADR 0037, amending ADR 0036): challengers' shadow entries
mirrored as minimum-lot demo orders, one at a time, chosen by a fixed round-robin over agent ids, through the RiskGate,
recorded in aux.db `cost_probes` for the costs pipeline only. Real trades take priority; a live account never probes;
the tournament, promotion and shadow scoring never see a probe."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.db import Database
from goldbot.db.migrations import AUX, migrate
from goldbot.engine import ConstantModel, Engine, EngineConfig, cost_probe
from goldbot.execution.broker import AccountInfo, OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops import ledger
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.risk import RiskLimits
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.telegram.approvals import ApprovalCenter

BASE: dict[str, Any] = dict(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=5)
TICKS = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
CUT = pd.Timestamp("2025-03-04 20:00", tz="UTC")   # the first bar close, in market hours; bars follow it


class _Long(SessionOpenSpecialist):
    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})


class _Terminal(PaperBroker):
    """A paper broker reporting a terminal trade mode, as MT5's account_info does (demo | real | contest)."""

    def __init__(self, trade_mode: str | None = "demo", **kw: Any):
        super().__init__(**kw)
        self.trade_mode = trade_mode

    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": self.trade_mode})


def _engine(tmp_path: Path, *, agents: list[Any] | None = None, probe: bool = True, mode: str = "demo",
            approval: str = "auto", trade_mode: str | None = "demo", live_shares: dict[str, float] | None = None,
            shadow: dict[str, str] | None = None, champions: dict[str, str] | None = None,
            data_root: str | None = None, pb: _Terminal | None = None,
            budget: float = 0.2, max_open: int = 2,
            max_loss: float = 4.0) -> tuple[Engine, _Terminal, Tick, pd.Timestamp]:
    """An engine one bar close away from deciding: the 1m bars before CUT, the last tick, the 15m close CUT.
    `shadow`: version -> agent id (the shadow set); `champions`: agent id -> its champion version."""
    agents = agents if agents is not None else [_Long()]
    pb = pb if pb is not None else _Terminal(trade_mode, equity=10_000, adverse_slip_points=0.0)
    shadow = shadow if shadow is not None else {f"{agents[0].agent_id}-v2": agents[0].agent_id}
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode=mode, approval_mode=approval,
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, shadow_host=True,
                              cost_probe=probe, cost_probe_budget=budget, cost_probe_max_open=max_open,
                              cost_probe_max_loss=max_loss, data_root=data_root),
                 pb, agents, {a.family: ConstantModel(p=0.9) for a in agents}, ApprovalCenter({111}),
                 limits=RiskLimits(**BASE), live_shares=live_shares if live_shares is not None else {},
                 shadow_models={v: (aid, ConstantModel(p=0.9)) for v, aid in shadow.items()},
                 live_versions=champions if champions is not None else {agents[0].agent_id: "champion-v1"})
    tick = _bars_until(eng, pb, CUT)
    return eng, pb, tick, CUT


def _bars_until(eng: Engine, pb: PaperBroker, close: pd.Timestamp) -> Tick:
    """The engine's 1m bars and the broker's quote as they are at the bar close `close`."""
    ticks = TICKS[TICKS["ts_utc"] < close]
    eng.bars_1m = ticks_to_1m(ticks)
    px = float(ticks["bid"].iloc[-1])
    tick = Tick(ts_utc=pd.Timestamp(ticks["ts_utc"].iloc[-1]), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    return tick


def _next_close(eng: Engine, pb: PaperBroker, close: pd.Timestamp) -> None:
    nxt = close + pd.Timedelta(minutes=15)
    eng.on_bar_close(nxt, _bars_until(eng, pb, nxt))


def _probe_orders(eng: Engine) -> list[dict]:
    return [d for d in eng.decisions if str(d.get("action", "")).startswith("cost_probe:")]


def _owner_position(pb: PaperBroker, tick: Tick) -> None:
    """A position the owner opened by hand (not this engine's magic)."""
    pb.place_order(OrderIntent(client_order_id="owner", symbol="XAUUSD", side=-1, lots=0.01, sl=tick.ask + 50,
                               tp=tick.bid - 50, magic=1, comment="owner"))


def _account(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="a", broker="icm", mode="demo", server="s", login=None, terminal_path="t",
                                server_tz="UTC", symbol="XAUUSD", magic_base=1, enabled=True)
    return acc_mod.Account(**{**base, **kw})


# ------------------------------------------------------------------------------------------------ the probe order
def test_a_challenger_shadow_entry_is_mirrored_as_one_minimum_lot_demo_order_checked_by_the_riskgate(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any):
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy                                     # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    [order] = _probe_orders(eng)
    assert order["action"] == "cost_probe:executed:auto"
    [pos] = pb.positions()
    assert pos.lots == pytest.approx(0.01)                   # the broker's minimum lot
    agent = next(iter(eng.agents.values()))
    [(intent, gd)] = [(i, g) for i, g in seen if i.agent_id == cost_probe.probe_id(agent.agent_id)]
    assert gd.allowed and intent.volume_max == intent.volume_min == 0.01   # the gate sized exactly the lots sent
    [tr] = eng.open.values()
    assert tr.agent_id == f"cost_probe:{agent.agent_id}" and tr.lots == pytest.approx(0.01)
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.purpose == "cost_probe" and rec.agent_id == agent.agent_id and rec.version == f"{agent.agent_id}-v2"
    assert rec.position_id == pos.position_id and rec.lots == pytest.approx(0.01)
    assert rec.signal_price is not None and rec.requested_price == pytest.approx(tick.ask)
    assert rec.fill_price is not None and rec.spread_usd == pytest.approx(0.2)
    assert rec.latency_ms is not None and rec.latency_ms >= 0
    assert rec.slippage_usd == pytest.approx(rec.fill_price - rec.signal_price)


def test_a_champion_shadow_entry_is_never_probed(tmp_path):
    agent = _Long()
    eng, pb, tick, close = _engine(tmp_path, agents=[agent], champions={agent.agent_id: f"{agent.agent_id}-v2"})
    eng.on_bar_close(close, tick)
    assert not _probe_orders(eng) and not pb.positions()


def test_no_probe_while_any_position_is_open_on_the_account(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    _owner_position(pb, tick)
    eng.on_bar_close(close, tick)
    assert not _probe_orders(eng) and len(pb.positions()) == 1


@pytest.mark.parametrize("max_open", [1, 2])
def test_at_most_max_open_probe_positions_are_open_at_a_time(tmp_path, max_open):
    """Amendment 2026-10-19: up to cost_probe.max_open (2) probes at once, one new probe per bar close, never two on
    one agent."""
    a, b, c = _Long(), _Long(min_body_pct=0.31), _Long(min_body_pct=0.32)
    eng, pb, tick, close = _engine(tmp_path, agents=[a, b, c], shadow={"va": a.agent_id}, champions={},
                                   max_open=max_open)
    eng.on_bar_close(close, tick)
    assert len(_probe_orders(eng)) == 1
    models: dict[str, Any] = {v: (aid, ConstantModel(p=0.9)) for v, aid in (("va", a.agent_id), ("vb", b.agent_id),
                                                            ("vc", c.agent_id))}
    eng.set_shadow_models(models)
    _next_close(eng, pb, close)
    assert eng.shadow is not None and [t.agent_id for t in eng.shadow.books["vb"].open] == [b.agent_id]   # signals
    _next_close(eng, pb, close + pd.Timedelta(minutes=15))
    open_probes = [t.agent_id for t in eng.open.values() if cost_probe.is_probe(t.agent_id)]
    assert len(open_probes) == len(pb.positions()) == max_open                 # never more than max_open
    assert len(set(open_probes)) == len(open_probes)                           # never two on one agent


def test_the_probe_count_is_configured_per_account_and_never_above_four():
    assert _account(cost_probe={"enabled": True}).cost_probe.max_open == 2
    assert _account(cost_probe={"enabled": True, "max_open": 4}).cost_probe.max_open == 4     # amendment 3
    with pytest.raises(ValidationError):
        _account(cost_probe={"enabled": True, "max_open": 5})


# ------------------------------------------------------------------------------------------------ the loss bound
def test_a_probe_stop_is_clamped_so_its_loss_at_the_stop_is_at_most_max_loss(tmp_path):
    """The paper account loses distance x 100 oz x 0.01 lot = $1 per $1: a $0.5 cap puts the stop $0.5 away."""
    eng, pb, tick, close = _engine(tmp_path, max_loss=0.5)
    eng.on_bar_close(close, tick)
    [tr] = eng.open.values()
    [pos] = pb.positions()
    assert tr.sl is not None and tr.open_price is not None
    loss = float(pb.loss_per_lot("XAUUSD", tr.side, tr.open_price, abs(tr.open_price - tr.sl)) or 0) * pos.lots
    assert loss <= 0.5 + 1e-6
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.stop_loss is not None and rec.stop_loss <= 0.5


def test_a_clamped_probe_opens_when_the_terminal_rounds_its_loss_to_the_cent(tmp_path):
    """Live 2026-10-19: the terminal's order_calc_profit is rounded to the cent per lot, so a stop aimed exactly at
    £4 measured £4.0002 and every box_break_close probe was refused probe_loss_above_cap."""
    eng, pb, tick, close = _engine(tmp_path, max_loss=4.0)

    def lpl(symbol: str, side: int, price: float, distance: float) -> float:
        return round(abs(distance) * 100 * 1.00004, 2)                  # per lot, cent-rounded: $1 reads 100.00, low
    pb.loss_per_lot = lpl                                                # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not any(str(d.get("action", "")).startswith("cost_probe_refused") for d in eng.decisions), eng.decisions
    [pos] = pb.positions()
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.stop_loss is not None and 3.9 <= rec.stop_loss <= 4.0


def _untaken(eng: Engine) -> None:
    """Every shadow entry from now on is below its threshold (would not have traded)."""
    assert eng.shadow is not None
    opened = eng.shadow.open_trade

    def untaken(**kw: Any) -> Any:
        return opened(**{**kw, "taken": False})
    eng.shadow.open_trade = untaken                                      # type: ignore[method-assign]


def test_a_challenger_entry_below_its_threshold_is_mirrored_too(tmp_path):
    """Owner 2026-10-19: learning needs trades, so an untaken shadow entry is probed as well (RiskGate still checks),
    as a learning probe on the gate, recorded untaken in the ledger."""
    eng, pb, tick, close = _engine(tmp_path)
    _untaken(eng)
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        seen.append(intent)
        return check(intent, *a, **kw)
    eng.gate.check = spy                                                 # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert eng.shadow is not None
    [st] = [t for b in eng.shadow.books.values() for t in b.open]
    assert not st.taken
    assert _probe_orders(eng) and pb.positions()
    [probe] = [i for i in seen if cost_probe.is_probe(i.agent_id)]
    assert probe.learning_probe and probe.account_mode == "demo" and probe.volume_max == probe.volume_min
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.taken is False


def test_untaken_probes_stop_at_the_daily_cap(tmp_path):
    """Directors' ruling 2026-10-19: at most UNTAKEN_PER_DAY below-threshold probes a risk day."""
    eng, pb, tick, close = _engine(tmp_path)
    _untaken(eng)
    assert eng.probe is not None
    with eng.probe.db.write() as conn:
        for i in range(cost_probe.UNTAKEN_PER_DAY):
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=f"cp-old-{i}", account_id="icm-demo", agent_id="a", side=1, lots=0.01,
                sent_utc=close, exit_utc=close, pnl=0.0,
                taken=False))
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(d.get("action") == "cost_probe_untaken:daily_cap" for d in eng.decisions)


def test_an_untaken_probe_whose_order_raised_still_counts_toward_the_daily_cap(tmp_path):
    """Safety review LOW-2: a send that raised writes no ledger row; it is counted all the same."""
    eng, pb, tick, close = _engine(tmp_path)
    _untaken(eng)

    def boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("bridge gone")
    eng._execute = boom                                                  # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert eng.probe is not None and len(eng.probe.untaken_sent) == 1
    eng.probe.untaken_sent.update({f"cp-mem-{i}": close for i in range(cost_probe.UNTAKEN_PER_DAY - 1)})
    assert cost_probe.untaken_refusal(eng, eng.probe, close, 10.0) == "daily_cap"


def test_an_open_probe_the_ledger_does_not_know_holds_an_untaken_slot(tmp_path, monkeypatch):
    """Safety review LOW-2: an adopted probe with no ledger row counts as untaken (fail closed)."""
    eng, pb, tick, close = _engine(tmp_path)
    assert eng.probe is not None
    monkeypatch.setattr(cost_probe, "_probes_open", lambda e: [4242])
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 50.0)
    assert cost_probe.untaken_refusal(eng, eng.probe, close, 10.0) == "slots_reserved"
    with eng.probe.db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-known", account_id="icm-demo", agent_id="a", side=1, lots=0.01, sent_utc=close,
            position_id=4242, taken=True))
    assert cost_probe.untaken_refusal(eng, eng.probe, close, 10.0) is None


def test_a_taken_probe_is_not_held_by_the_untaken_cap(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    assert eng.probe is not None
    with eng.probe.db.write() as conn:
        for i in range(cost_probe.UNTAKEN_PER_DAY):
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=f"cp-old-{i}", account_id="icm-demo", agent_id="a", side=1, lots=0.01,
                sent_utc=close, exit_utc=close, pnl=0.0,
                taken=False))
    eng.on_bar_close(close, tick)
    assert pb.positions()
    [rec] = [r for r in cost_probe.probes(tmp_path, "icm-demo") if not r.client_order_id.startswith("cp-old")]
    assert rec.taken is True


def test_a_wide_cap_keeps_the_challengers_own_stop(tmp_path):
    eng, pb, tick, close = _engine(tmp_path, max_loss=1_000.0)
    eng.on_bar_close(close, tick)
    [tr] = eng.open.values()
    assert eng.shadow is not None
    [st] = [t for b in eng.shadow.books.values() for t in b.open]
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert tr.sl is not None and rec.requested_price is not None
    assert abs(rec.requested_price - tr.sl) == pytest.approx(abs(st.entry - st.stop), abs=0.01)   # the shadow stop


@pytest.mark.parametrize("answer", [None, float("nan"), 0.0, "raise"])
def test_a_probe_whose_loss_cannot_be_computed_is_refused(tmp_path, answer):
    eng, pb, tick, close = _engine(tmp_path)

    def lpl(*a: Any) -> float | None:
        if answer == "raise":
            raise RuntimeError("terminal gone")
        return answer

    pb.loss_per_lot = lpl                                                # type: ignore[method-assign,assignment]
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(d.get("action") == "cost_probe_refused:probe_loss_unknown" for d in eng.decisions)


def test_a_stop_the_gate_floors_wider_than_the_cap_is_refused(tmp_path):
    from goldbot.risk.gate import GateDecision
    eng, pb, tick, close = _engine(tmp_path, max_loss=0.5)
    check = eng.gate.check

    def wide(intent: Any, *a: Any, **kw: Any) -> GateDecision:
        gd = check(intent, *a, **kw)
        return gd.model_copy(update={"stop_distance": 5.0})              # e.g. a stops level above the clamp
    eng.gate.check = wide                                                # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(d.get("action") == "cost_probe_refused:probe_loss_above_cap" for d in eng.decisions)


def _ledger_loss(eng: Engine, ts: pd.Timestamp, pnl: float) -> None:
    assert eng.probe is not None
    with eng.probe.db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id=f"cp-0-{pnl}", account_id="icm-demo", agent_id="x", side=1, lots=0.01, sent_utc=ts,
            exit_utc=ts, pnl=pnl))


def test_no_new_probe_when_realised_loss_plus_open_risk_plus_its_own_would_exceed_the_budget(tmp_path):
    """Worst case bound: today's realised probe loss + the open probes' loss at their stops + the new probe's never
    exceeds budget_share x the day's loss limit."""
    from goldbot.engine.limits_apply import daily_allowance
    a, b = _Long(), _Long(min_body_pct=0.31)
    eng, pb, tick, close = _engine(tmp_path, agents=[a, b], shadow={"va": a.agent_id}, champions={}, max_loss=4.0)
    eng._refresh_account(tick)
    limit = daily_allowance(eng.gate, eng.state)["daily_loss_limit"]
    assert limit is not None
    share = 0.2 * limit
    _ledger_loss(eng, tick.ts_utc, -(share - 6.0))       # 6.0 of the budget left: room for one $4 probe, not two
    eng.on_bar_close(close, tick)
    assert len(pb.positions()) == 1
    risk = cost_probe.open_risk(eng)
    assert risk is not None and 0 < risk <= 4.0 + 1e-6
    eng.set_shadow_models({"va": (a.agent_id, ConstantModel(p=0.9)), "vb": (b.agent_id, ConstantModel(p=0.9))})
    _next_close(eng, pb, close)
    held = cost_probe.open_risk(eng)
    assert held is not None and (share - 6.0) + held <= share + 1e-6          # the worst case stays inside the budget
    if len(pb.positions()) == 1:                                             # the 2nd probe did not fit
        assert any(str(d.get("action", "")).startswith("cost_probe_budget:") for d in eng.decisions)


def test_the_budget_headroom_subtracts_open_probe_risk_and_ignores_profits(tmp_path):
    from goldbot.engine.limits_apply import daily_allowance
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    assert eng.probe is not None and len(pb.positions()) == 1
    limit = daily_allowance(eng.gate, eng.state)["daily_loss_limit"]
    assert limit is not None
    held = cost_probe.open_risk(eng)
    assert held is not None
    why, headroom = cost_probe.budget_refusal(eng, eng.probe, close)
    assert why is None and headroom == pytest.approx(0.2 * limit - held, abs=0.02)
    _ledger_loss(eng, tick.ts_utc, 1_000.0)                                  # a probe profit adds no headroom
    assert cost_probe.budget_refusal(eng, eng.probe, close)[1] == pytest.approx(headroom, abs=0.02)
    _ledger_loss(eng, tick.ts_utc, -1_000_000.0)
    assert cost_probe.budget_refusal(eng, eng.probe, close)[0] == "probe_loss_reached"


def test_an_open_probe_whose_risk_is_unknown_refuses_new_probes(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    assert eng.probe is not None
    [tr] = eng.open.values()
    tr.sl = None
    assert cost_probe.budget_refusal(eng, eng.probe, close)[0] == "open_risk_unknown"


def test_the_shipped_icm_demo_probe_terms_are_four_probes_four_pounds_and_a_twenty_pound_day(monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    cp = acc_mod.load_accounts()["icm-demo"].cost_probe
    assert (cp.max_open, cp.max_loss, cp.budget_share) == (4, 4.0, 0.4)                     # amendment 3
    with pytest.raises(ValidationError, match="max_loss"):
        _account(daily_loss_limit_abs=50.0, cost_probe={"enabled": True, "max_loss": 11.0})


def test_the_rotation_is_round_robin_over_agent_ids_and_never_reads_p_or_pnl():
    assert cost_probe.rotate(["b", "a", "c"], None) == "a"
    assert cost_probe.rotate(["b", "a", "c"], "a") == "b"
    assert cost_probe.rotate(["b", "a", "c"], "b") == "c"
    assert cost_probe.rotate(["b", "a", "c"], "c") == "a"             # wraps
    assert cost_probe.rotate(["a", "c"], "a") == "c"                  # the next agent id that has a signal
    assert cost_probe.rotate(["a"], "a") == "a"
    # the rotation takes agent ids only: nothing else (p, score, P&L) can reach the choice
    import inspect
    assert list(inspect.signature(cost_probe.rotate).parameters) == ["agent_ids", "last"]


def test_the_rotation_resumes_after_a_restart_from_the_last_probe_in_the_ledger(tmp_path):
    a, b = _Long(), _Long(min_body_pct=0.31)
    assert a.agent_id < b.agent_id or a.agent_id > b.agent_id
    shadow = {"va": a.agent_id, "vb": b.agent_id}
    eng, pb, tick, close = _engine(tmp_path, agents=[a, b], shadow=shadow, champions={})
    eng.on_bar_close(close, tick)
    sent = sorted(cost_probe.probes(tmp_path, "icm-demo"), key=lambda r: r.sent_utc)
    assert sent[0].agent_id == min(a.agent_id, b.agent_id)                # the rotation starts at the first agent
    eng2, _, _, _ = _engine(tmp_path, agents=[a, b], shadow=shadow, champions={})
    assert eng2.probe is not None and eng2.probe.last_agent == sent[-1].agent_id


# ------------------------------------------------------------------------------------------------ amendment 3
def _two_agents(tmp_path: Path, **kw: Any) -> tuple[Engine, _Terminal, Tick, pd.Timestamp]:
    a, b = _Long(), _Long(min_body_pct=0.31)
    return _engine(tmp_path, agents=[a, b], shadow={"va": a.agent_id, "vb": b.agent_id}, champions={},
                   max_open=4, budget=0.4, **kw)


def test_two_agents_signalling_at_one_close_open_two_probes(tmp_path):
    """Amendment 3 (2026-10-19): up to PER_CLOSE new probes per bar close, from different agents."""
    eng, pb, tick, close = _two_agents(tmp_path)
    eng.on_bar_close(close, tick)
    assert len(pb.positions()) == 2
    assert len({r.agent_id for r in cost_probe.probes(tmp_path, "icm-demo")}) == 2


def test_no_more_than_per_close_probes_open_at_one_close(tmp_path, monkeypatch):
    monkeypatch.setattr(cost_probe, "PER_CLOSE", 1)
    eng, pb, tick, close = _two_agents(tmp_path)
    eng.on_bar_close(close, tick)
    assert len(pb.positions()) == 1


def test_the_same_side_cap_holds_back_a_probe_on_a_crowded_side(tmp_path, monkeypatch):
    """Amendment 3: at most MAX_SAME_SIDE open probes on one side (both agents here go long)."""
    monkeypatch.setattr(cost_probe, "MAX_SAME_SIDE", 1)
    eng, pb, tick, close = _two_agents(tmp_path)
    eng.on_bar_close(close, tick)
    assert len(pb.positions()) == 1


def test_real_trades_keep_their_reserve_of_the_days_allowance(tmp_path, monkeypatch):
    """Amendment 3: no new probe once the allowance left after it would fall below REAL_TRADE_RESERVE x the limit."""
    monkeypatch.setattr(cost_probe, "REAL_TRADE_RESERVE", 0.999)
    eng, pb, tick, close = _two_agents(tmp_path)
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(d.get("action") == "cost_probe_budget:real_trade_reserve" for d in eng.decisions)


# ------------------------------------------------------------------------------------------------ priority
def test_a_live_agent_candidate_closes_the_open_probe_before_its_own_decision(tmp_path):
    agent = _Long()
    eng, pb, tick, close = _engine(tmp_path, agents=[agent])
    eng.on_bar_close(close, tick)
    [probe_pid] = list(eng.open)
    eng.live_shares = {agent.agent_id: 1.0}                  # the agent goes live: its next candidate is a real trade
    _next_close(eng, pb, close)
    acts = [str(d["action"]) for d in eng.decisions]
    assert cost_probe.YIELD in acts and acts.index(cost_probe.YIELD) < acts.index("executed:auto")
    assert probe_pid not in eng.open and [t.agent_id for t in eng.open.values()] == [agent.agent_id]


def test_an_open_probe_is_closed_when_another_position_appears(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    [probe_pid] = list(eng.open)
    _owner_position(pb, tick)
    _next_close(eng, pb, close)
    assert probe_pid not in eng.open and probe_pid not in {p.position_id for p in pb.positions()}


# ------------------------------------------------------------------------------------------------ the lock
def test_a_live_account_with_the_cost_probe_on_is_refused_at_load():
    with pytest.raises(ValidationError, match="demo accounts only"):
        _account(mode="live", cost_probe={"enabled": True})
    assert _account(mode="live").cost_probe.enabled is False


def test_the_shipped_config_probes_on_icm_demo_only(monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    accs = acc_mod.load_accounts()
    assert accs["icm-demo"].cost_probe.enabled is True
    assert all(not a.cost_probe.enabled for a in accs.values() if a.mode != "demo")


def test_a_live_account_configured_with_probes_fails_to_load_from_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    raw = yaml.safe_load(Path("config/accounts.yaml").read_text())
    raw["accounts"]["icm-live"]["cost_probe"] = {"enabled": True}
    f = tmp_path / "accounts.yaml"
    f.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError, match="demo accounts only"):
        acc_mod.load_accounts(f)


@pytest.mark.parametrize("mode", ["live", "paper"])
def test_the_engine_refuses_to_start_probes_on_an_account_that_is_not_demo(tmp_path, mode):
    with pytest.raises(ValueError, match="cost probes"):
        _engine(tmp_path, mode=mode, approval="propose", trade_mode="real")


@pytest.mark.parametrize("trade_mode", ["real", "contest", None])
def test_a_demo_account_on_a_terminal_that_does_not_report_demo_sends_no_probe(tmp_path, trade_mode):
    eng, pb, tick, close = _engine(tmp_path, trade_mode=trade_mode)
    eng.on_bar_close(close, tick)
    assert not _probe_orders(eng) and not pb.positions()
    assert eng.cfg.approval_mode == "propose"                # the ADR 0036 lock, as for any auto entry


def test_the_owners_propose_mode_stops_probes(tmp_path):
    eng, pb, tick, close = _engine(tmp_path, approval="propose")
    eng.on_bar_close(close, tick)
    assert not _probe_orders(eng) and not pb.positions()


def test_a_gate_refusal_sends_no_probe(tmp_path):
    from goldbot.risk.gate import GateDecision
    eng, pb, tick, close = _engine(tmp_path)
    eng.gate.check = lambda *a, **kw: GateDecision(allowed=False, reasons=["daily_loss_cap"])  # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not pb.positions() and not cost_probe.probes(tmp_path, "icm-demo")
    assert any(d.get("action") == "cost_probe_refused:daily_loss_cap" for d in eng.decisions)


def test_a_probe_error_never_fails_the_bar_close(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)

    def boom(*a: Any, **kw: Any) -> float:
        raise RuntimeError("swap unreadable")

    eng._swap_usd_per_oz = boom                              # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not pb.positions() and any(d.get("action") == "cost_probe_error" for d in eng.decisions)
    assert (tmp_path / "shadow_book.json").exists()          # the shadow save after the probe step still ran


def test_probes_off_sends_nothing(tmp_path):
    eng, pb, tick, close = _engine(tmp_path, probe=False)
    eng.on_bar_close(close, tick)
    assert eng.probe is None and not pb.positions()


# ------------------------------------------------------------------------------------------------ the record
def test_a_closed_probe_goes_to_the_probe_ledger_and_never_to_closed_trades_fills_or_the_shadow_book(tmp_path):
    """The tournament, promotion and shadow scoring never read probe P&L: the shadow book (shadow_stats, attribution)
    is the same with and without probes, the closed-trade record (phase gate, throttle) and the store's fills (the
    nightly slippage table, attribution's live fills) never get a probe."""
    runs = {}
    for probe in (True, False):
        d = tmp_path / str(probe)
        d.mkdir()
        eng, pb, tick, close = _engine(d, probe=probe, data_root=str(d / "data"))
        eng.on_bar_close(close, tick)
        for pid in list(eng.open):
            eng._try_close(pid, "time_exit", None)
        eng._flush_closed()
        assert eng.shadow is not None
        version = next(iter(eng.shadow.books))
        book = eng.shadow.books[version]                     # started_utc is the wall clock: left out
        runs[probe] = ([t.model_dump(mode="json") for t in book.open + book.closed],
                       eng.shadow.stats(version, close).model_dump(exclude={"weeks", "trades_per_week"}), eng)
        assert load_closed_trades(d)[0] == []
        fills = Store(d / "data").read("fills") if (d / "data").exists() else pd.DataFrame()
        assert fills.empty or not fills["agent_id"].astype(str).str.startswith("cost_probe:").any()
    assert runs[True][:2] == runs[False][:2]
    [rec] = cost_probe.probes(tmp_path / "True", "icm-demo")
    assert rec.exit_utc is not None and rec.exit_price is not None and rec.exit_reason == "time_exit"
    assert rec.pnl is not None
    assert not cost_probe.probes(tmp_path / "False", "icm-demo")


class _RosterLong(_Long):
    """A frozen roster rule agent as the probe sees it (box_break_close / sweep_absorb, retired 2026-10-19 by H-50 /
    H-48 and still mirrored on demo, ADR 0040): shadow only, one frozen config, a RuleModel in the shadow set."""
    frozen_config = True

    @property
    def shadow_only(self) -> bool:
        return True


def test_a_retired_roster_agents_probe_never_reaches_its_shadow_book_closed_trades_or_fills(tmp_path):
    """ADR 0040: probes keep mirroring the retired roster agents. Their probe P&L must stay out of every evidence path:
    the agent's shadow book (promotion, tournament, attribution), the closed-trade record and the fills table."""
    from goldbot.research.rule_model import RuleModel
    runs = {}
    for probe in (True, False):
        d = tmp_path / str(probe)
        d.mkdir()
        agent = _RosterLong()
        eng, pb, tick, close = _engine(d, agents=[agent], probe=probe, data_root=str(d / "data"), champions={})
        version = next(iter(eng.shadow_models))
        eng.shadow_models[version] = (agent.agent_id, RuleModel(rule=agent.family))
        assert cost_probe._mirrorable(eng, version, agent)
        eng.on_bar_close(close, tick)
        assert bool(eng.open) is probe and all(cost_probe.is_probe(t.agent_id) for t in eng.open.values())
        for pid in list(eng.open):
            eng._try_close(pid, "time_exit", None)
        eng._flush_closed()
        assert eng.shadow is not None
        book = eng.shadow.books[version]
        runs[probe] = [t.model_dump(mode="json") for t in book.open + book.closed]
        assert all(not cost_probe.is_probe(t.agent_id) for t in book.open + book.closed)
        assert load_closed_trades(d)[0] == []
        fills = Store(d / "data").read("fills") if (d / "data").exists() else pd.DataFrame()
        assert fills.empty or not fills["agent_id"].astype(str).str.startswith("cost_probe:").any()
    assert runs[True] == runs[False]
    [rec] = cost_probe.probes(tmp_path / "True", "icm-demo")
    assert rec.agent_id == agent.agent_id and rec.exit_utc is not None and rec.pnl is not None


def test_the_cost_probes_table_is_an_additive_aux_migration_so_a_rollback_still_opens_aux_db(tmp_path):
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux")
        assert conn.execute("SELECT COUNT(*) FROM cost_probes").fetchone()[0] == 0
        assert migrate(conn, "aux", migrations=AUX[:-1]) == []          # the older code accepts the newer file
    assert AUX[-1].name == "cost_probes" and AUX[-1].additive


def test_the_broker_ledger_reconciles_closed_probes_in_the_probe_ledger_not_as_missing(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    [pid] = list(eng.open)
    eng._try_close(pid, "time_exit", None)
    eng._flush_closed()
    db = Database.open(tmp_path, "aux")
    assert cost_probe.closed_position_ids(db, "icm-demo") == {pid}
    assert cost_probe.closed_position_ids(db, "other") == set()
    assert ledger.probe_note({pid}) == "1 cost probe position(s) reconciled in the cost-probe ledger (ADR 0037)"


# ------------------------------------------------------------------------------------------------ review round 1
def test_a_probe_order_carries_its_own_magic_and_a_cp_comment(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    [pos] = pb.positions()
    assert pos.magic == eng.cfg.magic_base + cost_probe.PROBE_MAGIC_OFFSET and pos.comment.startswith("cp-")


def test_no_strategy_family_shares_the_probe_magic_or_the_smoke_magic():
    import zlib

    from goldbot.execution.broker import SMOKE_MAGIC_OFFSET
    from goldbot.specialists import SPECIALISTS
    assert cost_probe.PROBE_MAGIC_OFFSET != SMOKE_MAGIC_OFFSET
    reserved = (cost_probe.PROBE_MAGIC_OFFSET, SMOKE_MAGIC_OFFSET)
    clash = {f for f in SPECIALISTS if zlib.crc32(f.encode()) % 100 in reserved}
    assert not clash, f"families whose magic is reserved: {clash}"


def test_a_probe_found_after_the_orders_file_is_lost_is_adopted_as_a_probe_never_as_the_live_agent(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    [pid] = list(eng.open)
    (tmp_path / "orders_icm-demo.json").unlink()
    eng2, _, _, _ = _engine(tmp_path, pb=pb)
    assert not eng2.open
    eng2._reconcile(tick.ts_utc)
    assert cost_probe.is_probe(eng2.open[pid].agent_id)
    eng2._try_close(pid, "time_exit", None)
    eng2._flush_closed()
    assert load_closed_trades(tmp_path)[0] == []
    assert [r.position_id for r in cost_probe.probes(tmp_path, "icm-demo") if r.exit_utc is not None] == [pid]


def test_a_probe_survives_a_restart_and_its_time_exit_still_runs(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    [pid] = list(eng.open)
    eng2, _, _, _ = _engine(tmp_path, pb=pb)
    tr = eng2.open[pid]
    assert cost_probe.is_probe(tr.agent_id) and tr.max_bars == eng.open[pid].max_bars
    tr.bars_held = tr.max_bars - 1
    eng2._manage_open(pd.DataFrame())
    eng2._flush_closed()
    assert pid not in eng2.open and pid not in {p.position_id for p in pb.positions()}
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.exit_reason == "time_exit" and load_closed_trades(tmp_path)[0] == []


def test_a_probe_recovered_from_an_unconfirmed_send_gets_its_challengers_exit_policy(tmp_path):
    from goldbot.engine import unconfirmed
    from goldbot.engine.store import SentOrder
    from goldbot.execution.broker import Position
    agent = _Long()
    eng, pb, tick, close = _engine(tmp_path, agents=[agent])
    attached: list[Any] = []
    eng._attach_policy = lambda tr, a, **kw: attached.append(a)          # type: ignore[method-assign]
    rec = SentOrder(client_order_id="cp-1-a", ts_utc=tick.ts_utc, agent_id=cost_probe.probe_id(agent.agent_id), side=1,
                    magic=eng.cfg.magic_base + cost_probe.PROBE_MAGIC_OFFSET, lots=0.01, max_bars=8, sl=2390.0,
                    tp=2420.0, status="ambiguous", atr_usd=5.0)
    pos = Position(position_id=77, symbol="XAUUSD", side=1, lots=0.01, open_price=2400.0, sl=2390.0, tp=2420.0,
                   magic=rec.magic, comment="cp-1-a", open_time_utc=tick.ts_utc, profit=0.0)
    unconfirmed._adopt_row(eng, "cp-1-a", rec, pos)
    assert attached == [agent] and cost_probe.is_probe(eng.open[77].agent_id)


def test_the_kill_switch_closes_an_open_probe_and_no_probe_follows(tmp_path):
    a, b = _Long(), _Long(min_body_pct=0.31)
    eng, pb, tick, close = _engine(tmp_path, agents=[a, b], shadow={"va": a.agent_id}, champions={})
    eng.on_bar_close(close, tick)
    [pid] = list(eng.open)
    eng.state.balance_closed_hwm = eng.state.equity / 0.85             # a 15% drawdown: the 12% kill switch trips
    eng._refresh_account(pb.last_tick("XAUUSD"))
    eng._flush_closed()
    assert eng.state.stage.value == "halted"
    assert not pb.positions()
    assert [r.position_id for r in cost_probe.probes(tmp_path, "icm-demo") if r.exit_utc is not None] == [pid]
    eng.set_shadow_models({**eng.shadow_models, "vb": (b.agent_id, ConstantModel(p=0.9))})
    _next_close(eng, pb, close)
    assert len(_probe_orders(eng)) == 1 and not pb.positions()          # propose after the trip: no new probe


def test_the_owners_halt_blocks_probes_at_the_riskgate(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    refresh = eng._refresh_account

    def halted(tick: Tick) -> None:
        refresh(tick)
        eng.state.owner_halt = True

    eng._refresh_account = halted                                        # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(str(d.get("action", "")).startswith("cost_probe_refused:") and "owner_halt" in d["action"]
               for d in eng.decisions)


def test_no_probe_once_todays_realised_probe_loss_reaches_its_share_of_the_daily_allowance(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    assert eng.probe is not None
    with eng.probe.db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-0-x", account_id="icm-demo", agent_id="x", side=1, lots=0.01, sent_utc=tick.ts_utc,
            exit_utc=tick.ts_utc, pnl=-1_000_000.0))
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(str(d.get("action", "")).startswith("cost_probe_budget:") for d in eng.decisions)


def test_no_probe_while_the_daily_loss_remaining_is_below_the_probe_share(tmp_path):
    from goldbot.engine.limits_apply import daily_allowance
    eng, pb, tick, close = _engine(tmp_path, budget=0.2)
    refresh = eng._refresh_account

    def lossy(tick: Tick) -> None:                                        # 90% of today's allowance already lost
        refresh(tick)
        limit = daily_allowance(eng.gate, eng.state)["daily_loss_limit"]
        assert limit is not None
        eng.state.day_start_equity = eng.state.equity + 0.9 * limit

    eng._refresh_account = lossy                                         # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    assert any(str(d.get("action", "")).startswith("cost_probe_budget:") for d in eng.decisions)


def test_an_open_probe_already_due_to_close_is_not_closed_again_to_make_way(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.on_bar_close(close, tick)
    [pid] = list(eng.open)
    eng._close_due[pid] = ("time_exit", {"symbol": "XAUUSD"}, tick.ts_utc)
    cost_probe._make_way(eng, close)
    assert eng._close_due[pid][0] == "time_exit" and pid in eng.open
    assert not any(d.get("action") == cost_probe.YIELD for d in eng.decisions)


def test_the_probe_budget_share_is_configured_per_account_and_bounded():
    assert _account(cost_probe={"enabled": True}).cost_probe.budget_share == 0.2
    with pytest.raises(ValidationError):
        _account(cost_probe={"enabled": True, "budget_share": 1.5})


# ------------------------------------------------------------------------------------------------ dynamic limits
# docs/design/dynamic-limits-2026-10-19.md item 4: "per probe, max_loss = min(loss of 0.01 lot at the challenger's own
# label-spec stop, budget_share x daily limit / max_open). max_open_dyn = clamp(floor(budget_share x limit / median
# probe stop loss), 1, cfg.max_open). Config max_loss/max_open become the ceilings and the fallback."
def _dynamic(eng: Engine) -> cost_probe.CostProbe:
    assert eng.probe is not None
    eng.probe.dynamic = True
    return eng.probe


def _history(eng: Engine, *losses: float, native: bool = True) -> None:
    """Closed probes from an earlier day (no P&L today, no open slot), each with its loss at the strategy's own stop
    (`native_stop_loss`) and, as sent, that loss clamped to the £4 ceiling (`stop_loss`). `native=False`: rows written
    before the native loss was recorded."""
    assert eng.probe is not None
    old = pd.Timestamp("2025-03-01 12:00", tz="UTC")
    with eng.probe.db.write() as conn:
        for i, loss in enumerate(losses):
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=f"cp-hist-{i}", account_id="icm-demo", agent_id="h", side=1, lots=0.01,
                sent_utc=old + pd.Timedelta(minutes=i), exit_utc=old + pd.Timedelta(minutes=i), pnl=0.0,
                stop_loss=min(loss, 4.0), native_stop_loss=loss if native else None))


def test_with_dynamic_limits_a_probe_inside_the_ceiling_keeps_its_own_stop_and_records_its_native_loss(tmp_path):
    eng, pb, tick, close = _engine(tmp_path, max_loss=1_000.0)
    _dynamic(eng)
    eng.on_bar_close(close, tick)
    [tr] = eng.open.values()
    assert eng.shadow is not None
    [st] = [t for b in eng.shadow.books.values() for t in b.open]
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert tr.sl is not None and rec.requested_price is not None
    assert abs(rec.requested_price - tr.sl) == pytest.approx(abs(st.entry - st.stop), abs=0.01)   # its own stop
    assert rec.stop_loss is not None and rec.probe_max_loss == pytest.approx(rec.stop_loss, abs=0.01)
    assert rec.basis is not None and rec.basis.startswith("max_loss=native_stop")
    [order] = _probe_orders(eng)
    assert order["probe_max_loss"] == rec.probe_max_loss and order["probe_max_open"] == 2
    assert order["basis"] == rec.basis


def test_with_dynamic_limits_the_budgets_fair_share_caps_a_probe_below_the_configured_max_loss(tmp_path, monkeypatch):
    """A day limit tightened to £5: 0.2 x 5 / 2 probes = £0.50 a probe, under the configured £4 ceiling."""
    eng, pb, tick, close = _engine(tmp_path, max_loss=4.0)
    _dynamic(eng)
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 5.0)
    eng.on_bar_close(close, tick)
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.stop_loss is not None and rec.stop_loss <= 0.5 + 1e-6
    assert rec.probe_max_loss == pytest.approx(0.5) and rec.basis is not None
    assert rec.basis.startswith("max_loss=budget_cap")


def test_with_dynamic_limits_the_configured_max_loss_stays_the_ceiling(tmp_path):
    """The paper account loses $1 per $1 of stop at 0.01 lot: a $0.5 ceiling clamps a native stop wider than it."""
    eng, pb, tick, close = _engine(tmp_path, max_loss=0.5)
    _dynamic(eng)
    eng.on_bar_close(close, tick)
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.stop_loss is not None and rec.stop_loss <= 0.5 + 1e-6
    assert rec.probe_max_loss == pytest.approx(0.5) and rec.basis is not None
    assert rec.basis.startswith("max_loss=config_cap")


def test_with_dynamic_limits_off_the_configured_values_apply_and_the_record_says_fixed(tmp_path, monkeypatch):
    eng, pb, tick, close = _engine(tmp_path, max_loss=4.0)
    assert eng.probe is not None and eng.probe.dynamic is False                    # off unless the account says on
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 5.0)                         # ignored while off
    lim = cost_probe.probe_limits(eng, eng.probe)
    assert (lim.max_open, lim.max_loss, lim.loss_basis, lim.open_basis) == (2, 4.0, "fixed", "fixed")
    eng.on_bar_close(close, tick)
    [rec] = cost_probe.probes(tmp_path, "icm-demo")
    assert rec.probe_max_loss == 4.0 and rec.probe_max_open == 2 and rec.basis == "max_loss=fixed;max_open=fixed"


@pytest.mark.parametrize(("losses", "want", "why"), [
    ((6.0, 6.0, 6.0, 7.0, 7.0), 3, "budget"),       # floor(0.4 x 50 / 6) = 3, though each was sent clamped to £4
    ((1.0, 1.0, 1.0, 1.0, 1.0), 4, "config_cap"),   # floor(20 / 1) = 20, never above the configured 4
    ((50.0, 60.0, 60.0, 70.0, 70.0), 1, "budget"),  # floor(20 / 60) = 0, never below one
])
def test_the_dynamic_probe_count_comes_from_the_budget_over_the_median_probe_stop_loss_and_never_above_the_config(
        tmp_path, monkeypatch, losses, want, why):
    eng, pb, tick, close = _two_agents(tmp_path)
    probe = _dynamic(eng)
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 50.0)
    _history(eng, *losses)
    lim = cost_probe.probe_limits(eng, probe)
    assert lim.max_open == want and lim.open_basis == why
    assert lim.max_open <= eng.cfg.cost_probe_max_open


def test_the_dynamic_probe_count_uses_only_the_most_recent_probes(tmp_path, monkeypatch):
    eng, pb, tick, close = _two_agents(tmp_path)
    probe = _dynamic(eng)
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 50.0)
    _history(eng, *([60.0] * 5 + [1.0] * cost_probe.PROBE_LOSS_WINDOW))             # the old wide stops have aged out
    assert cost_probe.probe_limits(eng, probe).max_open == 4


def test_the_dynamic_probe_limits_fail_closed_to_the_configured_values(tmp_path, monkeypatch):
    eng, pb, tick, close = _two_agents(tmp_path)
    probe = _dynamic(eng)
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 50.0)
    lim = cost_probe.probe_limits(eng, probe)                                       # no probe history yet
    assert (lim.max_open, lim.open_basis) == (4, "fixed:no_history")
    _history(eng, *[60.0] * cost_probe.PROBE_LOSS_MIN_ROWS)

    def unreadable() -> Any:
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(probe.db, "connection", unreadable)
    lim = cost_probe.probe_limits(eng, probe)
    assert (lim.max_open, lim.open_basis) == (4, "fixed:ledger_unreadable")
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 0.0)
    lim = cost_probe.probe_limits(eng, probe)
    assert (lim.max_open, lim.max_loss, lim.loss_basis, lim.open_basis) == (4, 4.0, "fixed:limit_unknown",
                                                                            "fixed:limit_unknown")


def test_the_dynamic_probe_count_reads_the_strategies_own_stops_not_the_clamped_ones(tmp_path, monkeypatch):
    """Quant review: the loss as sent is clamped to budget / max_open, so a median of it can never tighten the count.
    Rows without the native loss (written before) and fewer than PROBE_LOSS_MIN_ROWS with it give the configured
    count."""
    eng, pb, tick, close = _two_agents(tmp_path)
    probe = _dynamic(eng)
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 50.0)
    _history(eng, *[60.0] * 10, native=False)                                       # sent at £4, native unknown
    assert (cost_probe.probe_limits(eng, probe).max_open, cost_probe.probe_limits(eng, probe).open_basis) == \
        (4, "fixed:no_history")
    with probe.db.write() as conn:
        for i in range(cost_probe.PROBE_LOSS_MIN_ROWS - 1):
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=f"cp-new-{i}", account_id="icm-demo", agent_id="h", side=1, lots=0.01,
                sent_utc=close - pd.Timedelta(days=1, minutes=-i), stop_loss=4.0, native_stop_loss=60.0))
    assert cost_probe.probe_limits(eng, probe).open_basis == "fixed:no_history"      # one row short
    with probe.db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-new-last", account_id="icm-demo", agent_id="h", side=1, lots=0.01,
            sent_utc=close - pd.Timedelta(hours=1), stop_loss=4.0, native_stop_loss=60.0))
    assert cost_probe.probe_limits(eng, probe).max_open == 1                         # floor(20 / 60), at least one


def test_a_clamped_probe_records_the_loss_at_its_strategys_own_stop(tmp_path):
    eng, pb, tick, close = _engine(tmp_path, max_loss=0.5)
    eng.on_bar_close(close, tick)
    eng2, *_ = _engine(tmp_path / "wide", max_loss=1_000.0)
    eng2.on_bar_close(close, _bars_until(eng2, eng2.broker, close))                 # type: ignore[arg-type]
    [clamped] = cost_probe.probes(tmp_path, "icm-demo")
    [own] = cost_probe.probes(tmp_path / "wide", "icm-demo")
    assert clamped.stop_loss is not None and clamped.stop_loss <= 0.5 + 1e-6
    assert clamped.native_stop_loss is not None and clamped.native_stop_loss > clamped.stop_loss
    assert clamped.native_stop_loss == pytest.approx(own.stop_loss, abs=0.02)       # the unclamped stop's loss


def test_with_dynamic_limits_a_strategy_with_wide_stops_gets_fewer_probes(tmp_path, monkeypatch):
    eng, pb, tick, close = _two_agents(tmp_path)
    _dynamic(eng)
    monkeypatch.setattr(cost_probe, "_limit", lambda e: 50.0)
    _history(eng, 50.0, 60.0, 60.0, 70.0, 70.0)                     # wide own stops, each sent clamped to £4
    eng.on_bar_close(close, tick)
    assert len(pb.positions()) == 1
    [order] = _probe_orders(eng)
    assert order["probe_max_open"] == 1 and order["basis"].endswith("max_open=budget")


def test_with_dynamic_limits_a_probe_whose_loss_cannot_be_computed_is_still_refused(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    _dynamic(eng)
    pb.loss_per_lot = lambda *a: None                                    # type: ignore[method-assign]
    eng.on_bar_close(close, tick)
    assert not pb.positions()
    refused = [d for d in eng.decisions if d.get("action") == "cost_probe_refused:probe_loss_unknown"]
    assert refused and refused[0]["basis"].startswith("max_loss=fixed:probe_loss_unknown")
