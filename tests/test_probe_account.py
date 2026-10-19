"""ADR 0037 amendment 4: a dedicated demo account (icm-probe) for cost probes, so probes never consume the real
(champion) demo trades' daily/weekly caps and drawdown stages. Config-only, reversible switch-over: exactly one enabled
shadow host at a time; a probe_only account refuses every non-probe entry (fail closed)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.engine import ConstantModel, Engine, EngineConfig, cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.ops import accounts as acc_mod
from goldbot.ops.services.engine import is_shadow_host
from goldbot.risk import RiskLimits
from goldbot.risk.supervisor import Supervisor, others_exposure
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_cost_probe import BASE, _account, _bars_until, _engine, _Long, _Terminal


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The committed accounts.yaml, copied to tmp_path, with no keyring lookups."""
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    cfg = tmp_path / "accounts.yaml"
    cfg.write_text(acc_mod.ACCOUNTS_FILE.read_text())
    return cfg


def _edit(path: Path, **accounts: Any) -> None:
    raw = yaml.safe_load(path.read_text())
    for aid, kw in accounts.items():
        for k, v in kw.items():
            if isinstance(v, dict):
                raw["accounts"][aid][k] = {**raw["accounts"][aid].get(k, {}), **v}
            else:
                raw["accounts"][aid][k] = v
    path.write_text(yaml.safe_dump(raw))


def _hosts(accs: dict[str, acc_mod.Account]) -> list[str]:
    return sorted(a.account_id for a in accs.values() if a.enabled and a.shadow_host)


# ------------------------------------------------------------------------------------------------ the registry
def test_the_shipped_registry_keeps_icm_demo_the_only_shadow_host_until_icm_probe_is_enabled(registry):
    accs = acc_mod.load_accounts(registry)
    assert _hosts(accs) == ["icm-demo"] and accs["icm-demo"].cost_probe.enabled
    probe = accs["icm-probe"]
    assert not probe.enabled and probe.mode == "demo" and probe.probe_only and probe.shadow_host
    assert probe.login is None and probe.cost_probe.enabled and probe.approval_mode == "auto"
    raw = yaml.safe_load(registry.read_text())["accounts"]["icm-probe"]
    assert raw["login"] is None                                   # public repo: never a login here


def test_the_probe_account_has_its_own_day_and_its_probes_fit_inside_it(registry):
    probe = acc_mod.load_accounts(registry)["icm-probe"]
    cp, cap = probe.cost_probe, probe.daily_loss_limit_abs
    assert cap is not None and (cp.max_open, cp.max_loss, cp.budget_share) == (4, 4.0, 0.8)
    assert cp.max_open * cp.max_loss <= cp.budget_share * cap        # four probes at their stops fit the probe budget
    assert probe.magic_base not in {a.magic_base for k, a in acc_mod.load_accounts(registry).items() if k != "icm-probe"}


def test_the_switch_over_is_config_only_and_leaves_icm_probe_the_only_host(registry):
    _edit(registry, **{"icm-probe": {"enabled": True},
                       "icm-demo": {"shadow_host": False, "cost_probe": {"enabled": False}}})
    accs = acc_mod.load_accounts(registry)
    assert _hosts(accs) == ["icm-probe"] and not accs["icm-demo"].cost_probe.enabled


def test_enabling_icm_probe_without_moving_the_host_off_icm_demo_fails_at_load(registry):
    _edit(registry, **{"icm-probe": {"enabled": True}})
    with pytest.raises(ValueError, match="shadow host"):
        acc_mod.load_accounts(registry)


def test_a_disabled_account_flagged_shadow_host_does_not_count_toward_the_one_host(registry):
    _edit(registry, **{"icm-demo": {"enabled": False}})
    assert _hosts(acc_mod.load_accounts(registry)) == []


@pytest.mark.parametrize("kw, match", [
    (dict(cost_probe={"enabled": False}, shadow_host=True), "probe_only"),
    (dict(cost_probe={"enabled": True}, shadow_host=False), "probe_only"),
])
def test_a_probe_only_account_needs_cost_probes_on_and_the_shadow_book(kw, match):
    with pytest.raises(ValidationError, match=match):
        _account(probe_only=True, daily_loss_limit_abs=20.0, approval_mode="auto", **kw)


def test_a_live_account_is_never_probe_only():
    with pytest.raises(ValidationError):
        _account(mode="live", probe_only=True, shadow_host=True, cost_probe={"enabled": True})


