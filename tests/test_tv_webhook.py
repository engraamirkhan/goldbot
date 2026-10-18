"""TradingView alerts carry every indicator plot into the inbox, the store and the features (owner, 2026-10-18)."""
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from goldbot.api.tv_status import tv_status
from goldbot.data.store import Store, asof_join
from goldbot.features import tradingview as tvf
from goldbot.features.registry import FEATURES
from goldbot.webhook import app as app_mod
from goldbot.webhook.app import Alert, caller_ip, create_app
from goldbot.webhook.inbox import TvInbox, sync_to_store

BODY: dict[str, Any] = {"indicator": "smc", "version": "1.0", "symbol": "XAUUSD", "tf": "15",
                        "bar_time": "2026-10-16T10:00:00Z", "fired_at": "2026-10-16T10:15:01Z", "secret": "s3cret"}
TV = ("52.89.214.238", 40000)


@pytest.fixture
def box(tmp_path):
    return TvInbox(tmp_path / "tv.db")


def client(box, **kw: Any) -> TestClient:
    return TestClient(create_app(secret="s3cret", inbox=box, enforce_ip=False, **kw))


def test_old_body_without_plots_or_price_still_accepted(box):
    old = {**BODY, "signal": "long", "strength": 1.0, "payload": {"note": "x"}}
    assert client(box).post("/tv", json=old).json() == {"ok": True, "dup": False, "intrabar": False}
    assert box.count() == 1


def test_pure_value_alert_needs_no_signal_and_defaults_to_plot(box):
    assert Alert.model_validate({**BODY, "plots": {"plot_0": "1.5"}}).signal == "plot"
    assert client(box).post("/tv", json={**BODY, "plots": {"plot_0": "1.5"}, "price": "2400.5"}).json()["ok"] is True


def test_quoted_nan_na_and_empty_plots_become_none_and_numbers_parse():
    a = Alert.model_validate({**BODY, "price": "2,400.5", "plots": {"plot_0": "NaN", "plot_1": "n/a", "plot_2": "",
                                                                    "plot_3": "-1.25e2", "plot_4": 7, "plot_5": None}})
    assert a.plots == {"plot_0": None, "plot_1": None, "plot_2": None, "plot_3": -125.0, "plot_4": 7.0, "plot_5": None}
    assert a.price == 2400.5


@pytest.mark.parametrize("bad", ["inf", "-Infinity", "1e300", "abc", True, [1]])
def test_non_finite_or_out_of_bounds_plot_is_refused(box, bad):
    r = client(box).post("/tv", json={**BODY, "plots": {"plot_0": bad}})
    assert r.status_code == 422 and box.count() == 0


def test_more_than_64_plots_is_refused(box):
    ok = {f"plot_{i}": "1" for i in range(64)}
    assert client(box).post("/tv", json={**BODY, "plots": ok}).status_code == 200
    r = client(box).post("/tv", json={**BODY, "bar_time": "2026-10-16T10:15:00Z", "plots": {**ok, "plot_64": "1"}})
    assert r.status_code == 422


def test_oversized_body_is_refused_before_parsing(box):
    r = client(box).post("/tv", content=b'{"x":"' + b"a" * 70_000 + b'"}', headers={"content-type": "application/json"})
    assert r.status_code == 413 and box.count() == 0


def test_validation_error_never_echoes_the_secret(box):
    r = client(box).post("/tv", json={**BODY, "tf": "", "secret": "s3cret"})
    assert r.status_code == 422 and "s3cret" not in r.text


def test_bad_secret_writes_nothing(box):
    assert client(box).post("/tv", json={**BODY, "secret": "nope"}).status_code == 403
    assert box.count() == 0


def test_unauthenticated_caller_gets_the_same_403_whether_or_not_the_body_is_valid(box):
    c = client(box)
    answers = [c.post("/tv", json={**BODY, "secret": "nope"}),
               c.post("/tv", json={**BODY, "secret": "nope", "tf": ""}),
               c.post("/tv", json={"secret": "nope"}),
               c.post("/tv", json={"tf": ""}),
               c.post("/tv", content=b"not json", headers={"content-type": "application/json"}),
               c.post("/tv", json=["secret"])]
    assert {(r.status_code, r.text) for r in answers} == {(403, answers[0].text)}
    assert box.count() == 0


