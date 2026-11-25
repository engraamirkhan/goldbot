"""config/history_sources.yaml (D1, lane W1-E) keys exactly the owner's 25 instruments, and loads lazily."""
import importlib.util
import sys
from pathlib import Path

from goldbot.data import era_quality as eq
from tests.test_owner_instrument_list import OWNER_25

ROOT = Path(__file__).resolve().parents[1]


def test_the_map_keys_are_the_owner_25():
    assert set(eq.load_history_sources().instruments) == set(OWNER_25)


def test_every_owner_instrument_has_a_fix_rule():
    hs = eq.load_history_sources()
    assert all(s.fix is not None for s in hs.instruments.values())


def test_one_dst_rule_per_histdata_era_covers_every_year():
    hs = eq.load_history_sources()
    rules = [hs.dst_rule(y) for y in range(2000, 2026)]
    assert set(rules) <= {"ny", "eet-7"}
    switches = sum(a != b for a, b in zip(rules, rules[1:]))
    assert switches <= 1                                               # one era change at most, never back


def test_a_broken_map_does_not_break_the_gold_pull_at_import(tmp_path, monkeypatch):
    """scripts/dukascopy_year.py runs the weekly gold refresh: the map is read only for a mapped symbol."""
    bad = tmp_path / "history_sources.yaml"
    bad.write_text("version: 1\ninstruments: {XAUUSD: {nope: 1}}\n")
    monkeypatch.setattr(eq, "DEFAULT_HISTORY_SOURCES", bad)
    eq.history_sources.cache_clear()
    try:
        spec = importlib.util.spec_from_file_location("dy_lazy", ROOT / "scripts" / "dukascopy_year.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["dy_lazy"] = mod
        spec.loader.exec_module(mod)
        assert mod.instrument("xauusd").code == "XAUUSD"
    finally:
        eq.history_sources.cache_clear()
