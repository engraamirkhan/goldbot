"""scripts/e2e_server.py refuses to run on a goldbot from another checkout (TD-3)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("e2e_server", REPO / "scripts" / "e2e_server.py")
assert _spec is not None and _spec.loader is not None
e2e_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e_server)


def test_this_tree_is_accepted() -> None:
    e2e_server.check_tree()


def test_another_tree_is_refused_with_both_paths(tmp_path: Path) -> None:
    other = tmp_path / "main-checkout" / "goldbot" / "__init__.py"
    with pytest.raises(SystemExit) as exc:
        e2e_server.check_tree(str(other))
    msg = str(exc.value)
    assert str(other.parent) in msg and str(REPO) in msg and "PYTHON=" in msg


def test_a_sibling_directory_with_the_same_prefix_is_refused() -> None:
    sibling = Path(str(REPO) + "-other") / "goldbot" / "__init__.py"       # not a string-prefix match
    with pytest.raises(SystemExit):
        e2e_server.check_tree(str(sibling))


# ---- the e2e TOTP clock (TD-4): stepping it replaces sleeping for the next real 30-s step
def _server_clock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import goldbot.api.auth as auth
    monkeypatch.setattr(auth, "_totp_now", auth.time.time)          # restored after the test
    e2e_server.install_totp_clock(str(tmp_path))
    return auth, tmp_path / e2e_server.TOTP_CLOCK_FILE


def _code_at(auth, secret: str, step_from_now: int) -> str:
    return auth.totp_code(secret, (int(auth.time.time() // 30) + step_from_now) * 30.0)


def test_clock_file_moves_the_servers_window_and_only_forward(monkeypatch, tmp_path) -> None:
    auth, clock = _server_clock(monkeypatch, tmp_path)
    secret = auth.new_totp_secret()
    assert auth.totp_step(secret, _code_at(auth, secret, 1)) is not None      # the real window: now+1 is accepted
    assert auth.totp_step(secret, _code_at(auth, secret, 3)) is None          # now+3 is not: this is what made tests sleep
    clock.write_text("2")
    assert auth.totp_step(secret, _code_at(auth, secret, 3)) is not None      # stepped two: now+3 is now+1 to the server
    assert auth.totp_step(secret, _code_at(auth, secret, 0)) is None          # and now+0 has left the window
    clock.write_text("1")                                                     # a smaller number never moves time back
    assert auth.totp_step(secret, _code_at(auth, secret, 3)) is not None


def test_clock_file_missing_or_half_written_keeps_the_last_value(monkeypatch, tmp_path) -> None:
    auth, clock = _server_clock(monkeypatch, tmp_path)
    secret = auth.new_totp_secret()
    assert auth.totp_step(secret, _code_at(auth, secret, 0)) is not None      # no file: the real clock
    clock.write_text("2")
    assert auth.totp_step(secret, _code_at(auth, secret, 2)) is not None
    clock.write_text("")                                                      # caught mid-write
    assert auth.totp_step(secret, _code_at(auth, secret, 2)) is not None
    clock.unlink()
    assert auth.totp_step(secret, _code_at(auth, secret, 2)) is not None


def test_replay_guard_still_holds_on_the_stepped_clock(monkeypatch, tmp_path) -> None:
    auth, clock = _server_clock(monkeypatch, tmp_path)
    store = auth.AuthStore(tmp_path / "auth", owner_email="owner@example.com")
    enrol = store.bootstrap_owner(store.setup_code or "", "owner@example.com", "owner password 123")
    secret = enrol.totp_uri.split("secret=")[1].split("&")[0]
    clock.write_text("5")
    code = _code_at(auth, secret, 5)
    assert store.verify_totp("owner@example.com", code) is True
    assert store.verify_totp("owner@example.com", code) is False              # each code is accepted once


# ---- the emailed-code HMAC key (ADR 0028): a throwaway key per e2e server, never the developer's keyring
def test_email_code_key_is_throwaway_and_never_touches_the_keyring(monkeypatch) -> None:
    import goldbot.api.auth as auth

    def keyring(*_: object) -> None:
        raise AssertionError("the e2e server must not read or write the OS keyring")

    monkeypatch.setattr(auth, "_KEY_CACHE", {})
    monkeypatch.setattr(auth, "_secret_get", keyring)
    monkeypatch.setattr(auth, "_secret_set", keyring)
    e2e_server.install_email_code_key()
    key = auth.email_code_key()
    assert key is not None and len(key) == 32
    monkeypatch.setattr(auth, "_KEY_CACHE", {})
    e2e_server.install_email_code_key()
    assert auth.email_code_key() != key                                       # a new key for every server