def test_schema_errors_are_only_shown_after_the_secret_is_right(box):
    assert client(box).post("/tv", json={**BODY, "tf": ""}).status_code == 422


def test_inbox_row_cap_per_hour_answers_429_and_logs_without_the_body(box, caplog):
    c = client(box, max_rows_per_hour=2)
    for i in range(2):
        assert c.post("/tv", json={**BODY, "bar_time": f"2026-10-16T1{i}:00:00Z"}).status_code == 200
    with caplog.at_level("WARNING"):
        r = c.post("/tv", json={**BODY, "bar_time": "2026-10-16T12:00:00Z", "price": "123.456"})
    assert r.status_code == 429 and box.count() == 2
    assert "inbox row cap" in caplog.text
    assert "s3cret" not in caplog.text and "123.456" not in caplog.text


def test_rows_older_than_the_hour_do_not_count_against_the_cap(box):
    now = datetime.now(timezone.utc)
    assert client(box, max_rows_per_hour=1).post("/tv", json=BODY).status_code == 200
    assert box.count_since(now - timedelta(hours=1)) == 1
    assert box.count_since(now + timedelta(hours=1)) == 0


def test_bar_alert_with_plots_is_stored_before_the_ack(box):
    assert client(box).post("/tv", json={**BODY, "plots": {"plot_0": "1.5", "plot_1": "NaN"}}).status_code == 200
    assert json.loads(box.rows_after(0)[0]["plots"]) == {"plot_0": 1.5, "plot_1": None}


def test_duplicate_bar_after_restart_is_recognised(tmp_path):
    path = tmp_path / "tv.db"
    first = client(TvInbox(path)).post("/tv", json={**BODY, "plots": {"plot_0": "1"}}).json()
    second = client(TvInbox(path)).post("/tv", json={**BODY, "plots": {"plot_0": "9"}, "signal": "bar"}).json()
    assert first["dup"] is False and second["dup"] is True
    assert TvInbox(path).count() == 1
    assert json.loads(TvInbox(path).rows_after(0)[0]["plots"]) == {"plot_0": 1.0}        # first receipt wins


def test_inbox_write_failure_returns_503_so_tradingview_retries():
    class Broken:
        def put(self, row: dict) -> bool:
            raise sqlite3.OperationalError("disk full")

        def count(self) -> int:
            return 0

        def count_since(self, since: datetime) -> int:
            return 0

    c = TestClient(create_app(secret="s3cret", inbox=Broken(), enforce_ip=False))  # type: ignore[arg-type]
    assert c.post("/tv", json=BODY).status_code == 503


def test_available_utc_is_never_before_receipt_nor_before_bar_close():
    row = app_mod.to_row(Alert.model_validate(BODY), pd.Timestamp("2026-10-16T10:15:03Z").to_pydatetime())
    assert row["available_utc"] == pd.Timestamp("2026-10-16T10:15:03Z")          # receipt, 3 s after the close
    early = app_mod.to_row(Alert.model_validate(BODY), pd.Timestamp("2026-10-16T10:05:00Z").to_pydatetime())
    assert early["available_utc"] == pd.Timestamp("2026-10-16T10:15:00Z")        # skewed clock: not before the close


def _req(peer: str, xff: str | None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1), "headers": headers})


def test_forwarded_ip_trusted_only_from_the_local_proxy_and_only_its_own_entry():
    assert caller_ip(_req("203.0.113.9", "52.89.214.238"), True) == "203.0.113.9"               # outsider's header ignored
    assert caller_ip(_req("127.0.0.1", "52.89.214.238"), True) == "52.89.214.238"               # the proxy appended it
    assert caller_ip(_req("127.0.0.1", "52.89.214.238, 203.0.113.9"), True) == "203.0.113.9"    # left entries are forgeable
    assert caller_ip(_req("127.0.0.1", None), True) == ""                                       # no address: refused
    assert caller_ip(_req("127.0.0.1", "52.89.214.238"), False) == "127.0.0.1"


