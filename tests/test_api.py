import json

import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import totp_code
from goldbot.telegram.approvals import ApprovalCenter, Proposal

pytestmark = pytest.mark.integration


def _secret_from_uri(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


def test_bootstrap_invite_roles_and_decisions(tmp_path):
    (tmp_path / "engine_icm.json").write_text(json.dumps({
        "account": "icm-demo", "broker": "icm", "mode": "demo", "equity": 9900, "day_start_equity": 10000,
        "week_start_equity": 10000, "balance_closed_hwm": 10000, "stage": "normal", "open_positions": 1,
        "account_class": "raw", "last_tick_age_s": 0.4, "spread_points": 21, "terminal_connected": True, "ts": 0}))
    center = ApprovalCenter({111})
    center.propose(Proposal(proposal_id="p1", account_id="icm-demo", agent_id="session_open-g0-x", side=1, lots=0.12, entry=2400, stop=2396, target=2406, p=0.61, ev_r=0.35, spread_points=22, top_features=[("a", 0.1)]))
    center.propose(Proposal(proposal_id="p2", account_id="icm-demo", agent_id="session_open-g0-x", side=-1, lots=0.10, entry=2400, stop=2404, target=2394, p=0.58, ev_r=0.20, spread_points=22, top_features=[("a", 0.1)]))
    app = create_app(tmp_path, center, web_dist=tmp_path / "nodist")
    c = TestClient(app)
    st = app.state.st

    # first run: owner bootstrap with the setup code
    assert c.get("/api/auth/state").json()["needs_setup"] is True
    assert c.post("/api/auth/setup", json={"setup_code": "wrong", "email": "a@x.io", "password": "correct horse battery"}).status_code == 403
    uri = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "aamir@x.io", "password": "correct horse battery"}).json()["totp_uri"]
    owner_secret = _secret_from_uri(uri)
    assert c.get("/api/auth/state").json()["needs_setup"] is False

    # login needs password AND authenticator code
    assert c.post("/api/auth/login", json={"email": "aamir@x.io", "password": "correct horse battery", "totp": "000000"}).status_code == 403
    r = c.post("/api/auth/login", json={"email": "aamir@x.io", "password": "correct horse battery", "totp": totp_code(owner_secret)}).json()
    owner = {"Authorization": f"Bearer {r['token']}"}
    assert r["role"] == "owner"
    assert c.get("/api/accounts", headers=owner).json()[0]["account_id"] == "icm-demo"

    # owner invites a viewer and an approver
    inv_v = c.post("/api/auth/invite", json={"email": "friend@x.io", "role": "viewer"}, headers=owner).json()["invite_token"]
    inv_a = c.post("/api/auth/invite", json={"email": "partner@x.io", "role": "approver"}, headers=owner).json()["invite_token"]
    assert c.post("/api/auth/accept", json={"token": inv_v, "password": "short"}).status_code == 400
    v_uri = c.post("/api/auth/accept", json={"token": inv_v, "password": "viewer password 123"}).json()["totp_uri"]
    a_uri = c.post("/api/auth/accept", json={"token": inv_a, "password": "approver password 123"}).json()["totp_uri"]
    assert c.post("/api/auth/accept", json={"token": inv_v, "password": "viewer password 123"}).status_code == 400  # one-time

    viewer = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": "friend@x.io", "password": "viewer password 123", "totp": totp_code(_secret_from_uri(v_uri))}).json()["token"]}
    approver = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": "partner@x.io", "password": "approver password 123", "totp": totp_code(_secret_from_uri(a_uri))}).json()["token"]}

    # viewer reads but cannot decide or administer
    assert c.get("/api/proposals", headers=viewer).status_code == 200
    assert c.post("/api/decisions", json={"proposal_id": "p1", "action": "approve"}, headers=viewer).status_code == 403
    assert c.get("/api/users", headers=viewer).status_code == 403
    # approver decides; rejection needs a reason code
    assert c.post("/api/decisions", json={"proposal_id": "p1", "action": "reject"}, headers=approver).status_code == 400
    assert c.post("/api/decisions", json={"proposal_id": "p1", "action": "reject", "reason_code": "cost"}, headers=approver).json()["outcome"] == "REJECTED"
    assert c.post("/api/decisions", json={"proposal_id": "p2", "action": "approve"}, headers=approver).json()["outcome"] == "APPROVED"
    # owner administers
    users = c.get("/api/users", headers=owner).json()
    assert {u["email"] for u in users} == {"aamir@x.io", "friend@x.io", "partner@x.io"}
    assert c.post("/api/users/disable", json={"email": "friend@x.io"}, headers=owner).json()["ok"]
    assert c.get("/api/proposals", headers=viewer).status_code == 401  # disabled user's session revoked
    assert c.post("/api/users/role", json={"email": "aamir@x.io", "role": "viewer"}, headers=owner).status_code == 400  # last owner
    # audit trail written
    events = [json.loads(line)["event"] for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert {"bootstrap_owner", "login", "login_failed", "invite", "accept_invite", "decision", "disable"} <= set(events)
    assert c.get("/api/status").json()["pending"] == 0


def test_lockout_after_five_failures(tmp_path):
    app = create_app(tmp_path, ApprovalCenter({111}), web_dist=tmp_path / "nodist")
    c = TestClient(app)
    st = app.state.st
    c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io", "password": "a long password here"})
    for _ in range(5):
        c.post("/api/auth/login", json={"email": "o@x.io", "password": "nope nope nope", "totp": "000000"})
    r = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here", "totp": "000000"})
    assert r.status_code == 403 and "too many" in r.json()["detail"]
