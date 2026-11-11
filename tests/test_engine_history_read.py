"""A store read of the engine's history fails closed (backlog 155; found by the duckdb 2.0 test of Dependabot #312,
where every `Store.read` with a ts filter raised a BinderException).

Design rule (engine/history.py): a history read that raises, or one that returns fewer 1m bars than the warm-start
minimum, refuses every entry with a journalled reason (`history_read:<tf>` / `history_short:<tf>`, and RiskGate's
`data_quality_error` for every other entry path) and fails the engine health check; exits and position management
never look at it. A later re-read that succeeds lifts it; a normal read changes nothing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, history, key_zones
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.specialists import SPECIALISTS
from tests.test_health import make_ctx

TICKS = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=1, seed=8)
LIVE_FROM = pd.Timestamp("2025-03-13", tz="UTC")


def _engine(tmp_path: Path, **cfg: Any) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                               data_root=str(tmp_path / "data"), **cfg),
                  PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)})


def _seed(tmp_path: Path, start: pd.Timestamp | None = None, end: pd.Timestamp = LIVE_FROM) -> None:
    t = TICKS[(TICKS["ts_utc"] < end) & ((TICKS["ts_utc"] >= start) if start is not None else True)]
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(t), source="dukascopy")


def _feed(eng: Engine, lo: pd.Timestamp, hi: pd.Timestamp) -> list[dict]:
    t = TICKS[(TICKS["ts_utc"] >= lo) & (TICKS["ts_utc"] < hi)]
    out: list[dict] = []
    for ts, bid, ask in zip(t["ts_utc"], t["bid"].to_numpy(float), t["ask"].to_numpy(float)):
        out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    return out


class Broken:
    """Store.read raising on every ts-filtered read while `on` (duckdb 2.0: TIMESTAMPTZ_NS vs TIMESTAMP WITH TIME
    ZONE), counting the reads it refused."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.on, self.refused, real = True, 0, Store.read

        def read(store: Store, table: str, **kw: Any) -> pd.DataFrame:
            if self.on and (kw.get("start") is not None or kw.get("end") is not None):
                self.refused += 1
                raise RuntimeError("Binder Error: Cannot compare TIMESTAMP_NS WITH TIME ZONE and TIMESTAMP")
            return real(store, table, **kw)
        monkeypatch.setattr(Store, "read", read)


class TickHistory:
    """A history client whose terminal holds `ticks` (copy_ticks_from semantics)."""

    def __init__(self, ticks: pd.DataFrame):
        self.ticks = ticks.reset_index(drop=True)

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        h = self.ticks
        return h[h["ts_utc"] >= since_utc.floor("s")].head(n).reset_index(drop=True)


def _settle(eng: Engine) -> None:
    f = eng.history.fill
    assert f is not None and f.done.wait(60)
    history.absorb(eng)


def _state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "engine_icm-demo.json").read_text())


# ------------------------------------------------------------------------------------------- a read that raises
def test_a_warm_start_whose_store_read_raises_starts_the_engine_and_blocks_entries(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    assert eng.warm_start(LIVE_FROM) == 0                      # no crash: the engine starts, so its exits run
    assert broken.refused >= 1
    assert "history_read" in eng._dq_blocking()               # RiskGate refuses every entry (data_quality_error)
    assert eng.history.backfill["read_error"] == "RuntimeError"


def test_entries_are_refused_history_read_while_the_store_cannot_be_read(tmp_path, monkeypatch):
    _seed(tmp_path)
    Broken(monkeypatch)
    eng = _engine(tmp_path, backfill_page_ticks=2000)
    eng.warm_start(LIVE_FROM, TickHistory(TICKS[TICKS["ts_utc"] < LIVE_FROM]))   # the broker refills ten days
    _settle(eng)
    assert len(eng.bars_1m) > 10_000
    decisions = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=12))
    assert decisions and {d["action"] for d in decisions} == {"history_read:15m"}
    assert eng.state.dq_error and not eng.open
    st = _state(tmp_path)
    assert st["history_backfill"]["read_error"] == "RuntimeError" and "history_read" in st["dq_checks"]


def test_a_failed_history_read_never_stops_an_open_trade_s_time_exit(tmp_path, monkeypatch):
    _seed(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    opened = LIVE_FROM + pd.Timedelta(hours=33)                # the 03-14 08:30 session_open entry
    _feed(eng, LIVE_FROM, opened)
    assert eng.open, "the healthy engine opened a trade"
    (pid, tr), = list(eng.open.items())[:1]
    Broken(monkeypatch)
    history.read_failed(eng, opened, RuntimeError("store broken"))
    tr.bars_held = tr.max_bars - 1                             # its time barrier is the next close
    decisions = _feed(eng, opened, opened + pd.Timedelta(hours=1))
    assert pid not in eng.open and pid not in {p.position_id for p in eng.broker.positions()}
    assert not any(d["action"].startswith("executed") for d in decisions)
    assert eng._dq_blocking() == {"history_read"}


def test_a_re_read_that_succeeds_restores_the_history_and_lifts_the_refusal(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    broken.on = False                                          # the store reads again (a fix, a restored file)
    decisions = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=36))
    assert eng.history.backfill["read_error"] is None and not eng._dq_blocking()
    assert len(eng.bars_1m) > 10_000                           # the release minutes are back in the window
    assert decisions and any(not d["action"].startswith("history") for d in decisions)


