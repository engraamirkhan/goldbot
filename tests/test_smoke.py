"""BACKLOG 1 / W1-1: `run.py bridge-smoke` (goldbot/ops/smoke.py), per the approved plan
docs/proposals/2026-10-w1-1-bridge-smoke-plan.md and ADR 0021 (fences F1-F6, findings S1-S11).

Every test runs the real RiskGate, ApprovalBus and ApprovalCenter on a temporary state directory against a fake
terminal; a fake clock drives the approval window and the polls (no real sleeps)."""
from __future__ import annotations

import json
import stat
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.config import HeatSettings, Tier2Blackout
from goldbot.execution.bridge import BridgeUnavailable
from goldbot.execution.broker import AccountInfo, OrderIntent, OrderResult, Position, SymbolInfo, Tick
from goldbot.ops import smoke
from goldbot.ops.accounts import Account
from goldbot.ops.bridge_compat import BridgeCompat
from goldbot.ops.watchdog import loop_heartbeat_path
from goldbot.risk.gate import AccountState, RiskGate, RiskLimits
from goldbot.telegram.bus import ApprovalBus

T0 = pd.Timestamp("2026-10-26 10:00", tz="UTC")       # a Monday: 12:00 Athens, market open, far from the rollover
LOGIN = 51234567                                      # made up; must never appear in the output
ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=LOGIN,
              terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
SMOKE_MAGIC = 260199
COMMIT = "0123456789abcdef0123456789abcdef01234567"   # the same commit on the brain and the MT5 box


class Clock:
    """Wall and monotonic time in one: `sleep` advances both and runs the hooks (a click, an engine start)."""

    def __init__(self) -> None:
        self.t = 0.0
        self.hooks: list[Callable[[], Any]] = []

    def now(self) -> pd.Timestamp:
        return T0 + pd.Timedelta(seconds=self.t)

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s
        for h in list(self.hooks):
            h()


class Terminal:
    """A demo MT5 terminal behind the bridge: records every call in `log` (shared with the gate spy)."""

    order_timeout_s = 15.0

    def __init__(self, clock: Clock, log: list[str]) -> None:
        self.clock, self.log = clock, log
        self.info = AccountInfo(login=LOGIN, equity=1000.0, balance=1000.0, margin=0.0, margin_free=1000.0,
                                leverage=30, currency="USD", server="ICMarketsSC-Demo", trade_mode="demo",
                                margin_mode="hedging")
        self.sym = SymbolInfo(name="XAUUSD", digits=2, point=0.01, contract_size=100.0, volume_min=0.01,
                              volume_step=0.01, volume_max=100.0, stops_level_points=0.0, filling_modes=["ioc"],
                              trade_allowed=True)
        self.bid, self.ask, self.tick_lag_s = 2400.0, 2400.2, 0.5
        self.open: dict[int, Position] = {}
        self.deals: list[dict[str, Any]] = []
        self.sent: list[OrderIntent] = []
        self.closes: list[int] = []
        self.next_id = 1000
        self.place_raises: Exception | None = None
        self.fill_on_raise = False
        self.closed_on_raise = False
        self.result_none = False
        self.comment_as: str | None = None           # a broker that rewrites the order comment
        self.sl_shift = 0.0                          # a broker that stores another stop or target than was sent
        self.tp_shift = 0.0
        self.protect = True
        self.close_ok = True
        self.raises: dict[str, Exception] = {}
        self.bars_age_s = 30.0

    def _maybe(self, name: str) -> None:
        self.log.append(name)
        if name in self.raises:
            raise self.raises[name]

    def account(self) -> AccountInfo:
        self._maybe("account")
        return self.info

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return self.sym

    def last_tick(self, symbol: str) -> Tick:
        self._maybe("last_tick")
        return Tick(ts_utc=self.clock.now() - pd.Timedelta(seconds=self.tick_lag_s), bid=self.bid, ask=self.ask)

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        self._maybe("get_bars")
        last = self.clock.now() - pd.Timedelta(seconds=self.bars_age_s)
        return pd.DataFrame({"ts_utc": [last - pd.Timedelta(minutes=1), last], "close": [2400.0, 2400.1]})

    def trading_sessions(self, symbol: str) -> list[Any]:
        raise NotImplementedError          # the static session table applies (as for a broker that cannot say)

    def margin_required(self, symbol: str, side: int, lots: float, price: float) -> float | None:
        return lots * price * 100 / 30

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        return distance * 100.0

    def account_positions(self) -> list[Position]:
        self._maybe("account_positions")
        return list(self.open.values())

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        self._maybe("positions")
        return list(self.open.values())

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        self._maybe("deals_since")
        df = pd.DataFrame(self.deals)
        return df[df["ts_utc"] >= since_utc].reset_index(drop=True) if not df.empty else df

    def _fill(self, oi: OrderIntent) -> int:
        pid = self.next_id
        self.next_id += 1
        comment = self.comment_as if self.comment_as is not None else oi.comment
        self.open[pid] = Position(position_id=pid, symbol=oi.symbol, side=oi.side, lots=oi.lots, open_price=self.ask,
                                  sl=oi.sl + self.sl_shift if self.protect else None,
                                  tp=oi.tp + self.tp_shift if self.protect else None,
                                  magic=oi.magic, comment=comment, open_time_utc=self.clock.now(), profit=0.0)
        self.deals.append({"ts_utc": self.clock.now(), "position_id": pid, "magic": oi.magic, "comment": comment,
                           "entry": 0, "type": 0, "volume": oi.lots, "price": self.ask})
        return pid

    def place_order(self, intent: OrderIntent) -> OrderResult | None:
        self.log.append("place_order")
        self.sent.append(intent)
        if self.place_raises is not None:
            if self.fill_on_raise:
                pid = self._fill(intent)
                if self.closed_on_raise:              # filled and stopped out before anyone looked
                    self.close(pid)
            raise self.place_raises
        pid = self._fill(intent)
        if self.result_none:
            return None
        return OrderResult(ok=True, retcode=10009, order_id=pid, position_id=pid, filled_lots=intent.lots,
                           price=self.ask)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        self.log.append("close")
        self.closes.append(position_id)
        if not self.close_ok or position_id not in self.open:
            return OrderResult(ok=False, retcode=10004, order_id=None, position_id=position_id, filled_lots=0.0,
                               price=None, message="requote")
        p = self.open.pop(position_id)
        self.deals.append({"ts_utc": self.clock.now(), "position_id": position_id, "magic": p.magic,
                           "comment": "close", "entry": 1, "type": 1, "volume": p.lots, "price": self.bid})
        return OrderResult(ok=True, retcode=10009, order_id=1, position_id=position_id, filled_lots=p.lots,
                           price=self.bid)