# ------------------------------------------------------------------------------------------------ the engine service
def test_the_engine_hosts_the_shadow_book_only_on_the_flagged_account_of_the_canonical_broker():
    settings = load_settings()
    canonical = next(b for b, c in settings.brokers.items() if c.canonical_costs)
    other = next(b for b, c in settings.brokers.items() if not c.canonical_costs)
    assert is_shadow_host(_account(broker=canonical, shadow_host=True), settings)
    assert not is_shadow_host(_account(broker=canonical, shadow_host=False), settings)
    assert not is_shadow_host(_account(broker=other, shadow_host=True), settings)


# ------------------------------------------------------------------------------------------------ the engine
def test_a_probe_only_engine_refuses_a_non_probe_entry_and_sends_nothing(tmp_path):
    agent = _Long()
    eng, pb, tick, close = _engine(tmp_path, agents=[agent], probe=False, live_shares={agent.agent_id: 1.0},
                                   champions={agent.agent_id: f"{agent.agent_id}-v2"})
    eng.cfg.probe_only = True
    eng.on_bar_close(close, tick)
    assert not pb.positions() and not eng.open
    assert any(d.get("action") == "probe_only_refused" and d.get("agent") == agent.agent_id for d in eng.decisions)


def test_a_probe_only_engine_still_sends_probes(tmp_path):
    eng, pb, tick, close = _engine(tmp_path)
    eng.cfg.probe_only = True
    eng.on_bar_close(close, tick)
    [pos] = pb.positions()
    [tr] = eng.open.values()
    assert cost_probe.is_probe(tr.agent_id) and pos.lots == pytest.approx(0.01)


def test_a_probe_only_account_keeps_no_real_trade_reserve_inside_its_own_day(tmp_path, monkeypatch):
    """The reserve keeps 60% of the day for real trades; a probe-only account has none, so its probe budget alone
    binds (still inside the account's own daily limit, which the RiskGate enforces unchanged). £50 day, £30 left."""
    from goldbot.engine import limits_apply
    monkeypatch.setattr(limits_apply, "daily_allowance",
                        lambda gate, st: {"daily_loss_limit": 50.0, "daily_loss_remaining": 30.0})
    shared, _, _, close = _engine(tmp_path / "shared", budget=0.4)
    assert shared.probe is not None
    assert cost_probe.budget_refusal(shared, shared.probe, close)[0] == "real_trade_reserve"   # 30 - 4 < 0.6 x 50
    own, _, _, close2 = _engine(tmp_path / "own", budget=0.4)
    own.cfg.probe_only = True
    assert own.probe is not None
    why, headroom = cost_probe.budget_refusal(own, own.probe, close2)
    assert why is None and headroom == pytest.approx(20.0)                  # 0.4 x £50, no reserve held back
    assert (cost_probe.real_trade_reserve(True), cost_probe.real_trade_reserve(False)) == (0.0, 0.6)


def test_the_dashboard_probe_stop_on_a_probe_only_account_is_its_probe_budget():
    from goldbot.api import home_cards as hc
    day = {"daily_loss_limit": 20.0, "daily_loss_remaining": 20.0}
    own = hc._meter(day, hc.ProbeTerms(budget_share=0.8, max_loss=4.0, probe_only=True))
    shared = hc._meter(day, hc.ProbeTerms(budget_share=0.8, max_loss=4.0))
    assert own is not None and shared is not None
    assert [(c.key, c.limit) for c in own.limits] == [("probe_stop", 16.0), ("daily_limit", 20.0)]
    assert [(c.key, c.limit) for c in shared.limits] == [("probe_stop", 4.0), ("daily_limit", 20.0)]


def test_the_new_host_continues_the_existing_shadow_books(tmp_path):
    """state/shadow_book.json is per state dir, not per account: icm-probe's engine picks up icm-demo's books."""
    now = pd.Timestamp("2025-03-04 20:00", tz="UTC")
    old = ShadowBook(tmp_path)
    old.track("agent-v2", now)
    old.save(now)
    pb = _Terminal("demo", equity=10_000, adverse_slip_points=0.0)
    agent = _Long()
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, shadow_host=True,
                              cost_probe=True, probe_only=True),
                 pb, [agent], {agent.family: ConstantModel(p=0.9)}, ApprovalCenter({111}),
                 limits=RiskLimits(**BASE), live_shares={},
                 shadow_models={"agent-v2": (agent.agent_id, ConstantModel(p=0.9))}, live_versions={})
    _bars_until(eng, pb, now)
    assert eng.shadow is not None and "agent-v2" in eng.shadow.books
    assert eng.shadow.books["agent-v2"].started_utc == now


