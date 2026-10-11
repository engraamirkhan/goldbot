"""The owner-only email sign-in link (goldbot/api/auth.py `request_email_link` / `redeem_email_link`, the
/api/auth/email-link routes) and the SMTP sender behind it (goldbot/api/mailer.py).

Design rule: the link replaces the password, never the authenticator code (this account approves real-money entries);
the request answers the same for every address, so nothing can be enumerated."""
import json
import logging
import re
import smtplib
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from goldbot.api import mailer as mailer_mod
from goldbot.api.app import create_app
from goldbot.api.auth import _EMAIL_LINKS, _USERS, EMAIL_LINK_PER_HOUR, LOCKOUT_FAILURES, AuthStore, totp_code
from goldbot.config import MailSettings, load_settings

PW = "a long password here"
OWNER = "o@x.io"
MAIL = MailSettings(enabled=True, host="smtp.test", port=587, from_address="bot@x.io",
                    dashboard_origin="https://dash.test")
GENERIC = "If this address can use email sign-in, a link is on its way"


class FakeMailer:
    """Records what would be sent; `fail` raises an SMTP error that carries the address and the body (as smtplib's
    errors can), so the test proves neither reaches the logs."""

    def __init__(self, ready: bool = True, fail: bool = False):
        self._ready, self.fail = ready, fail
        self.sent: list[tuple[str, str, str]] = []

    def ready(self) -> bool:
        return self._ready

    def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({to: (550, body.encode())})
        self.sent.append((to, subject, body))


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


def _token(body: str) -> str:
    m = re.search(r"#signin=([A-Za-z0-9_\-]+)", body)
    assert m, body
    return m.group(1)


def _app(tmp_path, mail: MailSettings = MAIL, mailer: FakeMailer | None = None):
    fake = mailer if mailer is not None else FakeMailer()
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email=OWNER, mail=mail, mailer=fake)
    c = TestClient(app)
    st = app.state.st
    r = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": OWNER, "password": PW}).json()
    owner_secret = _secret(r["totp_uri"])
    owner = c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(owner_secret)}).json()
    headers = {"Authorization": f"Bearer {owner['token']}"}
    tok = c.post("/api/auth/invite", json={"email": "v@x.io", "role": "viewer"}, headers=headers).json()["invite_token"]
    c.post("/api/auth/accept", json={"token": tok, "password": "viewer password 123"})
    return c, st, fake, owner_secret


@pytest.fixture
def api(tmp_path):
    return _app(tmp_path)


def _ask(c: TestClient, email: str):
    return c.post("/api/auth/email-link", json={"email": email})


def _redeem(c: TestClient, token: str, totp: str):
    return c.post("/api/auth/email-link/redeem", json={"token": token, "totp": totp})


# ----------------------------------------------------------------------------- request: generic, owner only
def test_the_request_answers_the_same_for_unknown_non_owner_and_owner_addresses(api):
    c, _, fake, _ = api
    answers = [_ask(c, e) for e in ("nobody@x.io", "v@x.io", OWNER)]
    assert {r.status_code for r in answers} == {200}
    assert {r.text for r in answers} == {json.dumps({"ok": True, "detail": GENERIC}, separators=(",", ":"))}
    assert len(fake.sent) == 1


def test_only_the_owner_address_gets_mail_matched_case_insensitively_and_trimmed(api):
    c, _, fake, _ = api
    _ask(c, "nobody@x.io")
    _ask(c, "v@x.io")
    assert fake.sent == []
    _ask(c, "  O@X.io ")
    assert [to for to, _, _ in fake.sent] == [OWNER]


def test_the_email_has_the_dashboard_link_in_the_fragment_the_expiry_and_ignore_note(api):
    c, _, fake, _ = api
    _ask(c, OWNER)
    _, subject, body = fake.sent[0]
    token = _token(body)
    assert f"https://dash.test/#signin={token}" in body
    assert "?" not in body.split("#signin=")[0].split("https://dash.test")[1]     # never in the query string
    assert "15 minutes" in body and "ignore" in body.lower() and "authenticator" in body.lower()
    assert token not in subject


def test_the_token_is_random_at_least_32_bytes_and_stored_only_as_a_hash(api):
    c, st, fake, _ = api
    _ask(c, OWNER)
    _ask(c, OWNER)
    t1, t2 = (_token(b) for _, _, b in fake.sent)
    assert t1 != t2 and len(t1) >= 43                         # token_urlsafe(32): 43 characters
    raw = st.auth.path.read_bytes()
    assert t1.encode() not in raw and t2.encode() not in raw


