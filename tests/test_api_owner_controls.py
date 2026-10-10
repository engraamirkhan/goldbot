"""Dashboard owner controls: the auto-mode card and switch (row A10), the research plan's governance fields and the
calibrated CUSUM h on the Health screen (row M25)."""
import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import explain
from goldbot.api.app import create_app
from goldbot.api.auth import totp_code
from goldbot.config import DriftSettings
from goldbot.engine.shadow import ShadowBook, VersionBook
from goldbot.telegram.bus import ApprovalBus
from tests.test_api_views import _login, seed_health, seed_research
from tests.test_automode import T0, _evidence, _same


def _owner_secret(c: TestClient, app) -> tuple[dict[str, str], str]:
    st = app.state.st
    uri = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io",
                                          "password": "owner password 123"}).json()["totp_uri"]
    secret = uri.split("secret=")[1].split("&")[0]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "owner password 123",
                                          "totp": totp_code(secret)}).json()["token"]
    return {"Authorization": "Bearer " + tok}, secret


@pytest.fixture
def app_client(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=docs, owner_email="o@x.io")
    return app, TestClient(app), state, docs


def seed_eligible(state: Path) -> None:
    """100 decided proposals whose approved and rejected outcomes come from one distribution: the evidence holds."""
    a, r = _same(100)
    props, trades = _evidence(100, a, r)
    bus = ApprovalBus(state)
    for p in props:
        bus.archive(p)
    book = ShadowBook(state)
    book.books["v1"] = VersionBook(version="v1", started_utc=pd.Timestamp(T0 - 9000, unit="s", tz="UTC"), closed=trades)
    book.save(pd.Timestamp(T0, unit="s", tz="UTC"))


def _audit(app) -> list[dict]:
    return [e for e in app.state.st.auth.audit_events() if e["event"].startswith("mode")]


# ------------------------------------------------------------------------------------------------ auto mode card
def test_auto_mode_card_shows_the_evidence_to_any_role_and_offers_nothing_without_it(app_client):
    app, c, state, _ = app_client
    h = _login(c, app)
    assert c.get("/api/automode").status_code == 401
    v = c.get("/api/automode", headers=h["viewer"]).json()
    assert v["owner_mode"] is None and not v["eligible"] and not v["can_enable"]
    assert v["decided"] == 0 and v["min_proposals"] == 100 and v["test"]["p_value"] is None
    assert any("0 decided proposals" in x for x in v["reasons"])
    assert c.get("/api/automode", headers=h["owner"]).json()["can_enable"] is False


def test_auto_mode_card_reports_eligibility_and_only_the_owner_can_enable(app_client):
    app, c, state, _ = app_client
    h = _login(c, app)
    seed_eligible(state)
    v = c.get("/api/automode", headers=h["owner"]).json()
    assert v["eligible"] and v["reasons"] == [] and v["can_enable"]
    assert v["decided"] == 100 and v["test"]["indistinguishable"] and v["test"]["ci_low"] <= 0 <= v["test"]["ci_high"]
    assert c.get("/api/automode", headers=h["viewer"]).json()["can_enable"] is False
    assert c.post("/api/automode", json={"mode": "auto", "totp": "123456"}, headers=h["viewer"]).status_code == 403
    assert ApprovalBus(state).control().approval_mode is None


def test_enable_auto_needs_a_fresh_owner_code_and_the_evidence_and_is_audited(app_client):
    app, c, state, _ = app_client
    owner, secret = _owner_secret(c, app)
    # no evidence: a valid code is still refused, with the reasons
    spent = totp_code(secret)
    r = c.post("/api/automode", json={"mode": "auto", "totp": spent}, headers=owner)
    assert r.status_code == 409 and "Auto mode not available" in r.json()["detail"]
    seed_eligible(state)
    # a wrong code and a non-numeric code are refused before the evidence is read
    assert c.post("/api/automode", json={"mode": "auto", "totp": "000000"}, headers=owner).status_code == 403
    assert c.post("/api/automode", json={"mode": "auto", "totp": "abc def"}, headers=owner).status_code == 403
    # the code spent above (replay guard shared with sign-in and /mode) cannot be used again
    assert c.post("/api/automode", json={"mode": "auto", "totp": spent}, headers=owner).status_code == 403
    assert ApprovalBus(state).control().approval_mode is None
    # a fresh code with the evidence switches, through the bus, by the dashboard owner
    r = c.post("/api/automode", json={"mode": "auto", "totp": totp_code(secret)}, headers=owner)
    assert r.status_code == 200 and r.json()["message"].startswith("Mode: AUTO")
    ctl = ApprovalBus(state).control()
    assert ctl.approval_mode == "auto" and ctl.mode_by == "dashboard:o@x.io"
    assert r.json()["view"]["owner_mode"] == "auto" and r.json()["view"]["can_enable"] is False
    events = [(e["event"], e.get("reason"), e.get("to")) for e in _audit(app)]
    assert events == [("mode_refused", "evidence", "auto"), ("mode_refused", "totp", "auto"),
                      ("mode_refused", "totp", "auto"), ("mode_refused", "totp", "auto"), ("mode", None, "auto")]


