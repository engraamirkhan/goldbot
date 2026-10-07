import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import totp_code
from goldbot.config import load_settings
from goldbot.data.econ_calendar import COLUMNS
from goldbot.data.store import Store
from goldbot.ops.scheduler import Schedule, Scheduler
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus

pytestmark = pytest.mark.integration


def _secret_from_uri(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


def test_bootstrap_invite_roles_and_decisions(tmp_path):
    (tmp_path / "engine_icm.json").write_text(json.dumps({
        "account": "icm-demo", "broker": "icm", "mode": "demo", "equity": 9900, "day_start_equity": 10000,
        "week_start_equity": 10000, "balance_closed_hwm": 10000, "stage": "normal", "open_positions": 1,
        "account_class": "raw", "last_tick_age_s": 0.4, "spread_points": 21, "terminal_connected": True, "ts": 0}))
    bus = ApprovalBus(tmp_path)                       # an engine (separate process) published two proposals
    bus.publish(Proposal(proposal_id="p1", account_id="icm-demo", agent_id="session_open-g0-x", side=1, lots=0.12, entry=2400, stop=2396, target=2406, p=0.61, ev_r=0.35, spread_points=22, top_features=[("a", 0.1)]))
    bus.publish(Proposal(proposal_id="p2", account_id="icm-demo", agent_id="session_open-g0-x", side=-1, lots=0.10, entry=2400, stop=2404, target=2394, p=0.58, ev_r=0.20, spread_points=22, top_features=[("a", 0.1)]))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist")
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
    assert c.post("/api/decisions", json={"proposal_id": "p1", "action": "reject", "reason_code": "cost"}, headers=approver).json()["outcome"] == "SUBMITTED"
    assert c.post("/api/decisions", json={"proposal_id": "p2", "action": "approve"}, headers=approver).json()["outcome"] == "SUBMITTED"
    assert c.post("/api/decisions", json={"proposal_id": "p2", "action": "approve"}, headers=owner).status_code == 400  # first wins
    assert c.get("/api/proposals", headers=viewer).json() == []
    # the engine picks the decisions up on its next tick and archives them with the outcome
    engine = ApprovalCenter({111}, bus=ApprovalBus(tmp_path))
    engine.pending = {p.proposal_id: p for p in (Proposal.model_validate_json((tmp_path / "approvals" / "pending" / f"{i}.json").read_text())
                                                 for i in ("p1", "p2"))}
    done = {p.proposal_id: p for p in engine.poll_bus()}
    assert done["p1"].outcome == Outcome.REJECTED and done["p1"].reason_code == "cost" and done["p1"].decided_via == "dashboard:partner@x.io"
    assert done["p2"].outcome == Outcome.APPROVED and bus.outcome("p2") == "APPROVED"
    # halt: an approver may stop new entries; re-arming needs the owner and an authenticator code
    assert c.post("/api/halt", json={"reason": "fomc surprise"}, headers=viewer).status_code == 403
    assert c.post("/api/halt", json={"reason": "fomc surprise"}, headers=approver).json()["halted"] is True
    assert bus.control().halted and c.get("/api/status", headers=viewer).json()["halted_by"] == "dashboard:partner@x.io"
    assert c.post("/api/rearm", json={"totp": totp_code(owner_secret)}, headers=approver).status_code == 403
    assert c.post("/api/rearm", json={"totp": "000000"}, headers=owner).status_code == 403
    assert c.post("/api/rearm", json={"totp": totp_code(owner_secret)}, headers=owner).json()["halted"] is False
    # owner administers
    users = c.get("/api/users", headers=owner).json()
    assert {u["email"] for u in users} == {"aamir@x.io", "friend@x.io", "partner@x.io"}
    assert c.post("/api/users/disable", json={"email": "friend@x.io"}, headers=owner).json()["ok"]
    assert c.get("/api/proposals", headers=viewer).status_code == 401  # disabled user's session revoked
    assert c.post("/api/users/role", json={"email": "aamir@x.io", "role": "viewer"}, headers=owner).status_code == 400  # last owner
    # audit trail written
    events = [json.loads(line)["event"] for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert {"bootstrap_owner", "login", "login_failed", "invite", "accept_invite", "decision", "disable", "halt", "rearm",
            "rearm_failed"} <= set(events)
    assert c.get("/api/status", headers=owner).json()["pending"] == 0


def test_lockout_after_five_failures(tmp_path):
    app = create_app(tmp_path, web_dist=tmp_path / "nodist")
    c = TestClient(app)
    st = app.state.st
    c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io", "password": "a long password here"})
    for _ in range(5):
        c.post("/api/auth/login", json={"email": "o@x.io", "password": "nope nope nope", "totp": "000000"})
    r = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here", "totp": "000000"})
    assert r.status_code == 403 and "too many" in r.json()["detail"]


def test_jobs_endpoint_reports_the_scheduler_state(tmp_path):
    now = {"t": pd.Timestamp("2026-10-02 12:00", tz="UTC")}

    def failing(slot):
        raise RuntimeError("no ticks table")
    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: now["t"])
    sch.add("nightly_costs", Schedule(kind="daily", at="23:10"), failing)
    now["t"] = pd.Timestamp("2026-10-02 23:11", tz="UTC")
    sch.run_pending()

    app = create_app(tmp_path, web_dist=tmp_path / "nodist")
    c = TestClient(app)
    assert c.get("/api/jobs").status_code == 401
    uri = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": "o@x.io", "password": "a long password here"}).json()["totp_uri"]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here", "totp": totp_code(_secret_from_uri(uri))}).json()["token"]
    rows = c.get("/api/jobs", headers={"Authorization": f"Bearer {tok}"}).json()
    assert [r["name"] for r in rows] == ["nightly_costs"]
    row = rows[0]
    assert row["last_ok"] is False and row["last_error"] == "RuntimeError: no ticks table"   # first line only
    assert row["failures"] == 1 and row["next_slot"].startswith("2026-10-03T23:10")
    assert row["heartbeat_age_s"] >= 0


