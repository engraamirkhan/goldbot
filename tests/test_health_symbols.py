"""Per-symbol health on a multi-instrument account (lane L4; goldbot/ops/checks/engine.py `check_symbols`).

Design rules (UI spec 2026-11-02 and the plan): amber for a stale feed or "terms not loaded: entries refused, exits
still close"; grey for a closed market or a disabled symbol; red only for a spent cap, which is the gate doing its job
(no health fault). A single-symbol engine reports nothing new: today's health report keeps its names."""
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
                         accounts=[AccountRef(account_id="icm-probe")], get_secret=lambda k: None,
                         disk_usage=lambda p: (100e9, 50e9, 50e9))


def _engine(tmp_path: Path, **kw: object) -> None:
    e = {"account_id": "icm-probe", "ts": NOW.timestamp(), **kw}
    (tmp_path / "engine_icm-probe.json").write_text(json.dumps(e))


def test_a_single_symbol_engine_adds_no_symbols_check(tmp_path):
    _engine(tmp_path)
    assert health.check_symbols(_ctx(tmp_path), "icm-probe") is None
    assert health.check_symbols(_ctx(tmp_path), "icm-nothing") is None        # no engine file: engine: reports it


def test_every_symbol_able_to_enter_or_closed_is_ok_and_counted(tmp_path):
    _engine(tmp_path, symbols={"XAUUSD": {"state": "can_enter"}, "US500": {"state": "market_closed"},
                               "XNGUSD": {"state": "disabled"}, "DE40": {"state": "group_cap"}})
    c = health.check_symbols(_ctx(tmp_path), "icm-probe")
    assert c is not None and c.name == "symbols:icm-probe" and c.status == "ok"
    assert "1 of 4 can enter" in c.reason


def test_missing_terms_or_a_stale_feed_warns_naming_the_symbols(tmp_path):
    _engine(tmp_path, symbols={"XAUUSD": {"state": "can_enter"}, "USDJPY": {"state": "terms_missing"},
                               "COPPER-C": {"state": "stale", "text": "Feed stale 3 min"}})
    c = health.check_symbols(_ctx(tmp_path), "icm-probe")
    assert c is not None and c.status == "warn"
    assert "USDJPY" in c.reason and "entries refused, exits still close" in c.reason and "COPPER" in c.reason


def test_the_registry_runs_it_per_account_only_when_the_engine_reports_symbols(tmp_path):
    _engine(tmp_path)
    names = {c.name for c in health.run_checks(_ctx(tmp_path)).checks}
    assert "symbols:icm-probe" not in names
    _engine(tmp_path, symbols={"XAUUSD": {"state": "can_enter"}}, ts=time.time())
    names = {c.name for c in health.run_checks(_ctx(tmp_path)).checks}
    assert "symbols:icm-probe" in names
