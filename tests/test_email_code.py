"""The owner's sign-in with password + a 6-digit code sent by email (BACKLOG 77, ADR 0028; goldbot/api/auth.py
`request_email_code` / `verify_email_code` / `resend_email_code` / `verify_stepup`, the /api/auth/email-code routes,
goldbot/api/mailer.py `email_code_message`).

Design rule (docs/ux/sign-in-email-code.md, security conditions 1-11): "password + emailed code signs the OWNER in
without the authenticator, only while mail is ready"; the answer is the same for every input; the code is single use,
10 minutes, 3 tries, stored only as a challenge-bound HMAC with a keyring key; a session opened with an email code
needs the authenticator (or a recovery code) for every owner admin action."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import smtplib
import statistics
import tempfile
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import goldbot.api.auth as auth_mod
from goldbot.api import mailer as mailer_mod
from goldbot.api.app import create_app
from goldbot.api.auth import (
    EMAIL_CODE_KEY,
    EMAIL_CODE_TRIES,
    EMAIL_CODE_TTL_S,
    EMAIL_LINK_PER_HOUR,
    LOCKOUT_FAILURES,
    RESEND_AFTER_S,
    AuthStore,
    totp_code,
)
from goldbot.config import MailSettings

PW = "a long password here"
OWNER = "o@x.io"
MAIL = MailSettings(enabled=True, host="smtp.test", port=587, from_address="bot@x.io",
                    dashboard_origin="https://dash.test")
STEPUP = "authenticator code required"
S = 10**9


class FakeMailer:
    """Records what would be sent; `fail` raises an SMTP error carrying the address and the body."""

    def __init__(self, ready: bool = True, fail: bool = False):
        self._ready, self.fail = ready, fail
        self.sent: list[tuple[str, str, str]] = []

    def ready(self) -> bool:
        return self._ready

    def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({to: (550, body.encode())})
        self.sent.append((to, subject, body))

    def codes(self) -> list[str]:
        return [_code(b) for _, s, b in self.sent if s == mailer_mod.EMAIL_CODE_SUBJECT]


def _code(body: str) -> str:
    first = body.split("\n", 1)[0]
    assert re.fullmatch(r"\d{6}", first), body
    return first


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


class Api:
    def __init__(self, tmp_path, mail: MailSettings = MAIL, mailer: FakeMailer | None = None):
        self.fake = mailer if mailer is not None else FakeMailer()
        self.app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email=OWNER, mail=mail, mailer=self.fake)
        self.c = TestClient(self.app)
        self.st = self.app.state.st
        r = self.c.post("/api/auth/setup", json={"setup_code": self.st.auth.setup_code, "email": OWNER,
                                                 "password": PW}).json()
        self.secret = _secret(r["totp_uri"])
        self.recovery = r["recovery_codes"]
        self.owner_h = self.login()
        self.secrets: dict[str, str] = {}
        for email, role in (("v@x.io", "viewer"), ("a@x.io", "approver")):
            tok = self.c.post("/api/auth/invite", json={"email": email, "role": role},
                              headers=self.owner_h).json()["invite_token"]
            uri = self.c.post("/api/auth/accept", json={"token": tok, "password": PW}).json()["totp_uri"]
            self.secrets[email] = _secret(uri)

    def login(self, email: str = OWNER) -> dict[str, str]:
        secret = self.secret if email == OWNER else self.secrets[email]
        r = self.c.post("/api/auth/login", json={"email": email, "password": PW, "totp": totp_code(secret)})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def ask(self, email: str = OWNER, password: str = PW):
        return self.c.post("/api/auth/email-code", json={"email": email, "password": password})

    def verify(self, challenge: str, code: str):
        return self.c.post("/api/auth/email-code/verify", json={"challenge": challenge, "code": code})

    def resend(self, challenge: str):
        return self.c.post("/api/auth/email-code/resend", json={"challenge": challenge})

    def code_session(self) -> dict[str, str]:
        """An owner session opened with password + emailed code."""
        ch = self.ask().json()["challenge"]
        r = self.verify(ch, self.fake.codes()[-1])
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def audit(self) -> list[dict]:
        return self.st.auth.audit_events()

    def events(self) -> list[str]:
        return [e["event"] for e in self.audit()]


@pytest.fixture
def api(tmp_path):
    return Api(tmp_path)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


def _store(tmp_path) -> AuthStore:
    s = AuthStore(tmp_path, owner_email=OWNER)
    s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    return s


# ----------------------------------------------------------------------------- the 14 acceptance tests (BACKLOG 77)
def test_email_code_is_single_use(api):
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    first = api.verify(ch, code)
    assert first.status_code == 200 and first.json()["signed_in_with"] == "email_code"
    assert api.verify(ch, code).status_code == 403


def test_email_code_ttl_is_10_minutes(api, lockout_clock):
    assert EMAIL_CODE_TTL_S == 600
    r = api.ask().json()
    assert r["expires_in_s"] == 600 and r["resend_after_s"] == RESEND_AFTER_S == 60
    lockout_clock[0] += EMAIL_CODE_TTL_S * S - 1                   # the last nanosecond of the 10 minutes
    assert api.verify(r["challenge"], api.fake.codes()[0]).status_code == 200
    r2 = api.ask().json()
    lockout_clock[0] += EMAIL_CODE_TTL_S * S                       # exactly 600 s: expired
    assert api.verify(r2["challenge"], api.fake.codes()[1]).status_code == 403


def test_email_code_is_6_digits_from_csprng(api, monkeypatch):
    calls: list[int] = []

    def randbelow(n: int) -> int:
        calls.append(n)
        return 42
    monkeypatch.setattr(auth_mod.secrets, "randbelow", randbelow)
    ch = api.ask().json()["challenge"]
    assert calls == [10**6]
    assert api.fake.codes() == ["000042"]                            # zero-padded to 6 digits
    assert api.verify(ch, "000042").status_code == 200


def test_email_code_hashed_at_rest(api, email_code_keyring):
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    with api.st.auth.db.connection() as conn:
        rows = conn.execute("SELECT challenge_sha256, code_hmac FROM email_codes").fetchall()
        dump = json.dumps([list(r) for r in conn.execute("SELECT * FROM email_codes")])
    key = bytes.fromhex(email_code_keyring[EMAIL_CODE_KEY])
    assert rows == [(hashlib.sha256(ch.encode()).hexdigest(),
                     hmac.new(key, f"{ch}|{code}".encode(), hashlib.sha256).hexdigest())]
    assert code not in dump and ch not in dump
    assert hashlib.sha256(code.encode()).hexdigest() not in dump      # keyed, not a plain hash of the 6 digits
    assert key.hex() not in api.st.auth.path.read_bytes().decode("latin-1")    # the key is never in aux.db


def test_email_code_never_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    a = Api(tmp_path / "ok")
    ch = a.ask().json()["challenge"]
    code = a.fake.codes()[0]
    a.verify(ch, _wrong(code))
    a.verify(ch, code)
    b = Api(tmp_path / "fail", mailer=FakeMailer(fail=True))
    assert b.ask().status_code == 200
    assert "email_code_send_failed" in b.events()
    assert "not sent" in caplog.text
    for text in (caplog.text, json.dumps(a.audit()), json.dumps(b.audit()),
                 (tmp_path / "ok" / "agent_runs.jsonl").read_text()):
        assert code not in text and ch not in text and OWNER not in caplog.text


def test_email_code_3_sends_per_hour(api, tmp_path):
    answers = [api.ask() for _ in range(EMAIL_LINK_PER_HOUR + 1)]
    assert len(api.fake.codes()) == EMAIL_LINK_PER_HOUR == 3                  # the 4th mail in the hour is refused
    assert {r.status_code for r in answers} == {200}
    assert {tuple(sorted(r.json())) for r in answers} == {("challenge", "expires_in_s", "resend_after_s")}
    assert answers[-1].json()["resend_after_s"] > RESEND_AFTER_S             # the screen shows when to try again
    assert "email_code_rate_limited" in api.events()
    # per client IP whatever the address, and shared with the email sign-in link (one budget of 3 mails an hour)
    b = Api(tmp_path / "shared")
    b.c.post("/api/auth/email-link", json={"email": "stranger@x.io"})
    b.c.post("/api/auth/email-link", json={"email": OWNER})
    b.ask("nobody@x.io")
    b.ask()
    assert b.fake.codes() == []
    # per address across IPs
    s = _store(tmp_path / "store")
    sent = [s.request_email_code(OWNER, PW, ip=f"10.0.0.{i}", enabled=True).code for i in range(4)]
    assert [c is not None for c in sent] == [True, True, True, False]


def test_email_code_replay_refused(api):
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    assert api.verify(ch, code).status_code == 200
    replay = api.verify(ch, code)
    decoy = api.verify("x" * 43, code)
    assert replay.status_code == decoy.status_code == 403 and replay.json() == decoy.json()


def test_email_code_wrong_code_counts_toward_lockout(api, lockout_clock):
    for _ in range(2):
        ch = api.ask().json()["challenge"]
        code = api.fake.codes()[-1]
        for _ in range(EMAIL_CODE_TRIES):
            if sum(e == "email_code_failed" for e in api.events()) < LOCKOUT_FAILURES:
                assert api.verify(ch, _wrong(code)).status_code == 403
    r = api.c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(api.secret)})
    assert r.status_code == 403 and "too many attempts" in r.json()["detail"]   # shared with password sign-in
    assert api.ask().status_code == 403                                        # and with the code request
    lockout_clock[0] += 15 * 60 * S + 1
    assert api.login()


def test_email_code_wrong_password_sends_nothing_same_answer(api):
    right = api.ask()
    wrong = api.ask(password="not the password")
    assert api.fake.codes() and len(api.fake.codes()) == 1
    assert right.status_code == wrong.status_code == 200
    rj, wj = right.json(), wrong.json()
    assert set(rj) == set(wj) and len(rj["challenge"]) == len(wj["challenge"])
    assert (rj["expires_in_s"], rj["resend_after_s"]) == (wj["expires_in_s"], wj["resend_after_s"])
    code = api.fake.codes()[0]
    decoy = api.verify(wj["challenge"], code)
    bad = api.verify(rj["challenge"], _wrong(code))
    assert decoy.status_code == bad.status_code == 403 and decoy.json() == bad.json()
    with api.st.auth.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key = ?", (f"email:{OWNER}",)).fetchone()[0] >= 1


def test_email_code_owner_only(api):
    for email in ("v@x.io", "a@x.io", "nobody@x.io"):
        assert api.ask(email).status_code == 200
    assert api.fake.sent == []
    assert "email_code_refused" in api.events()


def test_email_code_resend_invalidates_previous(api, lockout_clock):
    first = api.ask().json()["challenge"]
    early = api.resend(first).json()                       # before 60 s: nothing sent, the same challenge back
    assert early["challenge"] == first and 0 < early["resend_after_s"] <= RESEND_AFTER_S
    assert len(api.fake.codes()) == 1
    lockout_clock[0] += RESEND_AFTER_S * S
    second = api.resend(first).json()
    assert second["challenge"] != first and second["resend_after_s"] == RESEND_AFTER_S
    old, new = api.fake.codes()
    assert api.verify(first, old).status_code == 403          # only the newest code works
    assert api.verify(second["challenge"], new).status_code == 200


def test_email_code_burned_after_3_wrong(api, lockout_clock):
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    for _ in range(EMAIL_CODE_TRIES):                          # the 3rd wrong code burns the challenge
        assert api.verify(ch, _wrong(code)).status_code == 403
    assert api.verify(ch, code).status_code == 403
    assert "email_code_burned" in api.events()
    lockout_clock[0] += RESEND_AFTER_S * S
    new = api.resend(ch).json()["challenge"]                    # "Send a new one": Resend still works
    assert api.verify(new, api.fake.codes()[-1]).status_code == 200


def test_email_code_off_when_mail_not_ready(tmp_path):
    for i, (mail, fake) in enumerate(((MailSettings(), FakeMailer()), (MAIL, FakeMailer(ready=False)))):
        a = Api(tmp_path / str(i), mail=mail, mailer=fake)
        assert a.c.get("/api/auth/state").json()["email_code"] is False
        r = a.ask()
        assert r.status_code == 200 and set(r.json()) == {"challenge", "expires_in_s", "resend_after_s"}
        assert fake.sent == []
        assert "email_code_unavailable" in a.events()


def test_email_code_session_needs_totp_for_owner_admin_actions(api):
    h = api.code_session()
    assert api.c.get("/api/me", headers=h).json()["signed_in_with"] == "email_code"
    r = api.c.post("/api/auth/invite", json={"email": "n@x.io", "role": "viewer"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == STEPUP
    r = api.c.post("/api/auth/invite", json={"email": "n@x.io", "role": "viewer", "totp": "000000"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == STEPUP
    r = api.c.post("/api/auth/invite", json={"email": "n@x.io", "role": "viewer", "totp": totp_code(api.secret)},
                   headers=h)
    assert r.status_code == 200, r.text
    for path, body in (("/api/users/role", {"email": "v@x.io", "role": "approver"}),
                       ("/api/users/disable", {"email": "v@x.io"}),
                       ("/api/users/revoke-sessions", {"email": "a@x.io"})):
        assert api.c.post(path, json=body, headers=h).json()["detail"] == STEPUP
        assert api.c.post(path, json={**body, "totp": totp_code(api.secret)}, headers=h).status_code == 200
    # a password + authenticator session needs no step-up; approving and halting never do (one tap)
    assert api.c.post("/api/users/enable", json={"email": "v@x.io"}, headers=api.owner_h).status_code == 200
    assert api.c.post("/api/halt", json={"reason": "test"}, headers=h).status_code == 200
    assert "stepup" in api.events() and "stepup_failed" in api.events()


# ----------------------------------------------------------------------------- the security review's extra tests
def test_email_code_stepup_on_enable_and_reset_link(api):
    h = api.code_session()
    api.c.post("/api/users/disable", json={"email": "v@x.io"}, headers=api.owner_h)
    assert api.c.post("/api/users/enable", json={"email": "v@x.io"}, headers=h).json()["detail"] == STEPUP
    assert api.c.post("/api/auth/reset-link", json={"email": "v@x.io"}, headers=h).json()["detail"] == STEPUP
    assert api.c.post("/api/users/enable", json={"email": "v@x.io", "totp": totp_code(api.secret)},
                      headers=h).status_code == 200
    left = api.st.auth.recovery_codes_left(OWNER)
    r = api.c.post("/api/auth/reset-link", json={"email": "v@x.io", "totp": api.recovery[0]}, headers=h)
    assert r.status_code == 200, r.text                                   # a recovery code also steps up, once
    assert api.st.auth.recovery_codes_left(OWNER) == left - 1
    assert api.c.post("/api/auth/reset-link", json={"email": "v@x.io", "totp": api.recovery[0]},
                      headers=h).json()["detail"] == STEPUP


def test_email_code_suspended_after_burns(api, lockout_clock):
    for _ in range(3):
        ch = api.ask().json()["challenge"]
        code = api.fake.codes()[-1]
        for _ in range(EMAIL_CODE_TRIES):
            api.verify(ch, _wrong(code))
        lockout_clock[0] += 61 * 60 * S                     # a new hour: past the send limit and the lockout window
    n = len(api.fake.codes())
    assert api.ask().status_code == 200
    assert len(api.fake.codes()) == n                       # suspended: the generic answer, nothing sent
    assert "email_code_suspended" in api.events()
    notices = [json.loads(line) for line in (api.st.dir / "agent_runs.jsonl").read_text().splitlines()]
    assert sum("Email codes are suspended" in r["detail"] for r in notices) == 1
    lockout_clock[0] += 61 * 60 * S
    api.ask()
    notices = (api.st.dir / "agent_runs.jsonl").read_text()
    assert notices.count("Email codes are suspended") == 1  # one notice, not one per request
    api.login()                                              # the next password + authenticator sign-in resumes them
    assert "email_code_resumed" in api.events()
    lockout_clock[0] += 61 * 60 * S
    api.ask()
    assert len(api.fake.codes()) == n + 1


def test_email_code_suspended_after_a_lockout(api, lockout_clock):
    for _ in range(LOCKOUT_FAILURES + 1):
        api.c.post("/api/auth/login", json={"email": OWNER, "password": "wrong password!", "totp": "000000"})
    lockout_clock[0] += 16 * 60 * S
    api.ask()
    assert api.fake.codes() == [] and "email_code_suspended" in api.events()


def test_email_code_decoy_timing(tmp_path, lockout_clock):
    """Condition 6: scrypt runs on every request, so a wrong password or an unknown address takes as long as a real
    send. Medians of interleaved runs; 0.5-2.0x absorbs a busy CI worker, while a skipped scrypt shows 10x or more."""
    s = _store(tmp_path)
    s.request_email_code(OWNER, PW, ip="10.9.9.9", enabled=True)          # warm-up: the key is created lazily
    times: dict[str, list[float]] = {"real": [], "wrong_password": [], "unknown": []}
    inputs = {"real": (OWNER, PW), "wrong_password": (OWNER, "not the password!"), "unknown": ("n@x.io", PW)}
    for i in range(9):
        for kind, (email, pw) in inputs.items():
            lockout_clock[0] += 3601 * S                              # a fresh hour and lockout window each time
            t0 = time.perf_counter()
            issue = s.request_email_code(email, pw, ip=f"10.0.{i}.{len(kind)}", enabled=True)
            times[kind].append(time.perf_counter() - t0)
            assert (issue.code is not None) == (kind == "real")
    real = statistics.median(times["real"])
    for kind in ("wrong_password", "unknown"):
        assert 0.5 < statistics.median(times[kind]) / real < 2.0, (kind, times)


# ----------------------------------------------------------------------------- boundaries and lifecycle
def test_a_used_code_stays_used_after_an_auth_store_restart(tmp_path):
    s = _store(tmp_path)
    issue = s.request_email_code(OWNER, PW, ip="10.0.0.1", enabled=True)
    assert issue.code is not None
    assert s.verify_email_code(issue.challenge, issue.code, ip="10.0.0.1")
    again = AuthStore(tmp_path, owner_email=OWNER)
    with pytest.raises(PermissionError):
        again.verify_email_code(issue.challenge, issue.code, ip="10.0.0.1")


def test_email_codes_are_off_when_the_keyring_is_unreadable_or_the_key_corrupt(tmp_path, monkeypatch,
                                                                            email_code_keyring):
    email_code_keyring[EMAIL_CODE_KEY] = "not-hex"
    a = Api(tmp_path / "corrupt")
    assert a.c.get("/api/auth/state").json()["email_code"] is False
    a.ask()
    assert a.fake.sent == [] and email_code_keyring[EMAIL_CODE_KEY] == "not-hex"     # never overwritten

    def broken(key: str) -> str | None:
        raise RuntimeError("keyring locked")
    monkeypatch.setattr(auth_mod, "_secret_get", broken)
    b = Api(tmp_path / "unreadable")
    assert b.c.get("/api/auth/state").json()["email_code"] is False
    b.ask()
    assert b.fake.sent == []


def test_a_missing_key_is_created_once_as_32_random_bytes(api, email_code_keyring):
    assert EMAIL_CODE_KEY not in email_code_keyring
    assert api.c.get("/api/auth/state").json()["email_code"] is True
    key = email_code_keyring[EMAIL_CODE_KEY]
    assert len(bytes.fromhex(key)) == 32
    api.ask()
    assert email_code_keyring[EMAIL_CODE_KEY] == key


def test_an_unwritable_keyring_leaves_email_codes_off(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_mod, "_secret_set", lambda k, v: None)       # accepted, never stored
    a = Api(tmp_path)
    assert a.c.get("/api/auth/state").json()["email_code"] is False


def test_replay_after_sign_out_fails(api):
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    h = {"Authorization": f"Bearer {api.verify(ch, code).json()['token']}"}
    assert api.c.post("/api/auth/logout", headers=h).status_code == 200
    assert api.verify(ch, code).status_code == 403
    assert api.c.get("/api/me", headers=h).status_code == 401


@pytest.mark.parametrize("how", ["logout", "password_change", "revoke_sessions", "recovery_login", "forgot"])
def test_sign_out_password_change_revoke_and_recovery_delete_open_codes(api, how, lockout_clock):
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    if how == "logout":
        api.c.post("/api/auth/logout", headers=api.owner_h)
    elif how == "password_change":
        api.c.post("/api/auth/password/change", headers=api.owner_h,
                   json={"current_password": PW, "totp": totp_code(api.secret), "new_password": PW + " two"})
    elif how == "revoke_sessions":
        api.st.auth.revoke_sessions(OWNER, OWNER)
    elif how == "recovery_login":
        api.c.post("/api/auth/recovery/login", json={"email": OWNER, "password": PW,
                                                    "recovery_code": api.recovery[0]})
    else:
        r = api.c.post("/api/auth/password/forgot", json={"email": OWNER, "totp": totp_code(api.secret),
                                                         "new_password": PW + " two",
                                                         "recovery_code": api.recovery[1]})
        assert r.status_code == 200, r.text
    with api.st.auth.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_codes").fetchone()[0] == 0
    assert api.verify(ch, code).status_code == 403


def test_the_server_revoke_command_deletes_open_codes(api):
    api.ask()
    assert auth_mod.revoke_sessions_cli(["--state", str(api.st.dir)]) == 0
    with api.st.auth.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_codes").fetchone()[0] == 0


def test_every_email_code_sign_in_sends_a_telegram_notice_and_a_mail(api):
    api.code_session()
    runs = (api.st.dir / "agent_runs.jsonl").read_text()
    assert "emailed code" in runs and "revoke-sessions" in runs
    assert any(s == mailer_mod.SIGN_IN_NOTICE_SUBJECT_CODE for _, s, _ in api.fake.sent)
    assert "email_code_login" in api.events() and "email_code_sent" in api.events()


def test_the_email_has_the_code_alone_on_line_one_and_never_in_the_subject(api):
    api.ask()
    _, subject, body = api.fake.sent[0]
    code = _code(body)
    assert code not in subject and subject == "Your goldbot sign-in code"
    assert "UTC" in body and "revoke-sessions" in body and "http" not in body
    assert "never asks for this code" in body


def test_email_never_resets_the_password(api):
    """Condition 11: the code signs in; it is never accepted in place of the authenticator for a password change."""
    h = api.code_session()
    code_ch = api.ask().json()["challenge"]
    code = api.fake.codes()[-1]
    r = api.c.post("/api/auth/password/change", headers=h,
                   json={"current_password": PW, "totp": code, "new_password": PW + " two"})
    assert r.status_code == 403
    assert api.verify(code_ch, code).status_code == 200                # the emailed code was not spent there


def test_login_reports_the_sign_in_method(api):
    r = api.c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(api.secret)})
    assert r.json()["signed_in_with"] == "password_totp"
    assert api.c.get("/api/me", headers=api.owner_h).json()["signed_in_with"] == "password_totp"
    with api.st.auth.db.connection() as conn:
        assert {m for (m,) in conn.execute("SELECT method FROM sessions")} == {"password_totp"}


# ----------------------------------------------------------------------------- every owner admin route is gated
STEPUP_ROUTES = {"/api/auth/invite", "/api/auth/reset-link", "/api/users/role", "/api/users/disable",
                 "/api/users/enable", "/api/users/revoke-sessions"}
TOTP_GATED = {"/api/rearm", "/api/automode", "/api/auth/password/change", "/api/auth/recovery/codes"}


def _write_routes() -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(d, web_dist=f"{d}/nodist", owner_email=OWNER, mail=MAIL, mailer=FakeMailer())
        return sorted({r.path for r in app.routes if isinstance(r, APIRoute) and (r.methods or set()) - {"GET", "HEAD"}})


@pytest.mark.parametrize("path", _write_routes())
def test_every_owner_route_is_stepped_up_or_already_needs_an_authenticator_code(api, path):
    """A new owner-only write route fails here until it is put on STEPUP_ROUTES (and calls `stepup`) or is shown to
    ask for an authenticator code itself."""
    r = api.c.post(path, json={}, headers=api.login("v@x.io"))
    owner_only = r.status_code == 403 and r.json().get("detail") == "owner role required"
    if owner_only:
        assert path in STEPUP_ROUTES | TOTP_GATED, \
            f"{path}: owner-only routes must be stepped up (STEPUP_ROUTES) or ask for an authenticator code (TOTP_GATED)"
    assert owner_only or path not in STEPUP_ROUTES | {"/api/rearm", "/api/automode", "/api/auth/recovery/codes"}
    if path in STEPUP_ROUTES:
        body = {"email": "v@x.io", **({"role": "viewer"} if path in ("/api/auth/invite", "/api/users/role") else {})}
        r = api.c.post(path, json=body, headers=api.code_session())
        assert r.status_code == 403 and r.json()["detail"] == STEPUP, (path, r.text)
    elif path in TOTP_GATED:                    # an email-code session without an authenticator code gets nothing
        body = {"/api/rearm": {"totp": ""}, "/api/automode": {"mode": "auto", "totp": ""},
                "/api/auth/password/change": {"current_password": PW, "totp": "", "new_password": PW + " two"},
                "/api/auth/recovery/codes": {"password": PW, "totp": ""}}[path]
        r = api.c.post(path, json=body, headers=api.code_session())
        assert r.status_code == 403 or (path == "/api/automode" and r.json()["ok"] is False), (path, r.text)


# ----------------------------------------------------------------------------- security review round 1 (68a8cfc..81e730b)
def test_resend_from_another_ip_answers_a_real_challenge_exactly_as_a_decoy(tmp_path, lockout_clock):
    """Security 1: the visible resend reply depends only on the client IP and the challenge. A real challenge whose
    address is paced or over its hourly limit sends nothing and answers as a decoy (new challenge, standard wait), so
    a resend from a fresh IP cannot tell a right password from a wrong one without the mailbox."""
    s = _store(tmp_path)
    real = s.request_email_code(OWNER, PW, ip="10.0.0.1", enabled=True)
    decoy = s.request_email_code(OWNER, "not the password!", ip="10.0.0.2", enabled=True)
    assert real.code is not None and decoy.code is None
    lockout_clock[0] += 5 * S                                   # inside the address's 60 s pacing
    r_real = s.resend_email_code(real.challenge, ip="10.0.0.3", enabled=True)
    r_decoy = s.resend_email_code(decoy.challenge, ip="10.0.0.4", enabled=True)
    assert r_real.code is None                                  # paced: nothing sent, silently
    assert r_real.challenge != real.challenge and r_decoy.challenge != decoy.challenge
    assert r_real.resend_after_s == r_decoy.resend_after_s == RESEND_AFTER_S
    assert len(r_real.challenge) == len(r_decoy.challenge)
    assert "email_code_rate_limited" in [e["event"] for e in s.audit_events()]


def test_resend_replies_are_identical_over_http_for_a_real_and_a_decoy_challenge(api, monkeypatch):
    real = api.ask().json()["challenge"]
    decoy = api.ask(password="not the password").json()["challenge"]
    ips = iter(["198.51.100.7", "198.51.100.8"])                  # two other networks, one per resend
    monkeypatch.setattr("goldbot.api.app.client_ip", lambda request: next(ips))
    a, b = api.resend(real), api.resend(decoy)
    assert a.status_code == b.status_code == 200
    aj, bj = a.json(), b.json()
    assert set(aj) == set(bj) and aj["challenge"] != real and bj["challenge"] != decoy
    assert (aj["expires_in_s"], aj["resend_after_s"]) == (bj["expires_in_s"], bj["resend_after_s"])


def test_new_session_needs_its_sign_in_method_named():
    """Security 2: every caller names how the session signed in; only the column default (rollback) is implicit."""
    import inspect
    p = inspect.signature(AuthStore._new_session).parameters["method"]
    assert p.default is inspect.Parameter.empty


def test_the_key_cached_is_the_one_read_back_after_writing(monkeypatch, email_code_keyring):
    """Security 3: another writer won the race between our write and our read: the key read back is the one used."""
    other = "ab" * 32

    def racing_set(key: str, value: str) -> None:
        email_code_keyring[key] = other                         # someone else's write lands after ours
    monkeypatch.setattr(auth_mod, "_secret_set", racing_set)
    assert auth_mod.email_code_key() == bytes.fromhex(other)
    assert auth_mod._KEY_CACHE[EMAIL_CODE_KEY] == bytes.fromhex(other)


def test_verify_refuses_a_real_and_a_decoy_challenge_identically_while_locked_out(api, lockout_clock, monkeypatch):
    """Security 4: during the sign-in lockout a real challenge refuses with the same message as a decoy. (A lockout
    also suspends email codes and deletes open ones; that is switched off here so the live challenge survives.)"""
    monkeypatch.setattr(AuthStore, "_suspend_email_codes", lambda self, conn, email, reason: False)
    ch = api.ask().json()["challenge"]
    code = api.fake.codes()[0]
    for _ in range(LOCKOUT_FAILURES):
        api.c.post("/api/auth/login", json={"email": OWNER, "password": "wrong password!", "totp": "000000"})
    real, decoy = api.verify(ch, code), api.verify("x" * 43, code)
    assert real.status_code == decoy.status_code == 403 and real.json() == decoy.json()


def test_a_failure_anywhere_in_the_code_mail_is_audited_by_type_only(tmp_path, monkeypatch, caplog):
    """Code review: the whole background send, formatting included, is inside the audited try/except."""
    def broken(ts: float, tz_name: str) -> tuple[str, str]:
        raise ValueError("o@x.io 123456")
    monkeypatch.setattr("goldbot.api.app._local_and_utc", broken)
    a = Api(tmp_path)
    assert a.ask().status_code == 200
    assert "email_code_send_failed" in a.events()
    assert "o@x.io 123456" not in caplog.text
