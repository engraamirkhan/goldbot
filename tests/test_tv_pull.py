"""The TradingView direct pull (goldbot/data/tv_pull.py, goldbot/data/tv_wire.py; ADR 0030) against replayed chart
conversations (tests/data/tv_pull, synthetic values): no test reaches the network.

Rules under test: strictly read-only; the cookie never leaves the keyring (not in reports, logs, the store or the
pull log); closed bars only, `available_utc` = receipt for pull_live and close + p95 live latency for pull_history;
first receipt wins; polite pacing, a daily budget and backoff; fail soft with a persisted `tv_auth=expired` flag."""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import subprocess
import sys
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import SecretStr

from goldbot.config import TvPullSettings, load_settings
from goldbot.data import tv_catalog, tv_pull, tv_wire
from goldbot.data.store import Store, asof_join
from goldbot.webhook.inbox import TABLE, source_of

FIX = Path(__file__).parent / "data" / "tv_pull"
COOKIE = "sessionid=abcdefgh12345678secret; sessionid_sign=v2:SIGNsecret987654"
AUTH = "AUTHTOKEN-fixture-abcdef123456"
CAPTURED = Path(__file__).parent / "fixtures" / "tv"
T_BACKFILL = pd.Timestamp("2026-10-16T11:55:00Z")
T_LIVE = pd.Timestamp("2026-10-16T12:01:00Z")


class ReplaySocket:
    def __init__(self, script: dict[str, Any], sent: list[str]):
        self.queue: deque[str] = deque(script["hello"])
        self.replies = {k: deque(v) for k, v in script["replies"].items()}
        self.sent = sent
        self.closed = False

    def send(self, text: str) -> None:
        self.sent.append(text)
        for p in tv_wire.decode(text):
            if tv_wire.is_heartbeat(p):
                continue
            m = json.loads(p)["m"]
            if m in self.replies and self.replies[m]:
                self.queue.extend(self.replies[m].popleft())

    def recv(self, timeout: float) -> str:
        if not self.queue:
            raise TimeoutError
        return self.queue.popleft()

    def close(self) -> None:
        self.closed = True


class ReplayNetwork:
    """A recorded conversation: GET bodies by URL, websocket replies by the method that triggers them."""

    def __init__(self, name: str, *, fail_get: Exception | None = None, http: Mapping[str, str] | None = None):
        self.script = json.loads((FIX / name).read_text())
        self.http = dict(self.script["http"]) if http is None else dict(http)
        self.gets: list[tuple[str, dict[str, str]]] = []
        self.sent: list[str] = []
        self.sockets: list[ReplaySocket] = []
        self.urls: list[str] = []
        self.fail_get = fail_get

    def get(self, url: str, headers: Mapping[str, str]) -> str:
        self.gets.append((url, dict(headers)))
        if self.fail_get is not None:
            raise self.fail_get
        if url not in self.http:
            raise RuntimeError(f"404 {url}")
        return self.http[url]

    def open_socket(self, url: str, headers: Mapping[str, str]) -> ReplaySocket:
        assert url in (tv_wire.WS_URL, tv_wire.PRO_WS_URL) and "type=chart" in url
        assert "Cookie" not in headers                                # the socket never carries the cookie
        self.urls.append(url)
        s = ReplaySocket(self.script, self.sent)
        self.sockets.append(s)
        return s

    def methods(self) -> list[str]:
        return [m for t in self.sent for m in tv_wire.methods_in(t)]


class Clock:
    def __init__(self, now: pd.Timestamp):
        self.now = now
        self.mono = 1000.0
        self.sleeps: list[float] = []

    def wall(self) -> pd.Timestamp:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.mono += s
        self.now += pd.Timedelta(seconds=s)


def cfg(**kw: Any) -> TvPullSettings:
    base: dict[str, Any] = dict(enabled=True, timeframes=["15"], bars_per_request=6, study_warmup_bars=2,
                                min_interval_s=5.0, max_pages_per_tf=5)
    return TvPullSettings(**{**base, **kw})


def run(tmp_path: Path, net: Any, clock: Clock, settings: TvPullSettings | None = None,
        cookie: str | None = COOKIE, **kw: Any) -> tv_pull.PassReport:
    return tv_pull.run_pass(settings or cfg(), store=Store(tmp_path / "data"), log=tv_pull.PullLog(tmp_path / "tv.db"),
                            network=net, cookie=SecretStr(cookie) if cookie is not None else None,
                            now=clock.wall, monotonic=clock.monotonic, sleep=clock.sleep, **kw)


