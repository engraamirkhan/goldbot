"""Account-security review fixes (goldbot/api/auth.py): owner forgot-password needs a recovery code, owner notices,
TOTP replay, enumeration by timing, the owner pinned to auth.owner_email, reset-link revocation, fragment tokens /
Referrer-Policy, persisted lockout with a per-IP limit, and 0600 database files."""
import json
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

import goldbot.api.auth as auth_mod
from goldbot.api.app import client_ip, create_app
from goldbot.api.auth import AuthStore, hash_password, new_totp_secret, totp_code
from goldbot.api.schema import Role
from goldbot.telegram import automode
from goldbot.telegram.outbox import Outbox

PW = "a long password here"
OWNER = "o@x.io"


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


@pytest.fixture
def team(tmp_path):
    s = AuthStore(tmp_path, owner_email=OWNER)
    e = s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    secrets = {OWNER: _secret(e.totp_uri)}
    roles: tuple[tuple[str, Role], ...] = (("a@x.io", "approver"), ("v@x.io", "viewer"))
    for email, role in roles:
        _, uri = s.accept_invite(s.create_invite(OWNER, email, role), f"{role} password 123")
        secrets[email] = _secret(uri)
    return s, secrets, e.recovery_codes


def _events(s: AuthStore) -> list[str]:
    return [e["event"] for e in s.audit_events()]


def _notices(s: AuthStore) -> list[dict]:
    f = s.dir / "agent_runs.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


# ----------------------------------------------------------------------------- 1. owner forgot-password
def test_owner_forgot_password_with_only_the_authenticator_is_refused(team):
    s, sec, codes = team
    with pytest.raises(PermissionError, match="invalid credentials"):
        s.forgot_password(OWNER, totp_code(sec[OWNER]), "an attacker password")
    s.login(OWNER, PW, totp_code(sec[OWNER]))                                  # the password did not change
    assert s.recovery_codes_left(OWNER) == 10 and "password_forgot_failed" in _events(s)


def test_owner_forgot_password_needs_a_recovery_code_and_spends_it(team):
    s, sec, codes = team
    with pytest.raises(PermissionError):                                       # wrong authenticator: code kept
        s.forgot_password(OWNER, "000000", "a new owner password", recovery_code=codes[0])
    assert s.recovery_codes_left(OWNER) == 10
    with pytest.raises(PermissionError):                                       # wrong recovery code
        s.forgot_password(OWNER, totp_code(sec[OWNER]), "a new owner password", recovery_code="aaaa-bbbb-cccc-dddd")
    s.forgot_password(OWNER, totp_code(sec[OWNER]), "a new owner password", recovery_code=codes[0])
    assert s.recovery_codes_left(OWNER) == 9
    s.login(OWNER, "a new owner password", totp_code(sec[OWNER]))
    with pytest.raises(PermissionError):                                       # each recovery code works once
        s.forgot_password(OWNER, totp_code(sec[OWNER]), "another owner password", recovery_code=codes[0])


def test_every_successful_forgot_password_queues_a_telegram_notice_for_the_owner(team):
    s, sec, codes = team
    box = Outbox(s.dir)
    assert box.new_reports() == []                                             # first call starts at the end
    s.forgot_password("v@x.io", totp_code(sec["v@x.io"]), "a fresh viewer password")
    s.forgot_password(OWNER, totp_code(sec[OWNER]), "a new owner password", recovery_code=codes[1])
    msgs = box.new_reports()
    assert len(msgs) == 2
    assert "viewer account v***@x.io" in msgs[0] and "v@x.io" not in msgs[0]
    assert "owner account o***@x.io" in msgs[1] and "9 left" in msgs[1]
    assert {n["role"] for n in _notices(s)} == {"account_security"}


def test_a_failed_forgot_password_queues_no_notice(team):
    s, _, _ = team
    with pytest.raises(PermissionError):
        s.forgot_password("v@x.io", "000000", "a fresh viewer password")
    assert _notices(s) == []


def test_forgot_password_does_not_reveal_the_current_password_before_the_code_passes(team):
    s, sec, _ = team
    with pytest.raises(PermissionError, match="invalid credentials"):         # not "must differ"
        s.forgot_password("v@x.io", "000000", "viewer password 123")
    with pytest.raises(ValueError, match="differ"):
        s.forgot_password("v@x.io", totp_code(sec["v@x.io"]), "viewer password 123")


# ----------------------------------------------------------------------------- 2. TOTP replay
def test_a_totp_code_signs_in_once(team, real_totp_clock):
    s, sec, _ = team
    code = totp_code(sec["a@x.io"])
    s.login("a@x.io", "approver password 123", code)
    with pytest.raises(PermissionError):
        s.login("a@x.io", "approver password 123", code)
    assert _events(s)[-1] == "login_failed"


def test_a_code_for_an_earlier_step_than_the_last_spent_is_refused(team, real_totp_clock):
    s, sec, _ = team
    s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"], time.time() + 30))   # next step
    with pytest.raises(PermissionError):
        s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))                 # current step: older


