"""Incident 2026-11-02 (docs/incidents/2026-11-02-multi-instrument.md): on the CPU-starved icm-probe box 20 books' calls
queued behind one terminal and the bridge said nothing about it; the contention showed only as the engine's
ReadTimeouts. The bridge now logs every call that holds the terminal over SLOW_HOLD_S (method, seconds held, requests
waiting) and reports on GET /health how long calls waited for the terminal (`lock_wait_s`) and how many held it too
long (`slow_calls`), over the last LOCK_WAIT_WINDOW_S. Both fields are additive and optional: absent while no RPC ran
in the window, ignored by older brains and probes."""
from __future__ import annotations

import logging
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any, Iterator

import pandas as pd
import pytest
import requests

from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge import RemoteBroker, make_handler
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.ops.bridge_compat import BridgeInfo, read_bridge_info

TOKEN = "t" * 40
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
HOLD_S = 0.4


class SlowBroker(PaperBroker):
    """A terminal under CPU starvation: every last_tick takes HOLD_S."""

    def last_tick(self, symbol: str) -> Tick:
        time.sleep(HOLD_S)
        return super().last_tick(symbol)


@pytest.fixture
def slow_bridge(monkeypatch) -> Iterator[str]:
    monkeypatch.setattr(bridge_mod, "SLOW_HOLD_S", 0.2)          # the 2 s rule, scaled down for a fast test
    pb = SlowBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(pb, TOKEN, lock=threading.Lock()))
    httpd.daemon_threads = True
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _health(url: str) -> dict[str, Any]:
    out: dict[str, Any] = requests.get(f"{url}/health", timeout=10).json()
    return out


def test_the_slow_hold_threshold_is_two_seconds():
    assert bridge_mod.SLOW_HOLD_S == 2.0


def test_no_rpc_yet_no_lock_figures_on_health(slow_bridge):
    body = _health(slow_bridge)
    assert "lock_wait_s" not in body and "slow_calls" not in body


def test_a_call_holding_the_terminal_too_long_is_logged_with_its_waiters_and_shown_on_health(slow_bridge, caplog):
    caplog.set_level(logging.INFO, logger="goldbot.bridge")
    out: list[Any] = []

    def read() -> None:
        out.append(RemoteBroker(slow_bridge, TOKEN).last_tick("XAUUSD"))

    threads = [threading.Thread(target=read) for _ in range(3)]
    for t in threads:
        t.start()
        time.sleep(0.05)                       # all three queue while the first holds the terminal
    for t in threads:
        t.join(10)
    assert len(out) == 3
    slow = [r.getMessage() for r in caplog.records if "held the terminal" in r.getMessage()]
    assert len(slow) == 3 and all(m.startswith("bridge: last_tick held the terminal") for m in slow)
    assert any("2 waiting" in m for m in slow)            # the first call ended with the other two queued
    assert all(r.levelno == logging.WARNING for r in caplog.records if "held the terminal" in r.getMessage())
    body = _health(slow_bridge)
    # the third call waited for the first two (~2 x HOLD_S); one decimal, never a long digit run
    assert body["lock_wait_s"] >= 1.5 * HOLD_S and body["lock_wait_s"] == round(body["lock_wait_s"], 1)
    assert body["slow_calls"] == 3
    assert body["ok"] is True and body["api_version"] == bridge_mod.API_VERSION   # the rest of /health unchanged


def test_a_fast_call_is_not_logged_and_counts_no_slow_call(slow_bridge, caplog, monkeypatch):
    monkeypatch.setattr(bridge_mod, "SLOW_HOLD_S", 5.0)
    caplog.set_level(logging.INFO, logger="goldbot.bridge")
    RemoteBroker(slow_bridge, TOKEN).last_tick("XAUUSD")
    assert not [r for r in caplog.records if "held the terminal" in r.getMessage()]
    body = _health(slow_bridge)
    assert "lock_wait_s" in body and "slow_calls" not in body


def test_old_calls_leave_the_window(slow_bridge, monkeypatch):
    RemoteBroker(slow_bridge, TOKEN).last_tick("XAUUSD")
    assert "lock_wait_s" in _health(slow_bridge)
    monkeypatch.setattr(bridge_mod, "LOCK_WAIT_WINDOW_S", 0.0)
    body = _health(slow_bridge)
    assert "lock_wait_s" not in body and "slow_calls" not in body


def test_the_figure_is_capped():
    assert bridge_mod.LOCK_WAIT_CAP_S < 1000                 # at most 3 digits before the decimal point


# ------------------------------------------------------------------------------------------------ the brain's detection
# The brain reads the figures from the /health GET its bridge_compat check already makes (no extra call on a contended
# bridge) and WARNs `bridge_compat:<account>` when calls waited LOCK_WAIT_WARN_S or more, or SLOW_CALLS_WARN or more
# held the terminal over 2 s in 5 minutes. Telegram alerts on a WARN that persists (health's own rule).
TZ = "Europe/Athens"
WED = pd.Timestamp("2026-11-04 10:00", tz="UTC")


def test_the_brain_reads_the_figures_from_the_health_get(slow_bridge):
    RemoteBroker(slow_bridge, TOKEN).last_tick("XAUUSD")
    info = read_bridge_info(slow_bridge, 10.0)
    assert info.lock_wait_s is not None and info.lock_wait_s >= 0 and info.slow_calls == 1


def test_junk_figures_are_dropped():
    for body in ({"lock_wait_s": True, "slow_calls": "9"}, {"lock_wait_s": -1, "slow_calls": -2},
                 {"lock_wait_s": 1e9, "slow_calls": 10**9}):
        info = _info_from_body(body)
        assert info.lock_wait_s is None and info.slow_calls is None, body


def _info_from_body(extra: dict[str, Any]) -> BridgeInfo:
    import json as _json

    class R:
        status_code = 200

        def iter_content(self, n: int) -> Iterator[bytes]:
            yield _json.dumps({"ok": True, "server_tz": TZ, "api_version": 11, "min_compatible": 5,
                               "methods": sorted(bridge_mod.METHODS), "order_lane": "off", **extra}).encode()

        def close(self) -> None:
            pass
    return read_bridge_info("http://x", 5.0, get=lambda *a, **k: R())


def _hctx(tmp_path, info: BridgeInfo) -> health.HealthContext:
    return health.HealthContext(state_dir=tmp_path, now=WED, get_secret={"mt5-bridge-url-icm-probe": "http://x"}.get,
                                accounts=[health.AccountRef(account_id="icm-probe", server_tz=TZ)],
                                bridge_info=lambda url: info, commit=None)


@pytest.mark.parametrize("extra, warn", [({}, False), ({"lock_wait_s": 3.2}, False),
                                         ({"lock_wait_s": 12.4}, True), ({"lock_wait_s": 1.0, "slow_calls": 15}, True),
                                         ({"lock_wait_s": 1.0, "slow_calls": 9}, False)])
def test_a_contended_terminal_warns_bridge_compat_naming_the_figures(tmp_path, extra, warn):
    c = health.check_bridge_compat(_hctx(tmp_path, _info_from_body(extra)), health.AccountRef(account_id="icm-probe",
                                                                                              server_tz=TZ))
    assert c is not None
    if warn:
        assert c.status == "warn" and "terminal contended" in c.reason and "Bridge contention" in c.reason
    else:
        assert c.status == "ok", c.reason
