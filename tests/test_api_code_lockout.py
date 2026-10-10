"""Dashboard security review: authenticator codes for /api/automode, /api/rearm and Telegram /mode count towards the
owner's lockout (one shared counter) and alert the owner; mode_by is the owner's to see; error text carries the
exception type only; API responses are never cached."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import goldbot.api.auth as auth_mod
from goldbot.api import explain
from goldbot.api.app import create_app
from goldbot.api.auth import LOCKOUT_FAILURES, totp_code
from goldbot.telegram import automode
from goldbot.telegram.bus import ApprovalBus
from tests.test_api_owner_controls import _owner_secret, seed_eligible
from tests.test_api_views import _login

OWNER = "o@x.io"


@pytest.fixture
def app_client(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=docs, owner_email=OWNER)
    return app, TestClient(app), state, docs


def _notices(state) -> list[dict]:
    f = state / "agent_runs.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _events(app, prefix: str) -> list[str]:
    return [e["event"] for e in app.state.st.auth.audit_events() if e["event"].startswith(prefix)]


def _window_passes(monkeypatch) -> None:
    real_ns = time.time_ns
    monkeypatch.setattr(auth_mod.time, "time_ns", lambda: real_ns() + (auth_mod.LOCKOUT_WINDOW_S + 1) * 10**9)


def test_wrong_auto_mode_codes_lock_the_switch_even_for_a_valid_code_and_alert_the_owner(app_client, monkeypatch):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    seed_eligible(state)
    for _ in range(LOCKOUT_FAILURES):
        assert c.post("/api/automode", json={"mode": "auto", "totp": "000000"}, headers=owner).status_code == 403
    r = c.post("/api/automode", json={"mode": "auto", "totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 403 and "too many attempts" in r.json()["detail"]
    assert ApprovalBus(state).control().approval_mode is None
    assert _events(app, "mode_failed") == ["mode_failed"] * LOCKOUT_FAILURES and "mode_locked" in _events(app, "mode_")
    (n,) = _notices(state)                                   # one notice at the limit, relayed to Telegram
    assert n["role"] == "account_security" and n["status"] == "notice" and "auto mode" in n["detail"]
    assert OWNER not in n["detail"] and "000000" not in n["detail"]
    _window_passes(monkeypatch)
    r = c.post("/api/automode", json={"mode": "auto", "totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 200 and ApprovalBus(state).control().approval_mode == "auto"


def test_telegram_mode_and_the_dashboard_share_one_lockout_counter(app_client):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    seed_eligible(state)
    for _ in range(LOCKOUT_FAILURES - 1):                       # four wrong codes on Telegram ...
        assert not automode.owner_totp_ok(state, "000000", owner_email=OWNER)
    assert c.post("/api/automode", json={"mode": "auto", "totp": "000000"}, headers=owner).status_code == 403
    # ... plus one on the dashboard: both channels are locked, valid code or not
    assert c.post("/api/automode", json={"mode": "auto", "totp": totp_code(secret)}, headers=owner).status_code == 403
    msg = automode.mode_command(ApprovalBus(state), "auto " + totp_code(secret), "telegram:1",
                                totp_ok=lambda code: automode.owner_totp_ok(state, code, owner_email=OWNER),
                                eligibility=lambda: automode.auto_mode_eligibility_from_state(state),
                                audit=app.state.st.auth.audit)
    assert msg == automode.MODE_LOCKED and ApprovalBus(state).control().approval_mode is None
    assert any(e.get("reason") == "locked" for e in app.state.st.auth.audit_events() if e["event"] == "mode_refused")
    assert len(_notices(state)) == 1
    # propose lowers authority: never locked
    assert c.post("/api/automode", json={"mode": "propose"}, headers=owner).status_code == 200


def test_wrong_rearm_codes_lock_rearm_even_for_a_valid_code_and_alert_the_owner(app_client, monkeypatch):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    ApprovalBus(state).set_halt(True, by="dashboard:o@x.io", reason="test")
    for _ in range(LOCKOUT_FAILURES):
        assert c.post("/api/rearm", json={"totp": "000000"}, headers=owner).status_code == 403
    r = c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 403 and "too many attempts" in r.json()["detail"]
    assert ApprovalBus(state).control().halted
    assert _events(app, "rearm_failed") == ["rearm_failed"] * LOCKOUT_FAILURES and "rearm_locked" in _events(app, "rearm")
    (n,) = _notices(state)
    assert "re-arming" in n["detail"] and OWNER not in n["detail"]
    _window_passes(monkeypatch)
    assert c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=owner).json()["halted"] is False


def test_a_right_code_clears_the_owner_counter(app_client):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    for _ in range(LOCKOUT_FAILURES - 1):
        c.post("/api/rearm", json={"totp": "000000"}, headers=owner)
    assert c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=owner).status_code == 200
    assert c.post("/api/rearm", json={"totp": "000000"}, headers=owner).status_code == 403
    assert c.post("/api/rearm", json={"totp": totp_code(secret)}, headers=owner).status_code == 200   # not locked


def test_mode_by_shows_who_to_the_owner_and_only_the_channel_to_other_roles(app_client):
    app, c, state, _ = app_client
    h = _login(c, app)
    ApprovalBus(state).set_mode("propose", by="telegram:123456789")
    assert c.get("/api/automode", headers=h["owner"]).json()["mode_by"] == "telegram:123456789"
    assert c.get("/api/automode", headers=h["viewer"]).json()["mode_by"] == "telegram"


def test_automode_and_plan_errors_show_the_exception_type_not_the_detail(app_client, monkeypatch):
    app, c, state, _ = app_client
    h = _login(c, app)

    def broken(*_a, **_k):
        raise OSError("/home/goldbot/secret/path/shadow.json: permission denied")
    monkeypatch.setattr(automode, "auto_mode_eligibility_from_state", broken)
    v = c.get("/api/automode", headers=h["viewer"]).json()
    assert v["error"] == "evidence unreadable (OSError)" and not any("/home" in x for x in v["reasons"])
    (state / "research_plan.json").write_text("{not json")
    r = c.get("/api/research", headers=h["viewer"]).json()
    assert r["plan_error"] == "research_plan.json unreadable (JSONDecodeError)"


def test_a_failing_health_check_shows_the_type_only(tmp_path, monkeypatch):
    def broken(_ctx):
        raise RuntimeError("/srv/goldbot/models secret detail")
    import goldbot.ops.health as health
    monkeypatch.setattr(health, "check_drift", broken)
    rows = explain._checks(tmp_path, None)
    assert any(r.reason == "check failed (RuntimeError)" for r in rows)
    assert not any("/srv" in (r.reason or "") for r in rows)


def test_auth_responses_are_never_cached(app_client):
    app, c, state, _ = app_client
    st = app.state.st
    r = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": OWNER, "password": "owner password 123"})
    assert r.headers["cache-control"] == "no-store"
    secret = r.json()["totp_uri"].split("secret=")[1].split("&")[0]
    r = c.post("/api/auth/login", json={"email": OWNER, "password": "owner password 123", "totp": totp_code(secret)})
    assert r.headers["cache-control"] == "no-store"
    owner = {"Authorization": "Bearer " + r.json()["token"]}
    r = c.post("/api/auth/recovery/codes", json={"password": "owner password 123", "totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    inv = c.post("/api/auth/invite", json={"email": "v@x.io", "role": "viewer"}, headers=owner)
    assert inv.headers["cache-control"] == "no-store"
    c.post("/api/auth/accept", json={"token": inv.json()["invite_token"], "password": "viewer password 123"})
    r = c.post("/api/auth/reset-link", json={"email": "v@x.io"}, headers=owner)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert TestClient(app).get("/api/auth/state").headers["cache-control"] == "no-store"
