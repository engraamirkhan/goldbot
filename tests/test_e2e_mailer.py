"""The e2e-only fake mailer (goldbot/api/e2e_mailer.py) is refused outside a local test run and cannot be reached from
any deployed configuration: loopback bind AND the explicit flag AND an outbox in the temp dir, and no setting or
deployed module selects it."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from goldbot.api import e2e_mailer
from goldbot.api.e2e_mailer import E2E_FLAG, CaptureMailer, E2EMailerRefused
from goldbot.api.mailer import SmtpMailer, mailer_from_settings
from goldbot.config import MailSettings, Settings

ROOT = Path(__file__).resolve().parents[1]
ON = {E2E_FLAG: "1"}


@pytest.fixture
def outbox() -> Path:
    # an explicit temp dir: pytest's --basetemp may point outside the system temp directory
    return Path(tempfile.mkdtemp(prefix="goldbot-e2e-mailer-")) / "outbox.jsonl"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_captures_mail_on_loopback_with_the_flag(outbox, host):
    m = CaptureMailer(outbox, bind_host=host, environ=ON)
    assert m.ready()
    m.send("owner@example.com", "Your goldbot sign-in link", "http://127.0.0.1:1/#signin=tok\n")
    m.send("owner@example.com", "second", "b")
    rows = [json.loads(line) for line in outbox.read_text().splitlines()]
    assert rows == [{"to": "owner@example.com", "subject": "Your goldbot sign-in link",
                     "body": "http://127.0.0.1:1/#signin=tok\n"},
                    {"to": "owner@example.com", "subject": "second", "body": "b"}]


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "localhost", "127.0.0.2", "192.168.1.10", "dash.example.com",
                                  " 127.0.0.1", "127.0.0.1 ", "::ffff:127.0.0.1"])
def test_refused_unless_bound_to_exact_loopback(outbox, host):
    with pytest.raises(E2EMailerRefused, match="127.0.0.1 or ::1"):
        CaptureMailer(outbox, bind_host=host, environ=ON)
    assert not outbox.exists()


@pytest.mark.parametrize("env", [{}, {E2E_FLAG: ""}, {E2E_FLAG: "0"}, {E2E_FLAG: "true"}, {E2E_FLAG: "yes"},
                                 {E2E_FLAG: " 1"}, {"GOLDBOT_E2E": "1"}])
def test_refused_without_the_explicit_flag(outbox, env):
    with pytest.raises(E2EMailerRefused, match=E2E_FLAG):
        CaptureMailer(outbox, bind_host="127.0.0.1", environ=env)
    assert not outbox.exists()


def test_the_flag_is_read_from_the_process_environment(outbox, monkeypatch):
    monkeypatch.delenv(E2E_FLAG, raising=False)
    with pytest.raises(E2EMailerRefused):
        CaptureMailer(outbox, bind_host="127.0.0.1")
    monkeypatch.setenv(E2E_FLAG, "1")
    assert CaptureMailer(outbox, bind_host="127.0.0.1").ready()


@pytest.mark.parametrize("where", [ROOT / "state" / "e2e_outbox.jsonl", Path("/var/lib/goldbot/state/outbox.jsonl"),
                                   Path(tempfile.gettempdir()) / ".." / "outbox.jsonl"])
def test_refused_outside_the_temp_dir(where):
    """A deployed state dir (the repo's state/, /var/lib/goldbot) never gets an outbox, even with flag and loopback;
    `..` out of the temp dir is resolved first."""
    existed = where.exists()
    with pytest.raises(E2EMailerRefused, match="temp directory"):
        CaptureMailer(where, bind_host="127.0.0.1", environ=ON)
    assert where.exists() == existed                    # refused before touching the file


def test_every_guard_is_needed_on_its_own(outbox):
    """Each condition refuses alone, the other two holding (a mutation that drops one guard fails a case here)."""
    with pytest.raises(E2EMailerRefused):
        CaptureMailer(outbox, bind_host="0.0.0.0", environ=ON)
    with pytest.raises(E2EMailerRefused):
        CaptureMailer(outbox, bind_host="127.0.0.1", environ={})
    with pytest.raises(E2EMailerRefused):
        CaptureMailer(ROOT / "state" / "e2e_outbox.jsonl", bind_host="127.0.0.1", environ=ON)


def test_settings_cannot_select_the_fake_mailer(monkeypatch):
    """No setting names a mail backend: unknown keys are rejected, and whatever auth.mail holds (with the e2e flag
    set in the environment too) the deployed path builds the SMTP sender or nothing."""
    monkeypatch.setenv(E2E_FLAG, "1")
    for bad in ({"backend": "e2e"}, {"fake": True}, {"outbox": "/tmp/x"}, {"capture": True}):
        with pytest.raises(ValidationError):
            MailSettings(enabled=True, **bad)
    with pytest.raises(ValidationError):
        Settings.model_validate({"auth": {"e2e_mailer": True}})
    assert mailer_from_settings(MailSettings()) is None
    m = mailer_from_settings(MailSettings(enabled=True, host="127.0.0.1", from_address="a@example.com",
                                          dashboard_origin="http://127.0.0.1:8787"))
    assert type(m) is SmtpMailer


def test_no_deployed_module_imports_the_fake_mailer():
    """Only scripts/e2e_server.py builds it; nothing under goldbot/ (API, ops entry points, services) imports it."""
    pat = re.compile(r"\be2e_mailer\b|\bCaptureMailer\b")
    users = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "goldbot").rglob("*.py")
                   if p.name != "e2e_mailer.py" and pat.search(p.read_text(encoding="utf-8")))
    assert users == []
    assert pat.search((ROOT / "scripts" / "e2e_server.py").read_text(encoding="utf-8"))
    for cfg in (ROOT / "config").glob("*.y*ml"):
        assert not re.search(r"e2e|capture|fake", cfg.read_text(encoding="utf-8"), re.I), cfg.name


def test_the_e2e_server_hands_the_mailer_the_host_uvicorn_binds():
    """The loopback guard is only as good as the host it is shown: e2e_server passes args.host to both."""
    src = (ROOT / "scripts" / "e2e_server.py").read_text(encoding="utf-8")
    assert "CaptureMailer(" in src and "bind_host=args.host" in src
    assert re.search(r"uvicorn\.run\(app, host=args\.host,", src)
    assert e2e_mailer.LOOPBACK_BINDS == {"127.0.0.1", "::1"}
