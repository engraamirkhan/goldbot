"""Owner-only user creation, password change/forgot/reset, owner recovery codes and the sole-owner rule
(goldbot/api/auth.py)."""
import logging
import time

import pytest

from goldbot.api.auth import AuthStore, owner_health_note, totp_code
from goldbot.api.schema import Role
from goldbot.config import AuthSettings, load_settings

PW = "a long password here"
OWNER = "o@x.io"


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


@pytest.fixture
def team(tmp_path):
    """Owner, an approver and a viewer, with their authenticator secrets."""
    s = AuthStore(tmp_path, owner_email=OWNER)
    e = s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    secrets = {OWNER: _secret(e.totp_uri)}
    roles: tuple[tuple[str, Role], ...] = (("a@x.io", "approver"), ("v@x.io", "viewer"))
    for email, role in roles:
        _, uri = s.accept_invite(s.create_invite(OWNER, email, role), f"{role} password 123")
        secrets[email] = _secret(uri)
    return s, secrets, e.recovery_codes


def _sessions(s: AuthStore, email: str) -> int:
    with s.db.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM sessions WHERE email = ?", (email,)).fetchone()[0])


def _events(s: AuthStore) -> list[str]:
    return [e["event"] for e in s.audit_events()]


# ----------------------------------------------------------------------------- the owner is the sole admin
def test_owner_email_setting_defaults_to_unset_and_the_repo_holds_no_address():
    assert AuthSettings().owner_email is None
    configured = load_settings().auth.owner_email
    assert configured is None or configured.endswith("example.com")


def test_bootstrap_refuses_without_a_configured_owner_email(tmp_path):
    s = AuthStore(tmp_path)
    with pytest.raises(PermissionError, match="auth.owner_email is not set"):
        s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    assert s.user_count() == 0 and s.setup_code


def test_bootstrap_refuses_another_email_without_echoing_the_configured_one(tmp_path):
    s = AuthStore(tmp_path, owner_email="Real.Owner@x.io")
    with pytest.raises(PermissionError) as exc:
        s.bootstrap_owner(s.setup_code or "", "intruder@x.io", PW)
    assert "real.owner" not in str(exc.value).lower() and "auth.owner_email" in str(exc.value)
    e = s.bootstrap_owner(s.setup_code or "", "REAL.owner@x.io", PW)           # case-insensitive
    assert e.email == "real.owner@x.io" and len(e.recovery_codes) == 10


def test_bootstrap_applies_the_password_policy(tmp_path):
    s = AuthStore(tmp_path, owner_email=OWNER)
    with pytest.raises(ValueError, match="12"):
        s.bootstrap_owner(s.setup_code or "", OWNER, "short")


def test_owner_role_cannot_be_granted_by_invite_or_role_change(team):
    s, _, _ = team
    with pytest.raises(ValueError, match="owner role"):
        s.create_invite(OWNER, "x@x.io", "owner")
    with pytest.raises(ValueError, match="owner role"):
        s.set_role(OWNER, "a@x.io", "owner")
    with pytest.raises(ValueError, match="demote the owner"):
        s.set_role(OWNER, OWNER, "approver")
    with pytest.raises(ValueError):
        s.disable(OWNER, OWNER)
    assert [u["email"] for u in s.list_users() if u["role"] == "owner"] == [OWNER]


def test_owner_grants_and_changes_approver_and_viewer(team):
    s, _, _ = team
    s.set_role(OWNER, "v@x.io", "approver")
    s.set_role(OWNER, "a@x.io", "viewer")
    roles = {u["email"]: u["role"] for u in s.list_users()}
    assert roles["v@x.io"] == "approver" and roles["a@x.io"] == "viewer"


@pytest.mark.parametrize("who", ["a@x.io", "v@x.io"])
def test_approver_and_viewer_cannot_administer(team, who):
    s, _, _ = team
    for action in (lambda: s.create_invite(who, "n@x.io", "viewer"), lambda: s.set_role(who, "v@x.io", "viewer"),
                   lambda: s.disable(who, "v@x.io"), lambda: s.enable(who, "v@x.io"),
                   lambda: s.create_reset_link(who, "v@x.io"), lambda: s.revoke_sessions(who, "v@x.io")):
        with pytest.raises(PermissionError):
            action()


def test_an_invite_never_overwrites_an_existing_account(team):
    s, _, _ = team
    with pytest.raises(ValueError, match="already a user"):
        s.create_invite(OWNER, OWNER, "viewer")


