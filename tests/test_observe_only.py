"""The observe-only live account (ADR 0063, owner directive 2026-10-31): XAUUSD247 is listed on no Vantage demo, so it is
read through the owner's LIVE Vantage account, logged in with the MT5 INVESTOR (read-only) password, with "NO live
trading". Defence in depth: the broker rejects every order (investor login); the account is never enabled, never an
engine, arm or allocator account and never live or tiny-live anywhere; the brain's broker client refuses every order,
modify and close before the network; the bridge serves read-only methods only (from the account's kind or the
installer's environment flag) and its terminal-side broker refuses every order call too; and the terminal must report
trading disallowed (the investor login), or the bridge serves nothing."""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pytest
import requests
from pydantic import ValidationError

from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge import GuardedBroker, ObserveOnlyRefused, RemoteBroker, make_handler
from goldbot.execution.broker import AccountInfo, OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops import observe
from goldbot.ops import run as run_mod
from goldbot.ops.services import engine as engine_service

OBS = "vantage-live-obs"
TOKEN = "t" * 40
T0 = pd.Timestamp("2026-10-31 12:00", tz="UTC")
INVESTOR = "investor (read-only) password, not the master password"


@pytest.fixture
def isolated(tmp_path, monkeypatch) -> Iterator[Path]:
    """The real accounts.yaml copied to tmp, the phase file and the secrets file there too, no OS keyring."""
    cfg = tmp_path / "accounts.yaml"
    cfg.write_text(acc_mod.ACCOUNTS_FILE.read_text())
    monkeypatch.setattr(acc_mod, "ACCOUNTS_FILE", cfg)
    monkeypatch.setattr(acc_mod, "PHASE_FILE", tmp_path / "phase_state.json")
    monkeypatch.setattr(acc_mod, "ROOT", tmp_path)
    monkeypatch.setattr(acc_mod, "keyring", None)
    monkeypatch.setattr(acc_mod, "_PLATFORM", "linux")
    monkeypatch.setenv("GOLDBOT_ALLOW_PLAINTEXT", "1")
    yield cfg
    acc_mod.register_prompter(None)


def _gate_passed() -> None:
    acc_mod.PHASE_FILE.write_text(json.dumps({"phase": 4, "gates_passed": list(acc_mod.GATES)}))


def _obs(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id=OBS, broker="vantage_obs", mode="live", server="Example-Live", login=1234567,
                                terminal_path="t", server_tz="NY+7", symbol="XAUUSD247", magic_base=260800,
                                enabled=False, kind="observe_only", observe_symbols=("XAUUSD247", "XAUUSD"))
    base.update(kw)
    return acc_mod.Account(**base)


def _info(**kw: Any) -> AccountInfo:
    base: dict[str, Any] = dict(login=1234567, equity=1e3, balance=1e3, margin=0, margin_free=1e3, leverage=500,
                                currency="GBP", server="Example-Live", trade_mode="real", margin_mode="netting",
                                trade_allowed=False)
    base.update(kw)
    return AccountInfo(**base)


def _intent() -> OrderIntent:
    return OrderIntent(client_order_id="x-1-a", symbol="XAUUSD247", side=1, lots=0.01, sl=1.0, tp=3.0, magic=260800)


# ------------------------------------------------------------------------------------------------ the registry
def test_the_observe_account_is_configured_read_only_with_no_login_or_server_in_the_public_repo():
    raw = acc_mod.ACCOUNTS_FILE.read_text()
    acc = acc_mod.load_accounts(logins=False)[OBS]
    assert acc.kind == "observe_only" and acc.is_observe_only
    assert acc.mode == "live" and not acc.enabled
    assert acc.login is None and acc.server == ""          # both live only in the keyring (public repo)
    assert acc.symbol == "XAUUSD247" and acc.observe_symbols == ("XAUUSD247", "XAUUSD")
    assert acc.broker not in {a.broker for a in acc_mod.load_accounts(logins=False).values() if a.account_id != OBS}
    block = raw.split(f"  {OBS}:\n", 1)[1].split("\n  icm-live:", 1)[0]
    assert "    server: null" in block and "    login: null" in block