def chart_rows(tmp_path: Path) -> pd.DataFrame:
    return Store(tmp_path / "data").read(TABLE, source=source_of("chart", "OANDA:XAUUSD"))


def study_rows(tmp_path: Path) -> pd.DataFrame:
    store = Store(tmp_path / "data")
    srcs = [d.name.removeprefix("source=") for d in store.table_dir(TABLE).glob("source=K-Indi@*")]
    assert len(srcs) == 1
    return store.read(TABLE, source=srcs[0])


@pytest.fixture
def backfilled(tmp_path):
    net, clock = ReplayNetwork("backfill_15m.json"), Clock(T_BACKFILL)
    rep = run(tmp_path, net, clock)
    return tmp_path, net, clock, rep


# ------------------------------------------------------------------------------------------------- wire
def test_frames_round_trip_and_several_frames_share_one_message():
    a, b = tv_wire.encode("create_series", ["cs", 1]), tv_wire.encode_raw("~h~7")
    assert tv_wire.decode(a + b) == ['{"m":"create_series","p":["cs",1]}', "~h~7"]
    with pytest.raises(tv_wire.TvPullError):
        tv_wire.decode("~m~99~m~short")


def test_heartbeats_are_echoed(backfilled):
    _, net, _, _ = backfilled
    beats = [p for t in net.sent for p in tv_wire.decode(t) if tv_wire.is_heartbeat(p)]
    assert beats == ["~h~1", "~h~2"]


# ------------------------------------------------------------------------------------------------- captured frames
def captured_replies(name: str) -> dict[str, Any]:
    """A sanitised capture (tests/fixtures/tv, recorded by the local probe against TradingView on 2026-10-18, public
    STD;RSI only) as a replay script: the server frames that followed each client method."""
    script: dict[str, Any] = {"hello": [], "replies": {}}
    current: list[str] | None = None
    for line in (CAPTURED / name).read_text().splitlines():
        f = json.loads(line)
        if f["dir"] == "out":
            methods = tv_wire.methods_in(f["frame"])
            if methods:
                current = []
                script["replies"].setdefault(methods[0], []).append(current)
        elif current is not None:
            current.append(f["frame"])
    return script


def rsi_meta() -> tv_pull.StudyMeta:
    raw = json.loads((CAPTURED / "study_meta_std_rsi.json").read_text())
    declared = [i for i in raw["inputs"] if i["id"] not in ("text", "pineId", "pineVersion")]
    return tv_pull.StudyMeta.build(
        study_id=raw["pine_id"], name=raw["short"], version=raw["pine_version"],
        inputs={i["id"]: i.get("defval") for i in declared}, input_types={i["id"]: i["type"] for i in declared},
        plots=[tv_pull.PlotMeta(**p) for p in raw["plots"]])


def test_a_captured_tradingview_session_parses_into_bars_and_study_plots():
    sock = tv_wire.GuardedSocket(ReplaySocket(captured_replies("ws_auth_oanda_xauusd_60_rsi.jsonl"), []), [])
    clock = Clock(T_BACKFILL)
    chart = tv_pull._Chart(sock, tv_pull.Pacer(1.0, clock.monotonic, clock.sleep), clock.monotonic, 30.0)
    pages = chart.load("OANDA:XAUUSD", "60", 300, None, 1)
    rsi = chart.study(rsi_meta(), "IL", 1)
    assert pages == 1 and len(chart.bars) >= 400
    times = sorted(chart.bars)
    assert 1.5e9 < times[0] < 2.5e9 and all(b - a >= 3600 for a, b in zip(times, times[1:]))   # open, seconds
    assert len(set(rsi) & set(chart.bars)) >= 300
    row = rsi[times[-1]]
    assert len(row) == 14 and 0.0 <= row[0] <= 100.0                  # RSI plot_0
    values = [v for r in rsi.values() for v in r]
    assert any(math.isnan(v) for v in values) and max(abs(v) for v in values if not math.isnan(v)) < 1e99