def test_a_mismatched_existing_owner_is_reported_not_changed(tmp_path, caplog):
    s = AuthStore(tmp_path, owner_email=OWNER)
    s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    with caplog.at_level(logging.WARNING, logger="goldbot.api.auth"):
        moved = AuthStore(tmp_path, owner_email="someone.else@x.io")
    assert "do not match auth.owner_email" in caplog.text and "someone.else" not in caplog.text
    kept = moved.get_user(OWNER)
    assert kept is not None and kept.role == "owner"                          # nothing changed
    assert "do not match" in (owner_health_note(tmp_path, "someone.else@x.io") or "")
    assert owner_health_note(tmp_path, OWNER) is None
    assert "not set" in (owner_health_note(tmp_path, None) or "")
    assert owner_health_note(tmp_path / "nowhere", OWNER) is None


# ----------------------------------------------------------------------------- change password
def test_change_password_revokes_other_sessions_and_is_audited(team):
    s, sec, _ = team
    keep = s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    other = s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    with pytest.raises(PermissionError):
        s.change_password(keep, "approver password 123", "000000", "a brand new password")
    with pytest.raises(PermissionError):
        s.change_password(keep, "wrong password!!", totp_code(sec["a@x.io"]), "a brand new password")
    with pytest.raises(ValueError, match="differ"):
        s.change_password(keep, "approver password 123", totp_code(sec["a@x.io"]), "approver password 123")
    with pytest.raises(ValueError, match="12"):
        s.change_password(keep, "approver password 123", totp_code(sec["a@x.io"]), "short")
    s.change_password(keep, "approver password 123", totp_code(sec["a@x.io"]), "a brand new password")
    assert s.session_user(keep) is not None and s.session_user(other) is None
    s.login("a@x.io", "a brand new password", totp_code(sec["a@x.io"]))
    with pytest.raises(PermissionError):
        s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    assert {"password_change_failed", "password_change"} <= set(_events(s))


def test_change_password_needs_a_session(team):
    s, sec, _ = team
    with pytest.raises(PermissionError):
        s.change_password("forged", "approver password 123", totp_code(sec["a@x.io"]), "a brand new password")


# ----------------------------------------------------------------------------- forgot password
def test_forgot_password_with_a_current_code_revokes_every_session(team):
    s, sec, _ = team
    tok = s.login("v@x.io", "viewer password 123", totp_code(sec["v@x.io"]))
    s.forgot_password("V@x.io", totp_code(sec["v@x.io"]), "a fresh viewer password")
    assert s.session_user(tok) is None and _sessions(s, "v@x.io") == 0
    s.login("v@x.io", "a fresh viewer password", totp_code(sec["v@x.io"]))
    assert "password_forgot" in _events(s)


def test_forgot_password_is_enumeration_safe_and_counts_towards_the_lockout(team):
    s, sec, _ = team
    errors = []
    for email in ("v@x.io", "nobody@x.io"):
        with pytest.raises(PermissionError) as exc:
            s.forgot_password(email, "000000", "a fresh viewer password")
        errors.append(str(exc.value))
    assert errors[0] == errors[1] == "invalid credentials"
    for _ in range(4):
        with pytest.raises(PermissionError):
            s.forgot_password("v@x.io", "000000", "a fresh viewer password")
    with pytest.raises(PermissionError, match="too many"):                     # even with the right code now
        s.forgot_password("v@x.io", totp_code(sec["v@x.io"]), "a fresh viewer password")
    with pytest.raises(PermissionError, match="too many"):                     # and login shares the counter
        s.login("v@x.io", "viewer password 123", totp_code(sec["v@x.io"]))
    for _ in range(4):
        with pytest.raises(PermissionError):
            s.forgot_password("nobody@x.io", "000000", "a fresh viewer password")
    with pytest.raises(PermissionError, match="too many"):                     # unknown emails lock the same way
        s.forgot_password("nobody@x.io", "000000", "a fresh viewer password")
    assert "password_forgot_failed" in _events(s)


def test_forgot_password_policy_errors_do_not_depend_on_the_account(team):
    s, _, _ = team
    for email in ("v@x.io", "nobody@x.io"):
        with pytest.raises(ValueError, match="12"):
            s.forgot_password(email, "000000", "short")


def test_forgot_password_refuses_a_disabled_user(team):
    s, sec, _ = team
    s.disable(OWNER, "v@x.io")
    with pytest.raises(PermissionError, match="invalid credentials"):
        s.forgot_password("v@x.io", totp_code(sec["v@x.io"]), "a fresh viewer password")


