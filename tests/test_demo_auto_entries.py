"""ADR 0036, the owner 2026-10-18: "for the demo account, the trades will be opened and closed by the system, no
confirmation is needed, for real account with real money, stick to original plan of confirming from me".

Per-account entry approval (`approval_mode` in config/accounts.yaml) with a hard lock at three layers: the account
record (load), the engine's start, and every refresh plus every auto entry (the broker's own trade mode). The re-arm
window, the kill switch and the owner's /mode propose still force propose on demo; exits are unchanged."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, auto_entry
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.risk.gate import Stage
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus

OWNER = 111


class _Terminal(PaperBroker):
    """A paper broker that reports a terminal trade mode, as MT5's account_info does (demo | real | contest)."""

    def __init__(self, trade_mode: str | None, **kw: Any):
        super().__init__(**kw)
        self.trade_mode = trade_mode

    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": self.trade_mode})


def _engine(tmp_path: Path, *, mode: str = "demo", approval: str = "auto", trade_mode: str | None = "demo"):
    pb = _Terminal(trade_mode, equity=10_000)
    center = ApprovalCenter({OWNER}, bus=ApprovalBus(tmp_path / "bus"))
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)   # permissive for the test
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode=mode, approval_mode=approval, declared_class="raw",
                              state_dir=str(tmp_path), owner_user_id=OWNER),
                 pb, [spec], {"session_open": ConstantModel(p=0.65)}, center)
    return eng, pb, center


def _drive(eng: Engine, days: int = 5) -> list[dict]:
    ticks = synthetic_ticks("2025-03-03", f"2025-03-{3 + days:02d}", ticks_per_minute=1, seed=5)
    out: list[dict] = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    return out


def _actions(decisions: list[dict]) -> list[str]:
    return [str(d["action"]) for d in decisions]


def _notices(tmp_path: Path) -> list[str]:
    f = tmp_path / "bus" / "agent_runs.jsonl"
    return [json.loads(line)["detail"] for line in f.read_text().splitlines()] if f.exists() else []


def _account(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="a", broker="icm", mode="demo", server="s", login=None, terminal_path="t",
                                server_tz="UTC", symbol="XAUUSD", magic_base=1, enabled=True)
    return acc_mod.Account(**{**base, **kw})


# ---------------------------------------------------------------------------------------------- layer 1: the account
def test_the_shipped_icm_demo_account_enters_automatically_and_no_live_account_does(monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    accs = acc_mod.load_accounts()
    assert accs["icm-demo"].approval_mode == "auto"
    assert all(a.approval_mode == "propose" for a in accs.values() if a.mode != "demo")


def test_an_account_without_approval_mode_asks_the_owner():
    assert _account().approval_mode == "propose"


def test_a_live_account_configured_auto_is_refused_at_load(tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="demo accounts only"):
        _account(mode="live", approval_mode="auto")
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    raw = yaml.safe_load(Path("config/accounts.yaml").read_text())
    raw["accounts"]["icm-live"]["approval_mode"] = "auto"
    f = tmp_path / "accounts.yaml"
    f.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError, match="demo accounts only"):
        acc_mod.load_accounts(f)


# ---------------------------------------------------------------------------------------------- layer 2: engine start
@pytest.mark.parametrize("mode", ["live", "anything"])
def test_the_engine_refuses_to_start_a_non_demo_account_in_auto(tmp_path, mode):
    with pytest.raises(ValueError, match="auto entries"):
        _engine(tmp_path, mode=mode, trade_mode="real")


@pytest.mark.parametrize("mode, trade_mode, allowed", [
    ("demo", "demo", True), ("paper", None, True), ("paper", "demo", True),
    ("demo", "real", False), ("demo", None, False), ("demo", "contest", False), ("live", "real", False),
    ("live", "demo", False), ("paper", "real", False), ("anything", "demo", False)])
def test_auto_entries_need_a_demo_account_on_a_terminal_that_reports_demo(mode, trade_mode, allowed):
    assert (auto_entry.refusal(mode, trade_mode) is None) is allowed