def test_the_layout_page_s_metainfo_string_names_the_script_and_keeps_the_owner_s_inputs():
    content = json.loads((CAPTURED / "layout_content_anonymised.json").read_text())
    specs = tv_pull._studies_in(content)
    assert specs and specs[0].study_id == "PUB;script-1" and specs[0].version == "2.0"
    assert "in_0" in specs[0].inputs and "pineId" not in specs[0].inputs
    page = "<script>initData.content = " + json.dumps(content) + ";</script>"
    assert tv_pull._layout_content(page) == content


def test_the_daily_series_keeps_only_daily_bars_after_its_yearly_and_monthly_start():
    day = 86400
    yearly = [-1_700_000_000 + 365 * day * k for k in range(3)]            # before 1921
    monthly = [-1_500_000_000 + day * k for k in (0, 2)] + [-1_500_000_000 + 30 * day * k for k in range(1, 6)]
    daily = [1_700_000_000 + day * k for k in range(5)] + [1_700_000_000 + 9 * day]   # a long weekend stays
    assert tv_pull._drop_sparse_start(yearly + monthly + daily) == daily
    assert tv_pull._drop_sparse_start(daily) == daily


# ------------------------------------------------------------------------------------------------- read-only
def test_the_socket_refuses_any_method_that_is_not_a_read_only_chart_method():
    sent: list[str] = []
    sock = tv_wire.GuardedSocket(ReplaySocket({"hello": [], "replies": {}}, sent), [])
    for method in ("create_alert", "save_chart", "modify_study", "quote_add_symbols_and_place_order"):
        with pytest.raises(tv_wire.ReadOnlyViolation):
            sock.send(tv_wire.encode(method, []))
    assert sent == []


def test_gets_go_only_to_the_read_endpoints():
    net = tv_wire.ReadOnlyNetwork(ReplayNetwork("backfill_15m.json"), COOKIE, [])
    for url in ("https://pricealerts.tradingview.com/create_alert", "https://www.tradingview.com/savechart/",
                "http://www.tradingview.com/", "https://www.tradingview.com/?x=1", "https://evil.example/my-charts/"):
        with pytest.raises(tv_wire.ReadOnlyViolation):
            net.get(url)


def test_a_pass_sends_only_read_methods_and_gets(backfilled):
    _, net, _, rep = backfilled
    assert rep.ok, rep.reason
    assert set(net.methods()) <= tv_wire.READ_METHODS
    assert all(tv_wire._url_allowed(u) for u, _ in net.gets)
    assert net.sockets and all(s.closed for s in net.sockets)


# ------------------------------------------------------------------------------------------------- the data
def test_closed_bars_land_in_tv_indicators_and_the_forming_bar_is_dropped(backfilled):
    tmp_path, _, _, rep = backfilled
    df = chart_rows(tmp_path)
    bars = sorted(df["bar_time"].unique())
    assert bars[0] == pd.Timestamp("2026-10-16T09:30:00Z") and bars[-1] == pd.Timestamp("2026-10-16T11:30:00Z")
    assert len(bars) == 9                                  # 4 history + 5 closed; 11:45 still forming at 11:55
    assert set(df["plot"]) == {"open", "high", "low", "close", "volume"}
    assert set(df["origin"]) <= {"pull_live", "pull_history"} and not df["intrabar"].any()
    tf = rep.per_tf[0]
    assert (tf.tf, tf.bars_received, tf.dropped_forming, tf.new_bars) == ("15", 10, 1, 9)


def test_rows_keep_the_inbox_long_row_schema_plus_origin_and_meta_hash(backfilled):
    tmp_path, *_ = backfilled
    cols = set(chart_rows(tmp_path).columns) - {"source", "symbol", "year", "month"}
    assert cols == {"ts_utc", "bar_time", "available_utc", "fired_at", "received_at", "indicator", "version", "tf",
                    "price", "intrabar", "plot", "value", "origin", "meta_hash"}


def test_available_utc_is_receipt_for_live_bars_and_close_plus_latency_for_history(backfilled):
    tmp_path, _, _, rep = backfilled
    df = chart_rows(tmp_path).drop_duplicates("bar_time").set_index("bar_time")
    close = df.index + pd.Timedelta(minutes=15)
    live, hist = df["origin"] == "pull_live", df["origin"] == "pull_history"
    assert live.any() and hist.any()
    # live: received within live_max_lag_s (30 min) of the close; available exactly when received
    lag = df["received_at"] - pd.Series(close, index=df.index)
    assert (lag[live] <= pd.Timedelta(seconds=1800)).all()
    assert (df.loc[live, "available_utc"] == df.loc[live, "received_at"]).all()
    # history: close + the default latency until enough live bars measure the p95
    assert not rep.history_latency_measured and rep.history_latency_s == 960
    assert (df.loc[hist, "available_utc"] == close[hist.to_numpy()] + pd.Timedelta(seconds=960)).all()


