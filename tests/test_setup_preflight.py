"""`goldbot run setup` (goldbot/ops/setup_wizard.py) and `goldbot run preflight` (goldbot/ops/preflight.py)."""
import os
import shutil
import stat
from collections import deque
from pathlib import Path

import pytest
import yaml

from goldbot.config import DEFAULT_SETTINGS
from goldbot.execution.broker import AccountInfo
from goldbot.ops import accounts
from goldbot.ops import preflight as pf
from goldbot.ops import setup_wizard as sw

LOGIN = "51234567"
MT5_PW = "pw-SECRET-mt5-Zq9"
BRIDGE_TOKEN = "tok-SECRET-" + "b" * 40
TG_TOKEN = "123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"
TG_ID = "987654321"
EMAIL = "owner@example.com"
ANTHROPIC = "sk-ant-SECRET-" + "a" * 30
RESTIC_PW = "restic-SECRET-pw-777"
ACCESS = "access-SECRET-key-1"
SECRET_KEY = "secret-SECRET-key-2"
SECRETS_TYPED = [MT5_PW, BRIDGE_TOKEN, TG_TOKEN, ANTHROPIC, RESTIC_PW, ACCESS, SECRET_KEY]


@pytest.fixture
def store(monkeypatch):
    """The secret store, monkeypatched: every write is recorded; the wizard must go through set_secret only."""
    data: dict[str, str] = {}
    writes: list[str] = []

    def set_secret(key, value):
        writes.append(key)
        data[key] = value
    monkeypatch.setattr(accounts, "set_secret", set_secret)
    monkeypatch.setattr(accounts, "get_secret", lambda key: data.get(key))
    monkeypatch.setattr(accounts, "keyring", None)
    return data, writes


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(DEFAULT_SETTINGS, d / "settings.yaml")
    local = d / "settings.local.yaml"
    local.write_text("data_root: /srv/goldbot-data\ntelegram:\n  auto_alpha: 0.05\n")
    os.chmod(local, 0o644)
    return d / "settings.yaml"


def wizard(cfg, asks, secrets):
    out: list[str] = []
    a, s = deque(asks), deque(secrets)
    w = sw.Wizard(ask=lambda p: a.popleft(), ask_secret=lambda p: s.popleft(), out=out.append, settings_path=cfg)
    return w, out, a, s


FRESH_ASKS = [LOGIN, "", TG_ID, EMAIL, "y", "mynamespace", "eu-frankfurt-1", ""]
FRESH_SECRETS = [MT5_PW, BRIDGE_TOKEN, TG_TOKEN, ANTHROPIC, "", RESTIC_PW, ACCESS, SECRET_KEY]


def test_wizard_stores_secrets_only_via_set_secret_and_never_echoes(store, cfg):
    data, writes = store
    w, out, a, s = wizard(cfg, FRESH_ASKS, FRESH_SECRETS)
    assert w.run() == 0
    assert not a and not s                                     # every prompt consumed, in order
    assert data["mt5-login-icm-demo"] == LOGIN and data["mt5-icm-demo"] == MT5_PW
    assert data["mt5-bridge-url-icm-demo"] == sw.DEFAULT_BRIDGE_URL and data["mt5-bridge-token-icm-demo"] == BRIDGE_TOKEN
    assert data["telegram-bot-token"] == TG_TOKEN and data["anthropic-api-key"] == ANTHROPIC
    assert "github-token" not in data                          # optional, skipped with Enter
    assert data["restic-repository"] == ("s3:https://mynamespace.compat.objectstorage.eu-frankfurt-1.oraclecloud.com/"
                                         "goldbot-backup/goldbot")
    assert (data["restic-password"], data["restic-s3-access-key"], data["restic-s3-secret-key"]) == (RESTIC_PW, ACCESS, SECRET_KEY)
    text = "\n".join(out)
    for v in SECRETS_TYPED + [LOGIN, TG_ID, EMAIL, "mynamespace"]:
        assert v not in text, "a typed value was printed back"
    # nothing secret reached the settings file
    local = cfg.with_name("settings.local.yaml").read_text()
    assert not any(v in local for v in SECRETS_TYPED)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_settings_local_is_0600_and_merged(store, cfg):
    w, _out, _a, _s = wizard(cfg, FRESH_ASKS, FRESH_SECRETS)
    w.run()
    local = cfg.with_name("settings.local.yaml")
    assert stat.S_IMODE(local.stat().st_mode) == 0o600
    got = yaml.safe_load(local.read_text())
    assert got["data_root"] == "/srv/goldbot-data"                         # other keys kept
    assert got["telegram"] == {"auto_alpha": 0.05, "allowed_user_ids": [int(TG_ID)]}
    assert got["auth"] == {"owner_email": EMAIL}
    assert not list(local.parent.glob(".*.tmp"))


