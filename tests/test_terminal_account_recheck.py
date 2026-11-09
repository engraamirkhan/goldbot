"""Trading-safety review 2026-11-09, R1: the brain checked the terminal's account only when the engine started
(ops/services/engine.py). A terminal switched afterwards to another login would have had the engine read that login's
positions, deals and equity as its own: adopt its positions, infer closes of its own trades from a book that does not
hold them, and take its equity for the drawdown stages. Design rule: the same check as at start
(`accounts.verify_terminal_account`) runs on every account read; on a mismatch entries are refused
`terminal_account_changed`, the book is `positions_unreadable` (no close and no adoption is inferred from it; the
positions keep their broker-side stops, exits are never gated), and health FAILs with a Telegram alert. Back on the
account, trading resumes.

The logins below are made up."""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine
from goldbot.execution.broker import AccountInfo, OrderIntent, OrderResult
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts, health
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.risk.gate import Stage
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.bus import ApprovalBus
from tests.test_approved_card_cap import _approve, _card
from tests.test_engine_clock import WED, _fresh_bar, _intent, _tick
from tests.test_engine_clock import _engine as _clock_engine
from tests.test_engine_safety import _notices, _prop

LOGIN, OTHER, SERVER = 11112222, 33334444, "Example-Demo"
ACC = accounts.Account(account_id="icm-demo", broker="icm", mode="demo", server=SERVER, login=LOGIN,
                       terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
AGENT = SPECIALISTS["session_open"]()
PID = "icm-demo-r1-a"
SL, TP = 2396.0, 2406.0


class _Terminal:
    """Stands in for the RemoteBroker of one MT5 terminal: whatever login the terminal is on (`login`), its book.
    The engine's account (LOGIN) holds 10k, the other login 50k. Every order call is counted in `sends`."""

    name = "mt5-remote-icm-demo"

    def __init__(self) -> None:
        self.books: dict[int, PaperBroker] = {LOGIN: PaperBroker(equity=10_000), OTHER: PaperBroker(equity=50_000)}
        self.books[OTHER]._ids = itertools.count(900_001)   # tickets unique per server, as MT5's
        self.login = LOGIN
        self.sends = 0
        self.servers = {LOGIN: SERVER, OTHER: SERVER}       # another login on the same trade server, by default

    def __getattr__(self, name: str) -> Any:      # reads only: anything not defined here is the active login's
        return getattr(self.__dict__["books"][self.__dict__["login"]], name)

    def account(self) -> AccountInfo:
        info = self.books[self.login].account()
        return info.model_copy(update={"login": self.login, "server": self.servers[self.login], "trade_mode": "demo",
                                       "margin_mode": "hedging"})

    def on_tick(self, t: Any) -> None:
        for b in self.books.values():
            b.on_tick(t)

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.sends += 1
        return self.books[self.login].place_order(intent)


def _verify(info: Any) -> str | None:
    return accounts.verify_terminal_account(ACC, info)


def _running(tmp_path: Path, *, verify: bool = True) -> tuple[Engine, _Terminal, list[pd.Timestamp]]:
    """An engine on the terminal (live clock, WED), with the start check wired in as the service does, one trade of
    its own open on its account, and the other login holding a position in the engine's magic range."""
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": "raw"}))
    term = _Terminal()
    term.on_tick(_tick(WED))
    eng, _, clock = _clock_engine(tmp_path, pb=term)       # type: ignore[arg-type]
    eng.center.bus = ApprovalBus(tmp_path)
    if verify:
        eng.verify_account = _verify
    eng._refresh_account(_tick(WED))
    res = eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)
    assert res is not None and res.ok and res.position_id in eng.open
    [mine] = term.books[LOGIN].positions()
    term.books[OTHER].place_order(OrderIntent(client_order_id="someone-else", symbol="XAUUSD", side=-1, lots=0.5,
                                              sl=2410.0, tp=2390.0, magic=mine.magic))
    return eng, term, clock


def _passes(eng: Engine, at: pd.Timestamp) -> None:
    """Reconciliation and enough bar closes' position management for an unconfirmed close to be recorded anyway."""
    eng._reconcile(at)
    for _ in range(eng.cfg.close_confirm_checks + 1):
        eng._manage_open(pd.DataFrame())
    eng._flush_closed()


def _health(tmp_path: Path, eng: Engine, now: pd.Timestamp) -> health.Check:
    eng._write_state()
    return health.check_engine(health.HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None),
                               "icm-demo")


