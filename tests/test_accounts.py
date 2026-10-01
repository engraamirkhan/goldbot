import json

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
    # never written to the yaml
    assert "pw-from-owner" not in isolated.read_text()


def test_live_unlock_requires_gate_and_phrase(isolated, tmp_path):
    acc_mod.PHASE_FILE.write_text(json.dumps({"phase": 3, "gates_passed": ["backtest_to_paper", "paper_to_tiny_live"]}))
    assert not acc_mod.unlock_live("icm-live", "yes please", isolated)
    assert acc_mod.unlock_live("icm-live", "ENABLE LIVE icm-live", isolated)
    assert any(a.is_live for a in acc_mod.enabled_accounts())
