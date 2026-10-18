"""research_pass.py end to end for the course families on synthetic bars laid out like data-v1: each family's trial
is one rule-only screen (screening) with the lot fit on its candidates, H-37's screen carries the cost-gate reading,
and H-40 is ONE discovery over exactly its 21 pre-registered candidate groups (K_eff 21)."""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.quality import check_bars
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks

pytestmark = pytest.mark.integration

_spec = importlib.util.spec_from_file_location("research_pass_course",
                                               Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py")
assert _spec is not None and _spec.loader is not None
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)
H40_CONFIG = ('{"survivor_unit":"group","group_top_k":3,"top_k":39,"max_selected":39,"freq_threshold":0.6,'
              '"stability_threshold":0.6,"pool":"candidate"}')


@pytest.fixture(scope="module")
def release_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("release")
    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2023-10-01", "2025-10-01", ticks_per_minute=1, seed=11)))
    b1["source"] = "synthetic"
    years = pd.DatetimeIndex(b1["ts_utc"]).year
    for y in sorted(set(years)):
        b1[years == y].to_parquet(d / f"xauusd_1m_dukascopy_{y}.parquet", index=False)
    return d


def _run(release_dir: Path, tmp_path: Path, monkeypatch, *extra: str) -> tuple[str, list[dict]]:
    registry, report = tmp_path / "registry.jsonl", tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["research_pass.py", "--bars", str(release_dir), "--registry", str(registry),
                                      "--report", str(report), *extra])
    assert rp.main() == 0
    return report.read_text(), [json.loads(line) for line in registry.read_text().splitlines()]


def test_h37_is_one_screen_with_the_cost_gate_reading_and_the_lot_fit(release_dir, tmp_path, monkeypatch):
    text, rows = _run(release_dir, tmp_path, monkeypatch, "--specialist", "box_break", "--variants", '["L6"]',
                      "--lot-fit")
    assert [r["status"] for r in rows] == ["screened"] and rows[0]["config"] == {"rung": "L6"}
    gate = rows[0]["results"]["screen"]["cost_gate"]
    assert gate["threshold_r"] == 0.85 and gate["retire"] == (gate["gross_mean_r"] < 0.85)
    assert "Cost gate (research director ruling 2026-10-18)" in text and "| all_candidates |" in text


def test_h45_labels_enter_at_the_next_open_and_report_the_lot_fit(release_dir, tmp_path, monkeypatch):
    text, rows = _run(release_dir, tmp_path, monkeypatch, "--specialist", "trend_level_signal", "--variants",
                      '["L3"]', "--lot-fit")
    assert [r["status"] for r in rows] == ["screened"] and "lot_fit" in rows[0]["results"]
    assert "Cost gate" not in text


def test_h40_is_one_discovery_over_exactly_the_21_preregistered_groups(release_dir, tmp_path, monkeypatch):
    _, rows = _run(release_dir, tmp_path, monkeypatch, "--specialist", "box_course", "--variants", '["a11"]',
                   "--discover", "--discover-config", H40_CONFIG)
    assert [r["status"] for r in rows] == ["preregistered", "discovery"]
    d = rows[-1]["results"]["discovery"]
    assert (d["n_groups_screened"], d["k_eff"]) == (21, 21)
    assert d["config"]["pool"] == "candidate" and set(d["group_members"]["mfi_signals"]) == {
        f"{s}_{tf}" for tf in ("h1", "h4") for s in ("mfi_regime", "mfi_extreme_circle", "mfi_double_circle",
                                                     "mfi_cross50", "mfi_pullback_hold", "mfi_divergence")}