def test_a_terminal_switched_to_another_login_sends_no_order_infers_no_close_or_adoption_and_fails_health(tmp_path):
    eng, term, _ = _running(tmp_path)
    [own] = list(eng.open)
    prop = _card(eng, _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0)))   # allowed before the switch
    sends = term.sends
    term.login = OTHER                                      # someone logs the terminal in to another account
    eng._refresh_account(_tick(WED))
    assert "terminal_account_changed" in eng.gate.account_reasons(eng.state)
    assert eng.state.equity == pytest.approx(10_000, abs=50)   # the other login's 50k is never this account's equity
    _approve(eng, prop)
    assert term.sends == sends                              # refused at approval: nothing sent
    assert any("terminal_account_changed" in str(d.get("action")) for d in eng.decisions)
    _passes(eng, WED)
    assert list(eng.open) == [own]                          # its trade kept, the other login's position not adopted
    closed, _ = load_closed_trades(tmp_path)
    assert closed == []                                     # no close inferred from the other login's book
    assert eng._book_unreadable() > 0
    c = _health(tmp_path, eng, WED)
    assert c.status == "fail" and "not on this account" in c.reason
    [alert] = [n for n in _notices(tmp_path) if "MT5 terminal" in n]
    assert "33334444" not in alert and "11112222" not in alert and SERVER not in alert   # no login, no server


def test_the_mismatch_is_caught_by_reconciliation_before_the_next_account_refresh(tmp_path):
    eng, term, _ = _running(tmp_path)
    [own] = list(eng.open)
    term.login = OTHER
    eng._reconcile(WED)                                     # its own account read: the switch is seen here first
    assert list(eng.open) == [own]
    assert "terminal_account_changed" in eng.gate.account_reasons(eng.state)


def test_switching_back_to_the_account_resumes_trading(tmp_path):
    eng, term, clock = _running(tmp_path)
    term.login = OTHER
    eng._refresh_account(_tick(WED))
    term.login = LOGIN
    eng._refresh_account(_tick(WED))                      # back: entries wait out the resume delay from here
    assert eng.gate.account_reasons(eng.state) == ["data_recovering"]
    later = WED + pd.Timedelta(seconds=eng.cfg.healthy_resume_s + 1)
    clock[0] = later
    _fresh_bar(eng, later)
    term.on_tick(_tick(later))
    eng._refresh_account(_tick(later))
    assert eng.gate.account_reasons(eng.state) == []
    prop = _card(eng, _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0)))
    sends = term.sends
    _approve(eng, prop)
    assert term.sends == sends + 1
    assert _health(tmp_path, eng, later).status != "fail"
    assert any("back on" in n for n in _notices(tmp_path))


def test_a_matching_account_on_every_pass_behaves_exactly_as_without_the_check(tmp_path):
    seen = {}
    for verify in (True, False):
        d = tmp_path / str(verify)
        d.mkdir()
        eng, term, _ = _running(d, verify=verify)
        [mine] = term.books[LOGIN].positions()
        term.books[LOGIN].place_order(OrderIntent(client_order_id="orphan", symbol="XAUUSD", side=-1, lots=0.1,
                                                  sl=2410.0, tp=2390.0, magic=mine.magic))   # adopted as before
        for _ in range(3):
            eng._refresh_account(_tick(WED))
            _passes(eng, WED)
        prop = _card(eng, _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0)))
        sends = term.sends
        _approve(eng, prop)
        state = json.loads((d / "engine_icm-demo.json").read_text()) if (d / "engine_icm-demo.json").exists() else {}
        adopted = sum(1 for x in eng.decisions if x.get("action") == "adopt_orphan")
        seen[verify] = (term.sends - sends, sorted(eng.open), adopted, eng.gate.account_reasons(eng.state),
                        eng._book_unreadable(), state.get("terminal_account_changed"))
    assert seen[True] == seen[False]
    assert seen[True][0] == 1 and seen[True][2] == 1       # the entry sent, the orphan adopted


def test_a_check_that_raises_counts_as_a_mismatch(tmp_path):
    eng, term, _ = _running(tmp_path)

    def broken(info: Any) -> str | None:
        raise ValueError("bad reply")
    eng.verify_account = broken
    eng._refresh_account(_tick(WED))
    assert "terminal_account_changed" in eng.gate.account_reasons(eng.state)


def test_the_service_wires_in_the_start_check_itself():
    from goldbot.ops.services.engine import verify_account
    check = verify_account(ACC)
    term = _Terminal()
    assert check(term.account()) is None
    term.login = OTHER
    assert check(term.account()) == "terminal is logged in to another account than icm-demo"


# ---------------------------------------------------------------------------------------------- review round 1
def _switched(tmp_path: Path, *, check_raises: bool = False) -> tuple[Engine, _Terminal, list[pd.Timestamp], int]:
    """`_running`, then the mismatch flagged: a real switch to the other login, or (`check_raises`) a false positive,
    the terminal still on this account but the check failing. Returns the engine's own open position too."""
    eng, term, clock = _running(tmp_path)
    [own] = list(eng.open)
    if check_raises:
        def broken(info: Any) -> str | None:
            raise ValueError("bad reply")
        eng.verify_account = broken
    else:
        term.login = OTHER
    eng._refresh_account(_tick(WED))
    assert "terminal_account_changed" in eng.gate.account_reasons(eng.state)
    return eng, term, clock, own


