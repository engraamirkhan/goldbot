"""Scheduled cost-measurement probes, the M1 arm of the measured-cost protocol (docs/research/
prereg-cost-measurement-2026-10.md, section 1: "0.01 lot at a fixed slot time; side = +1 on even slot index, -1 on odd;
market close after 30 s; server SL at $3 from fill"), one per 15m close, through the probes' own order path and the
RiskGate's `check`; and the probe_only learning volume terms (mirror probes held at most 4 of their agent's bars, 48
below-threshold probes a day)."""
from __future__ import annotations

import zlib
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, cost_probe
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.ops import accounts as acc_mod
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.risk import RiskLimits
from goldbot.risk.gate import GateDecision
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.telegram.approvals import ApprovalCenter

BASE: dict[str, Any] = dict(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=5)
TICKS = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
CUT = pd.Timestamp("2025-03-04 20:00", tz="UTC")       # a Tuesday 15m close in market hours, outside the blackout
ON = acc_mod.ScheduledProbe(enabled=True)


class _Long(SessionOpenSpecialist):
    """Long at every close; its 0.2 ATR stop (~£3 at 0.01 lot here) fits the £4 probe cap, so it is mirrored."""

    @property
    def label_spec(self) -> BarrierSpec:
        return super().label_spec.model_copy(update={"stop_atr": 0.2})

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})


class _Terminal(PaperBroker):
    def __init__(self, trade_mode: str | None = "demo", **kw: Any):
        super().__init__(**kw)
        self.trade_mode = trade_mode

    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": self.trade_mode})


def _engine(tmp_path: Path, *, scheduled: acc_mod.ScheduledProbe = ON, approval: str = "auto",
            trade_mode: str | None = "demo", pb: _Terminal | None = None, probe_only: bool = False,
            shadow: bool = False, probe: bool = True, shadow_host: bool = True) -> tuple[Engine, _Terminal, Tick]:
    """An engine one 15m close (CUT) away; no shadow challenger unless `shadow`, so only scheduled probes trade;
    `shadow_host` False: no shadow book at all (a learning host such as vantage-demo)."""
    agent = _Long()
    pb = pb if pb is not None else _Terminal(trade_mode, equity=10_000, adverse_slip_points=0.0)
    versions = {f"{agent.agent_id}-v2": agent.agent_id} if shadow else {}
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", approval_mode=approval,
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, shadow_host=shadow_host,
                              cost_probe=probe, cost_probe_scheduled=scheduled, probe_only=probe_only),
                 pb, [agent], {agent.family: ConstantModel(p=0.9)}, ApprovalCenter({111}),
                 limits=RiskLimits(**BASE), live_shares={},
                 shadow_models={v: (aid, ConstantModel(p=0.9)) for v, aid in versions.items()},
                 live_versions={agent.agent_id: "champion-v1"})
    return eng, pb, _bars_until(eng, pb, CUT)


