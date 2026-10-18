import json
import os
import stat
from pathlib import Path

import pytest

from goldbot.ops import accounts as acc_mod


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    # redirect registry, phase file and secret fallback to tmp; disable keyring so the file fallback is used
    cfg = tmp_path / "accounts.yaml"
    cfg.write_text((acc_mod.ACCOUNTS_FILE).read_text())
    monkeypatch.setattr(acc_mod, "ACCOUNTS_FILE", cfg)
    monkeypatch.setattr(acc_mod, "PHASE_FILE", tmp_path / "phase_state.json")
    monkeypatch.setattr(acc_mod, "ROOT", tmp_path)
    monkeypatch.setattr(acc_mod, "keyring", None)
    monkeypatch.setattr(acc_mod, "_PLATFORM", "linux")                       # headless Linux CI: the one place a file may stand in
    monkeypatch.setenv("GOLDBOT_ALLOW_PLAINTEXT", "1")
    acc_mod.register_prompter(lambda msg, secret: "pw-from-owner" if secret else "12345")
    yield cfg
    acc_mod.register_prompter(None)


def test_registry_demo_first_and_live_locked(isolated):
    accs = acc_mod.load_accounts(isolated)
    assert {a.mode for a in accs.values() if a.enabled} == {"demo"}
    assert all(not a.is_live for a in acc_mod.enabled_accounts())
    assert not acc_mod.unlock_live("icm-live", "ENABLE LIVE icm-live", isolated)   # gate not passed


def test_prompt_stores_and_reuses_credential(isolated):
    acc = acc_mod.ensure_login(acc_mod.load_accounts(isolated)["icm-demo"])
    assert acc.login == 12345
    pw = acc_mod.account_password(acc)
    assert pw == "pw-from-owner"
    acc_mod.register_prompter(lambda m, s: (_ for _ in ()).throw(AssertionError("should not prompt twice")))
    assert acc_mod.account_password(acc) == "pw-from-owner"
    # never written to the yaml: neither the password nor the login (the repo is public)
    assert "pw-from-owner" not in isolated.read_text() and "12345" not in isolated.read_text()
    assert acc_mod.get_secret("mt5-login-icm-demo") == "12345"
    assert acc_mod.load_accounts(isolated)["icm-demo"].login == 12345      # read back from the keyring


def test_an_account_name_that_looks_like_a_login_fails_at_load(isolated, monkeypatch):
    """Account names reach the owner's dashboard, Telegram /status and the digest: a name carrying 5+ digits (an MT5
    login) is refused when config/accounts.yaml is loaded (the displays also cut such runs, as a backstop)."""
    import yaml
    raw = yaml.safe_load(isolated.read_text())
    raw["accounts"]["icm-demo-51234567"] = raw["accounts"].pop("icm-demo")
    isolated.write_text(yaml.safe_dump(raw))
    looked_up: list[str] = []
    real_get = acc_mod.get_secret

    def spy(key: str) -> str | None:
        looked_up.append(key)
        return real_get(key)
    monkeypatch.setattr(acc_mod, "get_secret", spy)
    with pytest.raises(ValueError, match="5 or more digits") as excinfo:
        acc_mod.load_accounts(isolated)
    # security review: the error never echoes the name, and the keyring is not asked for its login
    assert "51234567" not in str(excinfo.value) and "17 characters" in str(excinfo.value)
    assert not any("51234567" in k for k in looked_up)
    # backstop: building the record directly fails without the input in the error either
    with pytest.raises(ValueError) as direct:
        acc_mod.Account(account_id="icm-demo-51234567", broker="icm", mode="demo", server="s", login=None,
                        terminal_path="t", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=1, enabled=True)
    assert "51234567" not in str(direct.value)
    for ok in ("icm-demo", "icm-live", "ftmo-2026"):
        assert acc_mod.ACCOUNT_ID_LOGIN.search(ok) is None


def test_the_committed_registry_holds_no_login_numbers():
    import yaml
    raw = yaml.safe_load(acc_mod.ACCOUNTS_FILE.read_text())
    assert all(a.get("login") is None for a in raw["accounts"].values())