def test_rerun_skips_what_is_set_unless_changed(store, cfg):
    data, writes = store
    wizard(cfg, FRESH_ASKS, FRESH_SECRETS)[0].run()
    writes.clear()
    before = cfg.with_name("settings.local.yaml").read_text()
    # every "change it?" answered with Enter (No); github-token is still unset, so it is offered (skipped again)
    w, _out, a, s = wizard(cfg, [""] * 9, [""])
    w.run()
    assert writes == [] and not a and not s
    assert cfg.with_name("settings.local.yaml").read_text() == before
    # change only the Telegram bot token
    new_tok = "555555555:ZZZyyyXXXwwwVVVuuuTTTsssRRRqqqPPP"
    w, out, a, s = wizard(cfg, ["", "", "", "", "y", "", "", "", ""], [new_tok, ""])
    w.run()
    assert writes == ["telegram-bot-token"] and data["telegram-bot-token"] == new_tok
    assert new_tok not in "\n".join(out)


def test_invalid_input_is_reasked_without_quoting_it(store, cfg):
    data, _ = store
    w, out, _a, _s = wizard(cfg, [], ["not-a-token-SECRET", TG_TOKEN])
    w.telegram_token()
    assert data["telegram-bot-token"] == TG_TOKEN
    assert "not-a-token-SECRET" not in "\n".join(out)
    assert any("does not look right" in line for line in out)


def test_settings_that_would_not_load_are_never_written(store, cfg):
    local = cfg.with_name("settings.local.yaml")
    before = local.read_text()
    with pytest.raises(ValueError, match="nothing was changed"):
        sw.merge_local_settings({"telegram": {"auto_alpha": 9}}, cfg)      # out of range
    local.write_text("telegram: [unclosed\n")
    with pytest.raises(ValueError, match="does not parse"):
        sw.merge_local_settings({"auth": {"owner_email": EMAIL}}, cfg)
    assert local.read_text() == "telegram: [unclosed\n" and before.startswith("data_root")


def test_wizard_needs_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", None)
    assert sw.main([]) == 2
    assert "needs a terminal" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ preflight
GOOD_UNITS = {("is-active", "goldbot-bridge-tunnel"): "active", ("is-active", "chrony"): "active",
              ("is-enabled", "goldbot-deploy.timer"): "enabled"}


def fake_systemctl(units, *, ntp="yes"):
    calls: list[list[str]] = []

    def run(argv):
        calls.append(argv)
        if argv[0] == "timedatectl":
            return 0, ntp
        out = units.get((argv[1], argv[2]), "inactive")
        return (0 if out in ("active", "enabled") else 3), out
    return run, calls


def registry():
    acc = accounts.Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=int(LOGIN),
                           terminal_path="x", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=1, enabled=True)
    return {"icm-demo": acc}


def terminal(login=LOGIN, server="ICMarketsSC-Demo", mode="demo"):
    return AccountInfo(login=int(login), equity=1e4, balance=1e4, margin=0, margin_free=1e4, leverage=100,
                       currency="USD", server=server, trade_mode=mode)


@pytest.fixture
def server(tmp_path, cfg):
    root = tmp_path
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("<html></html>")
    state = root / "state"
    state.mkdir()
    db = state / "core.db"
    db.write_bytes(b"")
    os.chmod(db, 0o600)
    cfg.with_name("settings.local.yaml").write_text(
        yaml.safe_dump({"auth": {"owner_email": EMAIL}, "telegram": {"allowed_user_ids": [int(TG_ID)]}}))
    secrets = {"telegram-bot-token": TG_TOKEN, "mt5-bridge-url-icm-demo": "http://127.0.0.1:8765",
               "mt5-bridge-token-icm-demo": BRIDGE_TOKEN, **{k: "x" * 20 for k in sw.RESTIC_KEYS}}
    run, calls = fake_systemctl(GOOD_UNITS)

    def make(**over):
        kw = dict(root=root, state_dir=state, settings_path=cfg, get_secret=secrets.get, run_cmd=run,
                  bridge_probe=lambda url: None, terminal_account=lambda url, tok: terminal(),
                  load_accounts=registry, disk_usage=lambda p: (100e9, 50e9, 50e9),
                  meminfo=lambda: {"MemAvailable": 20_000_000, "MemTotal": 24_000_000})
        kw.update(over)
        return pf.PreflightContext(**kw)
    make.secrets = secrets          # type: ignore[attr-defined]
    make.calls = calls              # type: ignore[attr-defined]
    return make


EXPECTED = ["settings", "owner_email", "telegram_token", "telegram_allowlist", "tunnel_service", "bridge_config",
            "bridge_health", "terminal_account", "web_dist", "db_files_private", "backups", "time_sync", "disk",
            "memory", "deploy_timer"]


def test_preflight_all_green_exits_zero(server, capsys):
    ctx = server()
    items = pf.run_preflight(ctx)
    assert [i.name for i in items] == EXPECTED
    assert all(i.status == "ok" for i in items), [(i.name, i.detail) for i in items if i.status != "ok"]
    assert all(i.fix for i in items)                                   # every item carries its fix
    assert pf.main([], ctx) == 0
    text = capsys.readouterr().out
    assert "READY" in text and "❌" not in text and text.count("✅") == len(EXPECTED)
    assert BRIDGE_TOKEN not in text and "127.0.0.1:8765" not in text and LOGIN not in text