def test_asof_join_sees_a_pulled_value_at_available_utc_not_one_ns_before(backfilled):
    tmp_path, *_ = backfilled
    df = chart_rows(tmp_path)
    row = df[(df["plot"] == "close")].sort_values("bar_time").iloc[0]
    frame = pd.DataFrame({"available_utc": [row["available_utc"]], "close": [row["value"]]})
    t = pd.Timestamp(row["available_utc"])
    j = asof_join(pd.DataFrame({"ts_utc": [t - pd.Timedelta(1, "ns"), t]}), frame, prefix="tv_")
    assert pd.isna(j["tv_close"].iloc[0]) and j["tv_close"].iloc[1] == row["value"]


def test_history_latency_is_the_measured_p95_once_enough_live_bars_exist(tmp_path):
    store = Store(tmp_path / "data")
    bars = pd.date_range("2026-10-15T00:00Z", periods=40, freq="15min")
    lag = [60.0 + i for i in range(40)]
    rows = pd.DataFrame({"ts_utc": bars, "bar_time": bars, "plot": "close", "value": 1.0, "tf": "15",
                         "indicator": "chart", "version": "OANDA:XAUUSD", "origin": "pull_live", "intrabar": False,
                         "received_at": bars + pd.Timedelta(minutes=15) + pd.to_timedelta(lag, unit="s")})
    rows["available_utc"] = rows["received_at"]
    store.append(TABLE, rows, source=source_of("chart", "OANDA:XAUUSD"))
    s, measured = tv_pull.history_latency_s(store, cfg(), now=pd.Timestamp("2026-10-16T00:00Z"))
    assert measured and s == pytest.approx(pd.Series(lag).quantile(0.95))


def test_study_plots_are_stored_by_plot_id_with_na_as_nan_and_warmup_dropped(backfilled):
    tmp_path, *_ = backfilled
    df = study_rows(tmp_path)
    assert set(df["plot"]) == {"plot_0", "plot_1", "plot_2", "plot_3"}
    assert df["indicator"].iloc[0] == "K-Indi"
    bars = sorted(df["bar_time"].unique())
    assert bars[0] == pd.Timestamp("2026-10-16T10:00:00Z")    # 09:30 and 09:45 were warm-up
    ev = df[df["plot"] == "plot_2"].set_index("bar_time")["value"]
    assert ev[pd.Timestamp("2026-10-16T10:15:00Z")] == 1.0 and pd.isna(ev[pd.Timestamp("2026-10-16T10:00:00Z")])


def test_study_metadata_is_persisted_and_keyed_by_its_hash(backfilled):
    tmp_path, *_ = backfilled
    metas = tv_pull.PullLog(tmp_path / "tv.db").metas()
    assert len(metas) == 1
    m = metas[0]
    rows = study_rows(tmp_path)
    assert len(m.meta_hash8) == 8 and set(rows["meta_hash"]) == {m.meta_hash8} and set(rows["version"]) == {"7.0"}
    # the catalog (lane A) derives the same hash from the persisted metadata: one feature id for rows and catalog
    assert tv_catalog.StudyMeta.from_metainfo(m.metainfo, tf="15").meta_hash == m.meta_hash8
    assert m.history_recomputed
    assert m.study_id == "PUB;fixture123" and m.name == "K-Indi" and m.version == "7.0"
    assert m.inputs == {"in_0": 21, "in_1": True}                      # the layout's saved inputs, not the defaults
    assert [(p.id, p.title, p.type) for p in m.plots] == [("plot_0", "EMA", "line"), ("plot_1", "RSI", "line"),
                                                          ("plot_2", "Buy", "shapes"), ("plot_3", "plot_3", "colorer")]
    assert m.colourers == {"plot_3": "plot_0"}
    assert "fixture_il" not in json.dumps(m.model_dump())             # the author's compiled source is never kept