def test_replayed_codes_are_refused_for_password_change_and_forgot(team, real_totp_clock):
    s, sec, _ = team
    code = totp_code(sec["a@x.io"])
    tok = s.login("a@x.io", "approver password 123", code)
    with pytest.raises(PermissionError):
        s.change_password(tok, "approver password 123", code, "a brand new password")
    with pytest.raises(PermissionError):
        s.forgot_password("a@x.io", code, "a brand new password")
    s.change_password(tok, "approver password 123", totp_code(sec["a@x.io"], time.time() + 30), "a brand new password")


def test_a_wrong_password_does_not_spend_the_code(team, real_totp_clock):
    s, sec, _ = team
    code = totp_code(sec["a@x.io"])
    with pytest.raises(PermissionError):
        s.login("a@x.io", "wrong password!!", code)
    s.login("a@x.io", "approver password 123", code)


def test_mode_command_shares_the_owner_replay_guard(team, real_totp_clock):
    s, sec, _ = team
    code = totp_code(sec[OWNER])
    s.login(OWNER, PW, code)
    assert not automode.owner_totp_ok(s.dir, code, owner_email=OWNER)          # already spent on the dashboard
    nxt = totp_code(sec[OWNER], time.time() + 30)
    assert automode.owner_totp_ok(s.dir, nxt, owner_email=OWNER)
    assert not automode.owner_totp_ok(s.dir, nxt, owner_email=OWNER)
    assert not automode.owner_totp_ok(s.dir, totp_code(sec[OWNER]), owner_email="")   # unset fails closed


def test_a_new_authenticator_starts_a_fresh_replay_counter(team, real_totp_clock):
    s, sec, codes = team
    s.login(OWNER, PW, totp_code(sec[OWNER], time.time() + 30))
    r = s.recovery_login(OWNER, PW, codes[0])
    s.login(OWNER, PW, totp_code(_secret(r.totp_uri)))                         # same step, new secret: accepted


# ----------------------------------------------------------------------------- 3. timing
@pytest.mark.parametrize("email", ["a@x.io", "nobody@x.io"])
def test_login_runs_scrypt_whether_or_not_the_account_exists(team, monkeypatch, email):
    s, _, _ = team
    calls: list[str] = []
    real = auth_mod.verify_password

    def counting(pw: str, h: str) -> bool:
        calls.append(h)
        return real(pw, h)
    monkeypatch.setattr(auth_mod, "verify_password", counting)
    with pytest.raises(PermissionError):
        s.login(email, "wrong password!!", "000000")
    assert len(calls) == 1


@pytest.mark.parametrize("email", [OWNER, "nobody@x.io"])
def test_recovery_login_runs_scrypt_whether_or_not_the_account_exists(team, monkeypatch, email):
    s, _, _ = team
    calls: list[str] = []
    real = auth_mod.verify_password

    def counting(pw: str, h: str) -> bool:
        calls.append(h)
        return real(pw, h)
    monkeypatch.setattr(auth_mod, "verify_password", counting)
    with pytest.raises(PermissionError):
        s.recovery_login(email, "wrong password!!", "aaaa-bbbb-cccc-dddd")
    assert len(calls) == 1


# ----------------------------------------------------------------------------- 4. owner pinned to owner_email
def _legacy_users(tmp_path, owner: str) -> None:
    u = {"email": owner, "role": "owner", "password_hash": hash_password(PW), "totp_secret": new_totp_secret()}
    (tmp_path / "users.json").write_text(json.dumps({"users": {owner: u}, "invites": {}}))


def test_users_json_import_demotes_an_owner_that_is_not_owner_email(tmp_path, caplog):
    _legacy_users(tmp_path, "intruder@x.io")
    with caplog.at_level("WARNING", logger="goldbot.api.auth"):
        s = AuthStore(tmp_path, owner_email=OWNER)
    u = s.get_user("intruder@x.io")
    assert u is not None and u.role == "viewer"
    assert "import_owner_demoted" in _events(s) and "imported as a viewer" in caplog.text
    with pytest.raises(PermissionError):
        s.create_invite("intruder@x.io", "n@x.io", "viewer")


def test_users_json_import_keeps_the_matching_owner(tmp_path):
    _legacy_users(tmp_path, OWNER)
    s = AuthStore(tmp_path, owner_email=OWNER)
    assert s.get_user(OWNER).role == "owner"                                   # type: ignore[union-attr]
    s.create_invite(OWNER, "n@x.io", "viewer")


def test_owner_actions_need_the_owner_email_and_fail_closed_when_it_changes_or_is_unset(team):
    s, _, _ = team
    s.create_invite(OWNER, "n1@x.io", "viewer")
    moved = AuthStore(s.dir, owner_email="someone.else@x.io")                  # owner_email changed later
    with pytest.raises(PermissionError, match="auth.owner_email"):
        moved.create_invite(OWNER, "n2@x.io", "viewer")
    unset = AuthStore(s.dir)
    with pytest.raises(PermissionError):
        unset.disable(OWNER, "v@x.io")
    assert not unset.is_owner(unset.get_user(OWNER))