def test_preflight_blocking_failures_exit_nonzero_with_fixes(server, capsys, tmp_path):
    run, _ = fake_systemctl({**GOOD_UNITS, ("is-active", "goldbot-bridge-tunnel"): "inactive",
                             ("is-enabled", "goldbot-deploy.timer"): "disabled"}, ntp="no")
    os.chmod(tmp_path / "state" / "core.db", 0o644)
    del server.secrets["telegram-bot-token"]
    (tmp_path / "web" / "dist" / "index.html").unlink()
    cfg_local = tmp_path / "config" / "settings.local.yaml"
    cfg_local.write_text("{}\n")
    ctx = server(run_cmd=run, bridge_probe=lambda url: "unreachable (ConnectionError)",
                 meminfo=lambda: {"MemAvailable": 300_000, "MemTotal": 1_000_000})
    items = {i.name: i for i in pf.run_preflight(ctx)}
    failing = {n for n, i in items.items() if i.status == "fail"}
    assert failing == {"owner_email", "telegram_token", "telegram_allowlist", "tunnel_service", "bridge_health",
                       "terminal_account", "web_dist", "db_files_private", "time_sync", "memory", "deploy_timer"}
    assert "sudo goldbot-tunnel" in items["tunnel_service"].fix
    assert items["db_files_private"].fix == f"sudo chmod 600 {tmp_path / 'state' / 'core.db'}"
    assert items["deploy_timer"].fix == "sudo systemctl enable --now goldbot-deploy.timer"
    assert "goldbot run setup" in items["owner_email"].fix and "step 5" in items["owner_email"].fix
    assert pf.main([], ctx) == 1
    text = capsys.readouterr().out
    assert "NOT READY: 11 blocking" in text
    assert text.count("fix: ") == 11


def test_terminal_on_the_wrong_account_blocks(server):
    for info, why in ((terminal(login="11111111"), "another account"), (terminal(server="ICMarketsSC-Live"), "server"),
                      (terminal(mode="real"), "real")):
        items = {i.name: i for i in pf.run_preflight(server(terminal_account=lambda u, t, info=info: info))}
        assert items["terminal_account"].status == "fail" and why in items["terminal_account"].detail
        assert "11111111" not in items["terminal_account"].detail and LOGIN not in items["terminal_account"].detail


def test_bridge_rpc_error_is_reported_without_the_url(server):
    def boom(url, tok):
        raise ConnectionError(f"cannot reach {url} with {tok}")
    item = {i.name: i for i in pf.run_preflight(server(terminal_account=boom))}["terminal_account"]
    assert item.status == "fail" and "ConnectionError" in item.detail
    assert "127.0.0.1" not in item.detail and BRIDGE_TOKEN not in item.detail


def test_missing_backups_only_warn(server):
    for k in sw.RESTIC_KEYS:
        del server.secrets[k]
    items = pf.run_preflight(server())
    b = next(i for i in items if i.name == "backups")
    assert b.status == "warn" and not b.blocking and "backup --init" in b.fix
    assert pf.exit_code(items) == 0
    assert "READY: no blocking problems (1 warning(s))" in pf.render(items)


def test_no_bridge_configured_and_no_systemctl(server):
    del server.secrets["mt5-bridge-url-icm-demo"]
    items = {i.name: i for i in pf.run_preflight(server(run_cmd=lambda argv: (127, "")))}
    assert items["bridge_config"].status == "fail" and "address" in items["bridge_config"].detail
    assert items["bridge_health"].status == "fail" and items["terminal_account"].status == "fail"
    for n in ("tunnel_service", "time_sync", "deploy_timer"):
        assert items[n].status == "fail" and "on the brain" in items[n].detail


def test_preflight_is_read_only(server, tmp_path):
    before = {p: p.stat().st_mtime_ns for p in Path(tmp_path).rglob("*") if p.is_file()}
    pf.run_preflight(server())
    after = {p: p.stat().st_mtime_ns for p in Path(tmp_path).rglob("*") if p.is_file()}
    assert before == after
    assert all(c[0] == "timedatectl" or c[1] in ("is-active", "is-enabled") for c in server.calls)


def test_preflight_settings_error_never_echoes_the_bad_value():
    """A pydantic error message contains the rejected input (it can be the owner's email): show location and type only."""
    from pydantic import BaseModel, ValidationError

    from goldbot.ops.preflight import _safe_settings_error

    class M(BaseModel):
        owner_email: int

    try:
        M.model_validate({"owner_email": "someone@private.example"})
    except ValidationError as exc:
        msg = _safe_settings_error(exc)
    assert "someone@private.example" not in msg and "owner_email" in msg


def test_a_wal_file_that_vanishes_during_the_permission_check_is_skipped(server, monkeypatch):
    ctx = server()
    wal = ctx.state_dir / "core.db-wal"
    wal.write_text("")
    wal.chmod(0o644)
    real_stat = Path.stat

    def stat_gone(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "core.db-wal":                  # SQLite removed it between the glob and the stat
            raise FileNotFoundError(self)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat_gone)
    items = {i.name: i for i in pf.run_preflight(ctx)}
    assert items["db_files_private"].status == "ok" and items["db_files_private"].detail == "1 state file(s) are 0600"