def test_allow_list_behind_the_local_proxy_end_to_end(box):
    app = create_app(secret="s3cret", inbox=box)
    local = TestClient(app, client=("127.0.0.1", 40000))
    assert local.post("/tv", json=BODY, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403
    assert local.post("/tv", json=BODY, headers={"X-Forwarded-For": "52.89.214.238, 203.0.113.9"}).status_code == 403
    assert local.post("/tv", json=BODY).status_code == 403
    assert local.post("/tv", json=BODY, headers={"X-Forwarded-For": "52.89.214.238"}).status_code == 200


# --------------------------------------------------------------------------------------- store and features
def _post_bars(c: TestClient, n: int, tf: str = "15", skip: tuple[int, ...] = ()) -> None:
    t0 = pd.Timestamp("2026-10-16T08:00:00Z")
    sec = int(tf) * 60
    for i in range(n):
        if i in skip:
            continue
        bt = t0 + pd.Timedelta(seconds=i * sec)
        fired = bt + pd.Timedelta(seconds=sec + 1)
        r = c.post("/tv", json={**BODY, "tf": tf, "bar_time": bt.isoformat(), "fired_at": fired.isoformat(),
                                "price": str(2400 + i), "plots": {"plot_0": str(i), "plot_1": "NaN" if i == 1 else "10"}})
        assert r.status_code == 200


def test_every_alert_lands_in_the_tv_indicators_dataset_idempotently(tmp_path, box):
    store = Store(tmp_path / "data")
    c = client(box, store=store)
    _post_bars(c, 4)
    _post_bars(c, 4)                                                    # a full re-send: nothing doubles
    df = store.read("tv_indicators", source="smc@1.0")
    assert len(df) == 8 and set(df["plot"]) == {"plot_0", "plot_1"}
    assert sync_to_store(box, store) == 0
    assert len(store.read("tv_indicators", source="smc@1.0")) == 8
    assert (pd.to_datetime(df["available_utc"], utc=True) >= pd.to_datetime(df["received_at"], utc=True)).all()


def test_tv_features_joins_through_asof_on_available_utc_not_bar_time(tmp_path, box):
    store = Store(tmp_path / "data")
    _post_bars(client(box, store=store), 4)
    f = tvf.tv_features(store, "smc", "15", ["plot_0", "plot_1"])
    assert list(f.columns) == ["available_utc", "plot_0", "plot_1"] and len(f) == 4
    # the alerts were received now: a bar stamped before that sees none (not even the 08:15 close), a later one sees the last
    bars = pd.DataFrame({"ts_utc": pd.to_datetime(["2026-10-16T08:14:00Z", "2100-01-01T00:00:00Z"], utc=True)})
    j = asof_join(bars, f, prefix="tv_")
    assert math.isnan(j["tv_plot_0"].iloc[0]) and j["tv_plot_0"].iloc[1] == 3.0
    assert set(tvf.tv_features(store, "smc", "15", ["plot_9"]).columns) == {"available_utc", "plot_9"}
    assert tvf.tv_features(store, "Nope", "15").empty
    assert tvf.tv_features(store, "smc", "60").empty


def test_tradingview_feature_is_opt_in_and_versioned():
    assert "tradingview" not in FEATURES
    assert tvf.enable() == "tradingview" and FEATURES["tradingview"].version == tvf.VERSION
    try:
        out = tvf.f_tradingview(pd.DataFrame({"ts_utc": pd.to_datetime(["2026-10-16T09:00:00Z"], utc=True)}), {})
        assert out.shape == (1, 0)
    finally:
        FEATURES.pop("tradingview", None)


def test_status_reports_last_bar_plots_and_gaps(tmp_path, box):
    store = Store(tmp_path / "data")
    _post_bars(client(box, store=store), 6, skip=(3,))
    (row,) = tv_status(store, pd.Timestamp("2026-10-16T12:00:00Z")).indicators
    assert (row.indicator, row.version, row.tf) == ("smc", "1.0", "15")
    assert row.alerts_24h == 5 and row.gaps_24h == 1 and row.plots_seen == ["plot_0", "plot_1"]
    assert row.last_bar_time.startswith("2026-10-16T09:15")
    assert tv_status(Store(tmp_path / "empty"), pd.Timestamp("2026-10-16T12:00:00Z")).indicators == []
