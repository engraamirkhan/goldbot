"""The observe lane's collector (ADR 0063, goldbot/ops/observe.py): it reads the observe-only account's XAUUSD247 and its
weekday gold through the bridge, read-only, and records M1 bars, tick and spread samples and the symbol terms into the
local store and state (never published: data-local-only). Faster tick samples inside the weekend window; it keeps
running all week, outlives any bridge error and never calls an order method. Health shows the lane as observe only."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import ObserveSettings, load_settings
from goldbot.data.store import Store
from goldbot.execution.bridge import BridgeUnavailable
from goldbot.execution.broker import SymbolInfo, Tick
from goldbot.ops import observe
from goldbot.ops.checks._base import ObserveLaneRef
from goldbot.ops.health import HealthContext

OBS = "vantage-live-obs"
SAT = pd.Timestamp("2026-10-31 12:00", tz="UTC")       # Saturday: weekday gold closed, XAUUSD247 quoting
WED = pd.Timestamp("2026-10-28 12:00", tz="UTC")


class _Terminal:
    """A read-only fake of the observe bridge: bars every minute, a quote, the symbol terms. Any order call fails."""

    def __init__(self, names: tuple[str, ...] = ("XAUUSD", "XAUUSD247", "XAUUSD.r", "EURUSD")) -> None:
        self.names, self.down, self.contract = names, False, 1.0
        self.reads: list[str] = []

    def _read(self, what: str) -> None:
        if self.down:
            raise BridgeUnavailable(f"bridge {what}: ConnectionError")
        self.reads.append(what)

    def symbols_get(self, pattern: str = "*") -> list[str]:
        self._read("symbols_get")
        return [n for n in self.names if fnmatch.fnmatchcase(n, pattern)]

    def get_bars_range(self, symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        self._read("get_bars_range")
        assert tf == "1m"
        ts = pd.date_range(start.ceil("1min"), end.floor("1min"), freq="1min")
        return pd.DataFrame({"ts_utc": ts, "open": 2400.0, "high": 2401.0, "low": 2399.0, "close": 2400.5,
                             "tick_count": 10, "spread": 30})

    def last_tick(self, symbol: str) -> Tick:
        self._read("last_tick")
        return Tick(ts_utc=self.now, bid=2400.0, ask=2400.35 if symbol == "XAUUSD247" else 2400.2)

    def symbol_info(self, symbol: str) -> SymbolInfo:
        self._read("symbol_info")
        return SymbolInfo(name=symbol, digits=2, point=0.01, contract_size=self.contract, volume_min=0.01,
                          volume_step=0.01, volume_max=100, stops_level_points=20, filling_modes=["IOC"],
                          trade_allowed=False, swap_mode=1, swap_long=-6.5, swap_short=2.1)

    def __getattr__(self, name: str) -> Any:
        if name in ("place_order", "modify", "close"):
            raise AssertionError(f"the collector called {name}")
        raise AttributeError(name)

    now = SAT


def _collector(tmp_path: Path, term: _Terminal, **cfg: Any) -> observe.Collector:
    return observe.Collector(OBS, ("XAUUSD247", "XAUUSD"), term, Store(tmp_path / "data"), tmp_path / "state",
                             ObserveSettings(**cfg))


def _step(c: observe.Collector, term: _Terminal, now: pd.Timestamp) -> observe.ObserveStatus:
    term.now = now
    return c.step(now)


def test_the_settings_carry_the_observe_lane_with_the_weekend_window():
    s = load_settings().observe
    assert s.weekend_tick_every_s < s.tick_every_s
    assert s.weekend_window.inside(SAT) and not s.weekend_window.inside(WED)


@pytest.mark.parametrize("want, names, got", [
    ("XAUUSD247", ("XAUUSD", "XAUUSD247", "XAUUSD+"), "XAUUSD247"),
    ("XAUUSD", ("XAUUSD", "XAUUSD247", "XAUUSD+"), "XAUUSD"),     # the exact name first
    ("XAUUSD", ("XAUUSD247", "XAUUSD+"), "XAUUSD+"),               # a broker suffix, never the 24/7 symbol
    ("XAUUSD", ("XAUUSD247",), None),
    ("XAUUSD", ("XAUUSD.r", "XAUUSD+"), "XAUUSD+"),                # several suffixes: the first by name
])
def test_a_store_symbol_resolves_to_the_terminal_name_without_ever_taking_another_instrument(want, names, got):
    assert observe.resolve_symbol(names, want) == got


def test_a_step_records_closed_m1_bars_tick_samples_and_the_symbol_terms_into_the_local_store(tmp_path):
    term = _Terminal()
    c = _collector(tmp_path, term, backfill_hours=1)
    st = _step(c, term, SAT)
    store = Store(tmp_path / "data")
    bars = store.read("observe_bars_1m", source=OBS, symbol="XAUUSD247")
    assert len(bars) == 60 and bars["ts_utc"].max() == pd.Timestamp("2026-10-31 11:59", tz="UTC")   # closed only
    assert (bars["available_utc"] >= bars["ts_utc"] + pd.Timedelta(minutes=1)).all()
    assert len(store.read("observe_bars_1m", source=OBS, symbol="XAUUSD")) == 60
    ticks = store.read("observe_ticks", source=OBS, symbol="XAUUSD247")
    assert len(ticks) == 1 and ticks["spread"].iloc[0] == pytest.approx(0.35)
    specs = [json.loads(x) for x in (tmp_path / "state" / "observe" / OBS / "specs.jsonl").read_text().splitlines()]
    assert {(s["store_symbol"], s["broker_symbol"]) for s in specs} == {("XAUUSD247", "XAUUSD247"), ("XAUUSD", "XAUUSD")}
    assert all(s["contract_size"] == 1.0 and s["stops_level_points"] == 20 and s["swap_long"] == -6.5 for s in specs)
    assert st.connected and st.symbols["XAUUSD247"].spread == pytest.approx(0.35)
    saved = observe.ObserveStatus.model_validate_json(observe.status_path(tmp_path / "state", OBS).read_text())
    assert saved.observe_only and saved.symbols["XAUUSD"].last_bar_utc == pd.Timestamp("2026-10-31 11:59", tz="UTC")


def test_the_next_steps_add_only_new_bars_and_an_unchanged_spec_is_not_written_again(tmp_path):
    term = _Terminal()
    c = _collector(tmp_path, term, backfill_hours=1)
    _step(c, term, SAT)
    _step(c, term, SAT + pd.Timedelta(minutes=5))
    assert len(Store(tmp_path / "data").read("observe_bars_1m", source=OBS, symbol="XAUUSD247")) == 60   # buffered
    c.flush()
    bars = Store(tmp_path / "data").read("observe_bars_1m", source=OBS, symbol="XAUUSD247")
    assert len(bars) == 65 and bars["ts_utc"].is_unique
    specs = (tmp_path / "state" / "observe" / OBS / "specs.jsonl").read_text().splitlines()
    assert len(specs) == 2                                         # spec_every_s not reached yet
    term.contract = 100.0
    _step(c, term, SAT + pd.Timedelta(hours=2))
    assert len((tmp_path / "state" / "observe" / OBS / "specs.jsonl").read_text().splitlines()) == 4   # changed


def test_a_restart_resumes_after_the_last_stored_bar(tmp_path):
    term = _Terminal()
    _step(_collector(tmp_path, term, backfill_hours=1), term, SAT)
    _step(_collector(tmp_path, term, backfill_hours=1), term, SAT + pd.Timedelta(minutes=3))
    bars = Store(tmp_path / "data").read("observe_bars_1m", source=OBS, symbol="XAUUSD247")
    assert len(bars) == 63 and bars["ts_utc"].is_unique


def test_ticks_are_sampled_faster_inside_the_weekend_window_and_still_all_week(tmp_path):
    term = _Terminal()
    c = _collector(tmp_path, term, tick_every_s=30, weekend_tick_every_s=5)
    _step(c, term, SAT)
    assert c.seconds_to_next(SAT) == pytest.approx(5)
    term2 = _Terminal()
    c2 = _collector(tmp_path / "wk", term2, tick_every_s=30, weekend_tick_every_s=5)
    _step(c2, term2, WED)
    assert c2.seconds_to_next(WED) == pytest.approx(30)


def test_a_bridge_outage_is_recorded_never_raised_and_the_next_step_recovers(tmp_path):
    term = _Terminal()
    c = _collector(tmp_path, term, backfill_hours=1)
    term.down = True
    st = _step(c, term, SAT)
    assert not st.connected and "BridgeUnavailable" in (st.error or "")
    term.down = False
    st = _step(c, term, SAT + pd.Timedelta(minutes=2))
    assert st.connected and st.error is None


def test_an_absent_symbol_is_reported_and_the_others_are_still_collected(tmp_path):
    term = _Terminal(names=("XAUUSD247",))
    st = _step(_collector(tmp_path, term, backfill_hours=1), term, SAT)
    assert st.connected and st.symbols["XAUUSD"].broker_symbol is None and "not on" in (st.symbols["XAUUSD"].error or "")
    assert st.symbols["XAUUSD247"].last_bar_utc is not None


def test_the_collector_refuses_an_account_that_is_not_observe_only(tmp_path, capsys):
    assert observe.main(["once", "--account", "icm-demo", "--state", str(tmp_path)]) == 2
    assert "observe_only" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ health
def _ctx(tmp_path: Path, now: pd.Timestamp, installed: bool = True) -> HealthContext:
    return HealthContext(state_dir=tmp_path / "state", now=now, get_secret=lambda k: None,
                         observe_lanes=[ObserveLaneRef(account_id=OBS, installed=installed, symbol="XAUUSD247")])


def test_health_shows_the_observe_lane_connected_with_bar_age_and_spread_and_never_as_trading(tmp_path):
    term = _Terminal()
    _step(_collector(tmp_path, term, backfill_hours=1), term, SAT)
    chk = observe.check_observe(_ctx(tmp_path, SAT + pd.Timedelta(seconds=30)))
    assert chk.status == "ok" and chk.name == "observe"
    assert "observe only, never trades" in chk.reason and "connected" in chk.reason
    assert "XAUUSD247 last bar" in chk.reason and "spread 0.35" in chk.reason
    assert "trading" not in chk.reason.replace("never trades", "")


def test_health_warns_when_the_collector_is_silent_or_the_24_7_bars_stop(tmp_path):
    assert observe.check_observe(_ctx(tmp_path, SAT)).status == "warn"           # no status yet
    term = _Terminal()
    _step(_collector(tmp_path, term, backfill_hours=1), term, SAT)
    assert observe.check_observe(_ctx(tmp_path, SAT + pd.Timedelta(minutes=10))).status == "warn"   # status stale
    term.down = True
    c = _collector(tmp_path, term, backfill_hours=1)
    _step(c, term, SAT + pd.Timedelta(minutes=30))
    chk = observe.check_observe(_ctx(tmp_path, SAT + pd.Timedelta(minutes=30)))
    assert chk.status == "warn" and "not connected" in chk.reason


def test_health_says_not_installed_until_the_lane_has_its_bridge(tmp_path):
    chk = observe.check_observe(_ctx(tmp_path, SAT, installed=False))
    assert chk.status == "ok" and "not installed" in chk.reason


def test_health_reads_a_status_a_newer_collector_wrote_with_an_extra_field(tmp_path):
    """Persisted record (AGENT_STANDARDS S12): a status file from a newer release, with fields this one does not know,
    still reads on health (tolerant load_record_json), so a rollback never turns the lane's line into an error."""
    term = _Terminal()
    _step(_collector(tmp_path, term, backfill_hours=1), term, SAT)
    f = observe.status_path(tmp_path / "state", OBS)
    raw = json.loads(f.read_text())
    raw["new_field"] = 1
    raw["symbols"]["XAUUSD247"]["new_field"] = "x"
    f.write_text(json.dumps(raw))
    chk = observe.check_observe(_ctx(tmp_path, SAT + pd.Timedelta(seconds=30)))
    assert chk.status == "ok" and "XAUUSD247 last bar" in chk.reason


@pytest.mark.parametrize("names", [None, RuntimeError("IPC timeout")])
def test_symbol_selection_on_a_terminal_not_logged_in_yet_logs_and_continues(names, caplog):
    """Trading-safety LOW (PR 236): symbols_get None (no login yet) or an IPC error never ends the bridge's start."""
    class _B:
        def symbols_get(self, pattern: str = "*") -> Any:
            if isinstance(names, Exception):
                raise names
            return names

    class _Api:
        def symbol_select(self, name: str, on: bool) -> bool:
            raise AssertionError("nothing to select")
    assert observe.select_symbols(_Api(), _B(), ("XAUUSD247", "XAUUSD")) == []
    assert any("not selected" in r.getMessage() for r in caplog.records)
