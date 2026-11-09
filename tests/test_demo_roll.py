"""ADR 0049 D3 / T1: the demo epoch roll (amends ADR 0043 R1 on demo `probe_only` hosts only).

"It runs at the first 00:00 UTC risk-day roll after the pause, once the host is flat except P0 ... Mark reset: mark =
equity, stage NORMAL, automated on the fenced path (T1). It never touches `day_start_equity` or `week_start_equity`,
and never touches another engine." T1: "refused unless all of these hold: config `mode: demo` and `probe_only` and an
`approval_mode` that is not confirm; the broker-reported trade mode is DEMO when the roll is called; the drawdown is
>= 8% and < 12%; the host is flat except P0; it is a 00:00 UTC roll." D3: "-12% / HALTED: no roll. The ADR 0043 K2
re-arm path stays as it is." Real-money accounts keep the owner's TOTP re-arm unchanged.
"""
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig, demo_roll, exploration
from goldbot.engine.cost_probe import SCHEDULED_ID, SCHEDULED_MAGIC_OFFSET
from goldbot.engine.limits_apply import EngineLimits
from goldbot.engine.store import OpenTrade, PendingClose
from goldbot.execution.broker import AccountInfo, OrderIntent
from goldbot.risk.gate import RiskGate, Stage
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus
from tests.test_hwm_confirmation import T0, _Terminal, _tick

H = pd.Timedelta(hours=1)
NEXT_DAY = pd.Timestamp("2025-03-06 00:00:30", tz="UTC")       # Thursday: same risk week as T0 (Wednesday)


class _DemoTerminal(_Terminal):
    """A terminal that reports its own trade mode (MT5 ACCOUNT_TRADE_MODE), demo unless a test says otherwise."""

    trade_mode: str | None = "demo"

    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": self.trade_mode})


def _engine(tmp_path: Path, *, mode: str = "demo", probe_only: bool = True, approval: str = "auto",
            trade_mode: str | None = "demo", account_id: str = "icm-probe-51234567",
            bus: ApprovalBus | None = None) -> tuple[Engine, _DemoTerminal]:
    pb = _DemoTerminal(equity=10_000)
    pb.trade_mode = trade_mode
    pb.on_tick(_tick(T0))
    eng = _start(tmp_path, pb, mode=mode, probe_only=probe_only, approval=approval, account_id=account_id, bus=bus)
    eng._refresh_account(_tick(T0))                         # mark 10,000; risk day and week rolled at T0
    assert eng.state.balance_closed_hwm == 10_000
    return eng, pb


def _start(state: Path, pb: _DemoTerminal, *, mode: str = "demo", probe_only: bool = True, approval: str = "auto",
           account_id: str = "icm-probe-51234567", bus: ApprovalBus | None = None) -> Engine:
    """An engine on `state` from the account's own config (also a restart: the risk file is loaded)."""
    return Engine(EngineConfig(account_id=account_id, broker_name="icm", mode=mode, probe_only=probe_only,
                               approval_mode=approval, state_dir=str(state), owner_user_id=111, halt_checks=False),
                  pb, [], {}, ApprovalCenter({111}, bus=bus))


def _balance(monkeypatch: Any, pb: _DemoTerminal, balance: float, equity: float | None = None) -> None:
    """Every account read returns this balance and equity (equity = balance: a flat book, or P0 at no floating P&L)."""
    real = pb.account

    def read() -> AccountInfo:
        return real().model_copy(update={"balance": balance, "equity": balance if equity is None else equity})
    monkeypatch.setattr(pb, "account", read)


def _unchanged(eng: Engine, reason: str) -> None:
    """A refused roll: the mark, the stage and the epoch as they were, and exactly one `rotation_refused` row."""
    st = eng.state
    assert st.balance_closed_hwm == 10_000 and Stage(st.stage) == Stage.SIZE_DOWN and eng._epoch == 0
    assert not _rows(eng, demo_roll.ACTION)
    assert [r["reason"] for r in _rows(eng, demo_roll.REFUSED)] == [reason]


def _at(eng: Engine, t: pd.Timestamp) -> None:
    eng._refresh_account(_tick(t))


def _rows(eng: Engine, action: str) -> list[dict[str, Any]]:
    return [d for d in eng.decisions if d.get("action") == action]


def _paused_at_9pct(eng: Engine, pb: _DemoTerminal, monkeypatch: Any) -> None:
    _balance(monkeypatch, pb, 9_100.0)
    _at(eng, T0 + H)
    assert Stage(eng.state.stage) == Stage.SIZE_DOWN and exploration.paused(eng, T0 + H)