def test_the_retired_weekend_demo_stays_configured_off():
    """vantage-247 is retired (XAUUSD247 is on no Vantage demo): kept off so the WKS0/WKI0 code paths stay tested."""
    assert not acc_mod.load_accounts(logins=False)["vantage-247"].enabled


@pytest.mark.parametrize("change", [
    {"enabled": True},
    {"approval_mode": "propose", "probe_only": True},
    {"order_lane": True},
    {"shadow_host": True},
    {"observe_symbols": ()},
])
def test_an_observe_account_that_could_trade_is_refused_at_load(change):
    with pytest.raises(ValidationError, match="observe_only"):
        _obs(**change)


def test_observe_symbols_and_an_empty_server_belong_to_observe_accounts_only():
    with pytest.raises(ValidationError, match="observe_only"):
        _obs(kind="trading")                                  # observe_symbols on a trading account
    with pytest.raises(ValidationError, match="server"):
        _obs(kind="trading", observe_symbols=(), server="")


def _edit_obs(cfg: Path, old: str, new: str) -> None:
    raw = cfg.read_text()
    block_start = raw.index(f"  {OBS}:\n")
    head, tail = raw[:block_start], raw[block_start:]
    cfg.write_text(head + tail.replace(old, new, 1))


def test_an_observe_account_edited_to_enabled_true_is_dropped_alone_and_never_tradeable(isolated, caplog):
    """Trading-safety LOW (PR 236): the offending observe account is dropped with an ERROR, every other account still
    loads (one bad block never stops every service), and the drop is reported for health."""
    _gate_passed()
    _edit_obs(isolated, "    enabled: false", "    enabled: true")
    accs = acc_mod.load_accounts(isolated, logins=False)
    assert OBS not in accs and "icm-demo" in accs
    assert "observe_only" in acc_mod.refused_observe_accounts()[OBS]
    assert any(r.levelname == "ERROR" and OBS in r.getMessage() for r in caplog.records)
    assert OBS not in {a.account_id for a in acc_mod.enabled_accounts(loaded=accs)}
    assert OBS not in {a.account_id for a in acc_mod.observe_accounts(loaded=accs)}
    assert not acc_mod.unlock_live(OBS, f"ENABLE LIVE {OBS}", isolated)        # still never unlocked


def test_a_dropped_observe_account_warns_on_health(tmp_path):
    from goldbot.ops import observe
    from goldbot.ops.checks._base import ObserveLaneRef
    from goldbot.ops.health import HealthContext
    ctx = HealthContext(state_dir=tmp_path, now=T0, get_secret=lambda k: None,
                        observe_lanes=[ObserveLaneRef(account_id=OBS, installed=True, symbol="XAUUSD247",
                                                      refused="an observe_only account never trades: enabled")])
    chk = observe.check_observe(ctx)
    assert chk.status == "warn" and "refused at load" in chk.reason and "enabled" in chk.reason


def test_the_observe_account_is_never_an_enabled_or_live_account_even_past_every_gate(isolated):
    _gate_passed()
    loaded = acc_mod.load_accounts(isolated, logins=False)
    forced = {**loaded, OBS: loaded[OBS].model_copy(update={"enabled": True})}   # past the validators on purpose
    for mode in (None, "live", "demo"):
        assert OBS not in {a.account_id for a in acc_mod.enabled_accounts(mode, loaded=forced)}
    ids = acc_mod.account_ids()
    assert ids is not None and OBS in ids.listed and OBS not in ids.enabled
    assert not acc_mod.tiny_live_risk(loaded[OBS])
    assert [a.account_id for a in acc_mod.observe_accounts(loaded=loaded)] == [OBS]


def test_unlock_live_refuses_an_observe_account_and_leaves_the_file_unchanged(isolated, capsys):
    _gate_passed()
    before = isolated.read_text()
    assert not acc_mod.unlock_live(OBS, f"ENABLE LIVE {OBS}", isolated)
    assert isolated.read_text() == before
    assert "observe_only" in capsys.readouterr().out


def _store_login(aid: str, login: str, server: str | None = None) -> None:
    acc_mod.set_secret(acc_mod.login_key(aid), login)
    if server is not None:
        acc_mod.set_secret(acc_mod.server_key(aid), server)