@pytest.mark.integration
def test_owner_routes_refuse_an_owner_role_whose_email_is_not_owner_email(tmp_path):
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email=OWNER)
    c = TestClient(app)
    r = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": OWNER, "password": PW}).json()
    secret = _secret(r["totp_uri"])
    hdr = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": OWNER, "password": PW,
                                                                         "totp": totp_code(secret)}).json()["token"]}
    assert c.get("/api/users", headers=hdr).status_code == 200
    app.state.st.auth.owner_email = "someone.else@x.io"
    assert c.get("/api/users", headers=hdr).status_code == 403
    assert c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=hdr).status_code == 403


# ----------------------------------------------------------------------------- 5. reset links revoked
def _open_links(s: AuthStore, email: str) -> int:
    with s.db.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM password_resets WHERE email = ?", (email,)).fetchone()[0])


def test_password_change_forgot_and_disable_delete_open_reset_links(team):
    s, sec, _ = team
    link = s.create_reset_link(OWNER, "a@x.io")
    tok = s.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))
    s.change_password(tok, "approver password 123", totp_code(sec["a@x.io"]), "a brand new password")
    assert _open_links(s, "a@x.io") == 0
    with pytest.raises(PermissionError, match="invalid or expired"):
        s.use_reset_link(link, "yet another password")

    s.create_reset_link(OWNER, "v@x.io")
    s.forgot_password("v@x.io", totp_code(sec["v@x.io"]), "a fresh viewer password")
    assert _open_links(s, "v@x.io") == 0

    s.create_reset_link(OWNER, "v@x.io")
    s.disable(OWNER, "v@x.io")
    assert _open_links(s, "v@x.io") == 0


# ----------------------------------------------------------------------------- 6. Referrer-Policy
@pytest.mark.integration
def test_api_and_static_responses_send_no_referrer(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>goldbot</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    c = TestClient(create_app(tmp_path / "state", web_dist=dist, owner_email=OWNER))
    for path in ("/api/auth/state", "/", "/assets/app.js", "/some/route"):
        assert c.get(path).headers["referrer-policy"] == "no-referrer", path


# ----------------------------------------------------------------------------- 7. lockout
def test_lockout_survives_a_restart(team):
    s, sec, _ = team
    for _ in range(5):
        with pytest.raises(PermissionError, match="invalid credentials"):
            s.login("a@x.io", "wrong password!!", "000000")
    again = AuthStore(s.dir, owner_email=OWNER)                                # a new process
    with pytest.raises(PermissionError, match="too many"):
        again.login("a@x.io", "approver password 123", totp_code(sec["a@x.io"]))


def test_one_ip_is_limited_across_many_emails(team):
    s, sec, _ = team
    for i in range(auth_mod.IP_LOCKOUT_FAILURES):
        with pytest.raises(PermissionError, match="invalid credentials"):
            s.login(f"guess{i}@x.io", "wrong password!!", "000000", ip="203.0.113.7")
    with pytest.raises(PermissionError, match="too many"):
        s.forgot_password("v@x.io", totp_code(sec["v@x.io"]), "a fresh viewer password", ip="203.0.113.7")
    s.login("v@x.io", "viewer password 123", totp_code(sec["v@x.io"]), ip="198.51.100.9")   # another IP is fine


def test_failure_rows_are_pruned_to_the_lockout_window(team, monkeypatch):
    s, _, _ = team
    with pytest.raises(PermissionError):
        s.login("x@x.io", "wrong password!!", "000000", ip="203.0.113.7")
    real_ns = time.time_ns
    monkeypatch.setattr(auth_mod.time, "time_ns", lambda: real_ns() + (auth_mod.LOCKOUT_WINDOW_S + 1) * 10**9)
    with pytest.raises(PermissionError):
        s.login("y@x.io", "wrong password!!", "000000")
    with s.db.connection() as conn:
        keys = [k for (k,) in conn.execute("SELECT key FROM auth_failures")]
    assert keys == ["email:y@x.io"]


def test_client_ip_trusts_cf_connecting_ip_only_from_the_local_tunnel():
    class Req:
        def __init__(self, host: str, headers: dict[str, str]):
            self.client = type("C", (), {"host": host})()
            self.headers = headers
    assert client_ip(Req("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"})) == "203.0.113.7"   # type: ignore[arg-type]
    assert client_ip(Req("127.0.0.1", {})) == "127.0.0.1"                                       # type: ignore[arg-type]
    assert client_ip(Req("198.51.100.9", {"cf-connecting-ip": "1.2.3.4"})) == "198.51.100.9"   # type: ignore[arg-type]


# ----------------------------------------------------------------------------- 8. file modes
@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_database_files_are_made_private_on_every_open(team):
    s, _, _ = team
    os.chmod(s.path, 0o644)
    s.user_count()
    assert stat.S_IMODE(os.stat(s.path).st_mode) == 0o600
    wal = s.path.with_name(s.path.name + "-wal")
    if wal.exists():
        assert stat.S_IMODE(os.stat(wal).st_mode) == 0o600