def _other_book(term: _Terminal) -> list[int]:
    return [p.position_id for p in term.books[OTHER].positions()]


def test_a_risk_day_roll_during_a_mismatch_keeps_this_account_s_day_start_and_rolls_after_the_restore(tmp_path):
    eng, term, _, _ = _switched(tmp_path)
    day_start, week_start, last = eng.state.day_start_equity, eng.state.week_start_equity, eng._last_reset
    midnight = (WED + pd.Timedelta(days=1)).normalize() + pd.Timedelta(seconds=30)
    eng._roll_risk_period(midnight)
    assert (eng.state.day_start_equity, eng.state.week_start_equity) == (day_start, week_start)
    assert eng.state.equity < 20_000 and eng._last_reset == last           # not the other login's 50k; not rolled
    term.login = LOGIN
    eng._roll_risk_period(midnight)
    assert eng._last_reset == midnight and eng.state.day_start_equity < 20_000


@pytest.mark.parametrize("check_raises", [False, True])
def test_a_due_close_is_still_sent_by_its_ticket_while_a_mismatch_is_flagged(tmp_path, check_raises):
    eng, term, _, own = _switched(tmp_path, check_raises=check_raises)
    eng._close_due[own] = ("time_exit", {"symbol": "XAUUSD"}, WED)
    eng._retry_closes(WED + pd.Timedelta(seconds=30))
    assert [d["position"] for d in eng.decisions if d.get("action") == "time_exit"] == [own]   # sent, not dropped
    if check_raises:                                     # a false positive: the exit really happens
        assert own not in eng.open and own not in [p.position_id for p in term.books[LOGIN].positions()]
    else:                                                # another login: refused there (no such ticket), still due
        assert own in eng._close_due and own in eng.open and len(_other_book(term)) == 1


@pytest.mark.parametrize("check_raises", [False, True])
def test_a_trade_through_its_stop_is_closed_while_a_mismatch_is_flagged(tmp_path, check_raises):
    eng, term, _, own = _switched(tmp_path, check_raises=check_raises)
    eng._stop_check(_tick(WED + pd.Timedelta(seconds=5), bid=SL - 1.0))
    assert [d["position"] for d in eng.decisions if d.get("action") == "engine_stop_close"] == [own]
    if check_raises:
        assert own not in eng.open
    assert len(_other_book(term)) == 1


def test_the_kill_switch_during_a_mismatch_closes_this_account_s_known_tickets_only(tmp_path):
    eng, term, _, own = _switched(tmp_path)
    eng._kill_switch(everything=True)
    assert [d["position"] for d in eng.decisions if d.get("action") == "kill_switch_close"] == [own]
    assert len(_other_book(term)) == 1 and eng._kill_pending and eng._kill_everything   # never seen flat


def test_a_whole_account_kill_pending_then_a_switch_then_a_no_quote_pass_leaves_the_other_book_alone(tmp_path):
    eng, term, _ = _running(tmp_path)
    eng.state.stage = Stage.HALTED
    eng._kill_everything = eng._kill_pending = True
    term.login = OTHER                                   # no account refresh in between
    eng.no_quote_pass()
    assert len(_other_book(term)) == 1
    assert "terminal_account_changed" in eng.gate.account_reasons(eng.state)


def test_a_re_arm_during_a_mismatch_is_refused_unconsumed_and_works_after_the_restore(tmp_path):
    eng, term, _ = _running(tmp_path)
    eng.state.stage = Stage.HALTED
    eng._rearm_conditions = lambda now: None             # type: ignore[method-assign]
    eng._save_risk_state_now("test")
    risk = tmp_path / "risk_icm-demo.json"
    before, hwm = risk.read_text(), eng.state.balance_closed_hwm
    term.login = OTHER
    eng._refresh_account(_tick(WED))
    assert eng.center.bus is not None
    eng.center.bus.owner_rearm(by="dashboard:o")
    eng._poll_rearm(WED)
    assert eng.state.stage == Stage.HALTED and eng._rearm_refused == "terminal_account_changed"
    assert eng.state.balance_closed_hwm == hwm and risk.read_text() == before
    term.login = LOGIN
    eng._refresh_account(_tick(WED))
    eng._poll_rearm(WED)                                 # the same re-arm, not used up by the refusal
    stage: Stage = eng.state.stage                       # (mypy narrowed it to HALTED above)
    assert stage == Stage.NORMAL and eng._rearm_refused is None