def test_a_disabled_owner_gets_no_link(api):
    c, st, fake, _ = api
    with st.auth.db.write() as conn:
        u = _USERS.get(conn, OWNER)
        assert u is not None
        u.enabled = False
        _USERS.upsert(conn, u)
    assert _ask(c, OWNER).json()["detail"] == GENERIC
    assert fake.sent == []


def test_mail_not_configured_answers_the_same_and_sends_nothing(tmp_path):
    off = MailSettings()                                      # the default: disabled
    for i, (mail, fake) in enumerate(((off, FakeMailer()), (MAIL, FakeMailer(ready=False)))):
        c, st, _, _ = _app(tmp_path / str(i), mail=mail, mailer=fake)
        r = _ask(c, OWNER)
        assert r.status_code == 200 and r.json()["detail"] == GENERIC
        assert fake.sent == []
        assert c.get("/api/auth/state").json()["email_link"] is False
        with st.auth.db.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM email_links").fetchone()[0] == 0


def test_auth_state_says_whether_email_sign_in_is_offered(api):
    c, _, _, _ = api
    assert c.get("/api/auth/state").json()["email_link"] is True


# ----------------------------------------------------------------------------- redeem: still needs the authenticator
def test_the_link_with_the_authenticator_code_signs_the_owner_in_once(api):
    c, st, fake, secret = api
    _ask(c, OWNER)
    token = _token(fake.sent[0][2])
    r = _redeem(c, token, totp_code(secret))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == OWNER and body["role"] == "owner"
    assert c.get("/api/me", headers={"Authorization": f"Bearer {body['token']}"}).json()["email"] == OWNER
    assert _redeem(c, token, totp_code(secret)).status_code == 403          # single use


def test_an_expired_link_fails(api):
    c, st, fake, secret = api
    _ask(c, OWNER)
    with st.auth.db.write() as conn:
        (link,) = _EMAIL_LINKS.select(conn)
        link.expires = time.time() - 1
        _EMAIL_LINKS.upsert(conn, link)
    assert _redeem(c, _token(fake.sent[0][2]), totp_code(secret)).status_code == 403


def test_a_link_expires_after_fifteen_minutes(api):
    c, st, _, _ = api
    _ask(c, OWNER)
    with st.auth.db.connection() as conn:
        (expires_ns,) = conn.execute("SELECT expires_ns FROM email_links").fetchone()
    assert abs(expires_ns / 1e9 - (time.time() + 900)) < 30


def test_redeem_without_an_authenticator_code_fails(api):
    c, _, fake, _ = api
    _ask(c, OWNER)
    assert _redeem(c, _token(fake.sent[0][2]), "").status_code == 403
    assert c.post("/api/auth/email-link/redeem", json={"token": _token(fake.sent[0][2])}).status_code == 403


def test_a_wrong_authenticator_code_fails_and_burns_the_link(api):
    c, _, fake, secret = api
    _ask(c, OWNER)
    token = _token(fake.sent[0][2])
    assert _redeem(c, token, "000000").status_code == 403
    assert _redeem(c, token, totp_code(secret)).status_code == 403          # a stolen link gets one guess


def test_redeeming_revokes_every_other_outstanding_link(api):
    c, _, fake, secret = api
    _ask(c, OWNER)
    _ask(c, OWNER)
    first, second = (_token(b) for _, _, b in fake.sent)
    assert _redeem(c, second, totp_code(secret)).status_code == 200
    assert _redeem(c, first, totp_code(secret)).status_code == 403


def test_a_password_change_revokes_outstanding_links(api):
    c, _, fake, secret = api
    _ask(c, OWNER)
    token = _token(fake.sent[0][2])
    tok = c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(secret)}).json()["token"]
    r = c.post("/api/auth/password/change", headers={"Authorization": f"Bearer {tok}"},
               json={"current_password": PW, "totp": totp_code(secret), "new_password": "another long password"})
    assert r.status_code == 200, r.text
    assert _redeem(c, token, totp_code(secret)).status_code == 403