def _bars_until(eng: Engine, pb: PaperBroker, close: pd.Timestamp) -> Tick:
    ticks = TICKS[TICKS["ts_utc"] < close]
    eng.bars_1m = ticks_to_1m(ticks)
    px = float(ticks["bid"].iloc[-1])
    tick = Tick(ts_utc=pd.Timestamp(ticks["ts_utc"].iloc[-1]), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    return tick


def _allow(eng: Engine) -> list[Any]:
    """A stub of the gate's answer for a scheduled probe (allowed at the minimum lot with the $3 stop), so the order
    path's tests do not depend on the gate's fence; the real gate's own tests below and tests/test_cost_measurement_gate.py.
    Every intent the gate is asked about is recorded."""
    seen: list[Any] = []
    real = eng.gate.check

    def check(intent: Any, *a: Any, **kw: Any) -> GateDecision:
        seen.append(intent)
        if intent.agent_id != cost_probe.SCHEDULED_ID:
            return real(intent, *a, **kw)
        return GateDecision(allowed=True, lots=intent.volume_min, stop_distance=intent.stop_atr * intent.atr_usd)

    eng.gate.check = check                                  # type: ignore[method-assign]
    return seen


def _scheduled(eng: Engine) -> list[int]:
    return [pid for pid, t in eng.open.items() if t.agent_id == cost_probe.SCHEDULED_ID]


def _rows(tmp_path: Path) -> list[cost_probe.ProbeRecord]:
    return [r for r in cost_probe.probes(tmp_path, "icm-demo") if r.purpose == "cost_measurement"]


def _refusals(eng: Engine) -> list[str]:
    return [str(d["action"]) for d in eng.decisions if str(d.get("action", "")).startswith("cost_probe_scheduled")]


# ------------------------------------------------------------------------------------------------ the order
def test_a_scheduled_probe_at_a_15m_close_is_one_minimum_lot_order_checked_by_the_riskgate(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    seen = _allow(eng)
    eng.on_bar_close(CUT, tick)
    [intent] = [i for i in seen if i.agent_id == cost_probe.SCHEDULED_ID]
    assert intent.learning_probe and intent.account_mode == "demo"
    assert intent.volume_max == intent.volume_min == 0.01                # the gate sized exactly the lots sent
    assert intent.stop_atr * intent.atr_usd == pytest.approx(3.0)        # the $3 stop
    [pos] = pb.positions()
    assert pos.lots == pytest.approx(0.01)
    assert pos.sl is not None and abs(pos.open_price - pos.sl) == pytest.approx(3.0, abs=0.25)
    assert [cost_probe.is_probe(eng.open[p].agent_id) for p in _scheduled(eng)] == [True]


def test_a_scheduled_probe_carries_its_own_magic_and_a_recognisable_sched_marker(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    [pos] = pb.positions()
    assert pos.magic == eng.cfg.magic_base + cost_probe.SCHEDULED_MAGIC_OFFSET
    assert pos.comment.startswith("cm-sched-") and len(pos.comment) <= 31
    [rec] = _rows(tmp_path)
    assert rec.client_order_id.startswith("cm-sched-") and rec.basis == "scheduled_m1"
    assert rec.agent_id == cost_probe.SCHEDULED and rec.version is None


def test_a_scheduled_probe_is_journalled_and_recorded_with_its_quote_and_the_gbp_rate(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert any(d.get("action") == "cost_probe_scheduled:executed:auto" for d in eng.decisions)
    [rec] = _rows(tmp_path)
    assert rec.requested_price == pytest.approx(tick.ask if rec.side > 0 else tick.bid)
    assert rec.spread_usd == pytest.approx(0.2) and rec.fill_price is not None and rec.latency_ms is not None
    assert rec.fx_acct_per_usd is not None and rec.fx_acct_per_usd > 0       # K9: the conversion at send
    assert rec.native_stop_loss is None                                     # K10: never in the dynamic count


def test_the_real_gate_allows_a_scheduled_probe_under_the_cost_measurement_purpose(tmp_path):
    """ADR 0037 Amendment 5 (prereg K8): no p is invented; the probe asks for the cost-measurement purpose with the
    terminal's own trade mode, its hold and both probe budgets, and the real RiskGate places it at the minimum lot."""
    eng, pb, tick = _engine(tmp_path)
    seen: list[Any] = []
    real = eng.gate.check

    def check(intent: Any, *a: Any, **kw: Any) -> GateDecision:
        seen.append(intent)
        return real(intent, *a, **kw)

    eng.gate.check = check                                  # type: ignore[method-assign]
    eng.on_bar_close(CUT, tick)
    [intent] = [i for i in seen if i.agent_id == cost_probe.SCHEDULED_ID]
    assert intent.purpose == "cost_measurement" and intent.p == 0.5 and intent.terminal_trade_mode == "demo"
    assert intent.hold_s == ON.hold_s and intent.probe_headroom > 0 and intent.probe_max_loss > 0
    [pos] = pb.positions()
    assert pos.lots == pytest.approx(0.01) and pos.sl is not None and abs(pos.open_price - pos.sl) <= 3.0 + 1e-9
    assert [r.purpose for r in _rows(tmp_path)] == ["cost_measurement"]


def test_the_gate_refuses_a_scheduled_probe_when_the_terminal_does_not_read_demo_at_the_order(tmp_path, monkeypatch):
    """The engine's lock passed at the bar close; the gate is given the terminal's own read taken at the order."""
    eng, pb, tick = _engine(tmp_path)
    monkeypatch.setattr(cost_probe, "_terminal_trade_mode", lambda eng: "real")
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and not _rows(tmp_path)
    [why] = [a for a in _refusals(eng) if a.startswith("cost_probe_scheduled_refused:")]
    assert "cost_measurement_terminal_not_demo" in why


def test_an_unreadable_terminal_gives_the_gate_no_trade_mode(tmp_path):
    eng, pb, _ = _engine(tmp_path)

    def boom() -> AccountInfo:
        raise ConnectionError("bridge down")

    assert cost_probe._terminal_trade_mode(eng) == "demo"
    pb.account = boom                                        # type: ignore[method-assign]
    assert cost_probe._terminal_trade_mode(eng) is None


def test_sides_alternate_by_slot_index_never_by_outcome(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    nxt = CUT + pd.Timedelta(minutes=15)
    tick2 = _bars_until(eng, pb, nxt)
    eng._retry_closes(tick2.ts_utc)                          # the first one's 30 s are long over
    eng._flush_closed()
    eng.on_bar_close(nxt, tick2)
    sides = [r.side for r in sorted(_rows(tmp_path), key=lambda r: r.sent_utc)]
    assert sides == [cost_probe.scheduled_side(CUT), cost_probe.scheduled_side(nxt)] and sorted(sides) == [-1, 1]
    assert cost_probe.scheduled_side(CUT) == (1 if (int(CUT.timestamp()) // 900) % 2 == 0 else -1)


def test_only_one_scheduled_probe_is_open_at_a_time(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert eng.probe is not None and len(_scheduled(eng)) == 1
    nxt = CUT + pd.Timedelta(minutes=15)
    cost_probe.scheduled_step(eng, eng.probe, nxt, _bars_until(eng, pb, nxt))   # the first is still open
    assert len(_rows(tmp_path)) == 1 and len(_scheduled(eng)) == 1


def test_an_open_exploration_trade_does_not_make_the_scheduled_probe_busy(tmp_path):
    """Live 2026-10-26: exploration positions on the probe account counted as "anything else open", so every scheduled
    probe was skipped `busy` from the first exploration fill and cost measurement stopped. Exploration is learning
    too: it never makes a scheduled probe busy (a strategy position still does, below)."""
    from goldbot.engine.exploration import PREFIX
    from goldbot.engine.store import OpenTrade
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.open[987654] = OpenTrade(position_id=987654, agent_id=PREFIX + "S0", side=1, lots=0.01, entry_bar_ts=CUT,
                                 max_bars=4)
    eng.state.open_positions = 1
    eng.on_bar_close(CUT, tick)
    assert "cost_probe_scheduled_skip:busy" not in _refusals(eng) and len(_scheduled(eng)) == 1


def test_an_open_strategy_trade_still_makes_the_scheduled_probe_busy(tmp_path):
    from goldbot.engine.store import OpenTrade
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.open[987655] = OpenTrade(position_id=987655, agent_id="tsmom-g0-x", side=1, lots=0.01, entry_bar_ts=CUT,
                                 max_bars=4)
    eng.state.open_positions = 1
    eng.on_bar_close(CUT, tick)
    assert "cost_probe_scheduled_skip:busy" in _refusals(eng) and not _scheduled(eng)


def test_a_scheduled_probe_its_hold_exit_missed_is_closed_by_its_one_bar_time_barrier(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    [pid] = _scheduled(eng)
    assert eng.open[pid].max_bars == 1
    nxt = CUT + pd.Timedelta(minutes=15)
    eng.on_bar_close(nxt, _bars_until(eng, pb, nxt))        # no tick ran its hold exit: the backstop closes it
    assert pid not in eng.open


# ------------------------------------------------------------------------------------------------ the 30 s close
def _sent(tmp_path: Path, **kw: Any) -> tuple[Engine, _Terminal, Tick, int]:
    eng, pb, tick = _engine(tmp_path, **kw)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    [pid] = _scheduled(eng)
    return eng, pb, tick, pid


def test_a_scheduled_probe_is_closed_once_it_has_been_held_hold_s(tmp_path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick                                    # the quote the tick loop last saw
    t0 = eng.probe.held[pid]                                 # type: ignore[union-attr]
    eng._retry_closes(t0 + pd.Timedelta(seconds=29))
    assert pid in eng.open
    eng._retry_closes(t0 + pd.Timedelta(seconds=30))
    eng._flush_closed()
    assert pid not in eng.open and not pb.positions()
    [rec] = _rows(tmp_path)
    assert rec.exit_reason == cost_probe.HOLD_EXIT and rec.exit_utc is not None
    assert rec.exit_bid is not None and rec.exit_ask is not None             # K11: the quote the close was sent on
    assert load_closed_trades(tmp_path)[0] == []                            # never a closed trade


def _closed_with_unknown_pnl(tmp_path: Path) -> tuple[Engine, cost_probe.ProbeRecord]:
    """A scheduled probe closed by its hold, then its ledger row's P&L set to None, as `record_close` writes it when
    the exit deal is not in the broker's history yet."""
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    eng._retry_closes(eng.probe.held[pid] + pd.Timedelta(seconds=30))       # type: ignore[union-attr]
    eng._flush_closed()
    [rec] = _rows(tmp_path)
    assert rec.pnl is not None and eng.probe is not None
    with eng.probe.db.write() as conn:
        cost_probe.LEDGER.upsert(conn, rec.model_copy(update={"pnl": None}))
    return eng, rec


def test_a_closed_probe_with_no_pnl_yet_is_read_again_from_the_deal_history_not_frozen(tmp_path):
    """Review fix (code-review LOW): a closed row with pnl None no longer leaves no headroom until the risk-day reset;
    its P&L is read again from the broker's deals and written back."""
    eng, rec = _closed_with_unknown_pnl(tmp_path)
    assert eng.probe is not None
    share = eng.cfg.cost_probe_scheduled.budget_share * eng.cfg.cost_probe_budget * cost_probe._limit(eng)
    later = CUT + pd.Timedelta(minutes=15)
    assert cost_probe._scheduled_headroom(eng, eng.probe, later) == pytest.approx(share - max(0.0, -float(rec.pnl or 0)))
    assert cost_probe.budget_refusal(eng, eng.probe, later)[0] is None
    [again] = _rows(tmp_path)
    assert again.pnl == pytest.approx(rec.pnl)                               # written back to the ledger


def test_a_closed_probe_whose_pnl_stays_unknown_counts_at_its_worst_case_loss(tmp_path):
    eng, rec = _closed_with_unknown_pnl(tmp_path)
    assert eng.probe is not None
    with eng.probe.db.write() as conn:                      # no deal for this position in the history
        cost_probe.LEDGER.upsert(conn, rec.model_copy(update={"pnl": None, "position_id": 987654}))
    share = eng.cfg.cost_probe_scheduled.budget_share * eng.cfg.cost_probe_budget * cost_probe._limit(eng)
    worst = max(eng.cfg.cost_probe_max_loss, rec.stop_loss or 0.0)
    later = CUT + pd.Timedelta(minutes=15)
    assert cost_probe._scheduled_headroom(eng, eng.probe, later) == pytest.approx(share - worst)
    why, headroom = cost_probe.budget_refusal(eng, eng.probe, later)
    assert why is None and headroom == pytest.approx(eng.cfg.cost_probe_budget * cost_probe._limit(eng) - worst)


def test_the_hold_runs_on_the_host_clock_on_a_live_clock_engine(tmp_path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng.cfg.live_clock = True
    host = eng.probe.held[pid] + pd.Timedelta(seconds=31)    # type: ignore[union-attr]
    eng.clock = lambda: host
    eng._retry_closes(tick.ts_utc)                           # tick time has not moved; the host clock has
    assert pid not in eng.open


def test_the_hold_exit_is_never_gated_by_halted_entries_or_probes_switched_off(tmp_path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng.cfg.approval_mode = "propose"
    eng._auto_paused = (CUT, "order_raised")
    eng.probe = None                                         # probes switched off after the send
    eng._retry_closes(tick.ts_utc + pd.Timedelta(seconds=60))
    assert pid not in eng.open and not pb.positions()


def test_an_open_scheduled_probe_past_its_hold_is_closed_after_a_restart(tmp_path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng2, _, _ = _engine(tmp_path, pb=pb)
    assert eng2.open[pid].agent_id == cost_probe.SCHEDULED_ID
    eng2._retry_closes(tick.ts_utc + pd.Timedelta(seconds=45))
    eng2._flush_closed()
    assert not pb.positions()
    assert [r.exit_reason for r in _rows(tmp_path)] == [cost_probe.HOLD_EXIT]


def test_a_scheduled_probe_found_without_its_order_row_is_adopted_as_scheduled_and_closed(tmp_path):
    eng, pb, tick, pid = _sent(tmp_path)
    (tmp_path / "orders_icm-demo.json").unlink()
    (tmp_path / "engine_state_icm-demo.json").unlink(missing_ok=True)
    eng2, _, _ = _engine(tmp_path, pb=pb)
    eng2.open.clear()
    eng2._reconcile(tick.ts_utc)
    assert eng2.open[pid].agent_id == cost_probe.SCHEDULED_ID
    eng2._retry_closes(tick.ts_utc + pd.Timedelta(seconds=45))
    assert not pb.positions()


# ------------------------------------------------------------------------------------------------ refusals
def test_no_scheduled_probe_on_a_terminal_that_is_not_demo(tmp_path):
    eng, pb, tick = _engine(tmp_path, trade_mode="real")
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and not _rows(tmp_path)


def test_no_scheduled_probe_outside_auto_mode(tmp_path):
    eng, pb, tick = _engine(tmp_path, approval="propose")
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and "cost_probe_scheduled_skip:not_auto" in _refusals(eng)


def test_scheduled_probes_off_by_default_send_nothing(tmp_path):
    eng, pb, tick = _engine(tmp_path, scheduled=acc_mod.ScheduledProbe())
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and not _refusals(eng)


# ------------------------------------------------------------------------------------- no shadow book on the host
def test_an_engine_without_the_shadow_book_sends_scheduled_probes_through_the_gate(tmp_path):
    eng, pb, tick = _engine(tmp_path, shadow_host=False, probe_only=True)
    assert eng.shadow is None and eng.probe is not None
    seen = _allow(eng)
    eng.on_bar_close(CUT, tick)
    [intent] = [i for i in seen if i.agent_id == cost_probe.SCHEDULED_ID]
    assert intent.learning_probe and intent.volume_min == 0.01
    [pos] = pb.positions()
    assert pos.lots == pytest.approx(0.01)
    assert len(_scheduled(eng)) == 1 and len(_rows(tmp_path)) == 1
    assert not [d for d in eng.decisions if str(d.get("action", "")).endswith("_error")]


def test_without_the_shadow_book_mirror_probes_are_skipped_cleanly_and_scheduled_ones_still_close(tmp_path):
    eng, pb, tick = _engine(tmp_path, shadow_host=False, shadow=True, probe_only=True)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert [eng.open[p].agent_id for p in eng.open] == [cost_probe.SCHEDULED_ID]   # no mirror probe, no error
    assert not [d for d in eng.decisions if str(d.get("action", "")).endswith("_error")]
    cost_probe.hold_exits(eng, CUT + pd.Timedelta(seconds=eng.cfg.cost_probe_scheduled.hold_s + 1))
    assert not pb.positions()


def test_a_shared_account_without_the_shadow_book_gets_no_scheduled_probes(tmp_path):
    eng, pb, tick = _engine(tmp_path, shadow_host=False, probe_only=False)
    assert eng.probe is None
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and not _refusals(eng)


def test_without_the_shadow_book_a_scheduled_probe_open_at_a_restart_is_closed_by_its_hold_exit(tmp_path):
    eng, pb, tick, pid = _sent(tmp_path, shadow_host=False, probe_only=True)
    eng2, _, _ = _engine(tmp_path, pb=pb, shadow_host=False, probe_only=True)
    assert eng2.probe is not None and pid not in eng2.probe.held        # the hold clock falls back to opened_utc
    assert eng2.open[pid].agent_id == cost_probe.SCHEDULED_ID
    cost_probe.hold_exits(eng2, tick.ts_utc + pd.Timedelta(seconds=eng2.cfg.cost_probe_scheduled.hold_s + 15))
    eng2._flush_closed()
    assert not pb.positions()
    assert [r.exit_reason for r in _rows(tmp_path)] == [cost_probe.HOLD_EXIT]


def test_mirror_probes_alone_still_need_the_shadow_book(tmp_path):
    eng, _, _ = _engine(tmp_path, shadow_host=False, scheduled=acc_mod.ScheduledProbe())
    assert eng.shadow is None and eng.probe is None


def test_an_engine_without_the_shadow_book_and_probes_off_builds_no_probe(tmp_path):
    eng, pb, tick = _engine(tmp_path, shadow_host=False, probe=False, scheduled=acc_mod.ScheduledProbe())
    assert eng.probe is None
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and not _refusals(eng)


def _loss(eng: Engine, tick: Tick, share: float, *, scheduled: bool) -> None:
    """A closed probe today that lost `share` of the day's probe budget."""
    assert eng.probe is not None
    eng._refresh_account(tick)
    pnl = -share * eng.cfg.cost_probe_budget * cost_probe._limit(eng)
    with eng.probe.db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id=f"{'cm-sched' if scheduled else 'cp'}-0-{pnl}", account_id="icm-demo",
            agent_id=cost_probe.SCHEDULED if scheduled else "x", side=1, lots=0.01, sent_utc=tick.ts_utc,
            exit_utc=tick.ts_utc,
            pnl=pnl, purpose="cost_measurement" if scheduled else "cost_probe"))


def test_no_scheduled_probe_past_the_probe_budget(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    _loss(eng, tick, 1.0, scheduled=False)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and "cost_probe_scheduled_skip:budget:probe_loss_reached" in _refusals(eng)


def test_scheduled_probes_take_at_most_their_share_of_the_probe_budget(tmp_path):
    eng, pb, tick = _engine(tmp_path)
    _allow(eng)
    _loss(eng, tick, 0.5, scheduled=True)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and "cost_probe_scheduled_skip:scheduled_budget" in _refusals(eng)


@pytest.mark.parametrize("ts, why", [
    ("2025-03-04 21:45", "blackout"), ("2025-03-04 23:00", "blackout"), ("2025-03-04 23:15", None),   # EST: UTC-5
    ("2025-03-04 21:30", None),
    ("2025-03-07 19:15", None), ("2025-03-07 19:45", "friday_cut"),       # Athens UTC+2: the 21:30 cut is 19:30 UTC
    ("2025-03-08 12:00", "weekend"), ("2025-03-09 23:00", "weekend"), ("2025-03-04 20:00", None)])
def test_the_schedule_skips_the_rollover_blackout_the_friday_cut_and_the_weekend(tmp_path, ts, why):
    eng, _, _ = _engine(tmp_path)
    assert cost_probe.schedule_skip(eng, pd.Timestamp(ts, tz="UTC")) == why


@pytest.mark.parametrize("ts", [
    "2025-07-08 21:15", "2025-03-04 22:15", "2026-11-03 22:15",             # 17:15 New York (the daily break), EDT and EST
    "2025-07-08 22:00", "2025-03-04 23:00", "2026-11-03 23:00"])            # 18:00 New York: the reopen minute
def test_the_blackout_follows_new_york_so_the_break_and_the_reopen_are_blacked_out_in_summer_and_winter(tmp_path, ts):
    """Review fix (code-review MEDIUM): the window is 16:45-18:15 America/New_York, so it moves with US DST (after
    2026-11-01 a fixed UTC window left 22:15-22:45 UTC in the break and 23:00 UTC on the reopen)."""
    eng, _, _ = _engine(tmp_path)
    assert cost_probe.schedule_skip(eng, pd.Timestamp(ts, tz="UTC")) == "blackout"


@pytest.mark.parametrize("ts", ["2025-07-08 20:30", "2025-07-08 22:15", "2026-11-03 21:30", "2026-11-03 23:15"])
def test_outside_the_new_york_window_slots_are_sent_in_summer_and_winter(tmp_path, ts):
    eng, _, _ = _engine(tmp_path)
    assert cost_probe.schedule_skip(eng, pd.Timestamp(ts, tz="UTC")) is None


def test_no_scheduled_probe_in_the_configured_blackout(tmp_path):
    eng, pb, tick = _engine(tmp_path, scheduled=acc_mod.ScheduledProbe(enabled=True, blackout=("14:55", "15:05")))
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert not pb.positions() and "cost_probe_scheduled_skip:blackout" in _refusals(eng)


# ------------------------------------------------------------------------------------------------ K10 exclusion
def test_scheduled_rows_never_change_the_dynamic_probe_count(tmp_path):
    eng, _, _ = _engine(tmp_path)
    assert eng.probe is not None
    eng.probe.dynamic = True
    with eng.probe.db.write() as conn:
        for i in range(6):                                  # mirrored probes with a wide native stop
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=f"cp-{i}", account_id="icm-demo", agent_id="x", side=1, lots=0.01,
                sent_utc=CUT - pd.Timedelta(hours=1 + i), native_stop_loss=9.0))
    before = cost_probe.probe_limits(eng, eng.probe)
    with eng.probe.db.write() as conn:
        for i in range(30):                                 # many cheap scheduled probes, later than those
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=f"cm-sched-{i}", account_id="icm-demo", agent_id=cost_probe.SCHEDULED, side=1,
                lots=0.01, sent_utc=CUT + pd.Timedelta(minutes=i), native_stop_loss=0.5,
                purpose="cost_measurement"))
    assert cost_probe.probe_limits(eng, eng.probe) == before


def test_an_open_scheduled_probe_takes_no_mirror_slot(tmp_path):
    eng, pb, tick = _engine(tmp_path, shadow=True)
    _allow(eng)
    eng.on_bar_close(CUT, tick)
    assert len(_scheduled(eng)) == 1
    assert len(eng.open) == 2                                # the mirrored challenger entry still went out
    assert cost_probe._mirror_probes_open(eng) == [p for p in eng.open if p not in _scheduled(eng)]


def test_the_rotation_never_resumes_from_a_scheduled_row(tmp_path):
    eng, _, tick = _engine(tmp_path)
    _loss(eng, tick, 0.1, scheduled=True)
    eng2, _, _ = _engine(tmp_path)
    assert eng2.probe is not None and eng2.probe.last_agent is None


# ------------------------------------------------------------------------------------------------ settings
def test_the_scheduled_settings_default_off_with_the_preregistered_terms():
    s = acc_mod.CostProbe().scheduled
    assert (s.enabled, s.hold_s, s.stop_usd, s.blackout, s.blackout_tz, s.budget_share) == (
        False, 30, 3.0, ("16:45", "18:15"), "America/New_York", 0.5)


@pytest.mark.parametrize("kw", [{"hold_s": 61}, {"stop_usd": 3.5}, {"blackout": ("25:00", "22:15")}, {"blackout_tz": "Mars/Olympus"},
                                {"budget_share": 0.6}])
def test_scheduled_settings_outside_the_preregistered_bounds_are_refused(kw):
    with pytest.raises(ValidationError):
        acc_mod.ScheduledProbe(**kw)


def test_the_schedule_keeps_every_15m_close_by_default():
    assert acc_mod.ScheduledProbe().every_min == 15
    closes = pd.date_range("2025-03-04 00:00", periods=96, freq="15min", tz="UTC")
    assert all(cost_probe.scheduled_slot(ts, 15) for ts in closes)


@pytest.mark.parametrize("every_min", [45, 90, 10])
def test_a_cadence_that_is_not_15_30_60_or_120_minutes_is_refused(every_min):
    with pytest.raises(ValidationError):
        acc_mod.ScheduledProbe(every_min=every_min)


@pytest.mark.parametrize("every_min", [30, 60, 120])
def test_a_coarser_cadence_picks_exactly_one_15m_close_per_window(every_min):
    closes = pd.date_range("2025-03-04 00:00", periods=96 * 2, freq="15min", tz="UTC")
    picked = [ts for ts in closes if cost_probe.scheduled_slot(ts, every_min)]
    assert len(picked) == 2 * 1440 // every_min
    assert len({ts.floor(f"{every_min}min") for ts in picked}) == len(picked)


@pytest.mark.parametrize("every_min", [30, 60, 120])
def test_a_coarser_cadence_still_alternates_sides_and_flips_each_window_of_the_day_daily(every_min):
    """Cost of record 2026-10-31 section 6 (ICM: "24 a day M1 (one per open hour)"): the prereg's side rule (+1 on an
    even 15m slot index) is kept; the slot within each window rotates, so sides alternate window by window and the
    same window of the next day takes the other side (no hour of day is always a buy)."""
    day1 = pd.date_range("2025-03-04 00:00", periods=96, freq="15min", tz="UTC")
    s1 = [cost_probe.scheduled_side(ts) for ts in day1 if cost_probe.scheduled_slot(ts, every_min)]
    s2 = [cost_probe.scheduled_side(ts + pd.Timedelta(days=1)) for ts in day1
          if cost_probe.scheduled_slot(ts + pd.Timedelta(days=1), every_min)]
    assert all(a != b for a, b in zip(s1, s1[1:], strict=False))
    assert all(a == -b for a, b in zip(s1, s2, strict=True))


def test_on_an_hourly_cadence_a_close_that_is_not_the_hours_slot_sends_and_journals_nothing(tmp_path):
    eng, pb, tick = _engine(tmp_path, scheduled=acc_mod.ScheduledProbe(enabled=True, every_min=60))
    _allow(eng)
    quarter = pd.date_range(CUT, periods=4, freq="15min")
    off = next(ts for ts in quarter if not cost_probe.scheduled_slot(ts, 60))
    on = next(ts for ts in quarter if cost_probe.scheduled_slot(ts, 60))
    assert off < on                                          # CUT (20:00) is not this hour's slot
    eng.on_bar_close(off, _bars_until(eng, pb, off))
    assert not _rows(tmp_path) and not _refusals(eng)
    eng.on_bar_close(on, _bars_until(eng, pb, on))
    assert len(_rows(tmp_path)) == 1


def test_icm_probe_runs_hourly_probes_and_vantage_keeps_every_15m_close():
    """Demo trim (cost of record 2026-10-31 section 6): ICM 24 a day; Vantage kept at 40 a day until its exit legs are
    recorded and it has 200 strategy fills per session (then every_min 120, 12 a day)."""
    accts = acc_mod.load_accounts(logins=False)
    assert accts["icm-probe"].cost_probe.scheduled.every_min == 60
    assert accts["vantage-demo"].cost_probe.scheduled.every_min == 15


def test_scheduled_probes_need_the_cost_probe_on():
    with pytest.raises(ValidationError):
        acc_mod.CostProbe(enabled=False, scheduled=acc_mod.ScheduledProbe(enabled=True))


def test_no_strategy_family_shares_the_scheduled_probe_magic():
    from goldbot.execution.broker import SMOKE_MAGIC_OFFSET
    from goldbot.specialists import SPECIALISTS
    assert cost_probe.SCHEDULED_MAGIC_OFFSET not in (cost_probe.PROBE_MAGIC_OFFSET, SMOKE_MAGIC_OFFSET)
    assert not {f for f in SPECIALISTS if zlib.crc32(f.encode()) % 100 == cost_probe.SCHEDULED_MAGIC_OFFSET}


# ------------------------------------------------------------------------------------------------ probe_only volume (E)
def test_on_a_probe_only_account_a_mirror_probe_keeps_its_agents_own_time_exit(tmp_path):
    """Architect trace 2026-10-25: a probe holds its strategy's own time barrier (the former 4-bar cap is gone)."""
    eng, pb, tick = _engine(tmp_path, shadow=True, probe_only=True, scheduled=acc_mod.ScheduledProbe())
    eng.on_bar_close(CUT, tick)
    [tr] = eng.open.values()
    assert tr.max_bars == 17                                 # 16 + 1 bars of 15m, as `_base_bars` counts
    assert next(iter(eng._orders.values())).max_bars == 17   # the order row too: a restart keeps it


def test_on_a_shared_account_a_mirror_probe_keeps_its_agents_time_exit(tmp_path):
    eng, pb, tick = _engine(tmp_path, shadow=True, scheduled=acc_mod.ScheduledProbe())
    eng.on_bar_close(CUT, tick)
    [tr] = eng.open.values()
    assert tr.max_bars == 17


def test_a_probe_only_account_allows_48_below_threshold_probes_a_day_others_12(tmp_path):
    assert (cost_probe.untaken_cap(True), cost_probe.untaken_cap(False)) == (48, 12)
    for probe_only, expected in ((True, None), (False, "daily_cap")):
        eng, _, _ = _engine(tmp_path / str(probe_only), probe_only=probe_only)
        assert eng.probe is not None
        with eng.probe.db.write() as conn:
            for i in range(12):
                cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                    client_order_id=f"cp-u{i}", account_id="icm-demo", agent_id="x", side=1, lots=0.01,
                    sent_utc=CUT, taken=False))
        assert cost_probe.untaken_refusal(eng, eng.probe, CUT, headroom=1e9) == expected
