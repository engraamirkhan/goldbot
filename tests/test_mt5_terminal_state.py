"""The MT5 terminal's state on the bridge's GET /health and the brain's `terminal:<account>` check
(docs/proposals/2026-10-mt5-terminal-lifecycle.md: section 3, test 9 and security conditions 1 and 6).

The bridge maps what the terminal says (terminal_info, account_info, the registered-account check) to one fixed token,
`ok | not_running | not_connected | not_logged_in | wrong_account | autotrading_off`, never a login, server or balance; it answers 200
only for `ok` and 503 otherwise, and exits non-zero (systemd re-attaches it) after two consecutive `not_running`
reads. Until the terminal has been seen on the registered account the bridge serves /health only."""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from http.server import HTTPServer
from typing import Any

import pandas as pd
import pytest
import requests

from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge import GuardedBroker, make_handler, probe_health, probe_terminal
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.ops import health
from tests.test_health import make_ctx

TOKEN = "t" * 40
LOGIN = 51234567                       # a made-up 8-digit login: it must never show on /health or in the check
SERVER = "ICMarketsSC-Demo"
DIGITS = re.compile(r"\d{6,}")


class FakeTerminal:
    """The MT5Broker surface /health reads: terminal_info() and account(); `mode` picks what the terminal says."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.server_tz = "Europe/Athens"

    def terminal_info(self) -> Any:
        if self.mode == "dead":
            return None
        if self.mode == "ipc_error":
            raise RuntimeError("IPC timeout -10005")
        if self.mode == "legacy":                                   # a terminal_info without trade_allowed
            return type("TI", (), {"connected": True})()
        return type("TI", (), {"connected": self.mode not in ("disconnected", "disconnected_autotrading_off"),
                               "trade_allowed": "autotrading_off" not in self.mode})()

    def account(self) -> AccountInfo:
        if self.mode == "logged_out":
            raise AttributeError("'NoneType' object has no attribute 'login'")    # MT5Broker on account_info() None
        login = LOGIN + 1 if self.mode.startswith("other_account") else LOGIN
        return AccountInfo(login=login, equity=1000.0, balance=1000.0, margin=0.0, margin_free=1000.0, leverage=30,
                           currency="GBP", server=SERVER, trade_mode="demo", margin_mode="hedging")

    def last_tick(self, symbol: str) -> Tick:
        return Tick(ts_utc=pd.Timestamp("2026-10-17 10:00", tz="UTC"), bid=2400.0, ask=2400.2)


def _verify(info: Any) -> str | None:
    # the real check (accounts.verify_terminal_account) names the server; /health must only ever say wrong_account
    return None if info.login == LOGIN else f"terminal is logged in to another account ({info.login} on {info.server})"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


CLOCK = Clock()


@pytest.fixture
def served() -> Iterator[tuple[str, FakeTerminal, list[str]]]:
    term, lost = FakeTerminal(), list[str]()
    CLOCK.t = 1000.0
    g = GuardedBroker(term, magic_base=260100, verify=_verify)
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(g, TOKEN, commit="a" * 40, verify_first=True,
                                                     on_terminal_lost=lambda: lost.append("lost"), clock=CLOCK))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", term, lost
    httpd.shutdown()
    httpd.server_close()


def _health(url: str) -> requests.Response:
    """GET /health, then wait until the handler has fully finished. The handler replies BEFORE it counts not_running
    reads and calls on_terminal_lost, and HTTPServer is single-threaded, so a second request (a 404 that touches no
    state) is only served after that tail ran: no sleep, no polling, and the test cannot move CLOCK underneath it."""
    r = requests.get(f"{url}/health", timeout=5)
    requests.get(f"{url}/_barrier", timeout=5)
    return r


STATES = {"ok": "ok", "dead": "not_running", "ipc_error": "not_running", "disconnected": "not_connected",
          "logged_out": "not_logged_in", "other_account": "wrong_account", "autotrading_off": "autotrading_off",
          # the order: account and connection first, then AutoTrading (incident 2026-10-20)
          "disconnected_autotrading_off": "not_connected", "other_account_autotrading_off": "wrong_account",
          "legacy": "ok"}


# ---- test 9: /health maps the terminal to one token, with no login digits
@pytest.mark.parametrize("mode", sorted(STATES))
def test_t9_health_maps_the_terminal_to_one_fixed_token(served, mode):
    url, term, _ = served
    term.mode = mode
    r = requests.get(f"{url}/health", timeout=5)
    body = r.json()
    assert body["terminal"] == STATES[mode] and body["ok"] is (STATES[mode] == "ok")
    assert r.status_code == (200 if STATES[mode] == "ok" else 503)
    assert isinstance(body["terminal_for_s"], int) and 0 <= body["terminal_for_s"] <= bridge_mod.TERMINAL_FOR_CAP_S
    # security condition 6: no login, server or host; the commit is hex and carries its own digits, so it is left out
    text = json.dumps({k: v for k, v in body.items() if k != "commit"})
    assert not DIGITS.search(text) and SERVER not in text and str(LOGIN) not in r.text and SERVER not in r.text
    assert f'"terminal": "{STATES[mode]}"' in r.text                 # the exact form the root watchdog's probe matches
    assert len(r.content) <= 1024, len(r.content)                   # the probe reads at most 1 KiB


def test_t9_two_consecutive_not_running_reads_30_s_apart_exit_the_bridge(served):
    url, term, lost = served
    term.mode = "dead"
    _health(url)
    assert lost == []
    CLOCK.t += 40
    term.mode = "ok"                                                  # an answer in between resets the count
    _health(url)
    term.mode = "ipc_error"
    _health(url)
    assert lost == []
    CLOCK.t += bridge_mod.TERMINAL_LOST_MIN_SPAN_S
    term.mode = "dead"
    r = _health(url)
    assert r.status_code == 503 and r.json()["terminal"] == "not_running"
    assert lost == ["lost"]                                           # replied, then exit


def test_not_running_reads_within_30_s_never_exit_the_bridge(served):
    """Trading-safety L1: one brain health pass makes three GETs within a second; a terminal restarting for a moment
    must not end the bridge. Only reads spanning TERMINAL_LOST_MIN_SPAN_S do."""
    url, term, lost = served
    assert bridge_mod.TERMINAL_LOST_MIN_SPAN_S == 30
    term.mode = "dead"
    for _ in range(5):
        _health(url)
        CLOCK.t += 5
    assert lost == []                                                 # 20 s of not_running so far
    CLOCK.t += 10
    _health(url)
    assert lost == ["lost"]


def test_the_default_on_terminal_lost_exits_non_zero():
    with pytest.raises(SystemExit) as e:
        bridge_mod._exit_terminal_lost()
    assert e.value.code == bridge_mod.TERMINAL_LOST_EXIT != 0


def test_rpc_is_refused_until_the_terminal_is_seen_on_the_registered_account(served):
    url, term, _ = served
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    term.mode = "logged_out"
    r = requests.post(f"{url}/rpc", json={"method": "last_tick", "args": ["XAUUSD"]}, headers=hdr, timeout=5)
    assert r.status_code == 503 and r.json()["error"] == "terminal not verified: not_logged_in"
    term.mode = "other_account"
    r = requests.post(f"{url}/rpc", json={"method": "last_tick", "args": ["XAUUSD"]}, headers=hdr, timeout=5)
    assert r.status_code == 503 and str(LOGIN) not in r.text and SERVER not in r.text
    term.mode = "ok"
    r = requests.post(f"{url}/rpc", json={"method": "last_tick", "args": ["XAUUSD"]}, headers=hdr, timeout=5)
    assert r.status_code == 200
    term.mode = "disconnected"                                        # verified once: reads keep being served
    r = requests.post(f"{url}/rpc", json={"method": "last_tick", "args": ["XAUUSD"]}, headers=hdr, timeout=5)
    assert r.status_code == 200


def test_autotrading_off_still_verifies_the_account_so_reads_and_closes_are_served(served):
    """autotrading_off is checked after the account: the terminal is on the registered account, so the bridge serves
    reads and passes orders and closes on (the terminal itself refuses them with 10027 and the engine sees why)."""
    url, term, _ = served
    term.mode = "autotrading_off"
    assert _rpc(url, "last_tick", "XAUUSD").status_code == 200
    r = _rpc(url, "close", 1)
    assert r.status_code != 503 and "refused" not in r.text, r.text


def _rpc(url: str, method: str, *args: Any) -> requests.Response:
    return requests.post(f"{url}/rpc", json={"method": method, "args": list(args), "sent_at": __import__("time").time()},
                         headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)


def test_a_terminal_switched_to_another_account_serves_nothing_after_verification(served):
    """Trading-safety L2: the gate re-closes while the CURRENT state is wrong_account, reads and closes included."""
    url, term, _ = served
    assert _rpc(url, "last_tick", "XAUUSD").status_code == 200        # verified (ok)
    term.mode = "other_account"
    for method, args in (("last_tick", ["XAUUSD"]), ("close", [1]), ("positions", [])):
        r = _rpc(url, method, *args)
        assert r.status_code == 503 and r.json()["error"] == "terminal on another account: refused", (method, r.text)
        assert str(LOGIN) not in r.text and SERVER not in r.text
    term.mode = "ok"
    assert _rpc(url, "last_tick", "XAUUSD").status_code == 200        # back on the registered account


def test_a_logged_out_terminal_keeps_reads_and_closes_flowing_after_verification(served):
    """Exits are never gated: not_logged_in (or not_running) after verification does not refuse a call; the terminal
    answers what it can (here the fake has no close: a 500 from the terminal, not the gate's 503)."""
    url, term, _ = served
    assert _rpc(url, "last_tick", "XAUUSD").status_code == 200
    for mode in ("logged_out", "dead", "disconnected"):
        term.mode = mode
        assert _rpc(url, "last_tick", "XAUUSD").status_code == 200, mode
        r = _rpc(url, "close", 1)
        assert r.status_code != 503 and "refused" not in r.text, (mode, r.text)


def test_a_broker_without_terminal_info_keeps_the_old_health(tmp_path):
    from goldbot.execution.paper import PaperBroker
    for broker in (PaperBroker(equity=10_000), GuardedBroker(PaperBroker(equity=10_000), magic_base=260100)):
        httpd = HTTPServer(("127.0.0.1", 0), make_handler(broker, TOKEN))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            r = requests.get(f"http://127.0.0.1:{httpd.server_address[1]}/health", timeout=5)
            assert r.status_code == 200 and r.json()["ok"] is True and "terminal" not in r.json()
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_terminal_age_is_capped_below_six_digits():
    assert bridge_mod.TERMINAL_FOR_CAP_S < 100_000


# ---- the brain side: probe_terminal and probe_health
class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    def iter_content(self, n: int) -> Iterator[bytes]:
        yield self._raw

    def json(self) -> Any:
        return json.loads(self._raw)

    def close(self) -> None:
        pass


def _get(status: int, body: Any) -> Any:
    return lambda url, timeout, **kw: _Resp(status, body)


@pytest.mark.parametrize("state", list(bridge_mod.TERMINAL_STATES))
def test_probe_terminal_reads_200_and_503(state):
    status = 200 if state == "ok" else 503
    assert probe_terminal("http://x", get=_get(status, {"ok": state == "ok", "terminal": state,
                                                         "terminal_for_s": 42})) == (state, 42, None)


@pytest.mark.parametrize("status,body,want", [
    (200, {"ok": True}, "unreported"),                                  # a bridge older than this change
    (503, {"ok": False, "terminal": "rebooting"}, "noanswer"),
    (503, {"ok": False, "terminal": "ok"}, "noanswer"),                 # inconsistent: never trusted
    (500, {"error": "x"}, "noanswer"),
    (200, b"not json", "noanswer"),
])
def test_probe_terminal_trusts_only_a_known_token(status, body, want):
    assert probe_terminal("http://x", get=_get(status, body))[0] == want


def test_probe_terminal_without_an_answer():
    def boom(url: str, timeout: float, **kw: Any) -> Any:
        raise requests.ConnectionError("tunnel down")
    assert probe_terminal("http://x", get=boom) == ("noanswer", None, None)


def test_probe_health_counts_a_503_with_a_terminal_state_as_the_bridge_answering():
    """bridge:<account> is about the bridge; the terminal has its own check (no double alert, no wrong cause)."""
    assert probe_health("http://x", get=_get(503, {"ok": False, "terminal": "not_logged_in"})) is None
    assert probe_health("http://x", get=_get(503, {"ok": False})) == "HTTP 503"


@pytest.mark.parametrize("login,want", [("refused", "refused"), ("waiting", "waiting"), ("rate_limited", "rate_limited"),
                                        ("in_progress", "in_progress"), ("x; rm", None), (6, None), (None, None)])
def test_probe_terminal_reads_the_keyring_login_token_only_when_known(login, want):
    """Incident 2026-11-01: the bridge's /health said `login: refused` and the brain ignored it."""
    body = {"ok": False, "terminal": "not_logged_in", "terminal_for_s": 30, "login": login}
    assert probe_terminal("http://x", get=_get(503, body)) == ("not_logged_in", 30, want)


# ---- the terminal:<account> check (plan section 3 wording)
def _check(tmp_path: Any, state: str, for_s: int | None = 30, url: str | None = "http://127.0.0.1:18765") -> Any:
    ctx = make_ctx(tmp_path, terminal_probe=lambda u: (state, for_s),
                   get_secret=lambda k: url if k == "mt5-bridge-url-icm-demo" else "s3cr3t-value")
    return health.check_terminal(ctx, "icm-demo")


WORDING = {
    "not_running": ("fail", "MT5 terminal not running on the MT5 box; it is being restarted. No entries; stops stay at "
                            "the broker. If this lasts 10 min: RUNBOOK 'Terminal down'."),
    "not_logged_in": ("fail", "MT5 terminal running but not logged in (saved login lost?). No entries; stops stay at "
                              "the broker. The MT5 box logs it in from the keyring; if it stays: "
                              "goldbot-mt5 login <account>, RUNBOOK 'Terminal down'."),
    "wrong_account": ("fail", "MT5 terminal logged in to a different account than registered; the bridge refuses "
                              "orders."),
    "autotrading_off": ("fail", "MT5 terminal AutoTrading is off: the terminal refuses every order (10027). Restart "
                                "the terminal unit on the MT5 box (its start enables it): "
                                "`sudo systemctl restart goldbot-mt5-terminal@<account>`"),
}


@pytest.mark.parametrize("state", sorted(WORDING))
def test_terminal_check_wording(tmp_path, state):
    c = _check(tmp_path, state)
    assert c.name == "terminal:icm-demo" and (c.status, c.reason) == WORDING[state]


def test_terminal_check_not_connected_warns_then_fails_after_5_min(tmp_path):
    c = _check(tmp_path, "not_connected", for_s=120)
    assert c.status == "warn" and c.reason.startswith("MT5 terminal not connected to the broker server.")
    c = _check(tmp_path, "not_connected", for_s=health.TERMINAL_NOT_CONNECTED_FAIL_S)
    assert c.status == "fail" and c.reason.startswith("MT5 terminal not connected to the broker server.")
    assert health.TERMINAL_NOT_CONNECTED_FAIL_S == 300
    assert _check(tmp_path, "not_connected", for_s=None).status == "warn"


def test_terminal_check_ok_unknown_and_omitted(tmp_path):
    assert _check(tmp_path, "ok").status == "ok"
    c = _check(tmp_path, "noanswer")
    assert c.status == "warn" and "bridge:icm-demo" in c.reason
    c = _check(tmp_path, "unreported")
    assert c.status == "warn" and "deploy" in c.reason
    assert _check(tmp_path, "ok", url=None) is None
    assert health.check_terminal(make_ctx(tmp_path), "icm-demo") is None          # no probe configured


@pytest.mark.parametrize("state", ["ok", "not_running", "not_connected", "not_logged_in", "wrong_account",
                                   "autotrading_off", "noanswer", "unreported"])
def test_security_6_the_check_text_carries_no_login_server_or_host(tmp_path, state):
    for for_s in (30, 99_999):
        c = _check(tmp_path, state, for_s=for_s)
        assert not DIGITS.search(c.reason) and SERVER not in c.reason and "127.0.0.1" not in c.reason, c.reason


@pytest.mark.parametrize("state", ["not_logged_in", "not_running", "not_connected"])
def test_a_standing_login_refusal_fails_with_the_recovery_in_the_text(tmp_path, state):
    """Incident 2026-11-01: a latched refusal never heals by itself; the alert says so and names both recoveries."""
    ctx = make_ctx(tmp_path, terminal_probe=lambda u: (state, 30, "refused"),
                   get_secret=lambda k: "http://127.0.0.1:18765" if k == "mt5-bridge-url-icm-demo" else "s3cr3t-value")
    c = health.check_terminal(ctx, "icm-demo")
    assert c is not None
    assert c.name == "terminal:icm-demo" and c.status == "fail"
    assert "login refused" in c.reason and "does not heal by itself" in c.reason
    assert "RUNBOOK 'MT5 login'" in c.reason and "--retry-refused" in c.reason
    assert "goldbot-mt5 accounts set mt5-icm-demo" in c.reason and "Service is not available" in c.reason
    assert not DIGITS.search(c.reason) and SERVER not in c.reason and "127.0.0.1" not in c.reason


def test_a_login_token_beside_a_terminal_that_is_ok_changes_nothing(tmp_path):
    ctx = make_ctx(tmp_path, terminal_probe=lambda u: ("ok", 30, "refused"),
                   get_secret=lambda k: "http://127.0.0.1:18765" if k == "mt5-bridge-url-icm-demo" else "s3cr3t-value")
    c = health.check_terminal(ctx, "icm-demo")
    assert c is not None and c.status == "ok"


# ---- incident 2026-11-01: a bridge down for hours paged nothing useful; one clear page per outage, inside 6 min
def _bridge(tmp_path: Any, at: pd.Timestamp, up: bool) -> Any:
    ctx = make_ctx(tmp_path, now=at, bridge_probe=lambda u: None if up else "unreachable (ConnectionError)",
                   get_secret=lambda k: "http://127.0.0.1:18765" if k == "mt5-bridge-url-icm-demo" else "s3cr3t-value")
    return health.check_bridge(ctx, "icm-demo")


T0 = pd.Timestamp("2026-11-01 01:29", tz="UTC")


def _at(minutes: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(minutes=minutes)


def test_a_bridge_down_warns_first_and_fails_at_5_min_with_the_causes_and_steps(tmp_path):
    assert health.BRIDGE_DOWN_FAIL_S == 300         # with the 60 s health pass: paged within the 6 min objective
    c = _bridge(tmp_path, _at(0), up=False)
    assert c.status == "warn" and "ConnectionError" in c.reason
    assert _bridge(tmp_path, _at(4.9), up=False).status == "warn"
    c = _bridge(tmp_path, _at(5), up=False)
    assert c.status == "fail" and "for 5 min" in c.reason
    assert "journalctl -u goldbot-bridge@icm-demo" in c.reason and "RUNBOOK 'MT5 login'" in c.reason
    assert "password refused by the broker" in c.reason and "broker stop" in c.reason
    assert "18765" not in c.reason and "127.0.0.1" not in c.reason


def test_a_bridge_answering_between_outages_does_not_restart_the_clock(tmp_path):
    """A restart loop that answers now and then must still page (a reset clock would keep it silent forever)."""
    assert _bridge(tmp_path, _at(0), up=False).status == "warn"
    assert _bridge(tmp_path, _at(3), up=True).status == "ok"       # a blip: not an outage yet, nothing said
    assert _bridge(tmp_path, _at(5), up=False).status == "fail"    # the outage began at 0


def test_a_bridge_back_just_before_the_page_is_never_paged_as_an_incident(tmp_path):
    assert _bridge(tmp_path, _at(0), up=False).status == "warn"
    assert _bridge(tmp_path, _at(4.9), up=False).status == "warn"
    assert _bridge(tmp_path, _at(5.5), up=True).status == "ok"


def test_a_long_outage_closes_only_after_3_min_answering_and_then_starts_afresh(tmp_path):
    assert _bridge(tmp_path, _at(0), up=False).status == "warn"
    assert _bridge(tmp_path, _at(10), up=False).status == "fail"
    c = _bridge(tmp_path, _at(11), up=True)
    assert c.status == "fail" and "answers again" in c.reason       # one incident: no recovered + new page per flap
    assert _bridge(tmp_path, _at(12), up=False).status == "fail"
    assert _bridge(tmp_path, _at(13), up=True).status == "fail"
    c = _bridge(tmp_path, _at(16), up=True)
    assert c.status == "ok" and c.reason == "bridge answers /health"
    assert _bridge(tmp_path, _at(17), up=False).status == "warn"   # a new outage: its own clock


def test_a_bridge_restart_loop_pages_once_for_the_whole_incident(tmp_path):
    watch = health.HealthWatch(tmp_path)
    pages = []
    for m in range(0, 360):                                         # 6 h, health every minute, up 1 min in 7
        c = _bridge(tmp_path, _at(m), up=m % 7 == 6)
        text = watch.poll(health.HealthReport(ts=_at(m), status=c.status, checks=[c]))
        if text and "new failure" in text:
            pages.append((m, text))
    assert len(pages) == 1 and pages[0][0] <= 6, pages
    assert "MT5 bridge unreachable" in pages[0][1]


def test_an_unusable_outage_record_fails_at_once_rather_than_never(tmp_path):
    (tmp_path / health.BRIDGE_DOWN_FILE).write_bytes(b"not a database" * 100)
    assert _bridge(tmp_path, _at(0), up=False).status == "fail"
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert _bridge(ro, _at(0), up=False).status == "fail"
    finally:
        ro.chmod(0o700)


def test_run_checks_places_the_terminal_check_after_the_bridge(tmp_path):
    ctx = make_ctx(tmp_path, terminal_probe=lambda u: ("not_logged_in", 30), bridge_probe=lambda u: None,
                   get_secret=lambda k: "http://127.0.0.1:18765" if k.startswith("mt5-bridge-url") else "s3cr3t-value")
    names = [c.name for c in health.run_checks(ctx).checks]
    assert names.index("bridge:icm-demo") < names.index("terminal:icm-demo")
    assert {c.name: c.status for c in health.run_checks(ctx).checks}["terminal:icm-demo"] == "fail"


# ---- trading-safety L3: a terminal that initialize(path=...) launched under the bridge unit
def _proc(root: Any, pid: int, cmdline: bytes, cgroup: str) -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(cmdline)
    (d / "cgroup").write_text(cgroup)


def test_a_terminal_in_the_bridge_cgroup_is_found(tmp_path):
    from goldbot.ops.services.bridge import terminals_in_bridge_cgroup
    unit = "0::/system.slice/system-goldbot\\x2dmt5\\x2dterminal.slice/goldbot-mt5-terminal@icm-demo.service\n"
    bridge = "0::/system.slice/system-goldbot\\x2dbridge.slice/goldbot-bridge@icm-demo.service\n"
    _proc(tmp_path, 100, b"C:\\MT5\\ICMarkets\\terminal64.exe\x00", unit)            # the unit's own terminal
    _proc(tmp_path, 200, b"C:\\users\\python.exe\x00-m\x00goldbot.ops.run\x00", bridge)
    (tmp_path / "self").mkdir()
    assert terminals_in_bridge_cgroup(tmp_path) == 0
    _proc(tmp_path, 300, b"C:\\MT5\\ICMarkets\\TERMINAL64.EXE\x00", bridge)          # launched by initialize
    assert terminals_in_bridge_cgroup(tmp_path) == 1
    (tmp_path / "400").mkdir()                                                      # a process gone mid-scan
    assert terminals_in_bridge_cgroup(tmp_path) == 1
    assert terminals_in_bridge_cgroup(tmp_path / "missing") is None                 # cannot tell


def test_run_bridge_refuses_to_serve_a_terminal_it_launched():
    import inspect

    from goldbot.ops.services import bridge as svc
    src = inspect.getsource(svc.run_bridge)
    attach, check, serve = src.index("MT5Broker("), src.index("terminals_in_bridge_cgroup("), src.index("serve(")
    assert attach < check < serve and "SystemExit(TERMINAL_SPAWNED_EXIT)" in src
    assert svc.TERMINAL_SPAWNED_EXIT not in (0, bridge_mod.TERMINAL_LOST_EXIT)


def test_terminals_in_bridge_cgroup_counts_only_the_bridge_units_cgroup(tmp_path):
    """Trading-safety re-review: only a terminal inside goldbot-bridge@<account>.service counts; the terminal unit's
    own and anything in the watchdog's cgroup (goldbot-bridge-watchdog@, a prefix look-alike) do not; an unreadable
    proc root is "cannot tell" (None), never 0."""
    from goldbot.ops.services.bridge import terminals_in_bridge_cgroup
    exe = b"C:\\MT5\\ICMarkets\\terminal64.exe\x00"
    slice_ = "0::/system.slice/system-goldbot\\x2d{}.slice/{}@icm-demo.service\n"
    _proc(tmp_path, 10, exe, slice_.format("mt5\\x2dterminal", "goldbot-mt5-terminal"))
    _proc(tmp_path, 11, exe, slice_.format("bridge\\x2dwatchdog", "goldbot-bridge-watchdog"))
    assert terminals_in_bridge_cgroup(tmp_path) == 0
    _proc(tmp_path, 12, exe, slice_.format("bridge", "goldbot-bridge"))
    assert terminals_in_bridge_cgroup(tmp_path) == 1
    _proc(tmp_path, 13, b"C:\\wine\\wineserver\x00", slice_.format("bridge", "goldbot-bridge"))   # not a terminal
    assert terminals_in_bridge_cgroup(tmp_path) == 1
    assert terminals_in_bridge_cgroup(tmp_path / "missing") is None
    unreadable = tmp_path / "file-not-dir"
    unreadable.write_text("x")
    assert terminals_in_bridge_cgroup(unreadable) is None


def test_the_watchdog_acts_only_on_tokens_the_bridge_reports_and_ignores_the_rest():
    """Review MEDIUM (version skew): the probe forwards any [a-z_] token (no list to fall behind the bridge's), and the
    watchdog's restarts hang only on noanswer and on TERMINAL_STATES tokens; anything else falls to an arm that only
    logs. So a new token in a newer bridge can never become a restart loop on a box whose scripts are older."""
    from pathlib import Path
    linux = Path(__file__).resolve().parents[1] / "goldbot" / "ops" / "linux"
    probe = (linux / "goldbot-bridge-probe.sh").read_text()
    assert "for state in " not in probe and "[a-z_]{1,32}" in probe
    wd = (linux / "goldbot-bridge-watchdog.sh").read_text()
    block = wd.split('case "$tok" in', 1)[1].split("esac", 1)[0]
    arms = {ln.strip().split(")")[0]: ln for ln in block.splitlines() if re.match(r"\s+[a-z_|*\"]+\)", ln)}
    named = {t for arm in arms if arm not in ("*",) for t in arm.split("|")} - {'""'}
    assert named <= set(bridge_mod.TERMINAL_STATES) | {"noanswer"}, named - set(bridge_mod.TERMINAL_STATES)
    assert "*" in arms


# ---- trading-safety review MEDIUM (item 2): autotrading_off refuses entries on the box and alerts with open positions
def _ti(trade_allowed: bool) -> Any:
    return lambda: type("TI", (), {"connected": True, "trade_allowed": trade_allowed})()


def test_an_entry_is_refused_on_the_mt5_box_as_a_definite_reject_while_autotrading_is_off():
    from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
    from goldbot.execution.not_placed import not_placed, refusal_code
    from tests.test_bridge import _order, _pos, _Terminal
    t = _Terminal(equity=100_000.0, positions=[_pos(0.1)])
    t.terminal_info = _ti(False)                                   # type: ignore[attr-defined]
    g = GuardedBroker(t, magic_base=260100)
    res = g.place_order(_order(0.01))
    assert not res.ok and res.retcode == bridge_mod.REFUSED_RETCODE and t.sent == []
    assert res.message.startswith(f"{bridge_mod.AUTOTRADING_REFUSAL}: ")
    assert res.retcode in DEFINITE_REJECT_RETCODES
    code = refusal_code(res, paper=False, definite=DEFINITE_REJECT_RETCODES)
    assert code == bridge_mod.AUTOTRADING_REFUSAL and not_placed(code).by == "mt5_box"   # type: ignore[union-attr]
    assert g.close(7).ok                                           # closes are never gated by it
    t.terminal_info = _ti(True)                                    # type: ignore[attr-defined]
    assert g.place_order(_order(0.01)).ok and len(t.sent) == 1


def test_autotrading_off_with_open_positions_is_a_critical_failure_naming_them(tmp_path):
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"open_positions": 2}))
    c = _check(tmp_path, "autotrading_off")
    assert c.status == "fail" and c.reason.startswith("CRITICAL: 2 open position(s) on this account cannot be closed "
                                                      "by goldbot")
    assert "only their broker stop and target protect them" in c.reason and WORDING["autotrading_off"][1] in c.reason
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"open_positions": 0}))
    assert _check(tmp_path, "autotrading_off").reason == WORDING["autotrading_off"][1]