# ---------------------------------------------------------------------------------------------- the demo contract
@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_a_demo_account_on_a_demo_terminal_enters_through_the_riskgate_without_a_proposal(tmp_path):
    eng, pb, center = _engine(tmp_path)
    checks: list[bool] = []
    check = eng.gate.check

    def spy(*a: Any, **kw: Any):
        gd = check(*a, **kw)
        checks.append(gd.allowed)
        return gd

    eng.gate.check = spy
    decisions = _drive(eng)
    executed = [a for a in _actions(decisions) if a == "executed:auto"]
    assert executed and "proposed" not in _actions(decisions) and not center.pending
    assert sum(checks) >= len(executed)                        # every entry was allowed by the RiskGate first
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    assert (deals["type"] == "entry").sum() == len(executed)


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_a_demo_auto_entry_tells_the_owner_it_opened_with_no_buttons(tmp_path):
    eng, _, center = _engine(tmp_path)
    decisions = _drive(eng)
    n = _actions(decisions).count("executed:auto")
    opened = [t for t in _notices(tmp_path) if t.startswith("Opened automatically (demo):")]
    assert n >= 1 and len(opened) == n
    assert "XAUUSD on icm-demo" in opened[0] and "exits are automatic" in opened[0]
    assert not center.pending


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_exits_of_demo_auto_entries_stay_automatic(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    _drive(eng, days=5)
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    assert (deals["type"] == "entry").sum() >= 1 and (deals["type"] != "entry").sum() >= 1   # closed without asking


# ---------------------------------------------------------------------------------------------- layer 3: refresh/entry
@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_a_demo_config_on_a_real_terminal_proposes_instead_of_entering(tmp_path):
    eng, pb, center = _engine(tmp_path, trade_mode="real")
    decisions = _drive(eng)
    assert "executed:auto" not in _actions(decisions) and "proposed" in _actions(decisions)
    assert eng.cfg.approval_mode == "propose" and pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC")).empty


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_a_live_engine_tampered_to_auto_in_memory_still_proposes(tmp_path):
    eng, pb, _ = _engine(tmp_path, mode="live", approval="propose", trade_mode="real")
    eng.cfg.approval_mode = "auto"                             # a tampered config, after the start check
    eng.center.bus.set_mode("auto", by="dashboard", reason="test")   # and the owner's /mode auto
    decisions = _drive(eng)
    assert "executed:auto" not in _actions(decisions) and "proposed" in _actions(decisions)
    assert pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC")).empty


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_the_entry_check_alone_refuses_auto_on_a_real_terminal(tmp_path, monkeypatch):
    # the refresh layer broken (it lets anything through): the check at the order still reads the broker and proposes
    monkeypatch.setattr(auto_entry, "locked", lambda mode, cfg_mode, trade_mode: mode)
    eng, pb, _ = _engine(tmp_path, trade_mode="real")
    decisions = _drive(eng)
    assert eng.cfg.approval_mode == "auto"
    assert "executed:auto" not in _actions(decisions) and "proposed" in _actions(decisions)
    assert pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC")).empty


# ---------------------------------------------------------------------------------------------- what still wins on demo
@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_the_re_arm_window_keeps_a_demo_account_on_propose(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._propose_only_until = pd.Timestamp("2030-01-01", tz="UTC")
    decisions = _drive(eng)
    assert "executed:auto" not in _actions(decisions) and "proposed" in _actions(decisions)
    assert eng.cfg.approval_mode == "propose"


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_a_demo_account_enters_automatically_again_after_the_re_arm_window(tmp_path):
    eng, _, _ = _engine(tmp_path)
    eng._propose_only_until = pd.Timestamp("2025-03-01", tz="UTC")        # ended before the ticks start
    assert "executed:auto" in _actions(_drive(eng))


def test_the_kill_switch_keeps_a_demo_account_on_propose(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    tick = Tick(ts_utc=pd.Timestamp("2025-03-03 08:00", tz="UTC"), bid=2400.0, ask=2400.3)
    pb.on_tick(tick)
    eng.state.stage = Stage.HALTED
    eng._refresh_account(tick)
    assert eng.cfg.approval_mode == "propose"


@pytest.mark.integration      # drives 5 days of synthetic ticks through the engine (as tests/test_engine.py)
def test_the_owners_mode_propose_keeps_a_demo_account_on_propose(tmp_path):
    eng, _, _ = _engine(tmp_path)
    eng.center.bus.set_mode("propose", by="telegram", reason="owner")
    decisions = _drive(eng)
    assert "executed:auto" not in _actions(decisions) and "proposed" in _actions(decisions)
