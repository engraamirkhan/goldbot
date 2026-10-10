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
        store = AuthStore(tmp_path / "auth")
        store.bootstrap_owner(store.setup_code or "", "o@x.io", "a long password here")
    finally:
        os.umask(old)
    db_files = [p for p in (tmp_path / "auth").iterdir() if p.name.startswith("aux.db")]   # aux.db and its -wal/-shm
    assert tmp_path / "auth" / "aux.db" in db_files
    for f in (tmp_path / "state" / ".secrets.json", *db_files):
        assert stat.S_IMODE(f.stat().st_mode) & 0o077 == 0, f
    assert json.loads((tmp_path / "state" / ".secrets.json").read_text()) == {"mt5-icm-demo": "pw1", "telegram-bot-token": "tok"}
    assert not [p for p in (tmp_path / "state").iterdir() if p.name != ".secrets.json"]     # no temp files left behind
