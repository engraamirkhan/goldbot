"""`broker-history-depth`: the earliest bar a broker's MT5 server serves per timeframe, through the existing read-only
rates call (get_bars -> copy_rates_from_pos). Read-only by construction: only get_bars is ever called."""
from types import SimpleNamespace

import pandas as pd
import pytest

from goldbot.ops import broker_depth as bd


class FakeBroker:
    """Serves `have[tf]` bars ending now; copy_rates_from_pos returns at most the requested count."""

    def __init__(self, have: dict[str, int], fail: set[str] | None = None) -> None:
        self.have, self.fail = have, fail or set()
        self.calls: list[tuple[str, str, int]] = []

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        self.calls.append((symbol, tf, n))
        if tf in self.fail:
            raise TimeoutError("bridge timeout")
        step = {"1m": "1min", "1h": "1h", "1d": "1D"}[tf]
        k = min(n, self.have[tf])
        ts = pd.date_range(end=pd.Timestamp("2026-10-23 20:00", tz="UTC"), periods=k, freq=step)
        return pd.DataFrame({"ts_utc": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_count": 1.0,
                             "spread": 1})

    def __getattr__(self, name: str):                     # any other method (an order path) fails the test
        raise AssertionError(f"broker-history-depth called {name}")


def test_depth_reports_first_bar_per_timeframe_and_whether_the_request_capped_it():
    b = FakeBroker({"1m": 50, "1h": 1_000, "1d": 7_000})
    rows = bd.history_depth(b, "XAUUSD", {"1m": 100, "1h": 1_000, "1d": 20_000})
    by = {r.tf: r for r in rows}
    assert [r.tf for r in rows] == ["1m", "1h", "1d"]
    assert by["1m"].bars == 50 and not by["1m"].capped               # fewer than asked: the server's (or terminal's) start
    assert by["1h"].bars == 1_000 and by["1h"].capped                # exactly what was asked: history may go further
    assert by["1d"].first_utc == pd.Timestamp("2026-10-23 20:00", tz="UTC") - pd.Timedelta(days=6_999)
    assert by["1d"].last_utc == pd.Timestamp("2026-10-23 20:00", tz="UTC")
    assert [c[1:] for c in b.calls] == [("1m", 100), ("1h", 1_000), ("1d", 20_000)]


def test_one_failing_timeframe_is_reported_and_the_others_still_run():
    b = FakeBroker({"1m": 10, "1h": 10, "1d": 10}, fail={"1h"})
    rows = bd.history_depth(b, "XAUUSD", {"1m": 100, "1h": 100, "1d": 100})
    assert [r.error for r in rows] == ["", "TimeoutError: bridge timeout", ""]
    assert rows[1].bars == 0 and rows[1].first_utc is None


def test_requests_are_capped_so_the_bridge_is_never_asked_for_an_unbounded_frame():
    with pytest.raises(ValueError, match="at most"):
        bd.history_depth(FakeBroker({"1m": 1}), "XAUUSD", {"1m": bd.MAX_BARS + 1})


def _accounts(monkeypatch, endpoint=("http://127.0.0.1:8765", "tok")):
    acc = SimpleNamespace(account_id="icm-demo", symbol="XAUUSD", mode="demo")
    monkeypatch.setattr(bd, "_load_account", lambda account_id: acc if account_id == "icm-demo" else None)
    monkeypatch.setattr(bd, "_endpoint", lambda account_id: endpoint)
    return acc


def test_main_prints_a_table_and_exits_0(monkeypatch, capsys):
    _accounts(monkeypatch)
    fake = FakeBroker({"1m": 5, "1h": 5, "1d": 5})
    monkeypatch.setattr(bd, "_remote_broker", lambda url, token, account_id: fake)
    assert bd.main(["icm-demo", "--tf", "1m,1d", "--n", "1m=10", "--n", "1d=100"]) == 0
    out = capsys.readouterr().out
    assert "| icm-demo | XAUUSD | 1m | 5 | 10 |" in out and "| icm-demo | XAUUSD | 1d | 5 | 100 |" in out
    assert [c[1:] for c in fake.calls] == [("1m", 10), ("1d", 100)]


def test_main_refuses_an_unknown_account_or_a_missing_bridge(monkeypatch, capsys):
    _accounts(monkeypatch)
    assert bd.main(["nope"]) == 2
    _accounts(monkeypatch, endpoint=None)
    assert bd.main(["icm-demo"]) == 2
    assert "no bridge endpoint" in capsys.readouterr().out


def test_the_command_is_registered():
    from goldbot.ops.commands import registry
    assert registry.find("broker-history-depth") is not None