def test_switch_to_propose_needs_no_code_and_is_audited(app_client):
    app, c, state, _ = app_client
    owner, _ = _owner_secret(c, app)
    ApprovalBus(state).set_mode("auto", by="telegram:1")
    r = c.post("/api/automode", json={"mode": "propose"}, headers=owner)
    assert r.status_code == 200 and r.json()["message"].startswith("Mode: propose-and-approve")
    assert ApprovalBus(state).control().approval_mode == "propose"
    (e,) = _audit(app)
    assert e["event"] == "mode" and e["to"] == "propose" and e["previous"] == "auto" and e["by"] == "dashboard:o@x.io"


def test_owner_role_without_the_pinned_owner_email_cannot_switch(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    owner, secret = _owner_secret(c, app)
    app.state.st.auth.owner_email = "someone-else@x.io"            # settings now pin a different owner
    assert c.post("/api/automode", json={"mode": "propose"}, headers=owner).status_code == 403
    assert ApprovalBus(state).control().approval_mode is None


def test_re_arm_lock_and_kill_switch_are_shown_as_forcing_propose(app_client):
    app, c, state, _ = app_client
    h = _login(c, app)
    seed_eligible(state)
    (state / "risk_icm-demo.json").write_text(json.dumps({"stage": "normal", "propose_only_until": "2999-01-01T00:00:00+00:00"}))
    (state / "risk_icm-live.json").write_text(json.dumps({"stage": "halted", "halted_at": "2025-03-03T09:00:00+00:00"}))
    (state / "risk_broken.json").write_text("{not json")
    (state / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "approval_mode": "propose"}))
    v = c.get("/api/automode", headers=h["owner"]).json()
    kinds = {(f["account_id"], f["kind"]) for f in v["forced_propose"]}
    assert kinds == {("icm-demo", "rearm_lock"), ("icm-live", "kill_switch"), ("broken", "risk_unreadable")}
    assert not v["eligible"] and not v["can_enable"] and v["engine_modes"] == {"icm-demo": "propose"}
    assert any("propose-only" in x for x in v["reasons"]) and any("unreadable" in x for x in v["reasons"])


# ------------------------------------------------------------------------------------------------ research plan
def test_plan_shows_reservation_retired_families_moves_and_doc_drift(app_client):
    app, c, state, docs = app_client
    h = _login(c, app)
    seed_research(state, docs)
    plan = json.loads((state / "research_plan.json").read_text())
    plan["evidence"].append({"family": "mean_reversion", "trials": 3, "evidence": 0.0, "blocked": False, "flags": [],
                             "median_auc": None, "best_dsr": None, "shadow_trades": 0, "retired_id": "R-01",
                             "retired_status": "net negative after costs", "retired_since": "2026-07-01", "retired": True})
    plan.update(quarter_reserved=4, reservation={"setting": 6, "run": 2, "pending": 3, "reserved": 4},
                retired_floor={"mean_reversion": 1}, reinstate_t=2.61, hypotheses_sha256="0" * 64,
                hypotheses_drift=["R-02 in hypotheses.md but not in research.retired_families"],
                moves=[{"family": "tsmom", "source": "attribution", "detail": "net-R t 2.1 over 60 trades",
                        "budget_before": 9, "budget_after": 10, "share_before": 0.5, "share_after": 0.56, "shift_pct": 12.0}])
    (state / "research_plan.json").write_text(json.dumps(plan))
    p = c.get("/api/research", headers=h["viewer"]).json()["plan"]
    assert p["quarter_reserved"] == 4 and p["reservation"] == {"setting": 6, "run": 2, "pending": 3, "reserved": 4}
    assert p["retired"] == [{"family": "mean_reversion", "hypothesis_id": "R-01", "since": "2026-07-01",
                             "reason": "net negative after costs", "floor": 1, "reinstated": False, "new_evidence": []}]
    assert p["reinstate_t"] == 2.61 and p["moves"][0]["shift_pct"] == 12.0 and p["moves"][0]["budget_after"] == 10
    assert p["hypotheses_changed"] and p["hypotheses_sha256_now"] != "0" * 64
    assert p["hypotheses_drift"] == ["R-02 in hypotheses.md but not in research.retired_families"]