def test_an_unknown_token_fails_with_the_same_message(api):
    c, _, fake, secret = api
    _ask(c, OWNER)
    wrong = _redeem(c, "x" * 43, totp_code(secret))
    bad_code = _redeem(c, _token(fake.sent[0][2]), "000000")
    assert wrong.status_code == bad_code.status_code == 403
    assert wrong.json() == bad_code.json()


def test_a_wrong_code_counts_towards_the_sign_in_lockout_and_kills_every_outstanding_link(api):
    c, st, fake, _ = api
    for _ in range(EMAIL_LINK_PER_HOUR):
        _ask(c, OWNER)
    for _, _, body in fake.sent:
        assert _redeem(c, _token(body), "000000").status_code == 403

    def failures(key: str) -> int:
        with st.auth.db.connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key = ?", (key,)).fetchone()[0])
    assert failures(f"email:{OWNER}") == 1         # the wrong code on a live link, like a failed sign-in
    assert failures("ip:testclient") == EMAIL_LINK_PER_HOUR      # the other two links were already dead: IP only
    with st.auth.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_links").fetchone()[0] == 0


def test_garbage_tokens_never_count_against_the_owner_account(api):
    c, st, _, secret = api
    for i in range(LOCKOUT_FAILURES + 1):
        assert _redeem(c, f"garbage-{i}", "000000").status_code == 403
    with st.auth.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key LIKE 'email:%'").fetchone()[0] == 0
    r = c.post("/api/auth/login", json={"email": OWNER, "password": PW, "totp": totp_code(secret)})
    assert r.status_code == 200                       # password sign-in still works


# ----------------------------------------------------------------------------- rate limit
def test_at_most_three_links_an_hour_per_address(api):
    c, _, fake, _ = api
    answers = [_ask(c, OWNER) for _ in range(EMAIL_LINK_PER_HOUR + 2)]
    assert {r.json()["detail"] for r in answers} == {GENERIC}
    assert len(fake.sent) == EMAIL_LINK_PER_HOUR == 3


def test_at_most_three_requests_an_hour_per_ip_whatever_the_address(api):
    c, _, fake, _ = api
    for i in range(EMAIL_LINK_PER_HOUR):
        _ask(c, f"stranger{i}@x.io")
    assert _ask(c, OWNER).json()["detail"] == GENERIC
    assert fake.sent == []


def test_the_address_limit_holds_across_ips(tmp_path):
    s = AuthStore(tmp_path, owner_email=OWNER)
    s.bootstrap_owner(s.setup_code or "", OWNER, PW)
    sent = [s.request_email_link(OWNER, ip=f"10.0.0.{i}", enabled=True) for i in range(EMAIL_LINK_PER_HOUR + 1)]
    assert [t is not None for t in sent] == [True, True, True, False]
    assert "email_link_rate_limited" in [e["event"] for e in s.audit_events()]


# ----------------------------------------------------------------------------- nothing leaks
def test_every_send_and_redeem_is_audited_without_the_token(api):
    c, st, fake, secret = api
    _ask(c, OWNER)
    _ask(c, "v@x.io")
    tokens = [_token(b) for _, _, b in fake.sent]
    _redeem(c, "y" * 43, "000000")
    _redeem(c, tokens[0], totp_code(secret))
    events = st.auth.audit_events()
    names = [e["event"] for e in events]
    for name in ("email_link_sent", "email_link_refused", "email_link_redeem_failed", "email_link_login"):
        assert name in names, name
    dump = json.dumps(events)
    assert tokens[0] not in dump and "y" * 43 not in dump