def test_live_unlock_requires_gate_and_phrase(isolated, tmp_path):
    acc_mod.PHASE_FILE.write_text(json.dumps({"phase": 3, "gates_passed": ["backtest_to_paper", "paper_to_tiny_live"]}))
    assert not acc_mod.unlock_live("icm-live", "yes please", isolated)
    assert acc_mod.unlock_live("icm-live", "ENABLE LIVE icm-live", isolated)
    assert any(a.is_live for a in acc_mod.enabled_accounts())


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_secret_and_user_files_are_never_readable_by_others(isolated, tmp_path, monkeypatch):
    from goldbot.api.auth import AuthStore

    def chmod_fails(self, mode, **kw):              # e.g. a filesystem that ignores chmod: must not leave 0644
        raise OSError("chmod not supported")
    monkeypatch.setattr(Path, "chmod", chmod_fails)
    old = os.umask(0o022)
    try:
        acc_mod.set_secret("mt5-icm-demo", "pw1")
        acc_mod.set_secret("telegram-bot-token", "tok")
        store = AuthStore(tmp_path / "auth", owner_email="o@x.io")
        store.bootstrap_owner(store.setup_code or "", "o@x.io", "a long password here")
    finally:
        os.umask(old)
    db_files = [p for p in (tmp_path / "auth").iterdir() if p.name.startswith("aux.db")]   # aux.db and its -wal/-shm
    assert tmp_path / "auth" / "aux.db" in db_files
    for f in (tmp_path / "state" / ".secrets.json", *db_files):
        assert stat.S_IMODE(f.stat().st_mode) & 0o077 == 0, f
    assert json.loads((tmp_path / "state" / ".secrets.json").read_text()) == {"mt5-icm-demo": "pw1", "telegram-bot-token": "tok"}
    assert not [p for p in (tmp_path / "state").iterdir() if p.name != ".secrets.json"]     # no temp files left behind


class _FakeKeyring:
    """A working backend in a dict (class name outside the `fail` module, like macOS/Windows backends)."""
    def __init__(self): self.d = {}
    def set_password(self, svc, k, v): self.d[k] = v
    def get_password(self, svc, k): return self.d.get(k)
    def get_keyring(self): return type("Keyring", (), {"__module__": "keyring.backends.macOS"})()


def test_set_secret_refuses_plaintext_without_the_opt_in(isolated, monkeypatch, tmp_path):
    monkeypatch.delenv("GOLDBOT_ALLOW_PLAINTEXT")
    with pytest.raises(RuntimeError, match="GOLDBOT_ALLOW_PLAINTEXT"):
        acc_mod.set_secret("k", "v")
    assert not (tmp_path / "state" / ".secrets.json").exists()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_set_secret_never_falls_back_to_a_file_on_macos_or_windows(isolated, monkeypatch, tmp_path, platform):
    monkeypatch.setattr(acc_mod, "_PLATFORM", platform)             # opt-in is still set by the fixture
    with pytest.raises(RuntimeError, match="keyring"):
        acc_mod.set_secret("k", "v")
    assert not (tmp_path / "state" / ".secrets.json").exists()


def test_set_command_says_which_store_it_used(isolated, capsys):
    assert acc_mod.main(["set", "some-key"]) == 0
    assert "state/.secrets.json" in capsys.readouterr().out.replace("\\", "/")


def test_set_command_says_keyring_when_the_keyring_took_it(isolated, monkeypatch, capsys):
    monkeypatch.setattr(acc_mod, "keyring", _FakeKeyring())
    assert acc_mod.main(["set", "some-key"]) == 0
    assert "in the OS keyring" in capsys.readouterr().out


def test_get_secret_reads_an_existing_fallback_file_with_a_migration_hint(isolated, monkeypatch, tmp_path, capsys):
    f = tmp_path / "state" / ".secrets.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"old": "v"}))
    monkeypatch.setattr(acc_mod, "_PLATFORM", "darwin")
    assert acc_mod.get_secret("old") == "v"
    assert "migrate" in capsys.readouterr().err


def test_existing_fallback_file_moves_into_the_keyring_and_is_deleted(isolated, monkeypatch, tmp_path, capsys):
    f = tmp_path / "state" / ".secrets.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"a": "secret-a", "b": "secret-b"}))
    kr = _FakeKeyring()
    monkeypatch.setattr(acc_mod, "keyring", kr)
    assert acc_mod.get_secret("a") == "secret-a"
    assert kr.d == {"a": "secret-a", "b": "secret-b"}
    assert not f.exists()                                           # emptied, so removed
    out = capsys.readouterr()
    assert "secret-a" not in out.out + out.err                      # values are never printed


def test_a_keyring_that_fails_a_write_keeps_the_fallback_key(isolated, monkeypatch, tmp_path):
    f = tmp_path / "state" / ".secrets.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"a": "x", "b": "y"}))
    kr = _FakeKeyring()
    real = kr.set_password
    def flaky(s, k, v):
        if k != "a":
            raise OSError("locked")
        real(s, k, v)
    monkeypatch.setattr(kr, "set_password", flaky)
    monkeypatch.setattr(acc_mod, "keyring", kr)
    acc_mod.migrate_fallback()
    assert json.loads(f.read_text()) == {"b": "y"}


def test_secret_store_reports_keyring_for_a_failing_backend_as_file(isolated, monkeypatch):
    class Fail(_FakeKeyring):
        def get_keyring(self): return type("Keyring", (), {"__module__": "keyring.backends.fail"})()
    monkeypatch.setattr(acc_mod, "keyring", Fail())
    assert acc_mod.secret_store() == "file"
