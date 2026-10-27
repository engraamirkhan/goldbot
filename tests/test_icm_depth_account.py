"""ICM depth host (owner 2026-10-27; docs/deploy/icm-depth-host.md): the shipped registry carries `icm-depth`, a demo
IC Markets account on its own MT5 box, OFF and armless until the box is verified and the arm move is reviewed. The
registry still loads with it, the live accounts stay disabled, no login is written, its magics do not overlap another
account's, and the existing account templates (engine, bridge tunnel) serve it with no new unit file."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from goldbot.ops import accounts as acc_mod

REPO = Path(__file__).resolve().parents[1]
ACCOUNTS = REPO / "config" / "accounts.yaml"
SYSTEMD = REPO / "goldbot" / "ops" / "linux" / "systemd"
MAGIC_SPAN = 100            # goldbot/engine/magic.py: an account's magics are magic_base .. magic_base + 99


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_icm_depth_ships_as_a_disabled_armless_icm_demo(accounts) -> None:
    acc = accounts["icm-depth"]
    assert acc.mode == "demo" and acc.broker == "icm" and acc.server == "ICMarketsSC-Demo"
    assert not acc.enabled and acc.login is None
    assert acc.symbol == "XAUUSD" and acc.server_tz == "NY+7" and acc.magic_base == 260600
    assert acc.terminal_path == r"C:\MT5\ICMarkets\terminal64.exe"
    # nothing moves before the trading-safety + quant review of the arm move: no arms, probes, overrides or auto
    assert not acc.exploration.enabled and not acc.exploration.arms
    assert not acc.cost_probe.enabled and not acc.shadow_host and not acc.probe_only
    assert acc.approval_mode == "propose" and acc.risk_overrides is None and acc.max_positions is None
    assert acc_mod.Account.model_fields["dynamic_limits"].default == acc.dynamic_limits


def test_icm_depth_is_not_an_enabled_account(accounts) -> None:
    assert "icm-depth" not in {a.account_id for a in acc_mod.enabled_accounts()}


def test_live_accounts_stay_disabled_and_every_login_is_null(accounts) -> None:
    assert {a for a, x in accounts.items() if x.mode == "live"} == {"icm-live", "vantage-live"}
    assert not any(x.enabled for x in accounts.values() if x.mode == "live")
    raw = ACCOUNTS.read_text(encoding="utf-8")
    assert all(line.split("#")[0].strip() == "login: null" for line in raw.splitlines() if "login:" in line.split("#")[0])


def test_magic_ranges_do_not_overlap(accounts) -> None:
    bases = sorted(a.magic_base for a in accounts.values())
    assert len(bases) == len(set(bases))
    assert all(b - a >= MAGIC_SPAN for a, b in zip(bases, bases[1:]))


def test_the_account_templates_serve_icm_depth_without_a_new_unit() -> None:
    assert not list(SYSTEMD.glob("*icm-depth*"))
    engine = (SYSTEMD / "goldbot-engine@.service").read_text(encoding="utf-8")
    assert "goldbot.ops.run engine %i" in engine
    tunnel = (SYSTEMD / "goldbot-bridge-tunnel@.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/goldbot/bridge-%i.env" in tunnel
    # 8765 belongs to icm-demo: the template refuses it for any other account (icm-depth uses 8768)
    assert re.search(r'"\$\$\{LOCAL_PORT\}" = 8765 \] && \[ "%i" != icm-demo \]', tunnel)
