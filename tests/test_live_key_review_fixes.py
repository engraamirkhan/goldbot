"""Review findings on 7b439c30 (ADR 0091): data-engineer, quant and trading-safety rounds.

- Every per-account bars_1m_live reader filters by the gold symbol: an account's non-gold books write the same
  source (books.live_key = account, store symbol), so a gold read without its symbol would mix them in (data
  blocker, trade chart). The chart unions the account's own key over the broker's legacy key, as long_history does.
- The engine starts are read as engine_boot rows only, and a dq_events read that fails never breaks a bar read: the
  bars read with no legacy bound, the proof fails closed (`unproven_writer`).
- engine_boot (severity info) is not a data-quality warning; a duplicate engine refused for more than an hour is
  told again; the 2026-10-26 quarantine script, which writes the legacy key, refuses once the per-account key is live.
Synthetic rows only; every store and state dir is a temp copy."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from goldbot.data import account_bars as AB
from goldbot.data.store import Store
from goldbot.engine import history
from goldbot.ops import health, long_history
from tests.test_health import make_ctx

T0 = pd.Timestamp("2026-11-23 10:00", tz="UTC")
A = "a" * 32


def _bars(n: int, price: float, start: pd.Timestamp = T0) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="min")
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
                         **{f"{s}_{c}": price + (0.1 if s == "ask" else 0.0) for s in ("bid", "ask")
                            for c in ("open", "high", "low", "close")},
                         "tick_count": 1, "spread_mean": 0.1, "spread_max": 0.1})


def _boot(store: Store, account: str, at: pd.Timestamp, locked: bool = True, symbol: str = "XAUUSD") -> None:
    ev = pd.DataFrame({"ts_utc": [at], "check": [history.BOOT_CHECK], "severity": ["info"],
                       "detail": [json.dumps({"writer_lock": locked})], "writer": [A]})
    store.append("dq_events", ev, source=account, symbol=symbol, dedupe=False)


def _mixed_store(tmp_path: Path) -> Store:
    """icm-probe: gold minutes on its own key (2000) and the legacy key (1990, earlier minutes), and a non-gold book
    (EURUSD, 1.08) under the same source on its own store symbol, overlapping every gold minute."""
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _bars(30, 1990.0, T0 - pd.Timedelta(minutes=30)), source="icm", symbol="XAUUSD")
    store.append("bars_1m_live", _bars(30, 2000.0).assign(writer=A), source="icm-probe", symbol="XAUUSD")
    store.append("bars_1m_live", _bars(90, 1.08, T0 - pd.Timedelta(minutes=30)).assign(writer=A),
                 source="icm-probe", symbol="EURUSD.store")
    return store


# ---------------------------------------------------------------- 1: every reader filters by the gold symbol
def test_the_gold_trade_chart_never_mixes_in_a_non_gold_book_of_the_account_and_unions_both_live_keys(
        tmp_path, monkeypatch):
    from goldbot.api import trade_chart
    store = _mixed_store(tmp_path)
    monkeypatch.setattr(trade_chart, "_account_terms", lambda account: ("icm", "XAUUSD"))
    bars, feed = trade_chart._bars(store, "icm-probe", "1m", T0 - pd.Timedelta(minutes=30), T0 + pd.Timedelta(minutes=30))
    closes = [b.c for b in bars]
    assert feed == "broker" and len(bars) == 60                         # legacy then own: the deploy is spanned
    assert set(closes[:30]) == {1990.0} and set(closes[30:]) == {2000.0}   # never 1.08


def test_account_bars_and_minute_versions_read_only_the_gold_symbol_of_the_account(tmp_path):
    """account_bars_1m (the learner, har, tv_ideas) and minute_versions (feature snapshots, the arm read)."""
    store = _mixed_store(tmp_path)
    as_of = T0 + pd.Timedelta(hours=1)
    got = AB.account_bars_1m(store, account_id="icm-probe", broker="icm", symbol="XAUUSD", start=None, end=None,
                             as_of=as_of)
    assert len(got) == 60 and got["bid_close"].min() >= 1990.0
    v = AB.minute_versions(store, account_id="icm-probe", broker="icm", symbol="XAUUSD", start=None, end=None,
                           as_of=as_of, reference=None)
    assert len(v) == 60 and v["bid_close"].min() >= 1990.0


def test_the_long_history_reads_only_the_gold_symbol_of_the_account(tmp_path):
    store = _mixed_store(tmp_path)
    keys = (("icm", "XAUUSD"), ("icm-probe", "XAUUSD"))
    got = long_history.store_minutes(store, "XAUUSD", keys, T0 - pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=1))
    assert len(got) == 60 and got["bid_close"].min() >= 1990.0


# ---------------------------------------------------------------- 2: the boot read, cached, never breaking a bar read
def test_the_engine_starts_are_read_from_their_rows_only_and_the_cut_over_is_cached(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    other = pd.DataFrame({"ts_utc": [T0], "check": ["broker_bar_late"], "severity": ["warning"], "detail": ["x"]})
    store.append("dq_events", other, source="icm-probe", symbol="XAUUSD", dedupe=False)
    assert AB.engine_boots(store, "icm-probe").empty                    # no writer column anywhere: no starts
    _boot(store, "icm-probe", T0)
    boots = AB.engine_boots(store, "icm-probe")
    assert list(boots["writer"]) == [A] and list(boots["locked"]) == [True]
    AB.CUTOVER_CACHE.clear()
    assert AB.cached_cutover(store, "icm-probe") == T0
    monkeypatch.setattr(AB, "engine_boots", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read again")))
    assert AB.cached_cutover(store, "icm-probe") == T0                 # a first locked start never moves


def test_an_unreadable_dq_events_never_breaks_the_bar_read_and_fails_the_proof_closed(tmp_path, monkeypatch):
    from goldbot.research import arm_integrity as I
    store = _mixed_store(tmp_path)
    AB.CUTOVER_CACHE.clear()
    real = Store.read

    def broken(self: Store, table: str, **kw: Any) -> pd.DataFrame:
        if table == "dq_events":
            raise OSError("corrupt parquet footer")
        return real(self, table, **kw)
    monkeypatch.setattr(Store, "read", broken)
    got = AB.account_bars_1m(store, account_id="icm-probe", broker="icm", symbol="XAUUSD", start=None, end=None,
                             as_of=T0 + pd.Timedelta(hours=1))
    assert len(got) == 60                                               # legacy read with no bound, own wins
    m = I.AccountMinutes(pd.DataFrame(columns=["ts_utc", "rank"]), boots_unreadable=True)
    assert m.boots_unreadable


# ---------------------------------------------------------------- 4: a duplicate past an hour is told again
def test_a_duplicate_engine_refused_for_more_than_an_hour_is_told_again(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    now = pd.Timestamp.now("UTC")
    first = now.timestamp() - 2 * 3600
    (state / "engine_duplicate_icm-demo.json").write_text(json.dumps({"at": now.timestamp() - 5, "first": first,
                                                                      "holder_pid": 4242}))
    got = health.check_duplicate_engine(make_ctx(state, now=now), "icm-demo")
    checks = got if isinstance(got, list) else [got]
    names = {c.name: c for c in checks if c is not None}
    assert names["duplicate_engine:icm-demo"].status == "warn"
    late = names["duplicate_engine_1h:icm-demo"]
    assert late.status == "warn" and "4242" in late.reason and health._alerting(late.name, late.status)


def test_a_refusal_keeps_the_first_refusal_time_while_the_refusals_continue(tmp_path):
    from goldbot.ops.services import engine as svc
    for _ in range(2):
        with pytest.raises(SystemExit):
            svc.refuse_duplicate("icm-demo", "held", 4242, state_dir=tmp_path)
    d = json.loads((tmp_path / "engine_duplicate_icm-demo.json").read_text())
    assert d["first"] <= d["at"] <= time.time()
    d["at"] = d["first"] = time.time() - 3 * 3600                       # an old episode: a new one starts afresh
    (tmp_path / "engine_duplicate_icm-demo.json").write_text(json.dumps(d))
    with pytest.raises(SystemExit):
        svc.refuse_duplicate("icm-demo", "held", 4242, state_dir=tmp_path)
    assert json.loads((tmp_path / "engine_duplicate_icm-demo.json").read_text())["first"] > time.time() - 60


# ---------------------------------------------------------------- 5: engine_boot is not a data-quality warning
def test_engine_boot_records_are_not_counted_as_data_quality_warnings(tmp_path):
    from goldbot.ops.checks import feeds
    root = tmp_path / "data"
    store = Store(root)
    _boot(store, "icm-demo", pd.Timestamp.now("UTC") - pd.Timedelta(minutes=5))
    ctx = make_ctx(tmp_path / "state", now=pd.Timestamp.now("UTC"))
    assert ctx.settings is not None
    ctx.settings = ctx.settings.model_copy(update={"data_root": str(root)})
    feeds._DQ.clear()
    c = feeds.check_data_quality(ctx)
    assert c.status == "ok" and "(0 warnings)" in c.reason, c.reason


# ---------------------------------------------------------------- 6: the 10-26 script refuses on the new key
def _script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "quarantine_tz_ticks_2026_10_26.py"
    spec = importlib.util.spec_from_file_location("quarantine_tz_script_keys", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_2026_10_26_quarantine_script_refuses_once_an_account_has_a_locked_engine_start(tmp_path):
    mod = _script()
    store = Store(tmp_path / "data")
    mod.refuse_after_key_change(store)                                  # no start recorded: it may run
    _boot(store, "icm-probe", T0, locked=False)
    mod.refuse_after_key_change(store)                                  # an unlocked start proves no key change
    _boot(store, "icm-demo", T0)
    with pytest.raises(SystemExit, match="per-account live key"):
        mod.refuse_after_key_change(store)