def test_unlock_live_refuses_a_live_account_on_the_observe_accounts_login_and_server(isolated, capsys):
    """Trading-safety MEDIUM (PR 236): vantage-live is the owner's real account too; unlocking it on the login and
    server the observe account reads would put a trading engine on the observed account."""
    _gate_passed()
    _store_login(OBS, "7654321", "Example-Live 1")
    _store_login("vantage-live", "7654321")
    raw = isolated.read_text().replace("    server: VantageInternational-Live\n", "    server: Example-Live 1\n", 1)
    isolated.write_text(raw)
    before = isolated.read_text()
    assert not acc_mod.unlock_live("vantage-live", "ENABLE LIVE vantage-live", isolated)
    assert isolated.read_text() == before
    out = capsys.readouterr().out
    assert OBS in out and "7654321" not in out and "Example-Live" not in out


def test_unlock_live_still_works_for_a_live_account_on_another_login(isolated):
    _gate_passed()
    _store_login(OBS, "7654321", "Example-Live 1")
    _store_login("vantage-live", "1111111")
    assert acc_mod.unlock_live("vantage-live", "ENABLE LIVE vantage-live", isolated)


def test_a_trading_bridge_refuses_to_start_on_an_observe_accounts_login_and_server():
    obs = _obs(login=7654321, server="Example-Live 1")
    twin = _obs(account_id="vantage-live", kind="trading", observe_symbols=(), login=7654321, server="Example-Live 1",
                broker="vantage", symbol="XAUUSD", magic_base=260400)
    other = twin.model_copy(update={"login": 1111111})
    loaded = {obs.account_id: obs, twin.account_id: twin}
    why = acc_mod.observe_login_clash(twin, loaded.values())
    assert why is not None and OBS in why and "7654321" not in why and "Example-Live" not in why
    assert observe.trading_bridge_refusal(twin, loaded.values(), {}) == why
    assert acc_mod.observe_login_clash(other, [obs, other]) is None
    assert observe.trading_bridge_refusal(obs, loaded.values(), {}) is None          # the observe bridge itself


def test_add_asks_for_the_investor_password_and_keeps_login_server_and_password_in_the_keyring(isolated, capsys):
    asked: list[tuple[str, bool]] = []

    def prompter(msg: str, secret: bool) -> str:
        asked.append((msg, secret))
        if secret:
            return "investor-pw"
        return "Example-Live 1" if "server" in msg.lower() else "7654321"
    acc_mod.register_prompter(prompter)
    before = isolated.read_text()
    assert acc_mod.main(["add", OBS]) == 0
    assert isolated.read_text() == before                          # nothing written to the public config
    [pw_msg] = [m for m, s in asked if s]
    assert INVESTOR in pw_msg
    assert acc_mod.get_secret(f"mt5-{OBS}") == "investor-pw"
    assert acc_mod.get_secret(acc_mod.login_key(OBS)) == "7654321"
    assert acc_mod.get_secret(acc_mod.server_key(OBS)) == "Example-Live 1"
    acc = acc_mod.load_accounts(isolated)[OBS]                     # the keyring resolves both on the box
    assert acc.login == 7654321 and acc.server == "Example-Live 1"
    assert "7654321" not in capsys.readouterr().out


def test_a_trading_account_keeps_the_ordinary_password_prompt(isolated):
    asked: list[str] = []

    def prompter(msg: str, secret: bool) -> str:
        asked.append(msg)
        return "pw" if secret else "12345"
    acc_mod.register_prompter(prompter)
    assert acc_mod.main(["add", "icm-demo"]) == 0
    assert not any("investor" in m for m in asked)


def test_the_terminal_must_be_on_the_investor_login_for_an_observe_account():
    acc = _obs()
    assert acc_mod.verify_terminal_account(acc, _info()) is None     # netting is fine: nothing is ever opened
    assert "master" in (acc_mod.verify_terminal_account(acc, _info(trade_allowed=True)) or "")
    assert "investor" in (acc_mod.verify_terminal_account(acc, _info(trade_allowed=None)) or "")
    assert "demo" in (acc_mod.verify_terminal_account(acc, _info(trade_mode="demo")) or "")


