"""Health screen: read-only endpoints for any logged-in role, built from fixture state files."""
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import totp_code
from goldbot.engine.shadow import ShadowBook

T0 = pd.Timestamp("2026-09-01T00:00:00Z")


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


def _login(c: TestClient, app) -> dict[str, dict[str, str]]:
    """Owner by the setup code, then a viewer by invite: both get a bearer header."""
    st = app.state.st
    owner_secret = _secret(c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io",
                                                            "password": "owner password 123"}).json()["totp_uri"])
    owner = {"Authorization": "Bearer " + c.post("/api/auth/login", json={
        "email": "o@x.io", "password": "owner password 123", "totp": totp_code(owner_secret)}).json()["token"]}
    inv = c.post("/api/auth/invite", json={"email": "v@x.io", "role": "viewer"}, headers=owner).json()["invite_token"]
    v_secret = _secret(c.post("/api/auth/accept", json={"token": inv, "password": "viewer password 123"}).json()["totp_uri"])
    viewer = {"Authorization": "Bearer " + c.post("/api/auth/login", json={
        "email": "v@x.io", "password": "viewer password 123", "totp": totp_code(v_secret)}).json()["token"]}
    return {"owner": owner, "viewer": viewer}


def seed_health(state: Path) -> None:
    book = ShadowBook(state)
    book.track("tsmom-v3", T0)
    for i in range(12):                                # alternating p bins; targets in the high bin, stops in the low
        ts = T0 + pd.Timedelta(hours=i)
        p = 0.7 if i % 2 == 0 else 0.35
        t = book.open_trade(version="tsmom-v3", agent_id="tsmom-g0", side=1, bar_ts=ts, entry=2400.0, atr_usd=4.0,
                            target_atr=1.5, stop_atr=1.0, max_bars=8, p=p, timeframe="1h", threshold=0.4)
        assert t is not None
        book.on_bar(pd.Series({"ts_utc": ts + pd.Timedelta(hours=1), "bid_low": 2399.0 if p > 0.5 else 2390.0,
                               "bid_high": 2410.0 if p > 0.5 else 2401.0, "ask_high": 2401.0, "ask_low": 2399.0,
                               "bid_close": 2400.0, "ask_close": 2400.2}), timeframe="1h")
    book.save(T0 + pd.Timedelta(days=1))
    agent = {"agent_id": "tsmom-g0", "version": "tsmom-v3", "psi": {"atr_14": 0.31, "adx_14": 0.12, "rsi_14": 0.02},
             "psi_warn": ["adx_14"], "psi_size_down": ["atr_14"], "n_live_rows": 80, "ece": 0.05, "brier": 0.2,
             "n_calib": 12, "cusum": 1.2, "cusum_alarm": False, "dd_30d": 0.04, "backtest_dd": 0.05,
             "size_factor": 0.5, "halted": False, "notes": ["PSI > 0.25 on atr_14: sized down"]}
    other = {**agent, "agent_id": "trend-g1", "version": "trend-v1", "psi": {"atr_14": 0.05}, "psi_warn": [],
             "psi_size_down": [], "size_factor": 1.0, "halted": True, "cusum_alarm": True, "dd_30d": 0.09,
             "notes": ["CUSUM alarm on trade residuals: agent halted"]}
    (state / "drift.json").write_text(json.dumps({
        "ts": "2026-10-09T06:00:00+00:00", "agents": {"tsmom-g0": agent, "trend-g1": other},
        "halted": {"trend-g1": {"version": "trend-v1", "since": "2026-10-08T06:00:00+00:00",
                                "reasons": ["CUSUM alarm on trade residuals: agent halted"]}},
        "size_factor": {"tsmom-g0": 0.5},
        "system_halt": {"since": "2026-10-09T06:00:00+00:00", "reasons": ["trend-g1: 30-day drawdown 9.0% > 1.5x backtest 5.0%"]},
        "errors": {}}))
    (state / "agents.json").write_text(json.dumps([{"agent_id": "tsmom-g0", "capital_weight": 0.6}]))
    (state / "deploys.jsonl").write_text(json.dumps({"result": "rolled_back", "to": "abcdef123456", "role": "vps",
                                                     "ts": "2026-10-09T05:00:00Z", "detail": "health failed"}) + "\n")


@pytest.fixture
def client(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    return c, _login(c, app), state, docs


def test_views_need_login_and_are_empty_before_the_vps_runs(client):
    c, h, _, _ = client
    assert c.get("/api/health").status_code == 401
    v = c.get("/api/health", headers=h["viewer"]).json()
    assert v["agents"] == [] and v["system_halt"] is None and v["drift_ts"] is None
    assert v["psi"]["features"] == [] and v["reliability"] == [] and v["cusum"] == []
    assert {x["name"]: x["status"] for x in v["checks"]} == {"deploy": "ok", "data_quality": "ok", "drift": "ok"}


def test_health_view_from_fixture_state(client):
    c, h, state, _ = client
    seed_health(state)
    v = c.get("/api/health", headers=h["viewer"]).json()
    agents = {a["agent_id"]: a for a in v["agents"]}
    assert agents["tsmom-g0"]["size_factor"] == 0.5 and agents["tsmom-g0"]["capital_weight"] == 0.6
    assert agents["trend-g1"]["halted"] and agents["trend-g1"]["halt_reasons"] == ["CUSUM alarm on trade residuals: agent halted"]
    assert agents["trend-g1"]["halted_since"].startswith("2026-10-08")
    # PSI heatmap: features by worst PSI, agents that report PSI, null where the agent lacks the feature
    assert v["psi"]["features"] == ["atr_14", "adx_14", "rsi_14"] and v["psi"]["agents"] == ["trend-g1", "tsmom-g0"]
    assert v["psi"]["values"][1] == [None, 0.12] and (v["psi"]["warn"], v["psi"]["size_down"]) == (0.1, 0.25)
    # reliability from the shadow book: two bins with their counts; high p hit the target, low p the stop
    (curve,) = v["reliability"]
    assert curve["agent_id"] == "tsmom-g0" and curve["n"] == 12
    assert [(b["lo"], b["n"], b["hit_rate"]) for b in curve["bins"]] == [(0.3, 6, 0.0), (0.7, 6, 1.0)]
    # CUSUM trace: one point per closed trade, statistic never negative, the drift watch's threshold
    (trace,) = v["cusum"]
    assert len(trace["points"]) == 12 and all(p["s"] >= 0 for p in trace["points"]) and trace["h"] == 4.0
    assert v["system_halt"]["reasons"][0].startswith("trend-g1: 30-day drawdown")
    assert v["system_halt"]["clear_command"].startswith("python -m goldbot.ops.run drift-review --clear")
    assert v["dd_mult"] == 1.5
    checks = {x["name"]: x for x in v["checks"]}
    assert checks["deploy"]["status"] == "warn" and "rolled_back" in checks["deploy"]["reason"]
    assert checks["drift"]["status"] == "fail"


def test_unreadable_drift_file_is_shown_as_a_halt(client):
    c, h, state, _ = client
    (state / "drift.json").write_text("{not json")
    v = c.get("/api/health", headers=h["viewer"]).json()
    assert v["drift_error"] and v["system_halt"]["reasons"] == [v["drift_error"]]
