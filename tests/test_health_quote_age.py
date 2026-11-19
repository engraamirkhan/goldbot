"""quote_age:<account> (incident 2026-11-18/19, vantage-demo: the engine re-read a gold quote up to 105 min old while
gold's session was open and health only warned on engine:, which is not told to the owner). Design rule: a traded
symbol whose quote is older than the stale-tick limit while its session is open is told on Telegram (NOTIFY_ON_WARN).
Traded = gold's book (the primary) and every book holding a position: the quotes software exits run on."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from goldbot.config import load_settings
from goldbot.ops import health
from goldbot.ops.health import AccountRef, HealthContext

NOW = pd.Timestamp("2026-11-03 14:00", tz="UTC")       # a Tuesday afternoon


def _ctx(tmp_path: Path) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=NOW, settings=load_settings(),
                         accounts=[AccountRef(account_id="vantage-demo")], get_secret=lambda k: None,
                         disk_usage=lambda p: (100e9, 50e9, 50e9))


def _book(age: float, *, primary: bool = False, closed: bool = False, open_ids: list[int] | None = None) -> dict:
    return {"primary": primary, "last_tick_age_s": age, "market_closed": closed, "open": open_ids or []}


def _engine(tmp_path: Path, books: dict, **kw: object) -> None:
    e = {"account_id": "vantage-demo", "ts": NOW.timestamp(), "books": books, "symbols": {}, **kw}
    (tmp_path / "engine_vantage-demo.json").write_text(json.dumps(e))


def test_gold_quote_older_than_the_stale_limit_in_an_open_session_warns_and_tells_the_owner(tmp_path):
    _engine(tmp_path, {"XAUUSD": _book(6300.0, primary=True), "USINDEX": _book(10800.0, closed=True)})
    c = health.check_quote_age(_ctx(tmp_path), "vantage-demo")
    assert c is not None and c.name == "quote_age:vantage-demo" and c.status == "warn"
    assert "XAUUSD" in c.reason and "USINDEX" not in c.reason
    assert health._alerting(c.name, c.status)                  # NOTIFY_ON_WARN: Telegram tells the owner


def test_a_fresh_gold_quote_and_a_closed_session_are_ok(tmp_path):
    _engine(tmp_path, {"XAUUSD": _book(0.4, primary=True), "USINDEX": _book(10800.0, closed=True, open_ids=[7])})
    c = health.check_quote_age(_ctx(tmp_path), "vantage-demo")
    assert c is not None and c.status == "ok"


def test_a_symbol_holding_a_position_with_an_old_quote_warns(tmp_path):
    _engine(tmp_path, {"XAUUSD": _book(0.4, primary=True), "XAUJPY": _book(600.0, open_ids=[42])})
    c = health.check_quote_age(_ctx(tmp_path), "vantage-demo")
    assert c is not None and c.status == "warn" and "XAUJPY" in c.reason


def test_a_quiet_symbol_holding_nothing_is_not_told(tmp_path):
    _engine(tmp_path, {"XAUUSD": _book(0.4, primary=True), "COPPER-C": _book(600.0)})
    c = health.check_quote_age(_ctx(tmp_path), "vantage-demo")
    assert c is not None and c.status == "ok"


def test_a_single_symbol_engine_adds_no_quote_age_check(tmp_path):
    (tmp_path / "engine_vantage-demo.json").write_text(json.dumps({"account_id": "vantage-demo", "ts": NOW.timestamp()}))
    assert health.check_quote_age(_ctx(tmp_path), "vantage-demo") is None


def test_the_registry_runs_it_per_multi_book_account(tmp_path):
    _engine(tmp_path, {"XAUUSD": _book(0.4, primary=True)}, ts=time.time())
    names = {c.name for c in health.run_checks(_ctx(tmp_path)).checks}
    assert "quote_age:vantage-demo" in names