def test_a_store_read_that_keeps_failing_is_retried_and_stays_refused(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    first = broken.refused
    _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=2))
    assert broken.refused > first                              # re-read every REFILL_EVERY of engine clock
    assert "history_read" in eng._dq_blocking()


def test_a_failed_release_reload_blocks_entries_like_a_failed_warm_start(tmp_path, monkeypatch):
    _seed(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    assert not eng._dq_blocking()
    hole = pd.Timestamp("2025-03-11 13:00", tz="UTC")          # two in-session hours the release should heal
    b = eng.bars_1m
    eng.bars_1m = b[(b["ts_utc"] < hole) | (b["ts_utc"] >= hole + pd.Timedelta(hours=2))].reset_index(drop=True)
    eng.history.cutover = LIVE_FROM
    eng.history.gaps = {"15m": {"from": hole.isoformat()}}
    broken = Broken(monkeypatch)
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1))
    assert broken.refused >= 1 and "history_read" in eng._dq_blocking()


def test_the_engine_health_check_fails_and_names_the_failed_history_read(tmp_path, monkeypatch):
    _seed(tmp_path)
    Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(minutes=20))
    c = health.check_engine(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "history_read" in c.reason and "store read of the engine's history failed" in c.reason


# ------------------------------------------------------------------------------------------- a short read
def test_a_short_store_read_refuses_entries_history_short_until_the_window_holds_the_minimum(tmp_path):
    _seed(tmp_path, start=LIVE_FROM - pd.Timedelta(days=2))   # ~2 800 minutes stored
    eng = _engine(tmp_path, history_min_bars=4000)
    eng.warm_start(LIVE_FROM)
    assert "history_short" in eng._dq_blocking()
    short = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=12))
    assert short and {d["action"] for d in short} == {"history_short:15m"}
    shown = _state(tmp_path)["history_backfill"]["read_short"]
    assert shown["min"] == 4000 and 2800 < shown["bars"] < 4000      # the stored minutes and the live ones since
    later = _feed(eng, LIVE_FROM + pd.Timedelta(hours=12), LIVE_FROM + pd.Timedelta(hours=40))
    assert not eng._dq_blocking() and _state(tmp_path)["history_backfill"]["read_short"] is None
    assert any(not d["action"].startswith("history") for d in later)       # the live minutes reached the minimum


def test_a_short_read_on_a_failed_store_is_a_read_error_not_a_short_read(tmp_path, monkeypatch):
    _seed(tmp_path)
    Broken(monkeypatch)
    eng = _engine(tmp_path, history_min_bars=4000)
    eng.warm_start(LIVE_FROM)
    assert eng._dq_blocking() == {"history_read"}


# ------------------------------------------------------------------------------------------- a normal read
def test_a_normal_store_read_with_the_minimum_set_changes_nothing(tmp_path):
    _seed(tmp_path)
    eng = _engine(tmp_path, history_min_bars=4000)
    assert eng.warm_start(LIVE_FROM) > 10_000
    assert not eng._dq_blocking()
    decisions = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=36))
    assert decisions and not any(d["action"].startswith("history") for d in decisions)
    hb = _state(tmp_path)["history_backfill"]
    assert hb["read_error"] is None and hb["read_short"] is None


def test_the_other_books_never_carry_the_gold_warm_start_minimum():
    from goldbot.engine.books import BookSpec, book_cfg
    cfg = EngineConfig(account_id="icm-probe", broker_name="icm", history_min_bars=28_800)
    spec = BookSpec(key="EURUSD", symbol="EURUSD", store_symbol="EURUSD", magic_block=1, digits=5, point=0.00001,
                    contract_size=100_000.0)
    assert book_cfg(cfg, spec).history_min_bars == 0          # their warm-up is book_backfill's (books.warming)


def test_the_service_passes_the_warm_start_minimum_to_weekday_gold_only():
    from goldbot.config import load_settings
    from goldbot.ops.services.engine import warm_start_min_bars
    s = load_settings()
    assert s.engine.warm_start_min_bars == 120 * 240           # 120 bars of 4h, the longest decision timeframe
    weekday = type("A", (), {"weekend_only": False, "sessions": "broker"})()
    weekend = type("A", (), {"weekend_only": True, "sessions": "always_open"})()
    assert warm_start_min_bars(s, weekday) == 28_800 and warm_start_min_bars(s, weekend) == 0


# ------------------------------------------------------------------------------------------- key zones (display)
def test_an_unreadable_key_zone_cache_is_read_again_at_the_next_build(tmp_path, monkeypatch):
    """Display only (engine/key_zones.py): a failed cache read never blocks entries, but it is no longer final: the
    next build reads the cache again, so one bad read does not leave the chart on the engine's short history until a
    restart (the duckdb 2.0 test saw history_days 52.4 instead of > 700)."""
    broken = Broken(monkeypatch)
    w = key_zones.KeyZoneWorker("icm-demo", "XAUUSD", Store(tmp_path / "data"))
    w.refresh(LIVE_FROM)
    assert broken.refused == 1 and not w.loaded
    broken.on = False
    w.refresh(LIVE_FROM)
    assert w.loaded
