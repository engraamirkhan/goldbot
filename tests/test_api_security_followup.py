"""Security and trading-safety re-review of the dashboard lockout: no owner reset link from a session, an atomic
code lockout under its own key (sign-in stays open, halt and propose are never gated), rate-limited owner notices,
error text redacted for non-owner roles, owner-only security notices and a server-side revoke-sessions command."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import goldbot.api.auth as auth_mod
from goldbot.api.app import create_app
from goldbot.api.auth import LOCKOUT_FAILURES, AuthStore, PasswordReset, totp_code
from goldbot.telegram import automode
from goldbot.telegram.bus import ApprovalBus
from tests.test_api_owner_controls import _owner_secret

OWNER = "o@x.io"
PW = "owner password 123"


@pytest.fixture
def app_client(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=docs, owner_email=OWNER)
    return app, TestClient(app), state, docs


def _viewer(c: TestClient, owner: dict[str, str]) -> dict[str, str]:
    inv = c.post("/api/auth/invite", json={"email": "v@x.io", "role": "viewer"}, headers=owner).json()["invite_token"]
    uri = c.post("/api/auth/accept", json={"token": inv, "password": "viewer password 123"}).json()["totp_uri"]
    secret = uri.split("secret=")[1].split("&")[0]
    tok = c.post("/api/auth/login", json={"email": "v@x.io", "password": "viewer password 123",
                                          "totp": totp_code(secret)}).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _notices(state) -> list[dict]:
    f = state / "agent_runs.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _five_wrong_rearm_codes(c: TestClient, owner: dict[str, str]) -> None:
    for _ in range(LOCKOUT_FAILURES):
        assert c.post("/api/rearm", json={"totp": "000000"}, headers=owner).status_code == 403


def _window_passes(monkeypatch, windows: float = 1.0) -> None:
    real_ns = time.time_ns
    shift = int((auth_mod.LOCKOUT_WINDOW_S * windows + 1) * 10**9)
    monkeypatch.setattr(auth_mod.time, "time_ns", lambda: real_ns() + shift)


@pytest.fixture
def store(tmp_path):
    s = AuthStore(tmp_path, owner_email=OWNER)
    e = s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    return s, e.totp_uri.split("secret=")[1].split("&")[0]


# ------------------------------------------------------------------------------------- H1 owner reset links
def test_no_reset_link_can_be_made_for_the_owner_account(store, app_client):
    s, _ = store
    with pytest.raises(PermissionError, match="recovery code"):
        s.create_reset_link(OWNER, OWNER)
    app, c, _, _ = app_client
    owner, _ = _owner_secret(c, app)
    r = c.post("/api/auth/reset-link", json={"email": OWNER}, headers=owner)
    assert r.status_code == 400 and "recovery code" in r.json()["detail"]


def test_an_owner_reset_link_already_stored_is_refused(store):
    s, secret = store
    token = "planted-owner-link"
    with s.db.write() as conn:
        auth_mod._RESETS.upsert(conn, PasswordReset(token_hash=auth_mod._token_hash(token), email=OWNER,
                                                    expires=time.time() + 3600, created_by=OWNER))
    with pytest.raises(PermissionError):
        s.use_reset_link(token, "a brand new owner password")
    s.login(OWNER, PW, totp_code(secret))                      # password and authenticator unchanged


# ------------------------------------------------------------------------------------- M1 atomic lockout
def _burst(n: int, fn) -> None:
    start = threading.Barrier(n)

    def run() -> None:
        start.wait()
        try:
            fn()
        except PermissionError:
            pass
    ts = [threading.Thread(target=run) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


def test_a_concurrent_burst_of_codes_checks_at_most_the_lockout_limit(store, monkeypatch):
    s, _ = store
    checked = []
    real = AuthStore.verify_totp

    def counting(self, email, code):
        checked.append(code)
        time.sleep(0.02)                                       # widen the race between the check and the count
        return real(self, email, code)
    monkeypatch.setattr(AuthStore, "verify_totp", counting)
    _burst(32, lambda: s.verify_code_guarded("000000", "rearm", email=OWNER))
    assert 1 <= len(checked) <= LOCKOUT_FAILURES


def test_a_concurrent_burst_of_logins_checks_at_most_the_lockout_limit(store, monkeypatch):
    s, _ = store
    checked = []
    real = auth_mod.verify_password

    def counting(pw, stored):
        checked.append(pw)
        return real(pw, stored)
    monkeypatch.setattr(auth_mod, "verify_password", counting)
    _burst(32, lambda: s.login(OWNER, "wrong password!!", "000000"))
    assert 1 <= len(checked) <= LOCKOUT_FAILURES


def test_the_lock_notice_is_sent_on_crossing_the_limit_and_rate_limited(app_client, monkeypatch):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    _five_wrong_rearm_codes(c, owner)
    (n,) = _notices(state)
    assert "revoke-sessions" in n["detail"] and "/halt" in n["detail"] and OWNER not in n["detail"]
    assert "dashboard (Users)" not in n["detail"]
    _window_passes(monkeypatch)                                # the lock expires; a second burst inside the hour
    _five_wrong_rearm_codes(c, owner)
    assert len(_notices(state)) == 1


# ------------------------------------------------------------------------------------- M3 separate code counter
def test_wrong_codes_lock_rearm_and_mode_but_not_sign_in(app_client):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    _five_wrong_rearm_codes(c, owner)
    assert c.post("/api/automode", json={"mode": "auto", "totp": totp_code(secret)}, headers=owner).status_code == 403
    r = c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(secret)})
    assert r.status_code == 200 and r.json()["token"]


def test_failed_sign_ins_also_lock_rearm(app_client):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    for _ in range(LOCKOUT_FAILURES):
        c.post("/api/auth/login", json={"email": OWNER, "password": "wrong password!!", "totp": "000000"})
    r = c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 403 and "too many" in r.json()["detail"]


# ------------------------------------------------------------------------------------- safety: never gated
def test_the_code_lockout_never_gates_halt_or_propose(app_client):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    bus = ApprovalBus(state)
    bus.set_mode("auto", by="telegram:1")
    _five_wrong_rearm_codes(c, owner)
    # (a) the dashboard halt with the existing owner session
    r = c.post("/api/halt", json={"reason": "locked out"}, headers=owner)
    assert r.status_code == 200 and r.json()["halted"] is True
    # (b) Telegram /mode propose needs no code and is never locked
    msg = automode.mode_command(bus, "propose", "telegram:1",
                                totp_ok=lambda code: automode.owner_totp_ok(state, code, owner_email=OWNER),
                                eligibility=lambda: automode.auto_mode_eligibility_from_state(state),
                                audit=app.state.st.auth.audit)
    assert msg.startswith("Mode: propose-and-approve") and bus.control().approval_mode == "propose"
    # (c) the bot's /halt path (bus.set_halt) still halts
    bus.set_halt(False, by="test")
    bus.set_halt(True, by="telegram:1", reason="locked out")
    assert bus.control().halted
    # (d) sign-in is not locked by wrong codes (M3)
    assert c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(secret)}).status_code == 200
    # and the refusals say /halt still works
    r = c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 403 and "/halt still works" in r.json()["detail"]
    assert "/halt still works" in automode.MODE_LOCKED


# ------------------------------------------------------------------------------------- M2 redaction
def test_job_errors_show_the_type_only_to_non_owner_roles(app_client):
    app, c, state, _ = app_client
    owner, _ = _owner_secret(c, app)
    viewer = _viewer(c, owner)
    err = "OSError: [Errno 2] No such file or directory: '/srv/goldbot/state/x.parquet'\nTraceback (most recent call last):"
    (state / "scheduler.json").write_text(json.dumps({"ts": "2026-10-10T00:00:00+00:00", "jobs": {
        "nightly_costs": {"last_ok": False, "last_error": err, "runs": 1, "failures": 1}}}))
    (row,) = c.get("/api/jobs", headers=viewer).json()
    assert row["last_error"] == "OSError"
    (row,) = c.get("/api/jobs", headers=owner).json()
    assert row["last_error"] == err.splitlines()[0]


def test_drift_errors_show_the_type_only_to_non_owner_roles(app_client):
    app, c, state, _ = app_client
    owner, _ = _owner_secret(c, app)
    viewer = _viewer(c, owner)
    (state / "drift.json").write_text(json.dumps({"ts": "2026-10-10T00:00:00+00:00", "agents": {},
                                                  "errors": {"tsmom-g0": "OSError: /srv/goldbot/models/x.txt missing"}}))
    assert c.get("/api/health", headers=viewer).json()["errors"] == {"tsmom-g0": "OSError"}
    assert "/srv/goldbot" in c.get("/api/health", headers=owner).json()["errors"]["tsmom-g0"]


def test_redaction_keeps_the_type_and_removes_paths():
    from goldbot.api.explain import redact
    assert redact("ValueError: bad value in /srv/x.json") == "ValueError"
    assert redact("dq_events unreadable: [Errno 13] Permission denied: '/srv/goldbot/data/dq'") == \
        "dq_events unreadable: [Errno 13] Permission denied: '<path>'"
    assert redact(r"unreadable: C:\goldbot\state\x.json") == "unreadable: <path>"
    assert redact("approve/reject 12/34 ok") == "approve/reject 12/34 ok"
    assert redact("first line\nTraceback ...") == "first line"


# ------------------------------------------------------------------------------------- LOW
def test_account_security_notices_are_shown_to_the_owner_only(app_client):
    app, c, state, _ = app_client
    owner, _ = _owner_secret(c, app)
    viewer = _viewer(c, owner)
    _five_wrong_rearm_codes(c, owner)
    assert any(r["role"] == "account_security" for r in c.get("/api/agent-runs", headers=owner).json())
    assert not any(r["role"] == "account_security" for r in c.get("/api/agent-runs", headers=viewer).json())


def test_revoke_sessions_cli_signs_everyone_out(store, capsys):
    s, secret = store
    tok = s.login(OWNER, PW, totp_code(secret))
    assert auth_mod.revoke_sessions_cli(["--state", str(s.dir)]) == 0
    assert s.session_user(tok) is None and "1 session" in capsys.readouterr().out
    assert any(e["event"] == "revoke_all_sessions" for e in s.audit_events())


# ------------------------------------------------------------------------------------- every auth flow reserves first
@pytest.fixture
def team(store):
    s, secret = store
    s.accept_invite(s.create_invite(OWNER, "a@x.io", "approver"), "approver password 123")
    return s, secret


def _count_totp_checks(monkeypatch) -> list[str]:
    checked: list[str] = []
    real = AuthStore._spend_totp

    def counting(conn, u, code):
        checked.append(code)
        return real(conn, u, code)
    monkeypatch.setattr(AuthStore, "_spend_totp", staticmethod(counting))
    return checked


def test_a_concurrent_forgot_password_burst_checks_at_most_the_lockout_limit(team, monkeypatch):
    s, _ = team
    checked = _count_totp_checks(monkeypatch)
    _burst(32, lambda: s.forgot_password("a@x.io", "000000", "attacker password 99", ip="203.0.113.9"))
    assert 1 <= len(checked) <= LOCKOUT_FAILURES


def test_a_concurrent_recovery_login_burst_checks_at_most_the_lockout_limit(store, monkeypatch):
    s, _ = store
    checked: list[str] = []
    real = auth_mod.verify_password

    def counting(pw, stored):
        checked.append(pw)
        return real(pw, stored)
    monkeypatch.setattr(auth_mod, "verify_password", counting)
    _burst(32, lambda: s.recovery_login(OWNER, "wrong password!!", "AAAA-BBBB", ip="203.0.113.9"))
    assert 1 <= len(checked) <= LOCKOUT_FAILURES


def test_concurrent_change_password_and_recovery_code_bursts_check_at_most_the_limit(store, monkeypatch):
    s, secret = store
    tok = s.login(OWNER, PW, totp_code(secret))
    checked: list[str] = []
    real = auth_mod.verify_password

    def counting(pw, stored):
        checked.append(pw)
        return real(pw, stored)
    monkeypatch.setattr(auth_mod, "verify_password", counting)
    _burst(32, lambda: s.change_password(tok, "wrong password!!", "000000", "a brand new password"))
    assert 1 <= len(checked) <= LOCKOUT_FAILURES
    checked.clear()
    _window_passes(monkeypatch)
    _burst(32, lambda: s.regenerate_recovery_codes(tok, "wrong password!!", "000000"))
    assert 1 <= len(checked) <= LOCKOUT_FAILURES


def test_a_rejected_new_password_is_not_counted_as_a_failure(store):
    s, secret = store
    tok = s.login(OWNER, PW, totp_code(secret))
    for _ in range(LOCKOUT_FAILURES + 1):                    # right password, new one too short: never locks
        with pytest.raises(ValueError):
            s.change_password(tok, PW, "000000", "short")
    s.change_password(tok, PW, totp_code(secret), "a brand new password")


# ------------------------------------------------------------------------------------- revoke-sessions needs aux.db
def test_revoke_sessions_cli_refuses_a_state_dir_without_aux_db(tmp_path, capsys):
    missing = tmp_path / "not-the-state"
    missing.mkdir()
    assert auth_mod.revoke_sessions_cli(["--state", str(missing)]) != 0
    out = capsys.readouterr()
    assert str((missing / "aux.db").resolve()) in out.out + out.err
    assert not (missing / "aux.db").exists()


def test_revoke_sessions_cli_prints_the_database_it_used(store, capsys):
    s, _ = store
    assert auth_mod.revoke_sessions_cli(["--state", str(s.dir)]) == 0
    assert str((s.dir / "aux.db").resolve()) in capsys.readouterr().out


# ------------------------------------------------------------------------------------- Telegram /halt while locked
def test_telegram_halt_through_the_bot_still_halts_while_codes_are_locked(store):
    import asyncio
    import types

    from goldbot.telegram.bot import TelegramBot
    s, _ = store
    for _ in range(LOCKOUT_FAILURES):
        assert not s.verify_code_guarded("000000", "mode")
    with pytest.raises(PermissionError):
        s.verify_code_guarded("000000", "mode")
    bot = TelegramBot.__new__(TelegramBot)                    # no network: only the command handler's state
    bot.bus, bot.owner_ids, bot.state_dir = ApprovalBus(s.dir), {111}, str(s.dir)
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)
    msg = types.SimpleNamespace(text="/halt locked out", reply_text=reply_text)
    update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=111), message=msg)
    asyncio.run(bot._on_command(update, None))
    assert ApprovalBus(s.dir).control().halted and replies[0].startswith("HALTED")


# ------------------------------------------------------------------------------------- redaction of quoted and UNC paths
@pytest.mark.parametrize("text, shown", [
    ("unreadable: '/srv/gold bot/state/x.json'", "unreadable: '<path>'"),
    ('unreadable: "C:\\Program Files\\goldbot\\state\\x.json"', 'unreadable: "<path>"'),
    ("unreadable: 'C:\\Users\\Aamir Khan\\goldbot\\aux.db' (locked)", "unreadable: '<path>' (locked)"),
    ("unreadable: \\\\vps-host\\share\\goldbot\\x.parquet", "unreadable: <path>"),
    ("unreadable: '\\\\vps host\\share\\x.parquet'", "unreadable: '<path>'"),
])
def test_redaction_removes_quoted_windows_and_unc_paths_whole(text, shown):
    from goldbot.api.explain import redact
    assert redact(text) == shown