def test_no_token_or_address_reaches_the_logs_even_when_sending_fails(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    c, st, fake, secret = _app(tmp_path / "ok")
    _ask(c, OWNER)
    token = _token(fake.sent[0][2])
    _redeem(c, token, "000000")
    c2, st2, failing, _ = _app(tmp_path / "fail", mailer=FakeMailer(fail=True))
    r = _ask(c2, OWNER)
    assert r.status_code == 200 and r.json()["detail"] == GENERIC
    assert "email_link_send_failed" in [e["event"] for e in st2.auth.audit_events()]
    assert "not sent" in caplog.text                       # the failure is logged ...
    assert OWNER not in caplog.text and token not in caplog.text and "signin=" not in caplog.text   # ... bare


def test_validation_errors_never_echo_the_address_or_token(api):
    c, _, _, _ = api
    for path, body in (("/api/auth/email-link", {"email": "o@x.io", "extra": "leak-me-1"}),
                       ("/api/auth/email-link/redeem", {"token": "leak-me-2", "totp": "123456", "x": "leak-me-3"}),
                       ("/api/auth/email-link", {"email": ["o@x.io"]})):
        r = c.post(path, json=body)
        assert r.status_code == 422
        assert "o@x.io" not in r.text and "leak-me" not in r.text


# ----------------------------------------------------------------------------- settings and the SMTP sender
def test_mail_is_off_by_default_and_the_repo_holds_no_mail_address():
    assert MailSettings().enabled is False
    m = load_settings().auth.mail
    assert m.from_address is None or m.from_address.endswith("example.com")
    assert m.username is None or m.username.endswith("example.com")


def test_enabled_mail_needs_a_host_a_from_address_and_an_https_dashboard_origin():
    with pytest.raises(ValidationError):
        MailSettings(enabled=True, from_address="bot@x.io", dashboard_origin="https://dash.test")
    with pytest.raises(ValidationError):
        MailSettings(enabled=True, host="smtp.test", dashboard_origin="https://dash.test")
    with pytest.raises(ValidationError):
        MailSettings(enabled=True, host="smtp.test", from_address="bot@x.io", dashboard_origin="http://dash.test")
    ok = MailSettings(enabled=True, host="smtp.test", from_address="bot@x.io", dashboard_origin="https://dash.test/")
    assert ok.dashboard_origin == "https://dash.test"      # one trailing slash dropped: the link adds its own


class _FakeSmtp:
    calls: list[tuple] = []

    def __init__(self, host, port, timeout):
        _FakeSmtp.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        _FakeSmtp.calls.append(("starttls", context is not None))

    def login(self, user, password):
        _FakeSmtp.calls.append(("login", user, password))

    def send_message(self, msg):
        _FakeSmtp.calls.append(("send", msg["To"], msg["From"], msg.get_content()))


def test_the_smtp_sender_uses_starttls_a_timeout_and_the_keyring_password(monkeypatch):
    _FakeSmtp.calls = []
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", _FakeSmtp)
    store = {"smtp-password": "app-password-1"}
    m = mailer_mod.SmtpMailer(MAIL, secret=store.get)
    assert m.ready()
    m.send(OWNER, "subject", "body text")
    assert _FakeSmtp.calls[0] == ("connect", "smtp.test", 587, MAIL.timeout_s)
    assert _FakeSmtp.calls[1] == ("starttls", True)
    assert _FakeSmtp.calls[2] == ("login", "bot@x.io", "app-password-1")    # username defaults to from_address
    assert _FakeSmtp.calls[3][:3] == ("send", OWNER, "bot@x.io")


def test_the_smtp_sender_is_not_ready_without_a_password_in_the_keyring():
    m = mailer_mod.SmtpMailer(MAIL, secret=lambda key: None)
    assert not m.ready()
    with pytest.raises(mailer_mod.MailNotConfigured):
        m.send(OWNER, "s", "b")


def test_the_smtp_password_key_is_named_and_not_in_settings():
    assert mailer_mod.SMTP_PASSWORD_KEY == "smtp-password"
    assert "password" not in MailSettings.model_fields


def test_the_mail_test_command_refuses_until_mail_is_configured(monkeypatch, capsys):
    from goldbot.config import AuthSettings

    class _Settings:
        auth = AuthSettings(owner_email=OWNER)            # mail off, the default

    monkeypatch.setattr("goldbot.config.load_settings", lambda: _Settings)
    assert mailer_mod.main(["test"]) == 2
    assert "smtp-password" in capsys.readouterr().out


def test_the_mail_test_command_prints_only_the_error_type(monkeypatch, capsys):
    from goldbot.config import AuthSettings

    class _Settings:
        auth = AuthSettings(owner_email=OWNER, mail=MAIL)

    class _Failing(mailer_mod.SmtpMailer):
        def ready(self) -> bool:
            return True

        def send(self, to: str, subject: str, body: str) -> None:
            raise smtplib.SMTPRecipientsRefused({to: (550, b"no")})

    monkeypatch.setattr("goldbot.config.load_settings", lambda: _Settings)
    monkeypatch.setattr(mailer_mod, "mailer_from_settings", lambda s: _Failing(s))
    assert mailer_mod.main(["test"]) == 1
    out = capsys.readouterr().out
    assert "SMTPRecipientsRefused" in out and OWNER not in out