def test_the_same_study_with_other_inputs_is_a_different_series():
    base: dict[str, Any] = dict(study_id="PUB;x", name="K", version="1", plots=[], input_types={"in_0": "integer"})
    a = tv_pull.StudyMeta.build(**base, inputs={"in_0": 21})
    b = tv_pull.StudyMeta.build(**base, inputs={"in_0": 50})
    assert a.meta_hash8 != b.meta_hash8 and a.meta_hash8 == tv_pull.StudyMeta.build(**base, inputs={"in_0": 21}).meta_hash8


def test_the_next_pass_adds_only_new_bars_live_and_counts_repaints(backfilled):
    tmp_path, _, _, _ = backfilled
    before = chart_rows(tmp_path)
    net, clock = ReplayNetwork("live_15m.json"), Clock(T_LIVE)
    rep = run(tmp_path, net, clock, settings=cfg(discover_every_s=10**6))
    assert rep.ok, rep.reason
    df = chart_rows(tmp_path)
    new = df[~df["bar_time"].isin(before["bar_time"])]
    assert sorted(new["bar_time"].unique()) == [pd.Timestamp("2026-10-16T11:45:00Z")]
    assert set(new["origin"]) == {"pull_live"} and (new["available_utc"] == new["received_at"]).all()
    # first receipt wins: the repainted 11:30 close is counted, not written
    old = df[(df["bar_time"] == pd.Timestamp("2026-10-16T11:30:00Z")) & (df["plot"] == "close")]
    assert old["value"].tolist() == [2404.5]
    assert rep.per_tf[0].repaint_mismatch_bars == 1 and rep.per_tf[0].new_bars == 1
    assert "request_more_data" not in net.methods()                 # the store already reaches the loaded window
    assert not any("my-charts" in u for u, _ in net.gets)            # discovery is cached between passes


# ------------------------------------------------------------------------------------------------- politeness
def test_requests_are_spaced_by_the_minimum_interval(backfilled):
    _, _, clock, rep = backfilled
    assert rep.requests >= 8 and len(clock.sleeps) >= rep.requests - 1
    assert all(s == pytest.approx(5.0) for s in clock.sleeps)


def test_a_paid_plan_reads_from_the_prodata_host_and_a_free_one_from_data(tmp_path):
    free = ReplayNetwork("backfill_15m.json")
    run(tmp_path / "free", free, Clock(T_BACKFILL))
    http = dict(free.script["http"])
    http["https://www.tradingview.com/"] = http["https://www.tradingview.com/"].replace(
        '"username"', '"pro_plan":"pro_premium","username"')
    paid = ReplayNetwork("backfill_15m.json", http=http)
    assert run(tmp_path / "paid", paid, Clock(T_BACKFILL)).ok
    assert free.urls == [tv_wire.WS_URL] and paid.urls == [tv_wire.PRO_WS_URL]


def test_history_paging_stops_at_the_page_cap(tmp_path):
    net, clock = ReplayNetwork("backfill_15m.json"), Clock(T_BACKFILL)
    rep = run(tmp_path, net, clock, settings=cfg(max_pages_per_tf=1))
    assert rep.ok and net.methods().count("request_more_data") == 1 and rep.per_tf[0].pages == 1


def test_a_failed_pass_backs_off_and_the_next_one_is_skipped_without_a_request(tmp_path):
    clock = Clock(T_BACKFILL)
    rep = run(tmp_path, ReplayNetwork("backfill_15m.json", fail_get=ConnectionError("reset")), clock)
    assert not rep.ok and not rep.skipped
    clock.now += pd.Timedelta(seconds=120)
    net2 = ReplayNetwork("backfill_15m.json")
    rep2 = run(tmp_path, net2, clock)
    assert rep2.skipped and "backing off" in rep2.reason and net2.gets == []
    log = tv_pull.PullLog(tmp_path / "tv.db")
    assert log.next_allowed(cfg()) == pd.Timestamp(T_BACKFILL) + pd.Timedelta(seconds=300)


def test_backoff_doubles_up_to_the_cap(tmp_path):
    log = tv_pull.PullLog(tmp_path / "tv.db")
    t = pd.Timestamp("2026-10-16T00:00Z")
    for i in range(10):
        log.record(tv_pull.PassReport.failed(t, t, "x", requests=1))
    assert log.next_allowed(cfg()) == t + pd.Timedelta(seconds=21600)
    log.record(tv_pull.PassReport(ok=True, started_utc=t, finished_utc=t, requests=1))
    assert log.next_allowed(cfg()) is None


