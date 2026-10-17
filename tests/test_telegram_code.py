"""The owner's sign-in code delivered through the existing Telegram bot (BACKLOG 77 amendment, ADR 0028 amended
2026-10-17; goldbot/api/code_delivery.py `TelegramCodeDelivery`, the /api/auth/email-code routes).

Design rule (security conditions 1-7): the API sends the code itself with Bot API `sendMessage` over HTTPS to ONE
private chat whose id is in the keyring, is positive and is in `telegram.allowed_user_ids`; any failure sends nothing
and is audited `code_channel_unavailable`; the code is never in agent_runs.jsonl, the outbox, aux.db, a log or a file;
the API never calls getUpdates; only the message id is stored, and the message is deleted on use, burn or expiry."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import EMAIL_CODE_TRIES, totp_code
from goldbot.api.code_delivery import (
    BOT_TOKEN_KEY,
    CHAT_ID_KEY,
    TelegramCodeDelivery,
)
from goldbot.config import AuthSettings, load_settings

PW = "a long password here"
OWNER = "o@x.io"
TOKEN = "123456:SECRET-bot-token"
CHAT = 4242


class FakeBotApi:
    """A Bot API double behind httpx.MockTransport; records every call."""

    def __init__(self, chat_type: str = "private", fail_on: str | None = None):
        self.chat_type, self.fail_on = chat_type, fail_on
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https" and request.url.host == "api.telegram.org"
        method = urlparse(str(request.url)).path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if self.fail_on == method:
            raise httpx.ConnectError(f"boom {request.url}")
        if method == "getChat":
            return httpx.Response(200, json={"ok": True, "result": {"id": body["chat_id"], "type": self.chat_type}})
        if method == "sendMessage":
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def sent(self) -> list[dict]:
        return [b for m, b in self.calls if m == "sendMessage"]

    def codes(self) -> list[str]:
        out = []
        for b in self.sent():
            m = re.match(r"\d{6}", b["text"])
            assert m is not None
            out.append(m.group(0))
        return out


def delivery(bot: FakeBotApi, secrets: dict[str, str] | None = None, allowed: list[int] | None = None):
    store = {CHAT_ID_KEY: str(CHAT), BOT_TOKEN_KEY: TOKEN} if secrets is None else secrets
    return TelegramCodeDelivery(allowed_user_ids=[CHAT] if allowed is None else allowed, secret=store.get,
                                transport=httpx.MockTransport(bot.handler))


class Api:
    def __init__(self, tmp_path, bot: FakeBotApi | None = None, **kw):
        self.bot = bot or FakeBotApi()
        self.d = delivery(self.bot, **kw)
        self.app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email=OWNER, code_delivery=self.d)
        self.c = TestClient(self.app)
        self.st = self.app.state.st
        r = self.c.post("/api/auth/setup", json={"setup_code": self.st.auth.setup_code, "email": OWNER,
                                                 "password": PW}).json()
        self.secret = r["totp_uri"].split("secret=")[1].split("&")[0]

    def ask(self, password: str = PW):
        return self.c.post("/api/auth/email-code", json={"email": OWNER, "password": password})

    def verify(self, challenge: str, code: str):
        return self.c.post("/api/auth/email-code/verify", json={"challenge": challenge, "code": code})

    def resend(self, challenge: str):
        return self.c.post("/api/auth/email-code/resend", json={"challenge": challenge})

    def events(self) -> list[str]:
        return [e["event"] for e in self.st.auth.audit_events()]


@pytest.fixture
def api(tmp_path):
    return Api(tmp_path)


def test_the_default_code_channel_is_telegram():
    assert AuthSettings().code_channel == "telegram" and load_settings().auth.code_channel == "telegram"


def test_the_auth_state_names_the_channel_and_offers_codes_with_a_valid_chat(api):
    s = api.c.get("/api/auth/state").json()
    assert s["code_sign_in"] is True and s["code_channel"] == "telegram" and "email_code" not in s


@pytest.mark.parametrize("chat", ["", "abc", "-4242", "0", "4242.0", " 4242 ", "+4242", "99999"])
def test_a_chat_id_that_is_not_one_positive_allowed_integer_is_not_ready(tmp_path, chat):
    a = Api(tmp_path, secrets={CHAT_ID_KEY: chat, BOT_TOKEN_KEY: TOKEN})
    assert a.d.ready() is False
    assert a.c.get("/api/auth/state").json()["code_sign_in"] is False


def test_no_bot_token_means_not_ready(tmp_path):
    a = Api(tmp_path, secrets={CHAT_ID_KEY: str(CHAT)})
    assert a.d.ready() is False


def test_the_code_goes_by_sendmessage_over_https_to_the_private_chat_with_protect_content_and_no_parse_mode(api):
    assert api.ask().status_code == 200
    assert api.bot.methods() == ["getChat", "sendMessage"]
    body = api.bot.sent()[0]
    assert body["chat_id"] == CHAT and body["protect_content"] is True and "parse_mode" not in body
    assert re.fullmatch(r"\d{6}", body["text"].split("\n", 1)[0])
    assert "Not you? Your password is known" in body["text"] and "authenticator" in body["text"]
    assert "UTC" in body["text"] and "mail" not in body["text"].lower() and PW not in body["text"]


@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
def test_a_chat_that_is_not_private_gets_nothing_and_is_audited(tmp_path, kind):
    a = Api(tmp_path, bot=FakeBotApi(chat_type=kind))
    assert a.ask().status_code == 200
    assert a.bot.methods() == ["getChat"] and "code_channel_unavailable" in a.events()


def test_a_getchat_failure_sends_nothing_and_is_audited_by_type_only(tmp_path, caplog):
    a = Api(tmp_path, bot=FakeBotApi(fail_on="getChat"))
    with caplog.at_level(logging.DEBUG):
        assert a.ask().status_code == 200
    assert a.bot.methods() == ["getChat"] and "code_channel_unavailable" in a.events()
    ev = [e for e in a.st.auth.audit_events() if e["event"] == "code_channel_unavailable"][0]
    assert ev["error"] == "ConnectError" and TOKEN not in json.dumps(ev)


def test_the_api_never_calls_getupdates(api, lockout_clock):
    ch = api.ask().json()["challenge"]
    lockout_clock[0] += 61 * 10**9
    api.resend(ch)
    assert "getUpdates" not in api.bot.methods()


def test_a_wrong_password_and_a_right_one_answer_the_same_but_only_the_right_one_sends(api):
    a, b = api.ask("wrong password!").json(), api.ask().json()
    assert set(a) == set(b) and len(api.bot.sent()) == 1


def test_a_right_code_signs_the_owner_in_once_and_writes_a_tg_code_login_audit_row(api):
    ch = api.ask().json()["challenge"]
    r = api.verify(ch, api.bot.codes()[-1])
    assert r.status_code == 200 and r.json()["signed_in_with"] == "email_code"
    assert "tg_code_login" in api.events() and "email_code_login" not in api.events()
    assert api.verify(ch, api.bot.codes()[-1]).status_code == 403


def test_only_the_message_id_is_stored_and_the_message_is_deleted_on_use(api):
    ch = api.ask().json()["challenge"]
    mid = api.bot.next_id
    with api.st.auth.db.connection() as conn:
        assert conn.execute("SELECT message_id FROM email_codes").fetchone()[0] == str(mid)
    api.verify(ch, api.bot.codes()[-1])
    assert ("deleteMessage", {"chat_id": CHAT, "message_id": mid}) in api.bot.calls


def test_the_message_is_deleted_when_the_third_wrong_code_burns_it(api):
    ch = api.ask().json()["challenge"]
    mid = api.bot.next_id
    wrong = f"{(int(api.bot.codes()[-1]) + 1) % 10**6:06d}"
    for _ in range(EMAIL_CODE_TRIES):
        assert api.verify(ch, wrong).status_code == 403
    assert ("deleteMessage", {"chat_id": CHAT, "message_id": mid}) in api.bot.calls


def test_a_resend_deletes_the_old_message_and_sends_a_new_one(api, lockout_clock):
    ch = api.ask().json()["challenge"]
    old = api.bot.next_id
    lockout_clock[0] += 61 * 10**9
    r = api.resend(ch)
    assert r.status_code == 200 and len(api.bot.sent()) == 2
    assert ("deleteMessage", {"chat_id": CHAT, "message_id": old}) in api.bot.calls


def test_an_expired_message_is_deleted_by_the_expiry_timer(tmp_path, monkeypatch):
    fired: list[tuple[float, object]] = []

    class Timer:
        def __init__(self, interval, fn, args=()):
            fired.append((interval, lambda: fn(*args)))
            self.daemon = False

        def start(self):
            pass
    monkeypatch.setattr("goldbot.api.app.threading.Timer", Timer)
    a = Api(tmp_path)
    a.ask()
    mid = a.bot.next_id
    assert len(fired) == 1 and fired[0][0] > 600
    fired[0][1]()                                          # type: ignore[operator]
    assert ("deleteMessage", {"chat_id": CHAT, "message_id": mid}) in a.bot.calls


def test_the_code_is_in_no_file_under_the_state_dir_and_not_in_agent_runs(api, tmp_path):
    ch = api.ask().json()["challenge"]
    code = api.bot.codes()[-1]
    api.verify(ch, code)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert code.encode() not in p.read_bytes(), p
    assert code not in (api.st.dir / "agent_runs.jsonl").read_text()


def test_neither_the_token_nor_the_code_reaches_the_logs_even_when_the_send_fails(tmp_path, caplog):
    a = Api(tmp_path, bot=FakeBotApi(fail_on="sendMessage"))
    with caplog.at_level(logging.DEBUG):
        a.ask()
    assert "code_channel_unavailable" in a.events()
    assert TOKEN not in caplog.text and "SECRET-bot-token" not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING and logging.getLogger("httpcore").level >= logging.WARNING


def test_the_recent_sign_ins_list_shows_each_telegram_code_sign_in_with_a_coarse_network(api):
    ch = api.ask().json()["challenge"]
    tok = api.verify(ch, api.bot.codes()[-1]).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    rows = api.c.get("/api/auth/code-sign-ins", headers=h).json()["sign_ins"]
    assert len(rows) == 1 and set(rows[0]) == {"at_utc", "network"} and rows[0]["network"] == "an unknown network"


def test_the_recent_sign_ins_list_is_owner_only_and_empty_without_any(api):
    assert api.c.get("/api/auth/code-sign-ins").status_code == 401
    owner = {"Authorization": "Bearer " + api.c.post("/api/auth/login", json={
        "email": OWNER, "password": PW, "totp": totp_code(api.secret)}).json()["token"]}
    assert api.c.get("/api/auth/code-sign-ins", headers=owner).json() == {"sign_ins": [], "channel": "telegram"}


def test_the_telegram_notice_for_a_code_sign_in_does_not_talk_about_a_mailbox(api):
    ch = api.ask().json()["challenge"]
    code = api.bot.codes()[-1]
    api.verify(ch, code)
    runs = (api.st.dir / "agent_runs.jsonl").read_text()
    assert "Telegram code" in runs and "mailbox" not in runs and code not in runs


def test_sends_share_the_hourly_limit(api):
    for _ in range(5):
        api.ask()
    assert len(api.bot.sent()) == 3
