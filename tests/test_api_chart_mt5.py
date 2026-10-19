"""GET /api/chart/mt5: the MT5 terminal's screenshot, any signed-in role, 404 when missing or older than 5 minutes."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from tests.test_api_views import _login

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def ctx(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=docs, owner_email="o@x.io")
    c = TestClient(app)
    return state, c, _login(c, app)


def _put(state, age_s):
    (state / "chart").mkdir(exist_ok=True)
    f = state / "chart" / "mt5.png"
    f.write_bytes(PNG)
    t = time.time() - age_s
    os.utime(f, (t, t))


def test_requires_sign_in(ctx):
    _, c, _ = ctx
    assert c.get("/api/chart/mt5").status_code in (401, 403)


def test_missing_is_404(ctx):
    _, c, h = ctx
    assert c.get("/api/chart/mt5", headers=h["viewer"]).status_code == 404


def test_fresh_capture_is_served_with_its_time(ctx):
    state, c, h = ctx
    _put(state, 30)
    r = c.get("/api/chart/mt5", headers=h["viewer"])
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-captured-at"].endswith("+00:00")


def test_stale_capture_is_404(ctx):
    state, c, h = ctx
    _put(state, 301)
    r = c.get("/api/chart/mt5", headers=h["owner"])
    assert r.status_code == 404 and "stale" in r.json()["detail"]


def test_full_capture_is_cropped_and_headers_say_what_was_detected(ctx, monkeypatch):
    from goldbot.api import mt5_crop
    from tests.test_mt5_crop import screen
    monkeypatch.setattr(mt5_crop, "tesseract_reader", lambda strip: "")   # no OCR: same result with or without tesseract
    state, c, h = ctx
    _put(state, 30)
    (state / "chart" / "mt5.png").write_bytes(screen("b"))
    r = c.get("/api/chart/mt5", headers=h["viewer"])
    assert r.status_code == 200 and r.headers["x-chart-cropped"] == "true"
    assert r.headers["x-chart-symbol"] == "unknown" and r.headers["x-chart-gold"] == "unknown"
    assert len(r.content) < len(screen("b"))


def test_unrecognised_image_is_served_whole_with_cropped_false(ctx):
    state, c, h = ctx
    _put(state, 30)
    r = c.get("/api/chart/mt5", headers=h["viewer"])
    assert r.content == PNG and r.headers["x-chart-cropped"] == "false"
