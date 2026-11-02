"""The dashboard's read-through view cache (api/progress.py GatesCache), and the Progress, Research and Feeds routes
retired on 2026-11-02 (owner: remove the research and feeds screens): each answers 404 like any unknown path."""
import threading

import pytest
from fastapi.testclient import TestClient

from goldbot.api import progress
from goldbot.api.app import create_app
from tests.test_api_views import _login

RETIRED = ["/api/research", "/api/gates", "/api/attribution", "/api/trades/summary", "/api/agents", "/api/agent-runs",
           "/api/jobs", "/api/news", "/api/calendar", "/api/tradingview", "/api/activity", "/api/chart/layouts"]


def test_gates_cache_serves_one_evaluation_for_60_seconds():
    clock = [1000.0]
    cache = progress.GatesCache(ttl_s=60.0, clock=lambda: clock[0])
    calls: list[int] = []

    def build() -> int:
        calls.append(1)
        return len(calls)
    assert cache.get(build) == 1
    clock[0] += 59.9
    assert cache.get(build) == 1
    clock[0] += 0.2
    assert cache.get(build) == 2


def test_gates_cache_serves_the_old_value_while_one_rebuild_runs():
    clock = [0.0]
    cache = progress.GatesCache(ttl_s=60.0, clock=lambda: clock[0])
    assert cache.get(lambda: "old") == "old"
    clock[0] = 61.0
    started, release = threading.Event(), threading.Event()

    def slow() -> str:
        started.set()
        release.wait(5)
        return "new"
    rebuild = threading.Thread(target=lambda: cache.get(slow))
    rebuild.start()
    assert started.wait(5)
    got: list[str] = []
    other = threading.Thread(target=lambda: got.append(cache.get(lambda: "second build")))
    other.start()
    other.join(2)                                       # never blocked behind the running rebuild
    assert got == ["old"]
    release.set()
    rebuild.join(5)
    assert cache.get(lambda: "unused") == "new"


@pytest.fixture(scope="module")
def signed_in(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("retired")
    app = create_app(tmp / "state", web_dist=tmp / "nodist", data_root=tmp / "data", owner_email="o@x.io")
    c = TestClient(app)
    return c, _login(c, app)


@pytest.mark.parametrize("path", RETIRED)
def test_a_retired_research_or_feeds_route_answers_not_found_to_a_signed_in_owner(signed_in, path):
    c, h = signed_in
    assert c.get(path, headers=h["owner"]).status_code == 404


def test_the_feeds_and_health_routes_the_health_screen_reads_stay(signed_in):
    c, h = signed_in
    assert c.get("/api/feeds", headers=h["viewer"]).status_code == 200
    assert c.get("/api/health", headers=h["viewer"]).status_code == 200


def test_the_dashboard_frames_nothing_now_the_tradingview_chart_is_retired(signed_in):
    c, h = signed_in
    csp = c.get("/api/feeds", headers=h["viewer"]).headers["Content-Security-Policy"]
    assert "frame-src 'none'" in csp and "tradingview" not in csp


def test_a_retired_route_is_not_found_even_where_the_app_serves_the_built_dashboard(tmp_path):
    """Behind the SPA fallback an unknown /api/ path is a 404, never index.html with a 200: a tab still open on the old
    dashboard gets a clean error from a retired route instead of HTML it cannot parse."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("INDEX")
    app = create_app(tmp_path / "state", web_dist=dist, data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)
    assert c.get("/api/research", headers=h["owner"]).status_code == 404
    assert c.get("/research").text == "INDEX"                       # the dashboard's own paths still get the app