# ------------------------------------------------------------------------------------------------ the supervisor
def _engine_file(state: Path, account: str, *, equity: float, day0: float = 1_000.0, lots: float = 0.0,
                 currency: str = "GBP", **extra: Any) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "mode": "demo", "ts": time.time(), "currency": currency, "equity": equity,
        "day_start_equity": day0, "week_start_equity": day0, "balance_closed_hwm": day0, "open_lots": lots,
        "open_notional": lots * 100 * 2_000.0, **extra}))


def test_a_probe_accounts_losses_never_reach_the_real_accounts_combined_limits(tmp_path):
    _engine_file(tmp_path, "icm-demo", equity=1_000.0, lots=0.02)
    _engine_file(tmp_path, "icm-probe", equity=500.0, lots=0.04, probe_only=True)     # a 50% day on the probe account
    s = Supervisor(tmp_path).evaluate()
    assert not s["halt"] and s["reasons"] == [] and not s["size_down"]
    assert s["combined_equity"] == 1_000.0 and s["combined_day_start_equity"] == 1_000.0
    assert set(s["exposure"]) == {"icm-demo"} and s["combined_open_lots"] == pytest.approx(0.02)
    probe = s["probe_engines"]["icm-probe"]                                           # reported separately
    assert probe["lots"] == pytest.approx(0.04) and probe["equity"] == 500.0 and probe["currency"] == "GBP"


def test_a_probe_account_in_another_currency_does_not_halt_the_real_accounts(tmp_path):
    _engine_file(tmp_path, "icm-demo", equity=1_000.0)
    _engine_file(tmp_path, "icm-probe", equity=1_000.0, currency="USD", probe_only=True)
    s = Supervisor(tmp_path).evaluate()
    assert "mixed_account_currencies" not in s["reasons"] and s["currencies"] == {"icm-demo": "GBP"}


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_an_engine_file_without_a_literal_true_probe_flag_counts_as_a_real_account(tmp_path, flag):
    """Fail closed: a missing, false or malformed flag keeps the account inside the combined limits."""
    extra = {} if flag is None else {"probe_only": flag}
    _engine_file(tmp_path, "icm-demo", equity=1_000.0)
    _engine_file(tmp_path, "icm-probe", equity=500.0, **extra)
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and "combined_daily_cap" in s["reasons"] and "icm-probe" in s["exposure"]
    assert s["probe_engines"] == {}


def test_a_probe_engine_counts_the_real_accounts_lots_but_never_their_equity(tmp_path):
    """The probe engine's own combined caps stay strict: the real accounts' open lots count against it, their equity
    never widens its notional cap; a real engine never sees the probe's lots."""
    _engine_file(tmp_path, "icm-demo", equity=1_000.0, lots=0.02)
    _engine_file(tmp_path, "icm-probe", equity=1_000.0, lots=0.04, probe_only=True)
    s = Supervisor(tmp_path).evaluate()
    lots, notional, equity = others_exposure(s, "icm-probe", probe_only=True)
    assert lots == pytest.approx(0.02) and notional > 0 and equity == 0.0
    assert others_exposure(s, "icm-demo", probe_only=False) == (0.0, 0.0, 0.0)


def test_the_engine_file_says_whether_the_account_is_probe_only(tmp_path):
    eng, _, _, _ = _engine(tmp_path)
    eng.cfg.probe_only = True
    eng._write_state()
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["probe_only"] is True


# ------------------------------------------------------------------------------------------------ dynamic limits
def test_dynamic_limits_are_off_unless_the_account_turns_them_on():
    assert _account().dynamic_limits is False
    assert _account(dynamic_limits=True).dynamic_limits is True


def test_the_shipped_registry_turns_dynamic_limits_on_for_the_demo_accounts_and_off_for_live(registry):
    """docs/design/dynamic-limits-2026-10-19.md: "config/accounts.yaml (icm-demo, icm-probe true)"; live stays off
    (owner-only)."""
    accs = acc_mod.load_accounts(registry)
    assert accs["icm-demo"].dynamic_limits and accs["icm-probe"].dynamic_limits
    assert not any(a.dynamic_limits for a in accs.values() if a.mode == "live")
