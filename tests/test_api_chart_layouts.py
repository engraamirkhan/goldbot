"""The Chart tab's saved TradingView layouts: owner-only write, owner-only URL, strict URL validation."""
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.chart_layouts import valid_layout_url
from tests.test_api_views import _login

GOOD = "https://www.tradingview.com/chart/AbCd1234/"


@pytest.fixture
def app_client(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=docs, owner_email="o@x.io")
    return app, TestClient(app)


@pytest.mark.parametrize("url", [GOOD, "https://www.tradingview.com/chart/gold-day/AbCd1234/",
                                 "https://www.tradingview.com/chart/AbCd1234"])
def test_accepts_layout_urls(url):
    assert valid_layout_url(url)


@pytest.mark.parametrize("url", [
    "http://www.tradingview.com/chart/AbCd1234/", "https://tradingview.com/chart/AbCd1234/",
    "https://www.tradingview.com.evil.io/chart/AbCd1234/",
    "https://evil.io/?u=https://www.tradingview.com/chart/AbCd1234/",
    "https://user@www.tradingview.com/chart/AbCd1234/", "https://www.tradingview.com:444/chart/AbCd1234/",
    "https://www.tradingview.com/chart/", "https://www.tradingview.com/symbols/XAUUSD/",
    "https://www.tradingview.com/chart/AbCd1234/?x=1", "javascript:alert(1)", "", "https://www.tradingview.com/chart/a b/"])
def test_rejects_other_urls(url):
    assert not valid_layout_url(url)


def test_owner_saves_and_reads_layouts_and_the_audit_row_has_no_url(app_client):
    app, c = app_client
    owner = _login(c, app)["owner"]
    assert c.get("/api/chart/layouts", headers=owner).json() == {"layouts": [], "can_edit": True}
    r = c.post("/api/chart/layouts", json={"layouts": [{"name": " Gold swing ", "url": GOOD}]}, headers=owner)
    assert r.status_code == 200
    assert r.json() == {"layouts": [{"name": "Gold swing", "url": GOOD}], "can_edit": True}
    ev = [e for e in app.state.st.auth.audit_events() if e["event"] == "chart_layouts_set"]
    assert len(ev) == 1 and "tradingview" not in str(ev[0])


def test_others_and_anonymous_cannot_write_and_never_see_the_url(app_client):
    app, c = app_client
    h = _login(c, app)
    c.post("/api/chart/layouts", json={"layouts": [{"name": "A", "url": GOOD}]}, headers=h["owner"])
    assert c.get("/api/chart/layouts").status_code == 401
    assert c.post("/api/chart/layouts", json={"layouts": []}).status_code == 401
    assert c.post("/api/chart/layouts", json={"layouts": []}, headers=h["viewer"]).status_code == 403
    assert c.get("/api/chart/layouts", headers=h["viewer"]).json() == {"layouts": [{"name": "A", "url": None}], "can_edit": False}


def test_bad_url_and_too_many_layouts_are_rejected_and_nothing_is_saved(app_client):
    app, c = app_client
    owner = _login(c, app)["owner"]
    bad = c.post("/api/chart/layouts", json={"layouts": [{"name": "A", "url": "https://evil.io/chart/AbCd1234/"}]},
                 headers=owner)
    assert bad.status_code == 422
    four = {"layouts": [{"name": str(i), "url": GOOD} for i in range(4)]}
    assert c.post("/api/chart/layouts", json=four, headers=owner).status_code == 422
    assert c.get("/api/chart/layouts", headers=owner).json()["layouts"] == []