# ----------------------------------------------------------------------------- owner reset link (lost authenticator)
def test_reset_link_sets_password_and_new_authenticator_once(team):
    s, sec, _ = team
    tok = s.login("v@x.io", "viewer password 123", totp_code(sec["v@x.io"]))
    link = s.create_reset_link(OWNER, "v@x.io")
    with pytest.raises(ValueError, match="differ"):
        s.use_reset_link(link, "viewer password 123")
    e = s.use_reset_link(link, "a reset viewer password")
    assert e.email == "v@x.io" and _secret(e.totp_uri) != sec["v@x.io"]
    assert s.session_user(tok) is None
    with pytest.raises(PermissionError):                                       # the old authenticator is gone
        s.login("v@x.io", "a reset viewer password", totp_code(sec["v@x.io"]))
    s.failed.clear()
    s.login("v@x.io", "a reset viewer password", totp_code(_secret(e.totp_uri)))
    with pytest.raises(PermissionError, match="invalid or expired"):           # single use
        s.use_reset_link(link, "yet another password")
    assert {"reset_link", "password_reset"} <= set(_events(s))


def test_expired_or_unknown_reset_link_is_refused(team):
    s, _, _ = team
    link = s.create_reset_link(OWNER, "v@x.io", ttl_h=0)
    time.sleep(0.01)
    with pytest.raises(PermissionError, match="invalid or expired"):
        s.use_reset_link(link, "a reset viewer password")
    with pytest.raises(PermissionError):
        s.use_reset_link("forged", "a reset viewer password")
    with pytest.raises(KeyError):
        s.create_reset_link(OWNER, "nobody@x.io")


def test_reset_tokens_are_stored_hashed(team):
    s, _, _ = team
    link = s.create_reset_link(OWNER, "v@x.io")
    with s.db.connection() as conn:
        assert link not in "\n".join(conn.iterdump())


# ----------------------------------------------------------------------------- owner recovery codes
def test_recovery_code_logs_the_owner_in_once_and_reenrols_the_authenticator(team):
    s, sec, codes = team
    old = s.login(OWNER, PW, totp_code(sec[OWNER]))
    r = s.recovery_login(OWNER, PW, codes[0].upper())                         # case and spacing tolerant
    assert r.role == "owner" and r.recovery_codes_left == 9 and _secret(r.totp_uri) != sec[OWNER]
    assert s.session_user(r.token) is not None and s.session_user(old) is None
    with pytest.raises(PermissionError):                                       # each code works once
        s.recovery_login(OWNER, PW, codes[0])
    s.failed.clear()
    s.login(OWNER, PW, totp_code(_secret(r.totp_uri)))
    assert s.recovery_codes_left(OWNER) == 9 and "recovery_login" in _events(s)


def test_recovery_codes_need_the_password_and_work_only_for_the_owner(team):
    s, _, codes = team
    with pytest.raises(PermissionError):
        s.recovery_login(OWNER, "wrong password!!", codes[1])
    with pytest.raises(PermissionError):
        s.recovery_login("a@x.io", "approver password 123", codes[1])
    with pytest.raises(PermissionError):
        s.recovery_login(OWNER, PW, "aaaa-bbbb-cccc-dddd")
    assert s.recovery_codes_left(OWNER) == 10 and "recovery_login_failed" in _events(s)


def test_recovery_codes_are_stored_hashed_and_can_be_regenerated(team):
    s, sec, codes = team
    with s.db.connection() as conn:
        dump = "\n".join(conn.iterdump())
    assert not any(c in dump for c in codes)
    tok = s.login(OWNER, PW, totp_code(sec[OWNER]))
    with pytest.raises(PermissionError):
        s.regenerate_recovery_codes(tok, PW, "000000")
    new = s.regenerate_recovery_codes(tok, PW, totp_code(sec[OWNER]))
    assert len(set(new)) == 10 and not set(new) & set(codes)
    with pytest.raises(PermissionError):                                       # the old codes stop working
        s.recovery_login(OWNER, PW, codes[2])
    viewer = s.login("v@x.io", "viewer password 123", totp_code(sec["v@x.io"]))
    with pytest.raises(PermissionError):
        s.regenerate_recovery_codes(viewer, "viewer password 123", totp_code(sec["v@x.io"]))


# ----------------------------------------------------------------------------- enable, revoke, disable
def test_owner_reenables_a_user_and_revokes_sessions(team):
    s, sec, _ = team
    t1 = s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    t2 = s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    assert s.revoke_sessions(OWNER, "a@x.io") == 2
    assert s.session_user(t1) is None and s.session_user(t2) is None
    t3 = s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    s.disable(OWNER, "a@x.io")
    assert s.session_user(t3) is None
    with pytest.raises(PermissionError):
        s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    s.enable(OWNER, "a@x.io")
    s.failed.clear()
    assert s.session_user(s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))) is not None
    assert {"revoke_sessions", "disable", "enable"} <= set(_events(s))
    with pytest.raises(KeyError):
        s.enable(OWNER, "nobody@x.io")
