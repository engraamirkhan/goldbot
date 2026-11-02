"""Incident 2026-11-02: an instrument whose swap the engine cannot price (`swap_unknown`, swap modes 2/3 in an
unknown currency) refuses entries and still closes, and says so in its own words on the engine's report, the API and
health: "swap not priced: entries refused, exits still close", never "terms not loaded" (which sent the reader to
the wrong cause: the five index CFDs had their terms)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pandas as pd

from goldbot.api.instruments_view import entry_symbols
from goldbot.api.schema import InstrumentState
from goldbot.config import load_settings
from goldbot.engine import books
from goldbot.ops import health
from goldbot.ops.checks import engine as engine_checks
from goldbot.ops.health import AccountRef, HealthContext
from tests.test_blocked_books_quiet import _engine, _others

SWAP_WORDS = "swap not priced: entries refused, exits still close"


def test_a_book_with_an_unpriced_swap_reports_its_own_state_and_words(tmp_path):
    eng, _ = _engine(tmp_path, blocked=None)
    swap, missing = _others(eng)[:2]
    eng.books[swap].terms_problem = "swap_unknown"
    eng.books[missing].terms_problem = "instrument_terms_missing"
    pub = books.published(eng)["symbols"]
    assert pub[swap]["state"] == "swap_unknown" and pub[swap]["text"] == SWAP_WORDS
    assert pub[missing]["state"] == "terms_missing" and pub[missing]["text"] == books.STATE_TEXT["terms_missing"]
    assert books.STATE_TEXT["swap_unknown"] == SWAP_WORDS


def test_the_api_carries_the_state():
    assert "swap_unknown" in get_args(InstrumentState)
    rows = entry_symbols({"symbols": {"US500": {"state": "swap_unknown", "text": SWAP_WORDS}}})
    assert [(r.state, r.text) for r in rows] == [("swap_unknown", SWAP_WORDS)]


def test_health_warns_in_the_swap_words_and_keeps_terms_not_loaded_for_missing_terms(tmp_path: Path):
    now = pd.Timestamp("2026-11-03 14:00", tz="UTC")
    e = {"account_id": "icm-probe", "ts": now.timestamp(),
         "symbols": {"XAUUSD": {"state": "can_enter"}, "US500": {"state": "swap_unknown", "text": SWAP_WORDS},
                     "USDJPY": {"state": "terms_missing"}}}
    (tmp_path / "engine_icm-probe.json").write_text(json.dumps(e))
    ctx = HealthContext(state_dir=tmp_path, now=now, settings=load_settings(),
                        accounts=[AccountRef(account_id="icm-probe")], get_secret=lambda k: None,
                        disk_usage=lambda p: (100e9, 50e9, 50e9))
    c = health.check_symbols(ctx, "icm-probe")
    assert c is not None and c.status == "warn" and "1 of 3 can enter" in c.reason
    assert f"US500: {SWAP_WORDS}" in c.reason
    assert "USDJPY: terms not loaded: entries refused, exits still close" in c.reason
    assert engine_checks.SYMBOL_WARN_WORDS["swap_unknown"] == SWAP_WORDS
