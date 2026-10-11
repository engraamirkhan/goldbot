"""Trading-safety re-verification LOW-A (of 8c94ae0): when the terminal-account check refuses an engine start (another
login, server, demo vs real, or a margin mode that is not hedging), the engine used to exit with only a log line, so
health showed a merely stale engine. Now the refusal is written to engine_<account>.json as `startup_error` before the
exit and health names it; a margin mode the bridge does not report says what to do (an MT5 box older than protocol
v4: deploy it first). MIN_COMPATIBLE is 4, so bridge-compat and preflight also name an older bridge."""
import json
from typing import Any

import pandas as pd
import pytest

from goldbot.execution import bridge as bridge_mod
from goldbot.execution.broker import AccountInfo
from goldbot.ops import accounts, health
from goldbot.ops import run as run_mod
from goldbot.ops.bridge_compat import BridgeInfo, assess
from tests.test_health import make_ctx

ACC = accounts.Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=1234567,
                       terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)


def _info(**kw: Any) -> AccountInfo:
    base: dict[str, Any] = dict(login=1234567, equity=1e3, balance=1e3, margin=0, margin_free=1e3, leverage=30,
                                currency="GBP", server="ICMarketsSC-Demo", trade_mode="demo", margin_mode="hedging")
    base.update(kw)
    return AccountInfo(**base)


def test_an_unreported_margin_mode_says_to_deploy_the_mt5_box_first():
    why = accounts.verify_terminal_account(ACC, _info(margin_mode=None)) or ""
    assert "bridge older than protocol v4" in why and "deploy the MT5 box first" in why, why
    assert "netting" in (accounts.verify_terminal_account(ACC, _info(margin_mode="netting")) or "")


def test_a_refused_start_writes_the_cause_to_the_engine_state_and_health_names_it(tmp_path, monkeypatch):
    class Remote:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def account(self) -> AccountInfo:
            return _info(margin_mode=None)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {"icm-demo": ACC})
    monkeypatch.setattr(accounts, "bridge_endpoint", lambda aid: ("http://127.0.0.1:1", "t"))
    monkeypatch.setattr(bridge_mod, "RemoteBroker", Remote)
    with pytest.raises(SystemExit, match="deploy the MT5 box first"):
        run_mod.run_engine("icm-demo")
    e = json.loads((tmp_path / "state" / "engine_icm-demo.json").read_text())
    assert "deploy the MT5 box first" in e["startup_error"] and e["currency"] == "GBP" and e["dq_error"] is True
    c = health.check_engine(make_ctx(tmp_path / "state", now=pd.Timestamp.now("UTC")), "icm-demo")
    assert c.status == "fail" and "refused to start" in c.reason and "deploy the MT5 box first" in c.reason, c.reason


def test_min_compatible_is_4_so_a_v3_bridge_is_named_incompatible():
    assert bridge_mod.MIN_COMPATIBLE == 4
    old = BridgeInfo(reachable=True, server_tz="Europe/Athens", api_version=3, min_compatible=3,
                     methods=sorted(bridge_mod.METHODS))
    out = assess(old, "Europe/Athens", None)
    assert out.blocks
