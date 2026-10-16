"""W1-1 (architect plan, item A3): the MT5 adapter starts the terminal with its path and a 60 s timeout, so a terminal
that is slow to start under Wine (first login, a large history sync) is waited for instead of failing on the
package's short default and leaving the bridge down."""
from typing import Any

import pytest

from goldbot.execution import mt5_adapter
from tests.test_margin_and_tick_dedup import _fake_mt5


def test_initialize_gets_the_terminal_path_and_a_60_s_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def initialize(**kw: Any) -> bool:
        seen.append(kw)
        return True

    monkeypatch.setattr(mt5_adapter, "mt5", _fake_mt5(initialize=initialize))
    mt5_adapter.MT5Broker(terminal_path=r"C:\MT5\terminal64.exe", login=None, password=None, server="s",
                          server_tz="Europe/Athens", symbol="XAUUSD", account_label="icm-demo")
    assert mt5_adapter.MT5_INIT_TIMEOUT_MS == 60_000
    assert seen == [{"path": r"C:\MT5\terminal64.exe", "timeout": 60_000}]