def test_agent_runs_endpoint_serves_reports_from_the_state_dir_only(tmp_path):
    rep = tmp_path / "agent_reports" / "risk_officer"
    rep.mkdir(parents=True)
    (rep / "r.md").write_text("Drawdown 2.1%, no limits tripped.")
    outside = tmp_path.parent / "secret.md"
    outside.write_text("not for the dashboard")
    rows = [{"role": "risk_officer", "started_utc": "2026-10-05T23:45:00+00:00", "status": "ok", "turns": 3, "cost_usd": 0.21,
             "detail": None, "report_path": str(rep / "r.md")},
            {"role": "data_steward", "started_utc": "2026-10-05T23:46:00+00:00", "status": "ok", "turns": 1, "cost_usd": 0.05,
             "detail": None, "report_path": str(outside)}]
    (tmp_path / "agent_runs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    app = create_app(tmp_path, web_dist=tmp_path / "nodist")
    c = TestClient(app)
    uri = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": "o@x.io", "password": "a long password here"}).json()["totp_uri"]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here", "totp": totp_code(_secret_from_uri(uri))}).json()["token"]
    got = c.get("/api/agent-runs", headers={"Authorization": f"Bearer {tok}"}).json()
    assert [r["role"] for r in got] == ["data_steward", "risk_officer"]          # newest first, bad line skipped
    assert got[1]["report"].startswith("Drawdown") and got[0]["report"] is None   # outside the state dir: not served


def test_spa_fallback_never_serves_files_outside_web_dist(tmp_path):
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("INDEX")
    (dist / "favicon.svg").write_text("<svg/>")
    state = tmp_path / "state"
    state.mkdir()
    (state / ".secrets.json").write_text('{"mt5-icm-live": "SECRET"}')
    c = TestClient(create_app(state, web_dist=dist))
    assert c.get("/favicon.svg").text == "<svg/>"
    assert c.get("/approvals").text == "INDEX"                     # client-side route -> the app shell
    for path in ("/..%2f..%2fstate/.secrets.json", "/%2e%2e/%2e%2e/state/.secrets.json", "/..%2F..%2Fstate%2F.secrets.json"):
        r = c.get(path)
        assert "SECRET" not in r.text and r.text == "INDEX", path


def test_status_and_live_socket_need_a_session(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": 0, "combined_equity": 123456.0}))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist")
    c = TestClient(app)
    assert c.get("/api/status").status_code == 401                    # equity and halt details are not public
    with c.websocket_connect("/ws") as ws:                             # bad token: closed without any event
        ws.send_json({"token": "forged"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
    assert app.state.st.ws_clients == set()
    uri = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": "o@x.io", "password": "a long password here"}).json()["totp_uri"]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here", "totp": totp_code(_secret_from_uri(uri))}).json()["token"]
    assert c.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).json()["supervisor"]["combined_equity"] == 123456.0
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"token": tok})
        assert ws.receive_json() == {"type": "hello"}
        assert len(app.state.st.ws_clients) == 1


