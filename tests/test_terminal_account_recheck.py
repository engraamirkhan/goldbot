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
        self.login = LOGIN
        self.sends = 0

    def __getattr__(self, name: str) -> Any:      # reads only: anything not defined here is the active login's
        return getattr(self.__dict__["books"][self.__dict__["login"]], name)

    def account(self) -> AccountInfo:
        info = self.books[self.login].account()
        return info.model_copy(update={"login": self.login, "server": SERVER, "trade_mode": "demo",
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
