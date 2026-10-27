"""An account `enabled: false` in config/accounts.yaml never trades (SRE finding 2026-10-27: a demo engine started and
traded on a disabled account).

* Disabled, nothing of its own open at the broker: the engine refuses to start, journals why and exits with
  ENGINE_DISABLED_EXIT, which the unit's RestartPreventExitStatus= (NSSM AppExit) keeps from a restart loop. Nothing is
  written to engine_<account>.json: a `startup_error` there would halt every real account through the supervisor.
* Disabled with its own positions open (or a positions read that fails): the engine starts to wind down. Every entry is
  refused `account_disabled` by the RiskGate; exits are never gated, so the open positions keep their stops, targets,
  time exits and Friday cut. The next start with nothing open refuses as above.
* A live account starts only when `enabled_accounts("live")` returns it (enabled and the phase gate passed): a disabled
  live account never starts, not even to wind down."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.execution.broker import Position
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts
from goldbot.ops import run as run_mod
from goldbot.ops.services import engine as engine_service
from goldbot.risk.gate import AccountState, RiskGate, RiskLimits
from tests.test_engine_history_gap import _engine
from tests.test_engine_start_safety import _Built, _start
from tests.test_engine_watchdog import ACC

DISABLED = ACC.model_copy(update={"enabled": False})


def _position(magic: int) -> Position:
    return Position(position_id=7, symbol="XAUUSD", side=1, lots=0.01, open_price=2000.0, sl=1990.0, tp=2020.0,
                    magic=magic, comment="", open_time_utc=pd.Timestamp("2026-10-27 10:00", tz="UTC"), profit=0.0)


def _paper(monkeypatch, positions: Any) -> None:
    monkeypatch.setenv("GOLDBOT_PAPER_ENGINE", "1")

    def read(self, magic_prefix=None):
        if isinstance(positions, Exception):
            raise positions
        return positions

    monkeypatch.setattr(PaperBroker, "positions", read)


def test_a_disabled_account_with_nothing_open_refuses_to_start_with_the_no_restart_exit_code(tmp_path, monkeypatch,
                                                                                               caplog):
    _paper(monkeypatch, [])
    built = _start(tmp_path, monkeypatch, DISABLED)
    with pytest.raises(SystemExit) as exc:
        run_mod.run_engine("icm-demo")
    assert exc.value.code == engine_service.ENGINE_DISABLED_EXIT
    assert built == []
    assert any("enabled: false" in r.getMessage() for r in caplog.records)
    # no startup_error file: the supervisor halts every real account on one (engine_startup_refused)
    assert not (tmp_path / "state" / "engine_icm-demo.json").exists()


def test_a_disabled_account_with_only_other_engines_positions_open_still_refuses(tmp_path, monkeypatch):
    _paper(monkeypatch, [_position(ACC.magic_base + 100)])      # another engine's magic range: never touched
    built = _start(tmp_path, monkeypatch, DISABLED)
    with pytest.raises(SystemExit) as exc:
        run_mod.run_engine("icm-demo")
    assert exc.value.code == engine_service.ENGINE_DISABLED_EXIT
    assert built == []


def test_a_disabled_account_with_its_own_position_open_starts_to_wind_down_and_manage_the_exit(tmp_path, monkeypatch):
    _paper(monkeypatch, [_position(ACC.magic_base + 3)])
    built = _start(tmp_path, monkeypatch, DISABLED)
    with pytest.raises(_Built):
        run_mod.run_engine("icm-demo")
    [cfg] = built
    assert cfg.wind_down is True


def test_a_disabled_account_whose_positions_cannot_be_read_starts_to_wind_down_rather_than_strand_one(
        tmp_path, monkeypatch):
    _paper(monkeypatch, ConnectionError("bridge reply lost"))
    built = _start(tmp_path, monkeypatch, DISABLED)
    with pytest.raises(_Built):
        run_mod.run_engine("icm-demo")
    [cfg] = built
    assert cfg.wind_down is True


def test_an_enabled_account_starts_trading_normally(tmp_path, monkeypatch):
    _paper(monkeypatch, [])
    built = _start(tmp_path, monkeypatch, ACC)
    with pytest.raises(_Built):
        run_mod.run_engine("icm-demo")
    [cfg] = built
    assert cfg.wind_down is False


@pytest.mark.parametrize("aid", ["icm-live", "vantage-live"])
def test_the_configured_live_accounts_never_start_even_with_positions_open(tmp_path, monkeypatch, aid):
    live = accounts.load_accounts(logins=False)[aid]
    assert live.is_live and not live.enabled                        # config/accounts.yaml as committed
    _paper(monkeypatch, [_position(live.magic_base)])
    built = _start(tmp_path, monkeypatch, live)
    connected: list[str] = []
    monkeypatch.setattr(accounts, "bridge_endpoint", lambda a: connected.append(a))
    with pytest.raises(SystemExit) as exc:
        run_mod.run_engine(aid)
    assert exc.value.code == engine_service.ENGINE_DISABLED_EXIT
    assert built == [] and connected == []                          # refused before any broker connection


def test_an_enabled_live_account_without_the_phase_gate_still_never_starts(tmp_path, monkeypatch):
    live = accounts.load_accounts(logins=False)["icm-live"].model_copy(update={"enabled": True})
    _paper(monkeypatch, [])
    built = _start(tmp_path, monkeypatch, live)
    monkeypatch.setattr(accounts, "phase_state", lambda: {"gates_passed": []})
    with pytest.raises(SystemExit) as exc:
        run_mod.run_engine("icm-live")
    assert exc.value.code == engine_service.ENGINE_DISABLED_EXIT
    assert built == []


def test_the_gate_refuses_every_entry_on_a_winding_down_account():
    gate = RiskGate(RiskLimits())
    st = AccountState(equity=1000, balance_closed_hwm=1000, day_start_equity=1000, week_start_equity=1000,
                      open_positions=1, margin_used=0, last_tick_age_s=0, spread_points=10, account_disabled=True)
    assert "account_disabled" in gate.account_reasons(st)
    assert "account_disabled" not in gate.account_reasons(st.model_copy(update={"account_disabled": False}))


def test_a_winding_down_engine_carries_the_block_from_its_first_tick(tmp_path):
    assert _engine(tmp_path, wind_down=True).state.account_disabled is True
    assert _engine(tmp_path / "on").state.account_disabled is False


def test_the_engine_unit_does_not_restart_a_refused_disabled_account():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "goldbot/ops/linux/systemd/goldbot-engine@.service").read_text()
    assert f"RestartPreventExitStatus={engine_service.ENGINE_DISABLED_EXIT}" in unit
    ps1 = (root / "goldbot/ops/vps_bootstrap.ps1").read_text()
    assert f"AppExit {engine_service.ENGINE_DISABLED_EXIT} Exit" in ps1