# ------------------------------------------------------------------------------------ the brain's broker client
@pytest.mark.parametrize("call", [lambda rb: rb.place_order(_intent()), lambda rb: rb.modify(1, 1.0, None),
                                  lambda rb: rb.close(1)])
def test_the_brain_client_refuses_every_order_call_of_an_observe_account_before_the_network(call):
    sent: list[Any] = []
    rb = RemoteBroker("http://b", TOKEN, observe_only=True, post=lambda *a, **k: sent.append(a))
    with pytest.raises(ObserveOnlyRefused):
        call(rb)
    assert sent == []


def test_the_brain_client_of_an_observe_account_still_reads():
    class _R:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"result": ["XAUUSD247"]}
    rb = RemoteBroker("http://b", TOKEN, observe_only=True, post=lambda *a, **k: _R())
    assert rb.symbols_get("XAUUSD2*") == ["XAUUSD247"]


# ------------------------------------------------------------------------------------------------ the bridge
@pytest.fixture
def obs_bridge() -> Iterator[tuple[str, PaperBroker]]:
    pb = PaperBroker(symbol="XAUUSD247", equity=1_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.3))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(pb, TOKEN, observe_only=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", pb
    httpd.shutdown()
    httpd.server_close()


def _rpc(url: str, method: str, args: list[Any]) -> requests.Response:
    import time
    body = {"method": method, "args": [bridge_mod._encode(a) for a in args], "sent_at": time.time()}
    return requests.post(f"{url}/rpc", json=body, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)


def test_an_observe_bridge_refuses_every_order_method_and_the_terminal_never_sees_it(obs_bridge):
    url, pb = obs_bridge
    for method, args in (("place_order", [_intent()]), ("modify", [1, 1.0, None]), ("close", [1, None])):
        r = _rpc(url, method, args)
        assert r.status_code == 403 and "observe_only" in r.json()["error"], (method, r.text)
    assert pb.positions() == [] and pb.deals_since(T0 - pd.Timedelta(days=1)).empty


def test_an_observe_bridge_serves_reads_and_says_so_on_health(obs_bridge):
    url, pb = obs_bridge
    assert RemoteBroker(url, TOKEN).last_tick("XAUUSD247") == pb.last_tick("XAUUSD247")
    h = requests.get(f"{url}/health", timeout=5).json()
    assert h["observe_only"] is True
    assert set(h["methods"]) == bridge_mod.OBSERVE_METHODS
    assert not {"place_order", "modify", "close"} & set(h["methods"])


def test_the_observe_allowlist_is_bars_ticks_and_terms_only():
    """Security LOW (PR 236): no positions, history, sizing or order method on an observe bridge or client."""
    assert bridge_mod.OBSERVE_METHODS == {"get_bars", "get_bars_range", "copy_ticks", "last_tick", "symbol_info",
                                          "symbols_get", "account"}
    assert all(bridge_mod.METHODS[m][1] for m in bridge_mod.OBSERVE_METHODS)          # all read-only


@pytest.mark.parametrize("method, args", [("positions", [None]), ("account_positions", []),
                                          ("deals_since", [T0]), ("broker_terms", ["x", T0, T0]),
                                          ("margin_required", ["XAUUSD247", 1, 0.01, 2400.0]),
                                          ("loss_per_lot", ["XAUUSD247", 1, 2400.0, 1.0]),
                                          ("trading_sessions", ["XAUUSD247"])])
def test_an_observe_bridge_and_client_refuse_every_method_outside_the_allowlist(obs_bridge, method, args):
    url, _ = obs_bridge
    r = _rpc(url, method, args)
    assert r.status_code == 403 and "observe_only" in r.json()["error"]
    sent: list[Any] = []
    rb = RemoteBroker("http://b", TOKEN, observe_only=True, post=lambda *a, **k: sent.append(a))
    with pytest.raises(ObserveOnlyRefused):
        rb._call(method, *args)
    assert sent == []


class _MasterTerminal(PaperBroker):
    """A terminal on the observe account's login and server that reports trading ALLOWED (the master password)."""

    def terminal_info(self) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(connected=True, trade_allowed=True)

    def account(self) -> AccountInfo:
        return _info(trade_allowed=True)


@pytest.mark.integration
def test_an_observe_bridge_on_a_terminal_that_may_trade_serves_nothing_and_says_wrong_account():
    """Trading-safety LOW (PR 236): a master-password login is refused end to end: reads 503, /health wrong_account."""
    obs = _obs()
    g = GuardedBroker(_MasterTerminal(symbol="XAUUSD247"), magic_base=260800, observe_only=True,
                      verify=lambda info: acc_mod.verify_terminal_account(obs, info))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(g, TOKEN, verify_first=True, observe_only=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        r = _rpc(url, "last_tick", ["XAUUSD247"])
        assert r.status_code == 503
        h = requests.get(f"{url}/health", timeout=5)
        assert h.status_code == 503 and h.json()["terminal"] == "wrong_account"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("raw, got", [(True, True), (False, False), (None, None), (1, True), (0, False)])
def test_the_mt5_adapter_maps_trade_allowed(raw, got):
    from types import SimpleNamespace

    from goldbot.execution.mt5_adapter import account_info_from_mt5
    a = dict(login=1, equity=1.0, balance=1.0, margin=0.0, margin_free=1.0, leverage=500, currency="GBP",
             server="Example-Live", trade_mode=2, margin_mode=2)
    if raw is not None:
        a["trade_allowed"] = raw
    assert account_info_from_mt5(SimpleNamespace(**a)).trade_allowed is got


def test_a_wrong_server_message_shows_neither_server_name():
    why = acc_mod.verify_terminal_account(_obs(), _info(server="Other-Live 2")) or ""
    assert "server mismatch" in why and "Other-Live" not in why and "Example-Live" not in why


@pytest.mark.parametrize("call", [lambda g: g.place_order(_intent()), lambda g: g.modify(1, 1.0, None),
                                  lambda g: g.close(1)])
def test_the_terminal_side_broker_of_an_observe_bridge_refuses_every_order_call(call):
    pb = PaperBroker(symbol="XAUUSD247", equity=1_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.3))
    with pytest.raises(PermissionError, match="observe_only"):
        call(GuardedBroker(pb, magic_base=260800, observe_only=True))
    assert pb.positions() == []


@pytest.mark.parametrize("kind, env, expected", [
    ("observe_only", {}, True),
    ("trading", {observe.OBSERVE_ENV: "1"}, True),
    ("observe_only", {observe.OBSERVE_ENV: "0"}, True),            # the stricter of the two always wins
    ("trading", {}, False),
])
def test_a_bridge_is_observe_only_from_the_account_kind_or_the_installer_flag(kind, env, expected):
    acc = _obs() if kind == "observe_only" else _obs(kind="trading", observe_symbols=())
    assert observe.observe_bridge(acc, env) is expected


# ------------------------------------------------------------------------------------------------ the engine
def test_the_engine_never_starts_for_an_observe_account_before_any_broker_connection(tmp_path, monkeypatch, caplog):
    from tests.test_engine_start_safety import _start
    monkeypatch.setenv("GOLDBOT_PAPER_ENGINE", "1")
    built = _start(tmp_path, monkeypatch, _obs())
    touched: list[str] = []
    monkeypatch.setattr(acc_mod, "bridge_endpoint", lambda aid: touched.append(aid))
    with pytest.raises(SystemExit) as exc:
        run_mod.run_engine(OBS)
    assert exc.value.code == engine_service.ENGINE_DISABLED_EXIT
    assert built == [] and touched == []
    assert any("observe_only" in r.getMessage() for r in caplog.records)
    assert not (tmp_path / "state" / f"engine_{OBS}.json").exists()


def test_no_arm_cap_or_cost_probe_ever_reaches_an_observe_account():
    acc = acc_mod.load_accounts(logins=False)[OBS]
    assert acc_mod.exploration_arm_caps(acc) == {}
    assert not acc.cost_probe.enabled and not acc.exploration.enabled and not acc.pilot.enabled


# ------------------------------------------------------------------------------------------------ reporting
def test_the_today_plan_never_lists_an_observe_account():
    from goldbot.api.today_ledger import plans_from_config
    assert OBS not in plans_from_config(None)