def test_a_trade_recorded_during_a_mismatch_takes_this_account_s_equity_before(tmp_path):
    eng, term, _, own = _switched(tmp_path, check_raises=True)
    eng.open[own].equity_before = None                   # an adopted trade: no equity stamped at send
    eng._try_close(own, "time_exit", WED)
    term.books[LOGIN]._balance = 77_000.0                # the broker's balance read is not used while flagged
    eng._flush_closed()
    closed, _ = load_closed_trades(tmp_path)
    assert [c.equity_before for c in closed] == [pytest.approx(eng.state.equity)]


def test_every_book_waits_out_the_resume_delay_after_the_restore(tmp_path):
    eng, term, clock, _ = _switched(tmp_path)
    term.login = LOGIN
    soon = WED + pd.Timedelta(seconds=20)
    clock[0] = soon
    _fresh_bar(eng, soon)
    term.on_tick(_tick(soon))
    eng._refresh_account(_tick(soon))                    # the restore
    eng._unhealthy_at = None                             # as a book that never refreshed during the mismatch
    eng._refresh_account(_tick(soon))
    assert "data_recovering" in eng.gate.account_reasons(eng.state)
    later = soon + pd.Timedelta(seconds=eng.cfg.healthy_resume_s + 1)
    clock[0] = later
    _fresh_bar(eng, later)
    term.on_tick(_tick(later))
    eng._refresh_account(_tick(later))
    assert eng.gate.account_reasons(eng.state) == []


# ---------------------------------------------------------------------------------------------- residual-risk ruling
OTHER_SERVER = "Elsewhere-Live"     # made up


def _closes_sent(eng: Engine) -> list[tuple[str, int]]:
    return [(d["action"], d["position"]) for d in eng.decisions
            if d.get("action") in ("time_exit", "engine_stop_close", "kill_switch_close")]


def _on_other_server(tmp_path: Path) -> tuple[Engine, _Terminal, int]:
    eng, term, _ = _running(tmp_path)
    [own] = list(eng.open)
    term.servers[OTHER] = OTHER_SERVER
    term.login = OTHER
    eng._refresh_account(_tick(WED))
    assert "terminal_account_changed" in eng.gate.account_reasons(eng.state)
    return eng, term, own


def test_a_switch_to_another_trade_server_sends_no_close_by_ticket(tmp_path, caplog):
    eng, term, own = _on_other_server(tmp_path)
    eng._close_due[own] = ("time_exit", {"symbol": "XAUUSD"}, WED)
    eng._retry_closes(WED + pd.Timedelta(seconds=30))
    eng._stop_check(_tick(WED + pd.Timedelta(seconds=31), bid=SL - 1.0))
    eng._kill_switch(everything=True)
    assert _closes_sent(eng) == []                       # tickets are unique per server only: nothing is sent
    assert own in eng._close_due and own in eng.open and len(_other_book(term)) == 1   # kept, its stops at the broker
    text = " ".join(_notices(tmp_path)) + caplog.text + json.dumps(eng.decisions, default=str)
    assert OTHER_SERVER not in text and SERVER not in text       # never a server name


def test_back_on_the_account_after_another_server_closes_by_ticket_again(tmp_path):
    eng, term, own = _on_other_server(tmp_path)
    term.login = LOGIN
    eng._refresh_account(_tick(WED))
    assert eng._terminal_other_server is False
    eng._close_due[own] = ("time_exit", {"symbol": "XAUUSD"}, WED)
    eng._retry_closes(WED + pd.Timedelta(seconds=30))
    assert _closes_sent(eng) == [("time_exit", own)]


@pytest.mark.parametrize("check_raises", [False, True])
def test_another_login_on_the_same_server_or_a_check_that_raised_still_sends_every_close(tmp_path, check_raises):
    eng, term, _, own = _switched(tmp_path, check_raises=check_raises)
    assert eng._terminal_other_server is False
    eng._close_due[own] = ("time_exit", {"symbol": "XAUUSD"}, WED)
    eng._retry_closes(WED + pd.Timedelta(seconds=30))
    eng._kill_switch(everything=True)
    sent = _closes_sent(eng)
    assert ("time_exit", own) in sent


def test_an_empty_server_in_the_read_still_sends_by_ticket(tmp_path):
    eng, term, _ = _running(tmp_path)
    [own] = list(eng.open)
    term.servers[OTHER] = ""
    term.login = OTHER
    eng._refresh_account(_tick(WED))
    assert eng._terminal_mismatch is not None and eng._terminal_other_server is False
    eng._close_due[own] = ("time_exit", {"symbol": "XAUUSD"}, WED)
    eng._retry_closes(WED + pd.Timedelta(seconds=30))
    assert _closes_sent(eng) == [("time_exit", own)]