def test_plan_written_before_the_governance_fields_reads_with_defaults(app_client):
    app, c, state, docs = app_client
    h = _login(c, app)
    seed_research(state, docs)
    p = c.get("/api/research", headers=h["viewer"]).json()["plan"]
    assert p["reservation"] is None and p["retired"] == [] and p["moves"] == [] and not p["hypotheses_changed"]


# ------------------------------------------------------------------------------------------------ calibrated CUSUM
def _trades(state: Path) -> list:
    seed_health(state)
    return [t for t in ShadowBook(state).books["tsmom-v3"].closed if t.taken]


def test_cusum_h_falls_back_to_the_fixed_h_with_a_label(tmp_path, monkeypatch):
    trades = _trades(tmp_path)
    monkeypatch.setitem(sys.modules, "goldbot.research.cusum", None)        # module absent: import fails
    h, src, note, _ = explain.cusum_h(trades, 2.0, DriftSettings())
    assert (h, src) == (4.0, "fixed") and "not installed" in note
    fake = types.ModuleType("goldbot.research.cusum")
    fake.calibrated_h = lambda *a, **k: 3.0                                   # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "goldbot.research.cusum", fake)
    h, src, note, _ = explain.cusum_h(trades, None, DriftSettings())
    assert (h, src) == (4.0, "fixed") and "no trade rate" in note


def test_cusum_h_is_calibrated_at_the_agents_trade_rate_and_mean_p(tmp_path, monkeypatch):
    trades = _trades(tmp_path)
    calls: list = []
    fake = types.ModuleType("goldbot.research.cusum")

    def calibrated_h(tpw, k, rate, p=None):
        calls.append((tpw, k, rate, p))
        return 2.37
    fake.calibrated_h = calibrated_h                                          # type: ignore[attr-defined]
    fake.FALSE_ALARM_QUARTER = 0.05                                           # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "goldbot.research.cusum", fake)
    trace = explain._cusum("tsmom-g0", "tsmom-v3", trades, DriftSettings(), 3.5)
    assert trace.h == 2.37 and trace.h_source == "calibrated" and trace.trades_per_week == 3.5
    ((tpw, k, rate, p),) = calls
    assert (tpw, k, rate) == (3.5, 0.5, 0.05) and p == pytest.approx(0.525) and trace.p_mean == pytest.approx(0.525)
    assert "calibrated to 5% false alarms a quarter at 3.5 trades/week" in trace.h_note


def test_health_view_reads_the_trade_rate_from_the_model_registry(tmp_path, monkeypatch):
    state, models = tmp_path / "state", tmp_path / "models"
    state.mkdir()
    models.mkdir()
    (models / "registry.json").write_text(json.dumps([{"version": "tsmom-v3", "backtest": {"trades_per_week": 4.0}}]))
    seed_health(state)
    seen: list = []
    fake = types.ModuleType("goldbot.research.cusum")

    def calibrated_h(tpw, k, rate, p=None):
        seen.append(tpw)
        return 3.1
    fake.calibrated_h = calibrated_h                                          # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "goldbot.research.cusum", fake)
    from goldbot.config import load_settings
    s = load_settings()
    s = s.model_copy(update={"research": s.research.model_copy(update={"models_dir": str(models)})})
    (trace,) = explain.health_view(state, s).cusum
    assert seen == [4.0] and trace.h == 3.1 and trace.h_source == "calibrated"
    assert not (models / "tsmom").exists()                                    # read-only: nothing created


def test_cusum_h_matches_the_real_calibration_when_installed(tmp_path):
    cal = pytest.importorskip("goldbot.research.cusum")
    trades = _trades(tmp_path)
    h, src, _, _ = explain.cusum_h(trades, 1.0, DriftSettings())
    p = explain._mean_p(trades)
    try:
        want = cal.calibrated_h(1.0, 0.5, getattr(DriftSettings(), "cusum_false_alarm", cal.FALSE_ALARM_QUARTER), p=p)
    except TypeError:                                                         # older module without p: fixed fallback
        assert src == "fixed"
        return
    assert src == "calibrated" and h == pytest.approx(round(want, 3))