def _write(path: Path, d: Any) -> None:
    path.write_text(json.dumps(d))


@pytest.fixture
def w(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = tmp_path / "state"
    state.mkdir()
    log: list[str] = []
    clock = Clock()
    # spy on the gate: its calls land in the same log as the terminal's, so their order can be asserted
    real = RiskGate.check_operational

    def spy(self: RiskGate, *a: Any, **k: Any) -> Any:
        log.append("gate")
        return real(self, *a, **k)
    monkeypatch.setattr(RiskGate, "check_operational", spy)
    _write(state / "risk_icm-demo.json", {"last_reset": T0.replace(hour=0).isoformat(), "day_start_equity": 1000.0,
                                          "week_start_equity": 1000.0, "balance_closed_hwm": 1000.0,
                                          "stage": "normal", "stage2_pending_since": None})
    _write(state / "supervisor.json", {"ts": time.time(), "halt": False, "reasons": [], "size_down": False,
                                       "exposure": {"icm-demo": {"lots": 0.0, "notional": 0.0, "equity": 1000.0}}})
    _write(state / "classifier_icm-demo.json", {"class": "raw"})
    events = pd.DataFrame({"ts_utc": [T0 + pd.Timedelta(days=1)], "tier": [1], "title": ["CPI"],
                           "received_utc": [T0 - pd.Timedelta(hours=2)]})
    out: list[str] = []
    ns = SimpleNamespace(state=state, clock=clock, log=log, broker=Terminal(clock, log), bus=ApprovalBus(state),
                         out=out, engine="inactive", events=events, news=pd.DataFrame(), limits=RiskLimits(),
                         phase=tmp_path / "phase_state.json",
                         compat=BridgeCompat(status="ok", reason="ok", bridge_commit=COMMIT, brain_commit=COMMIT),
                         calendar_raises=None, news_raises=None, acc=ACC)
    return ns


def _io(w: SimpleNamespace) -> smoke.SmokeIO:
    def calendar(now: pd.Timestamp) -> pd.DataFrame:
        if w.calendar_raises:
            raise w.calendar_raises
        return w.events

    def news(now: pd.Timestamp) -> pd.DataFrame:
        if w.news_raises:
            raise w.news_raises
        return w.news
    return smoke.SmokeIO(monotonic=w.clock.monotonic, sleep=w.clock.sleep, out=w.out.append,
                         engine_state=lambda acc_id: w.engine, calendar=calendar, news=news)


def _cfg(w: SimpleNamespace) -> smoke.SmokeConfig:
    return smoke.SmokeConfig(limits=w.limits, phase_file=w.phase)


def _run(w: SimpleNamespace) -> smoke.SmokeReport:
    return smoke.run_smoke(w.broker, w.bus, w.acc, w.state, now=w.clock.now, cfg=_cfg(w), compat=w.compat, io=_io(w))


def _click(w: SimpleNamespace, approve: bool = True, then: Callable[[], Any] | None = None) -> None:
    """The owner decides on the dashboard at the first poll of the window (then `then` runs, e.g. a halt)."""
    def hook() -> None:
        pending = w.bus.pending()
        if pending:
            w.bus.submit(pending[0].proposal_id, approve, None if approve else "other", "dashboard",
                         card_created=pending[0].created)
            if then is not None:
                then()
            w.clock.hooks.remove(hook)
    w.clock.hooks.append(hook)


def _done(w: SimpleNamespace) -> list[dict[str, Any]]:
    return [json.loads(f.read_text()) for f in sorted((w.state / "approvals" / "done").glob("*.json"))]


def _alerts(w: SimpleNamespace) -> str:
    f = w.state / "agent_runs.jsonl"
    return f.read_text() if f.exists() else ""


# ---------------------------------------------------------------------------------------------- the round trip
def test_prints_masked_login_server_balance_and_tick_then_opens_and_closes_the_minimum_lot(w):
    _click(w)
    rep = _run(w)
    text = "\n".join(w.out)
    assert rep.exit_code == smoke.EXIT_OK, text
    assert "****567" in text and "ICMarketsSC-Demo" in text and "1,000.00 USD" in text
    assert "bid 2400.0 ask 2400.2" in text
    assert [o.lots for o in w.broker.sent] == [0.01] and w.broker.sent[0].magic == SMOKE_MAGIC
    assert rep.position_id is not None and w.broker.closes == [rep.position_id] and w.broker.open == {}
    assert rep.login == "****567" and rep.balance == 1000.0
    assert _done(w)[0]["outcome"] == "APPROVED" and _done(w)[0]["agent_id"] == "bridge-smoke"
    cid = w.broker.sent[0].client_order_id
    assert cid.startswith("icm-demo-") and cid.endswith("-smoke") and w.broker.sent[0].comment == cid[-31:]


def test_full_login_never_appears_in_the_output(w):
    _click(w)
    rep = _run(w)
    w2_text = "\n".join(w.out)
    stored = "".join(f.read_text() for f in w.state.rglob("*.json")) + _alerts(w)
    assert str(LOGIN) not in w2_text and str(LOGIN) not in stored and str(LOGIN) not in rep.model_dump_json()
    w.broker.info = w.broker.info.model_copy(update={"trade_mode": "real"})      # a refusal names no login either
    w.out.clear()
    _run(w)
    assert str(LOGIN) not in "\n".join(w.out) and "****567" in "\n".join(w.out)
    assert smoke.mask_login(LOGIN) == "****567" and smoke.mask_login(None) == "unknown"


@pytest.mark.parametrize("change", ["terminal_real", "terminal_contest", "registry_live", "other_login"])
def test_exits_2_when_the_terminal_is_not_demo(w, change):
    if change == "terminal_real":
        w.broker.info = w.broker.info.model_copy(update={"trade_mode": "real"})
    elif change == "terminal_contest":
        w.broker.info = w.broker.info.model_copy(update={"trade_mode": "contest"})
    elif change == "registry_live":
        w.acc = ACC.model_copy(update={"mode": "live"})
    else:
        w.broker.info = w.broker.info.model_copy(update={"login": 7654321})
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_NOT_DEMO
    assert w.broker.sent == [] and "gate" not in w.log and _done(w) == [] and w.bus.pending() == []


# ---------------------------------------------------------------------------------------------- approval (F6)
def test_no_click_in_90_s_expires_exits_3_and_sends_nothing(w):
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_UNAPPROVED and w.broker.sent == []
    assert 90.0 <= w.clock.t <= 92.0                               # the window, by the monotonic deadline
    assert [d["outcome"] for d in _done(w)] == ["EXPIRED_UNAPPROVED"]
    assert w.log.count("gate") == 1                                # checked once, never re-run without a click


def test_a_rejection_sends_nothing(w):
    _click(w, approve=False)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_UNAPPROVED and w.broker.sent == []
    assert [d["outcome"] for d in _done(w)] == ["REJECTED"] and w.clock.t < 5


def test_approval_re_runs_the_gate_and_a_halt_meanwhile_sends_nothing(w):
    _click(w, then=lambda: w.bus.set_halt(True, "telegram", "owner halt"))
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_GATE and w.broker.sent == []
    assert w.log.count("gate") == 2 and "owner_halt" in rep.gate_reasons
    done = _done(w)[0]
    assert done["outcome"] == "APPROVED" and "owner_halt" in done["gate_refusal"]   # "refused at approval"


def test_the_gate_runs_twice_and_before_the_order(w):
    _click(w)
    _run(w)
    gates = [i for i, e in enumerate(w.log) if e == "gate"]
    assert len(gates) == 2 and gates[-1] < w.log.index("place_order")


# ---------------------------------------------------------------------------------------------- preconditions (S3)
@pytest.mark.parametrize("unit, beat_age_s, allowed", [
    ("active", None, False), ("activating", None, False), ("failed", None, False), ("unknown", None, False),
    ("inactive", 149.0, False), ("inactive", 150.0, True), ("inactive", None, True), ("inactive", -30.0, False),
])
def test_engine_active_or_heartbeat_149_s_refuses_150_s_inactive_allows(w, unit, beat_age_s, allowed):
    w.engine = unit
    if beat_age_s is not None:
        _write(loop_heartbeat_path(w.state, "icm-demo"), {"ts": T0.timestamp() - beat_age_s, "phase": "loop"})
    _click(w)
    rep = _run(w)
    if allowed:
        assert rep.exit_code == smoke.EXIT_OK and len(w.broker.sent) == 1
    else:
        assert rep.exit_code == smoke.EXIT_PRECONDITION and w.broker.sent == [] and "gate" not in w.log


def test_engine_started_during_the_wait_is_caught_before_place_order(w):
    def engine_starts() -> None:
        w.engine = "active"
        _write(loop_heartbeat_path(w.state, "icm-demo"), {"ts": w.clock.now().timestamp(), "phase": "starting"})
    _click(w, then=engine_starts)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_PRECONDITION and w.broker.sent == []
    assert _done(w)[0]["gate_refusal"] == ["engine_running"]


def test_a_second_smoke_cannot_take_the_lock(w):
    with smoke.smoke_lock(w.state, "icm-demo"):
        _click(w)
        rep = _run(w)
    assert rep.exit_code == smoke.EXIT_PRECONDITION and "lock" in rep.outcome
    assert w.broker.sent == [] and "gate" not in w.log and "positions" not in w.log
    lock = w.state / ".smoke-icm-demo.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    _click(w)
    assert _run(w).exit_code == smoke.EXIT_OK                       # released when the first smoke ends


# ---------------------------------------------------------------------------------------------- AccountState (S2)
def _state(w: SimpleNamespace) -> AccountState:
    from goldbot.data.calendar import SessionTable
    b = w.broker
    return smoke.smoke_state(b, w.bus, w.acc, w.state, now=w.clock.now(), cfg=_cfg(w), io=_io(w), compat=w.compat,
                             info=b.account(), sym=b.sym, tick=b.last_tick("XAUUSD"),
                             sessions=SessionTable(server_tz="Europe/Athens"), fx=1.0)


def test_smoke_state_sets_every_account_state_field(w):
    st = _state(w)
    fields = set(AccountState.model_fields)
    assert set(smoke.STATE_SOURCES) == fields                     # every field names its source
    assert st.model_fields_set == fields, fields - st.model_fields_set     # none left to a record default
    assert all(smoke.STATE_SOURCES[f].strip() for f in fields)
    assert st.equity == 1000.0 and st.account_class == "raw" and st.open_risk == 0.0 and not st.bridge_incompatible
    assert RiskGate().account_reasons(st, T0) == []


def _set_risk(**kw: Any) -> Callable[[SimpleNamespace], None]:
    def f(w: SimpleNamespace) -> None:
        path = w.state / "risk_icm-demo.json"
        _write(path, {**json.loads(path.read_text()), **kw})
    return f


def _set_sup(**kw: Any) -> Callable[[SimpleNamespace], None]:
    def f(w: SimpleNamespace) -> None:
        path = w.state / "supervisor.json"
        d = {**json.loads(path.read_text()), **kw}
        _write(path, {k: v for k, v in d.items() if v is not None})
    return f


def _sup_without_size_down(w: SimpleNamespace) -> None:
    path = w.state / "supervisor.json"
    d = json.loads(path.read_text())
    d.pop("size_down")
    _write(path, d)


def _position(w: SimpleNamespace, **kw: Any) -> None:
    base: dict[str, Any] = dict(position_id=7, symbol="XAUUSD", side=1, lots=0.5, open_price=2390.0, sl=2380.0,
                                tp=2420.0, magic=260142, comment="", open_time_utc=T0, profit=0.0)
    base.update(kw)
    w.broker.open[base["position_id"]] = Position(**base)


def _attr(obj: str, **kw: Any) -> Callable[[SimpleNamespace], None]:
    def f(w: SimpleNamespace) -> None:
        target = getattr(w, obj)
        for k, v in kw.items():
            setattr(target, k, v)
    return f


def _w(**kw: Any) -> Callable[[SimpleNamespace], None]:
    def f(w: SimpleNamespace) -> None:
        for k, v in kw.items():
            setattr(w, k, v)
    return f


def _info(**kw: Any) -> Callable[[SimpleNamespace], None]:
    def f(w: SimpleNamespace) -> None:
        w.broker.info = w.broker.info.model_copy(update=kw)
    return f


def _clock_to(ts: str) -> Callable[[SimpleNamespace], None]:
    def f(w: SimpleNamespace) -> None:
        w.clock.t = (pd.Timestamp(ts, tz="UTC") - T0).total_seconds()
        _set_risk(last_reset=pd.Timestamp(ts, tz="UTC").isoformat())(w)
    return f


def _two_positions(w: SimpleNamespace) -> None:
    _position(w, position_id=7)
    _position(w, position_id=8)


def _tier2_gap(w: SimpleNamespace) -> None:
    w.limits = RiskLimits(tier2=Tier2Blackout(enabled=True))
    w.events = w.events.drop(columns=["received_utc"])          # no fetch time: the calendar cannot judge tier 2


def _heat_unknown(w: SimpleNamespace) -> None:
    _position(w, sl=None)                                        # no stop: its remaining risk is unknown
    w.limits = RiskLimits(heat=HeatSettings(enabled=True))


def _fx_unknown(w: SimpleNamespace) -> None:
    w.broker.info = w.broker.info.model_copy(update={"currency": "GBP"})
    w.broker.loss_per_lot = lambda *a: None                      # the broker cannot convert


MUTATIONS: dict[str, Callable[[SimpleNamespace], Any]] = {
    "risk_file_missing": lambda w: (w.state / "risk_icm-demo.json").unlink(),
    "risk_file_corrupt": lambda w: (w.state / "risk_icm-demo.json").write_text("{nope"),
    "risk_stage_halted": _set_risk(stage="halted"),
    "risk_stage2_pending": _set_risk(stage2_pending_since=T0.isoformat()),
    "risk_day_loss_at_cap": _set_risk(day_start_equity=1020.5),
    "risk_week_loss_at_cap": _set_risk(week_start_equity=1053.0),
    "drawdown_halt": _set_risk(balance_closed_hwm=1200.0),
    "positions_unreadable": lambda w: w.broker.raises.update(account_positions=RuntimeError("bridge")),
    "two_positions_open": _two_positions,
    "tick_unreadable": lambda w: w.broker.raises.update(last_tick=RuntimeError("bridge")),
    "tick_stale": _attr("broker", tick_lag_s=60.0),
    "tick_from_future": _attr("broker", tick_lag_s=-30.0),
    "spread_wide": _attr("broker", ask=2401.0),
    "bars_unreadable": lambda w: w.broker.raises.update(get_bars=RuntimeError("bridge")),
    "bars_stale": _attr("broker", bars_age_s=3600.0),
    "owner_halt": lambda w: w.bus.set_halt(True, "dashboard", "halt"),
    "control_unreadable": lambda w: (w.state / "control.json").write_text("{nope"),
    "supervisor_halt": _set_sup(halt=True, reasons=["combined_daily_cap"]),
    "supervisor_stale": _set_sup(ts=time.time() - 120),
    "supervisor_missing": lambda w: (w.state / "supervisor.json").unlink(),
    "supervisor_no_size_down": _sup_without_size_down,
    "combined_exposure": _set_sup(exposure={"other": {"lots": 2.995, "notional": 0.0, "equity": 1000.0}}),
    "rollover": _clock_to("2026-10-26 22:01"),
    "weekend": _clock_to("2026-10-31 10:00"),
    "friday_cut": _clock_to("2026-10-30 19:45"),
    "classifier_missing": lambda w: (w.state / "classifier_icm-demo.json").unlink(),
    "classifier_unknown": lambda w: _write(w.state / "classifier_icm-demo.json", {"class": "unknown"}),
    "drift_halt": lambda w: _write(w.state / "drift.json", {"system_halt": {"reasons": ["cusum"]}}),
    "drift_unreadable": lambda w: (w.state / "drift.json").write_text("{nope"),
    "bridge_incompatible": _w(compat=BridgeCompat(status="incompatible", reason="v4")),
    "bridge_unchecked": _w(compat=BridgeCompat(status="unchecked", reason="never")),
    "calendar_unreadable": _w(calendar_raises=OSError("disk")),
    "calendar_empty": _w(events=pd.DataFrame()),
    "news_blackout": _w(events=pd.DataFrame({"ts_utc": [T0 + pd.Timedelta(minutes=5)], "tier": [1], "title": ["NFP"],
                                             "received_utc": [T0]})),
    "news_unreadable": _w(news_raises=OSError("disk")),
    "news_shock": _w(news=pd.DataFrame({"ts_utc": [T0], "received_utc": [T0 - pd.Timedelta(minutes=2)],
                                        "shock": [True], "relevance": [0.9], "title": ["war"]})),
    "tier2_calendar_gap": _tier2_gap,
    "heat_unknown_open_risk": _heat_unknown,
    "phase_after_live_gate": lambda w: _write(w.phase, {"gates_passed": ["foundation_to_backtest", "backtest_to_paper",
                                                                          "paper_to_tiny_live"]}),
    "phase_unreadable": lambda w: w.phase.write_text("{nope"),
    "margin_heavy": _info(margin=340.0),
    "equity_nan": _info(equity=float("nan")),
    "fx_unknown": _fx_unknown,
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_source_mutated_to_unsafe_refuses(w, name):
    MUTATIONS[name](w)
    _click(w)
    rep = _run(w)
    assert rep.exit_code in (smoke.EXIT_GATE, smoke.EXIT_PRECONDITION), (name, rep.outcome, w.out)
    assert w.broker.sent == [] and w.bus.pending() == []


def test_the_unmutated_world_trades(w):
    """The mutation test's control: each refusal above comes from its own mutation."""
    _click(w)
    assert _run(w).exit_code == smoke.EXIT_OK


def test_a_stale_baseline_is_reset_in_memory_and_never_written(w):
    path = w.state / "risk_icm-demo.json"
    _set_risk(last_reset="2026-10-23T00:00:00+00:00", day_start_equity=1500.0, week_start_equity=1500.0)(w)
    before = path.read_bytes()
    st = _state(w)
    assert st.day_start_equity == 1000.0 and st.week_start_equity == 1000.0     # a new day and week: today's equity
    _click(w)
    assert _run(w).exit_code == smoke.EXIT_OK                     # yesterday's day start no longer refuses daily_cap
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------------------------- send and close
def test_bridge_error_on_send_polls_25_s_then_exits_6_and_never_resends(w):
    w.broker.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    _click(w)
    rep = _run(w)
    sent_at = w.log.index("place_order")
    assert rep.exit_code == smoke.EXIT_UNKNOWN and len(w.broker.sent) == 1
    assert w.log.count("place_order") == 1 and "gate" not in w.log[sent_at:]
    assert w.clock.t >= 25.0 and w.log[sent_at:].count("deals_since") >= 10
    assert "UNKNOWN" in _alerts(w)


def test_a_found_fill_after_a_timeout_is_closed(w):
    w.broker.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    w.broker.fill_on_raise = True
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_OK and len(w.broker.sent) == 1
    assert w.broker.open == {} and len(w.broker.closes) == 1


def test_missing_sl_or_tp_after_the_fill_closes_at_once(w):
    w.broker.protect = False
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_FAILED and w.broker.open == {} and len(w.broker.closes) == 1
    assert "no stop" in "\n".join(w.out)


def test_left_open_exits_5_and_alerts(w):
    w.broker.close_ok = False
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_LEFT_OPEN and len(w.broker.closes) == smoke.CLOSE_ATTEMPTS
    assert len(w.broker.open) == 1 and "LEFT OPEN" in _alerts(w) and str(LOGIN) not in _alerts(w)


def test_an_unclosable_leftover_blocks_a_new_order(w):
    _position(w, position_id=5, magic=SMOKE_MAGIC, lots=0.01)
    w.broker.close_ok = False
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_LEFT_OPEN and w.broker.sent == [] and "gate" not in w.log
    assert w.broker.closes == [5] * smoke.CLOSE_ATTEMPTS


def test_a_closable_leftover_is_closed_before_the_new_order(w):
    _position(w, position_id=5, magic=SMOKE_MAGIC, lots=0.01)
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_OK and w.broker.closes[0] == 5
    assert w.log.index("close") < w.log.index("place_order")


def test_a_smoke_deal_in_the_last_24_h_is_found_by_its_comment(w):
    b = w.broker
    comment = "1761472800000000000-smoke"
    b.deals = [
        {"ts_utc": T0 - pd.Timedelta(hours=23), "position_id": 1, "magic": SMOKE_MAGIC, "comment": comment,
         "entry": 0, "type": 0, "volume": 0.01, "price": 2400.0},
        {"ts_utc": T0 - pd.Timedelta(hours=25), "position_id": 2, "magic": SMOKE_MAGIC, "comment": comment,
         "entry": 0, "type": 0, "volume": 0.01, "price": 2400.0},
        {"ts_utc": T0 - pd.Timedelta(hours=1), "position_id": 3, "magic": SMOKE_MAGIC, "comment": "other-smoke",
         "entry": 0, "type": 0, "volume": 0.01, "price": 2400.0},
        {"ts_utc": T0 - pd.Timedelta(hours=1), "position_id": 4, "magic": 260142, "comment": comment,
         "entry": 0, "type": 0, "volume": 0.01, "price": 2400.0},
    ]
    _, deals = smoke.find_smoke(b, SMOKE_MAGIC, T0 - smoke.LOOKUP_WINDOW, comment)
    assert deals["position_id"].tolist() == [1]
    _, every = smoke.find_smoke(b, SMOKE_MAGIC, T0 - smoke.LOOKUP_WINDOW)
    assert sorted(every["position_id"].tolist()) == [1, 3]


@pytest.mark.parametrize("stops_level, spread_ask, distance", [(0.0, 2400.2, 1.0), (150.0, 2400.3, 1.8)])
def test_stop_and_target_sides_and_stops_level(w, stops_level, spread_ask, distance):
    w.broker.sym = w.broker.sym.model_copy(update={"stops_level_points": stops_level})
    w.broker.ask = spread_ask
    _click(w)
    rep = _run(w)
    oi = w.broker.sent[0]
    assert rep.exit_code == smoke.EXIT_OK and oi.side == 1                       # S11: long only
    assert oi.sl <= round(spread_ask - distance, 2) + 1e-9 and oi.tp >= round(spread_ask + distance, 2) - 1e-9
    assert oi.sl <= w.broker.bid - stops_level * 0.01 + 1e-9 and oi.tp >= w.broker.ask + stops_level * 0.01 - 1e-9
    assert oi.sl == pytest.approx(spread_ask - distance) and oi.tp == pytest.approx(spread_ask + distance)


@pytest.mark.parametrize("vmin, step, lots", [(0.01, 0.01, 0.01), (0.02, 0.01, 0.02), (0.1, 0.1, 0.1)])
def test_lots_are_volume_min_on_the_step(w, vmin, step, lots):
    w.broker.sym = w.broker.sym.model_copy(update={"volume_min": vmin, "volume_step": step})
    w.broker.info = w.broker.info.model_copy(update={"equity": 100_000.0, "balance": 100_000.0})
    _set_risk(day_start_equity=100_000.0, week_start_equity=100_000.0, balance_closed_hwm=100_000.0)(w)
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_OK and [o.lots for o in w.broker.sent] == [lots]


def test_close_path_never_calls_the_gate(w, monkeypatch):
    _click(w)
    _run(w)
    sent = w.log.index("place_order")
    assert "close" in w.log[sent:] and "gate" not in w.log[sent:]
    # and the close helper alone, with every gate entry point and the bus's halt raising: it still closes
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("the close path called the gate")
    monkeypatch.setattr(RiskGate, "check_operational", boom)
    monkeypatch.setattr(RiskGate, "check", boom)
    monkeypatch.setattr(RiskGate, "account_reasons", boom)
    w.bus.set_halt(True, "dashboard", "halt")
    _position(w, position_id=9, magic=SMOKE_MAGIC, lots=0.01)
    assert smoke.close_position(w.broker, 9, _io(w)) and 9 not in w.broker.open


# ---------------------------------------------------------------------------------------------- review round 1
def test_a_fill_landing_between_the_positions_and_deals_reads_is_settled_not_reported_closed(w):
    """Trading-safety HIGH-1 (race probe): after a send that raised, the fill lands after positions() was read and
    before deals_since(); its entry deal alone must never be reported "already closed" while it is open."""
    b = w.broker
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    real = b.deals_since
    armed = {"on": True}

    def deals_since(since: pd.Timestamp) -> pd.DataFrame:
        if armed["on"] and b.sent:
            armed["on"] = False
            b._fill(b.sent[-1])
        return real(since)
    b.deals_since = deals_since
    _click(w)
    rep = _run(w)
    assert b.open == {} and len(b.closes) == 1 and len(b.sent) == 1
    assert rep.exit_code == smoke.EXIT_OK and rep.position_id == 1000


def test_entry_deals_without_an_exit_deal_keep_polling_then_exit_6(w):
    """HIGH-1: an entry deal whose position is never listed and has no exit deal is not "closed": UNKNOWN."""
    b = w.broker
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    b.fill_on_raise = True
    real = b.positions
    b.positions = lambda magic_prefix=None: [] if b.sent else real()
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_UNKNOWN and len(b.sent) == 1 and w.clock.t >= 25.0
    assert "UNKNOWN" in _alerts(w)


def test_entry_and_exit_deals_found_after_a_timeout_report_closed(w):
    b = w.broker
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    b.fill_on_raise = b.closed_on_raise = True
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_OK and "already closed" in rep.outcome and b.open == {}
    assert len(b.sent) == 1


def test_a_fill_not_listed_is_closed_and_exits_1_unverified(w):
    """Trading-safety MEDIUM-2 (race probe): a fill whose stop and target were never seen is not a success."""
    b = w.broker
    real = b.positions
    b.positions = lambda magic_prefix=None: [] if b.sent else real()
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_FAILED and "unverified" in rep.outcome
    assert b.closes == [1000] and b.open == {}


@pytest.mark.parametrize("sl_shift, tp_shift", [(0.05, 0.0), (0.0, -0.05), (2.0, 0.0), (0.0, -2.0)])
def test_a_fill_whose_stop_or_target_differs_from_the_order_is_closed_and_exits_1(w, sl_shift, tp_shift):
    """MEDIUM-2: sl and tp must equal the order's within one point and sit either side of the open (buy:
    sl < open < tp); (2.0, 0) puts the stop above the open, (0, -2.0) the target below it."""
    w.broker.sl_shift, w.broker.tp_shift = sl_shift, tp_shift
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_FAILED and "unverified" in rep.outcome and w.broker.open == {}


def test_a_stop_and_target_within_one_point_of_the_order_verify(w):
    w.broker.sl_shift, w.broker.tp_shift = 0.01, -0.01
    _click(w)
    assert _run(w).exit_code == smoke.EXIT_OK and w.broker.open == {}


def test_a_none_result_is_ambiguous_and_resolved_by_the_look_up(w):
    """LOW-4: a send that returns None is ambiguous (never an assert): the look-up finds the fill and closes it."""
    w.broker.result_none = True
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_OK and len(w.broker.sent) == 1 and w.broker.open == {}


def test_a_rewritten_comment_is_matched_by_magic_and_open_time(w):
    """LOW-5: a broker that rewrites the comment: the fill is found by its magic, opened at or after the send."""
    b = w.broker
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    b.fill_on_raise = True
    b.comment_as = "[sl 2399.20]"
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_OK and b.open == {} and len(b.sent) == 1


def test_a_position_opened_before_the_send_is_not_taken_for_the_fill(w):
    """LOW-5's guard: magic alone matches only a position opened at or after the send."""
    b = w.broker
    _position(w, position_id=5, magic=SMOKE_MAGIC, lots=0.01, comment="older",
              open_time_utc=T0 - pd.Timedelta(hours=1))
    old = b.open.pop(5)
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    real = b.positions
    b.positions = lambda magic_prefix=None: [*real(), old] if b.sent else real()
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_UNKNOWN and 5 not in b.closes


def test_an_error_inside_the_locked_run_is_not_reported_as_a_lock_failure(w):
    """LOW-4: only the lock's own acquisition maps to "cannot take the lock"."""
    def engine_state(acc_id: str) -> str:
        raise PermissionError("systemctl denied")
    io = _io(w).model_copy(update={"engine_state": engine_state})
    with pytest.raises(PermissionError):
        smoke.run_smoke(w.broker, w.bus, w.acc, w.state, now=w.clock.now, cfg=_cfg(w), compat=w.compat, io=io)
    assert w.broker.sent == []


def test_a_planted_lock_symlink_is_refused(w, tmp_path):
    """Security: the lock is opened O_NOFOLLOW | O_CLOEXEC and must be a regular file this user owns."""
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    (w.state / ".smoke-icm-demo.lock").symlink_to(target)
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_PRECONDITION and w.broker.sent == [] and target.read_text() == "keep"


def test_a_lock_that_is_not_a_regular_file_is_refused(w):
    import os
    os.mkfifo(w.state / ".smoke-icm-demo.lock")
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_PRECONDITION and w.broker.sent == []


def test_the_lock_is_not_inherited_by_child_processes(w):
    import fcntl
    import os
    lock = w.state / ".smoke-icm-demo.lock"
    with smoke.smoke_lock(w.state, "icm-demo"):
        ident = (lock.stat().st_dev, lock.stat().st_ino)
        mine = []
        for fd in range(3, 256):
            try:
                st = os.fstat(fd)
            except OSError:
                continue
            if (st.st_dev, st.st_ino) == ident:
                mine.append(fd)
        assert mine and all(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC for fd in mine)


def test_a_bridge_on_another_commit_refuses_exit_4(w):
    """SRE S1: the MT5 box must run this commit (F4 lives there): deploy it to the MT5 box first."""
    w.compat = BridgeCompat(status="ok", reason="ok", commit_differs=True, bridge_commit="aaaaaaa",
                            brain_commit="bbbbbbb")
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_PRECONDITION and w.broker.sent == []
    assert "MT5 box" in "\n".join(w.out)


def _run_a_deals(pid: int = 900, comment: str = "icm-demo-1793008790000000000-smoke"[-31:]) -> list[dict[str, Any]]:
    """An earlier smoke (run A) that entered and closed seconds before this run's send."""
    return [{"ts_utc": T0 - pd.Timedelta(seconds=10), "position_id": pid, "magic": SMOKE_MAGIC, "comment": comment,
             "entry": 0, "type": 0, "volume": 0.01, "price": 2400.0},
            {"ts_utc": T0 - pd.Timedelta(seconds=9), "position_id": pid, "magic": SMOKE_MAGIC, "comment": "close",
             "entry": 1, "type": 1, "volume": 0.01, "price": 2400.0}]


def test_previous_smoke_closed_seconds_ago_is_not_taken_for_this_order(w):
    """Trading-safety NEW-1 (back-to-back probe): run A's closed trade, inside the 30 s fallback window, is never
    reported as run B's fill; B's order is UNKNOWN (exit 6), never "already closed"."""
    b = w.broker
    b.deals = _run_a_deals()
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_UNKNOWN and rep.position_id != 900 and len(b.sent) == 1


def test_another_smokes_comment_is_never_matched_by_the_magic_fallback(w):
    """NEW-1, second guard: a smoke deal that appears only after the pre-send look-up (so not among the known ids)
    is still not this order's when its comment is another smoke's."""
    b = w.broker
    b.place_raises = BridgeUnavailable("bridge place_order: ReadTimeout")
    real = b.deals_since

    def deals_since(since: pd.Timestamp) -> pd.DataFrame:
        if b.sent and not any(d["position_id"] == 901 for d in b.deals):
            b.deals.extend(_run_a_deals(901, "-1793008795000000000-smoke"))
        return real(since)
    b.deals_since = deals_since
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_UNKNOWN and rep.position_id != 901


@pytest.mark.parametrize("bridge_commit, brain_commit", [(None, COMMIT), (COMMIT, None), (None, None), ("", COMMIT)])
def test_an_unknown_bridge_or_brain_commit_refuses_exit_4(w, bridge_commit, brain_commit):
    """SRE follow-up 1: `commit_differs` is False when either commit is unknown (the engine's rule, unchanged); the
    smoke needs both known AND equal, or F4 may not be on the MT5 box."""
    w.compat = BridgeCompat(status="ok", reason="ok", bridge_commit=bridge_commit, brain_commit=brain_commit)
    assert not w.compat.commit_differs
    _click(w)
    rep = _run(w)
    assert rep.exit_code == smoke.EXIT_PRECONDITION and w.broker.sent == [] and "gate" not in w.log
    assert "MT5 box" in "\n".join(w.out)