def test_a_pass_beyond_the_daily_request_budget_is_skipped(tmp_path):
    log = tv_pull.PullLog(tmp_path / "tv.db")
    log.record(tv_pull.PassReport(ok=True, started_utc=T_BACKFILL - pd.Timedelta(hours=2),
                                  finished_utc=T_BACKFILL - pd.Timedelta(hours=2), requests=50))
    net = ReplayNetwork("backfill_15m.json")
    rep = run(tmp_path, net, Clock(T_BACKFILL), settings=cfg(max_requests_per_day=50))
    assert rep.skipped and "budget" in rep.reason and net.gets == []


# ------------------------------------------------------------------------------------------------- fail soft, auth
def test_an_expired_cookie_sets_the_persisted_auth_flag_and_writes_nothing(tmp_path):
    http = dict(json.loads((FIX / "backfill_15m.json").read_text())["http"])
    http["https://www.tradingview.com/"] = "<html>sign in</html>"
    rep = run(tmp_path, ReplayNetwork("backfill_15m.json", http=http), Clock(T_BACKFILL))
    assert not rep.ok and rep.auth_expired and "cookie" in rep.reason
    log = tv_pull.PullLog(tmp_path / "tv.db")
    assert log.auth_state() == "expired" and notices(tmp_path) == 1
    assert not (tmp_path / "data" / TABLE).exists()
    assert log.next_allowed(cfg()) == T_BACKFILL + pd.Timedelta(seconds=21600)


def test_a_new_cookie_retries_at_once_after_an_expiry(tmp_path):
    http = dict(json.loads((FIX / "backfill_15m.json").read_text())["http"])
    http["https://www.tradingview.com/"] = "<html>sign in</html>"
    clock = Clock(T_BACKFILL)
    state = tv_pull.FollowState()
    run(tmp_path, ReplayNetwork("backfill_15m.json", http=http), clock, state=state)
    rep = run(tmp_path, ReplayNetwork("backfill_15m.json"), clock, cookie=COOKIE + "x", state=state)
    assert rep.ok, rep.reason
    assert tv_pull.PullLog(tmp_path / "tv.db").auth_state() == "ok"


def test_no_stored_cookie_names_the_hidden_prompt_and_reaches_no_network(tmp_path):
    net = ReplayNetwork("backfill_15m.json")
    rep = run(tmp_path, net, Clock(T_BACKFILL), cookie=None)
    assert not rep.ok and "goldbot.ops.accounts set tradingview-sessionid" in rep.reason and net.gets == []
    assert "tradingview-sessionid-sign" in rep.reason
    log = tv_pull.PullLog(tmp_path / "tv.db")
    assert log.auth_state() == "expired" and log.next_allowed(cfg()) is None   # no network reached: no backoff


def notices(tmp_path: Path) -> int:
    f = tmp_path / "agent_runs.jsonl"
    return len(f.read_text().splitlines()) if f.exists() else 0


def test_an_unauthorized_answer_records_the_expiry_once_and_a_later_success_records_ok(tmp_path):
    clock = Clock(T_BACKFILL)
    state = tv_pull.FollowState()
    refused = ReplayNetwork("backfill_15m.json")
    refused.script["replies"]["create_series"] = [[tv_wire.encode("critical_error", ["cs", "unauthorized_access"])]]
    rep = run(tmp_path, refused, clock, state=state)
    assert rep.auth_expired and tv_pull.PullLog(tmp_path / "tv.db").auth_state() == "expired"
    clock.now += pd.Timedelta(hours=7)                     # past the expiry backoff: refused again, no second notice
    refused2 = ReplayNetwork("backfill_15m.json")
    refused2.script["replies"]["create_series"] = [[tv_wire.encode("critical_error", ["cs", "unauthorized_access"])]]
    assert run(tmp_path, refused2, clock, state=state).auth_expired
    assert notices(tmp_path) == 1
    ok = run(tmp_path, ReplayNetwork("backfill_15m.json"), clock, cookie=COOKIE + "new", state=state)
    assert ok.ok, ok.reason
    assert tv_pull.PullLog(tmp_path / "tv.db").auth_state() == "ok"


class LeakyNetwork(ReplayNetwork):
    """Its socket fails with a message quoting every secret the pass holds by then."""

    def open_socket(self, url: str, headers: Mapping[str, str]) -> ReplaySocket:
        raise ConnectionError(f"upstream echoed {COOKIE} auth={AUTH} user=4242")