def _notices(state: Path) -> list[str]:
    path = state / "agent_runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["detail"] for line in path.read_text().splitlines() if line.strip()]


def _open(pb: _DemoTerminal, magic: int, cid: str = "x") -> int:
    r = pb.place_order(OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=0.01, sl=2390.0, tp=2410.0,
                                   magic=magic, comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


# ---------------------------------------------------------------------------------------------- the roll
def test_a_demo_probe_host_paused_at_minus_8pct_rolls_at_the_next_00utc_risk_day_roll_and_resumes(tmp_path,
                                                                                                 monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    st = eng.state
    assert st.balance_closed_hwm == 9_100 and Stage(st.stage) == Stage.NORMAL
    assert not exploration.paused(eng, NEXT_DAY)                                    # exploration resumes
    [row] = _rows(eng, demo_roll.ACTION)
    assert (row["epoch_from"], row["epoch_to"]) == (0, 1)
    assert (row["mark_from"], row["mark_to"], row["balance"], row["equity"]) == (10_000, 9_100, 9_100, 9_100)
    assert row["drawdown"] == pytest.approx(0.09) and row["epoch_loss"] == pytest.approx(900.0)
    # the journal carries amounts, never an identifier (public repo: no login, no account id)
    assert set(row) == {"ts", "action", "by", "epoch_from", "epoch_to", "epoch_started", "mark_from", "mark_to",
                        "balance", "equity", "drawdown", "epoch_loss"}


def test_the_rolled_mark_is_the_larger_of_balance_and_equity_so_an_open_p0_loss_is_not_banked(tmp_path,
                                                                                            monkeypatch):
    """Trading-safety F3 on PR #290: mark = max(balance, equity); both are journalled."""
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    _balance(monkeypatch, pb, 9_200.0, equity=9_100.0)      # P0 open at a 100 floating loss
    _at(eng, NEXT_DAY)
    [row] = _rows(eng, demo_roll.ACTION)
    assert eng.state.balance_closed_hwm == 9_200 and row["mark_to"] == 9_200
    assert (row["balance"], row["equity"]) == (9_200, 9_100)
    assert eng._hwm.mark == 9_200


def test_a_roll_whose_risk_state_cannot_be_saved_sends_no_owner_notice(tmp_path, monkeypatch):
    """Trading-safety F4 on PR #290: the Telegram notice goes only after a successful save."""
    state = tmp_path / "state"
    bus = ApprovalBus(state)
    eng, pb = _engine(state, bus=bus)
    _paused_at_9pct(eng, pb, monkeypatch)

    def full_disk() -> None:
        raise OSError("no space left on device")
    monkeypatch.setattr(eng, "_save_risk_state", full_disk)
    _at(eng, NEXT_DAY)                                       # never raises
    assert _rows(eng, demo_roll.ACTION) and eng.state.balance_closed_hwm == 9_100   # in memory, retried on disk
    assert _notices(state) == []


def test_the_roll_never_touches_the_day_or_week_start_so_this_weeks_loss_is_not_forgiven(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    st = eng.state
    assert st.day_start_equity == 9_100                    # the ordinary risk-day roll (gate.new_day), not the roll's
    assert st.week_start_equity == 10_000                  # same risk week: the 900 lost stays in the weekly cap
    assert _rows(eng, demo_roll.ACTION)


def test_inside_the_same_risk_day_a_paused_host_is_not_rolled(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    for h in (2, 6, 13):                                   # Wednesday 12:01 .. 23:01: no 00:00 UTC boundary
        _at(eng, T0 + h * H)
    assert eng.state.balance_closed_hwm == 10_000 and Stage(eng.state.stage) == Stage.SIZE_DOWN
    assert not _rows(eng, demo_roll.ACTION) and not _rows(eng, demo_roll.REFUSED)


def test_a_host_above_the_pause_is_left_alone_at_the_day_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _balance(monkeypatch, pb, 9_300.0)                      # -7%: warned, not paused
    _at(eng, T0 + H)
    _at(eng, NEXT_DAY)
    assert eng.state.balance_closed_hwm == 10_000
    assert not _rows(eng, demo_roll.ACTION) and not _rows(eng, demo_roll.REFUSED)


def test_every_roll_is_sent_to_the_owner_on_telegram_without_the_account_login(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bus = ApprovalBus(state)
    eng, pb = _engine(state, bus=bus)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    [text] = _notices(state)
    assert "epoch 1" in text and "10,000.00" in text and "9,100.00" in text and "9.0%" in text
    assert "51234567" not in text and "icm-probe-…67" in text                     # entry_text.account_name masking
    assert "?" not in text                                                          # information, never an ask


def test_the_epoch_counts_up_across_rolls_and_survives_a_restart(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    again = Engine(eng.cfg.model_copy(), pb, [], {}, ApprovalCenter({111}))
    assert again._epoch == 1 and again._epoch_utc == NEXT_DAY
    _balance(monkeypatch, pb, 8_300.0)                      # -8.8% from the new mark
    _at(again, NEXT_DAY + 10 * H)
    _at(again, NEXT_DAY + pd.Timedelta(days=1))
    [row] = _rows(again, demo_roll.ACTION)
    assert (row["epoch_from"], row["epoch_to"], row["mark_from"], row["mark_to"]) == (1, 2, 9_100, 8_300)
    assert row["epoch_started"] == NEXT_DAY.isoformat()


def test_the_rolled_mark_is_anchored_so_a_later_deposit_still_moves_it_by_the_deposit(tmp_path, monkeypatch):
    """ADR 0042 D3 after a roll: the deal-history anchor is the roll, so a top-up after it is a capital flow."""
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    assert eng._hwm.mark == 9_100 and eng._hwm.mark_utc == NEXT_DAY


# ---------------------------------------------------------------------------------------------- exits and P0
def test_an_open_scheduled_cost_probe_does_not_block_the_roll_and_keeps_its_stop_and_target(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, eng.cfg.magic_base + SCHEDULED_MAGIC_OFFSET, "cm-sched-1")
    before = pb.positions()
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    assert _rows(eng, demo_roll.ACTION)
    assert [(p.position_id, p.sl, p.tp) for p in pb.positions()] == [(pid, before[0].sl, before[0].tp)]


@pytest.mark.parametrize("magic_offset", [0, 50, 97])          # a strategy, an exploration arm, a mirror probe
def test_any_open_position_other_than_p0_refuses_the_roll_and_is_left_to_its_own_exit(tmp_path, monkeypatch,
                                                                                    magic_offset):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, eng.cfg.magic_base + magic_offset)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    assert eng.state.balance_closed_hwm == 10_000 and not _rows(eng, demo_roll.ACTION)
    [row] = _rows(eng, demo_roll.REFUSED)
    assert row["reason"] == "not_flat"
    assert [p.position_id for p in pb.positions()] == [pid]


def test_an_engine_open_trade_other_than_p0_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    eng.open[7] = OpenTrade(position_id=7, agent_id="explore:S0", side=1, lots=0.01, entry_bar_ts=T0, max_bars=5)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    assert [r["reason"] for r in _rows(eng, demo_roll.REFUSED)] == ["not_flat"]


def test_an_engine_open_trade_of_p0_alone_does_not_refuse_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, eng.cfg.magic_base + SCHEDULED_MAGIC_OFFSET, "cm-sched-1")
    eng.open[pid] = OpenTrade(position_id=pid, agent_id=SCHEDULED_ID, side=1, lots=0.01, entry_bar_ts=T0, max_bars=5)
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    assert _rows(eng, demo_roll.ACTION) and pid in eng.open


# ---------------------------------------------------------------------------------------------- refusals (T1)
@pytest.mark.parametrize(("trade_mode", "reason"), [("real", "terminal_not_demo"), ("contest", "terminal_not_demo"),
                                                    (None, "terminal_unknown")])
def test_a_terminal_that_does_not_report_demo_refuses_the_roll(tmp_path, monkeypatch, trade_mode, reason):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    pb.trade_mode = trade_mode
    _at(eng, NEXT_DAY)
    assert eng.state.balance_closed_hwm == 10_000 and Stage(eng.state.stage) == Stage.SIZE_DOWN
    assert [r["reason"] for r in _rows(eng, demo_roll.REFUSED)] == [reason]


def test_a_demo_probe_host_in_propose_mode_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, approval="propose")
    _paused_at_9pct(eng, pb, monkeypatch)
    _at(eng, NEXT_DAY)
    assert eng.state.balance_closed_hwm == 10_000
    assert [r["reason"] for r in _rows(eng, demo_roll.REFUSED)] == ["approval_not_auto"]


def test_an_unreadable_account_at_the_roll_refuses_it_and_never_raises(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    eng.state.equity = 9_100.0
    monkeypatch.setattr(pb, "positions", lambda: (_ for _ in ()).throw(RuntimeError("positions_get failed")))
    demo_roll.at_day_roll(eng, NEXT_DAY)
    assert eng.state.balance_closed_hwm == 10_000
    assert [r["reason"] for r in _rows(eng, demo_roll.REFUSED)] == ["positions_unreadable"]


def test_a_halted_demo_probe_host_is_never_rolled_and_keeps_the_owner_rearm(tmp_path, monkeypatch):
    """ADR 0049 D3: "-12% / HALTED: no roll. A roll is never available at or after a halt"."""
    eng, pb = _engine(tmp_path)
    _balance(monkeypatch, pb, 8_700.0)                      # -13%
    for s in (0, 30, 60):                                   # ADR 0016: the 12% read confirmed
        _at(eng, T0 + H + pd.Timedelta(seconds=s))
    assert Stage(eng.state.stage) == Stage.HALTED
    _at(eng, NEXT_DAY)
    assert Stage(eng.state.stage) == Stage.HALTED and eng.state.balance_closed_hwm == 10_000
    assert not _rows(eng, demo_roll.ACTION)


def test_a_drawdown_at_the_halt_line_waiting_for_confirmation_is_never_rolled(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    eng.state.equity = 8_700.0                              # -13%, the stage machine not yet run on it
    demo_roll.at_day_roll(eng, NEXT_DAY)
    assert eng.state.balance_closed_hwm == 10_000
    assert [r["reason"] for r in _rows(eng, demo_roll.REFUSED)] == ["drawdown_at_halt"]


# trading-safety F2 on PR #290: one test per remaining refusal; each leaves the mark, stage and epoch as they were
def test_an_order_in_flight_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    eng.state.unconfirmed_order = True
    demo_roll.at_day_roll(eng, NEXT_DAY)
    _unchanged(eng, "order_in_flight")


def test_a_close_waiting_to_be_recorded_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    trade = OpenTrade(position_id=7, agent_id="explore:S0", side=1, lots=0.01, entry_bar_ts=T0, max_bars=5)
    eng._closes = [PendingClose(position_id=7, trade=trade, reason="stop", at=T0 + H)]
    demo_roll.at_day_roll(eng, NEXT_DAY)
    _unchanged(eng, "closes_pending")


def test_a_fresh_read_back_above_the_pause_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    _balance(monkeypatch, pb, 9_600.0)                      # the read at the call: -4%, out of the 8-12% band
    demo_roll.at_day_roll(eng, NEXT_DAY)
    _unchanged(eng, "drawdown_moved")


def test_an_unknown_drawdown_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    eng.state.equity = float("nan")
    demo_roll.at_day_roll(eng, NEXT_DAY)
    _unchanged(eng, "drawdown_unknown")


def test_an_unexpected_error_in_the_checks_refuses_the_roll_and_never_raises(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)

    def broken(*a: Any) -> Any:
        raise RuntimeError("unexpected")
    monkeypatch.setattr(demo_roll, "_refusal", broken)
    _at(eng, NEXT_DAY)                                       # through the risk-day roll itself
    _unchanged(eng, "error_RuntimeError")


@pytest.mark.parametrize("halt", ["owner_halt", "supervisor_halt", "drift_halt"])
def test_a_standing_entry_halt_refuses_the_roll(tmp_path, monkeypatch, halt):
    """Trading-safety F5 on PR #290: no roll while the owner's, the supervisor's or the drift halt stands."""
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    setattr(eng.state, halt, True)
    demo_roll.at_day_roll(eng, NEXT_DAY)
    _unchanged(eng, halt)


def test_the_owners_halt_on_the_bus_refuses_the_roll(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bus = ApprovalBus(state)
    eng, pb = _engine(state, bus=bus)
    _paused_at_9pct(eng, pb, monkeypatch)
    bus.set_halt(True, by="telegram")
    demo_roll.at_day_roll(eng, NEXT_DAY)
    _unchanged(eng, "owner_halt")


# trading-safety F1 on PR #290: the effective approval mode, after a restart
def test_after_a_restart_the_owners_mode_propose_refuses_the_roll(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bus = ApprovalBus(state)
    eng, pb = _engine(state, bus=bus)
    _paused_at_9pct(eng, pb, monkeypatch)
    bus.set_mode("propose", by="dashboard")
    again = _start(state, pb, bus=bus)                      # the account itself is configured auto
    _at(again, NEXT_DAY)
    _unchanged(again, "approval_not_auto")


def test_after_a_restart_the_propose_only_window_of_an_owner_rearm_refuses_the_roll(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    eng._propose_only_until = NEXT_DAY + pd.Timedelta(days=30)
    eng._save_risk_state()
    again = _start(tmp_path, pb)
    assert again._propose_only_until == NEXT_DAY + pd.Timedelta(days=30)
    _at(again, NEXT_DAY)
    _unchanged(again, "propose_only")


def test_after_a_restart_the_auto_lock_refusing_on_a_real_terminal_refuses_the_roll(tmp_path, monkeypatch):
    """The lock (`auto_entry.locked`) refuses auto on a terminal that is not demo; the roll names the terminal."""
    eng, pb = _engine(tmp_path)
    _paused_at_9pct(eng, pb, monkeypatch)
    pb.trade_mode = "real"
    again = _start(tmp_path, pb)
    _at(again, NEXT_DAY)
    assert again.cfg.approval_mode == "propose"             # the engine's own lock refused auto too
    _unchanged(again, "terminal_not_demo")


# ---------------------------------------------------------------------------------------------- real money untouched
@pytest.mark.parametrize(("mode", "probe_only", "approval", "trade_mode"), [
    ("live", False, "propose", "real"),        # a real-money account
    ("demo", False, "auto", "demo"),           # a demo strategy account (not a probe host)
    ("paper", False, "propose", None),         # the paper broker
])
def test_any_account_other_than_a_demo_probe_host_never_rolls_and_journals_nothing(tmp_path, monkeypatch, mode,
                                                                                 probe_only, approval, trade_mode):
    eng, pb = _engine(tmp_path, mode=mode, probe_only=probe_only, approval=approval, trade_mode=trade_mode,
                      account_id="icm-live")
    _balance(monkeypatch, pb, 9_100.0)
    _at(eng, T0 + H)
    _at(eng, NEXT_DAY)
    st = eng.state
    assert st.balance_closed_hwm == 10_000 and Stage(st.stage) == Stage.SIZE_DOWN
    assert not _rows(eng, demo_roll.ACTION) and not _rows(eng, demo_roll.REFUSED)
    assert eng._epoch == 0


def test_the_roll_reads_nothing_from_the_broker_on_a_live_account(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, mode="live", probe_only=False, approval="propose", trade_mode="real")
    eng.state.equity = 9_100.0

    def boom(*a: Any) -> Any:
        raise AssertionError("the demo roll read the broker of a live account")
    monkeypatch.setattr(pb, "account", boom)
    monkeypatch.setattr(pb, "positions", boom)
    demo_roll.at_day_roll(eng, NEXT_DAY)
    assert not _rows(eng, demo_roll.ACTION) and not _rows(eng, demo_roll.REFUSED)
    assert eng.state.balance_closed_hwm == 10_000


# The owner's TOTP re-arm (dashboard -> control.json rearm id -> Engine._poll_rearm -> RiskGate.rearm) is the only
# mark-lowering path on a real-money account (ADR 0043 R1, ADR 0049 D3 "the ADR 0043 K2 re-arm path stays as it is").
# Its source is pinned: changing it needs a trading-safety review, and the pin is updated in that same PR.
REARM_PATH_SHA256 = {
    # trading-safety R1 finding 4 (fix/account-recheck-1109): the re-arm waits, unconsumed, while the terminal is on
    # another account (its account read is checked); reviewed in that PR's trading-safety round
    "Engine._poll_rearm": "3854dfab25196f42c37088b2fa18928b939e38b13af12342222d426da2f3a290",
    "Engine._rearm_conditions": "8460d520bd750f874fbb416f643dc201675b4fb03ce453a49309beccdf22c519",
    "RiskGate.rearm": "0f5656464592607aa3b434fae1aad74e4c6fcf134455ef90fb5f3c6387496d94",
    # trading-safety F6 on PR #290: the stage machine and the owner's reset of the dynamic-limit revert
    "RiskGate.update_stage": "d3d92a2815fcc5c938de670c0c36f831b6627e82f9909703e4df24990ce5b911",
    "EngineLimits.reset": "0b2310e79c7f5de3c9e08e6c89a696fe2c675297d459d4a9f8296d8435f88f4d",
}


def test_the_owner_totp_rearm_path_is_byte_identical():
    got: dict[str, Callable[..., Any]] = {"Engine._poll_rearm": Engine._poll_rearm,
                                          "Engine._rearm_conditions": Engine._rearm_conditions,
                                          "RiskGate.rearm": RiskGate.rearm,
                                          "RiskGate.update_stage": RiskGate.update_stage,
                                          "EngineLimits.reset": EngineLimits.reset}
    digests = {k: hashlib.sha256(inspect.getsource(f).encode()).hexdigest() for k, f in got.items()}
    assert digests == REARM_PATH_SHA256
