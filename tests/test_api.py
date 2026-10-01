import json

from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.telegram.approvals import ApprovalCenter, Proposal


def test_api_login_accounts_proposals_decide(tmp_path):
    (tmp_path / "engine_icm.json").write_text(json.dumps({
        "account": "icm-demo", "broker": "icm", "mode": "demo", "equity": 9900, "day_start_equity": 10000,
        "week_start_equity": 10000, "balance_closed_hwm": 10000, "stage": "normal", "open_positions": 1,
        "account_class": "raw", "last_tick_age_s": 0.4, "spread_points": 21, "terminal_connected": True, "ts": 0}))
    center = ApprovalCenter({111}, totp_verify=lambda c: c == "123456")
    center.propose(Proposal("p1", "icm-demo", "session_open-g0-x", 1, 0.12, 2400, 2396, 2406, 0.61, 0.35, 22, [("a", 0.1)]))
    app = create_app(tmp_path, center, totp_verify=lambda c: c == "123456", web_dist=tmp_path / "nodist")
    c = TestClient(app)
    assert c.get("/api/accounts").status_code == 401
    assert c.post("/api/login", json={"totp": "000000"}).status_code == 403
    tok = c.post("/api/login", json={"totp": "123456"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    acc = c.get("/api/accounts", headers=h).json()
    assert acc[0]["account_id"] == "icm-demo" and abs(acc[0]["day_pnl_pct"] + 0.01) < 1e-9
    props = c.get("/api/proposals", headers=h).json()
    assert props[0]["proposal_id"] == "p1" and props[0]["side"] == "long"
    r = c.post("/api/decisions", json={"proposal_id": "p1", "action": "reject"}, headers=h)
    assert r.status_code == 400  # rejection needs a reason code
    r = c.post("/api/decisions", json={"proposal_id": "p1", "action": "reject", "reason_code": "cost"}, headers=h)
    assert r.json()["outcome"] == "REJECTED"
    assert c.get("/api/status").json()["pending"] == 0
    feeds = c.get("/api/feeds", headers=h).json()
    assert feeds[0]["terminal_connected"] is True