def test_the_cookie_and_tokens_never_appear_in_reports_logs_or_files(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    boom = ConnectionError(f"proxy said: Cookie: {COOKIE}")
    reps = [run(tmp_path / "a", ReplayNetwork("backfill_15m.json", fail_get=boom), Clock(T_BACKFILL)),
            run(tmp_path / "b", LeakyNetwork("backfill_15m.json"), Clock(T_BACKFILL)),
            run(tmp_path / "c", ReplayNetwork("backfill_15m.json"), Clock(T_BACKFILL))]
    assert [r.ok for r in reps] == [False, False, True]
    text = "".join(r.model_dump_json() for r in reps) + caplog.text
    for f in [*tmp_path.rglob("*.db"), *tmp_path.rglob("*.db-wal"), *tmp_path.rglob("*.parquet")]:
        text += f.read_bytes().decode("latin-1")
    for secret in ("abcdefgh12345678secret", "v2:SIGNsecret987654", AUTH, "user=4242"):
        assert secret not in text
    assert "[redacted]" in reps[0].reason and "[redacted]" in reps[1].reason


def test_a_protocol_error_fails_the_pass_softly(tmp_path):
    net = ReplayNetwork("backfill_15m.json")
    net.script["replies"]["create_series"] = [[tv_wire.encode("series_error", ["cs", "$prices", "no data"])]]
    rep = run(tmp_path, net, Clock(T_BACKFILL))
    assert not rep.ok and "series_error" in rep.reason and not rep.auth_expired


def test_a_script_tradingview_no_longer_serves_is_listed_and_the_rest_still_pulls(tmp_path):
    http = dict(json.loads((FIX / "backfill_15m.json").read_text())["http"])
    http["https://pine-facade.tradingview.com/pine-facade/translate/PUB%3Bfixture123/7.0"] = '{"success": false}'
    rep = run(tmp_path, ReplayNetwork("backfill_15m.json", http=http), Clock(T_BACKFILL))
    assert rep.ok and rep.studies == [] and rep.unavailable[0].startswith("PUB;fixture123")
    assert rep.per_tf[0].new_bars == 9 and rep.per_tf[0].studies == 0


def test_a_bar_from_the_future_fails_closed_and_writes_nothing(tmp_path):
    rep = run(tmp_path, ReplayNetwork("backfill_15m.json"), Clock(pd.Timestamp("2026-10-16T11:00:00Z")))
    assert not rep.ok and "future" in rep.reason
    assert not (tmp_path / "data" / TABLE).exists()


def test_the_webhook_never_imports_the_puller():
    code = "import sys, goldbot.webhook.app; assert not any(m.startswith('goldbot.data.tv_pull') or " \
           "m.startswith('goldbot.data.tv_wire') for m in sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_pull_log_rows_a_newer_release_wrote_still_read(backfilled):
    """ADR 0010: persisted state is read tolerantly (an unknown field is dropped and counted, never an error)."""
    tmp_path, *_ = backfilled
    log = tv_pull.PullLog(tmp_path / "tv.db")
    conn = sqlite3.connect(tmp_path / "tv.db")
    with conn:
        for table, col in (("tv_pull_runs", "report"), ("tv_study_meta", "meta")):
            raw = json.loads(conn.execute(f"SELECT {col} FROM {table}").fetchone()[0])
            conn.execute(f"UPDATE {table} SET {col} = ?", (json.dumps({**raw, "added_later": 1}),))
        specs = json.loads(conn.execute("SELECT value FROM tv_pull_state WHERE key = 'discovered'").fetchone()[0])
        conn.execute("UPDATE tv_pull_state SET value = ? WHERE key = 'discovered'",
                     (json.dumps([{**x, "added_later": 1} for x in specs]),))
    conn.close()
    last = log.last()
    assert last is not None and last.ok and log.metas()[0].name == "K-Indi"
    rep = run(tmp_path, ReplayNetwork("live_15m.json"), Clock(T_LIVE), settings=cfg(discover_every_s=10**6))
    assert rep.ok and rep.studies


# ------------------------------------------------------------------------------------------------- settings
def test_settings_keep_the_pull_off_and_conservative_by_default():
    s = load_settings().tv_pull
    assert s == TvPullSettings() and not s.enabled
    assert s.min_interval_s >= 5 and s.bars_per_request <= 5000 and s.backoff_max_s >= 3600