def _owner_headers(c: TestClient, app) -> dict[str, str]:
    uri = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": "o@x.io", "password": "a long password here"}).json()["totp_uri"]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here", "totp": totp_code(_secret_from_uri(uri))}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _seed_calendar_and_news(root, now: pd.Timestamp) -> None:
    store = Store(root)
    ev = [("cpi", now + pd.Timedelta(hours=2), "USD", "CPI m/m", "High", 1, "0.3%", "0.2%"),
          ("ism", now + pd.Timedelta(hours=1), "USD", "ISM Manufacturing PMI", "High", 2, "49.5", "48.7"),
          ("gbp", now + pd.Timedelta(hours=3), "GBP", "Construction PMI", "Low", 3, "", ""),
          ("nfp", now + pd.Timedelta(days=10), "USD", "Non-Farm Employment Change", "High", 1, "150K", "142K")]
    store.append("calendar_events", pd.DataFrame(
        [{"event_id": i, "ts_utc": t, "country": c, "title": ti, "impact": im, "tier": tier, "forecast": f, "previous": p,
          "received_utc": now} for i, t, c, ti, im, tier, f, p in ev], columns=COLUMNS), source="forexfactory")
    nan = float("nan")
    items = [("n1", 10, "Fed's Powell signals pause", 0.8, "dovish", "risk_on", "negative", "none", False, True),
             ("n2", 30, "Missile strike reported near Gulf shipping lane", 0.95, "neutral", "risk_off", "positive", "none", True, True),
             ("n3", 60, "Local sports results", nan, "", "", "", "", False, False),
             ("n4", 600, "Old item from this morning", 0.5, "neutral", "neutral", "neutral", "none", False, True),
             ("n5", 120, "ECB minutes show split", 0.3, "hawkish", "neutral", "negative", "none", False, True)]
    store.append("news", pd.DataFrame(
        [{"item_id": i, "ts_utc": now - pd.Timedelta(minutes=m), "received_utc": now - pd.Timedelta(minutes=m - 1),
          "source": "forexlive", "title": t, "summary": "", "link": f"https://example.com/{i}" if i != "n3" else "",
          "scored": sc, "relevance": rel, "rates": ra, "risk": ri, "dollar": d, "surprise": su, "shock": sh}
         for i, m, t, rel, ra, ri, d, su, sh, sc in items]), source="rss")


def test_calendar_and_news_endpoints(tmp_path):
    now = pd.Timestamp.now("UTC").floor("s")
    _seed_calendar_and_news(tmp_path / "data", now)
    shock = {"title": "Missile strike reported near Gulf shipping lane", "kind": "news_shock",
             "received_utc": (now - pd.Timedelta(minutes=29)).isoformat()}
    (tmp_path / "engine_icm.json").write_text(json.dumps({"account": "icm-demo", "blackout": shock}))
    (tmp_path / "engine_vantage.json").write_text(json.dumps({"account": "vantage-demo", "blackout": shock}))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", data_root=tmp_path / "data")
    c = TestClient(app)
    assert c.get("/api/calendar").status_code == 401 and c.get("/api/news").status_code == 401
    h = _owner_headers(c, app)

    cal = c.get("/api/calendar", headers=h).json()
    assert [e["event_id"] for e in cal["events"]] == ["ism", "cpi"]          # 7 days, tiers 1-2, by time
    cpi = cal["events"][1]
    blackout = load_settings().risk.blackout
    assert pd.Timestamp(cpi["blackout_start"]) == now + pd.Timedelta(hours=2) - pd.Timedelta(minutes=blackout.before_min)
    assert pd.Timestamp(cpi["blackout_end"]) == now + pd.Timedelta(hours=2) + pd.Timedelta(minutes=blackout.after_min)
    assert cal["events"][0]["blackout_start"] is None and cal["events"][0]["forecast"] == "49.5"
    ab = cal["active_blackout"]
    assert ab["kind"] == "news_shock" and ab["title"].startswith("Missile") and ab["accounts"] == ["icm-demo", "vantage-demo"]
    # inputs are clamped, not rejected
    assert [e["event_id"] for e in c.get("/api/calendar?days=99&max_tier=9", headers=h).json()["events"]] == ["ism", "cpi", "gbp", "nfp"]
    assert [e["event_id"] for e in c.get("/api/calendar?days=0&max_tier=0", headers=h).json()["events"]] == ["cpi"]

    news = c.get("/api/news", headers=h).json()
    assert [n["item_id"] for n in news] == ["n1", "n2", "n3", "n5", "n4"]    # newest first, last 24 h
    unscored = news[2]
    assert unscored["relevance"] is None and unscored["rates"] is None and unscored["link"] is None and unscored["shock"] is False
    assert news[1]["shock"] is True and news[1]["risk"] == "risk_off" and news[0]["link"] == "https://example.com/n1"
    assert [n["item_id"] for n in c.get("/api/news?min_relevance=0.7", headers=h).json()] == ["n1", "n2"]
    assert [n["item_id"] for n in c.get("/api/news?hours=0&min_relevance=-3", headers=h).json()] == ["n1", "n2"]   # 1 h, all
    assert c.get("/api/news?min_relevance=5", headers=h).json() == []


def test_calendar_and_news_without_archive_or_engines(tmp_path):
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", data_root=tmp_path / "empty")
    c = TestClient(app)
    h = _owner_headers(c, app)
    cal = c.get("/api/calendar", headers=h).json()
    assert cal["events"] == [] and cal["active_blackout"] is None and "no calendar archived" in cal["note"]
    assert c.get("/api/news", headers=h).json() == []
