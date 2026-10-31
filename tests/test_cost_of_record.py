"""The cost of record (config/cost_of_record.yaml, docs/research/cost-of-record-2026-10-31.md): the per-broker x session
round trip and stop overshoot every research screen reads through `arms.load_costs`."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from goldbot.research import arms

ROOT = Path(__file__).resolve().parents[1]
COSTS = ROOT / "config" / "arm_costs.yaml"
SESSIONS = ("asia", "london", "newyork", "all")


def _write(tmp_path: Path, table: dict | None) -> Path:
    raw = yaml.safe_load(COSTS.read_text())
    raw.pop("cost_of_record", None)
    if table is not None:
        (tmp_path / "cor.yaml").write_text(yaml.safe_dump(table))
        raw["cost_of_record"] = "cor.yaml"
    p = tmp_path / "arm_costs.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def _table() -> dict:
    return yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())


def test_the_repo_cost_model_carries_the_cost_of_record() -> None:
    c = arms.load_costs(COSTS)
    assert c.round_trip_usd == 0.23 and c.overshoot_scenarios_usd == (0.0, 0.70)     # the frozen model is unchanged
    assert c.of_record is not None and c.of_record.version.startswith("cor-")
    for broker in ("icm", "vantage"):
        for session in SESSIONS:
            cell = c.of_record.cells[broker][session]
            assert cell.n_fills > 0 and cell.n_exit_legs > 0
            for mean, (lo, hi) in ((cell.spread_usd, cell.spread_ci90), (cell.entry_slip_usd, cell.entry_slip_ci90),
                                   (cell.round_trip_usd, cell.round_trip_ci90), (cell.overshoot_usd, cell.overshoot_ci90)):
                assert lo <= mean + 1e-3 and mean <= hi + 1e-3
            expect = cell.spread_usd + max(0.0, cell.entry_slip_usd) + max(0.0, cell.exit_slip_usd) + cell.commission_usd
            assert cell.round_trip_usd == pytest.approx(expect, abs=0.003)


def test_charged_round_trip_is_the_upper_bound_never_below_the_frozen_model() -> None:
    c = arms.load_costs(COSTS)
    assert c.of_record is not None
    for broker in ("icm", "vantage"):
        for session in SESSIONS:
            hi = c.of_record.cells[broker][session].round_trip_ci90[1]
            assert c.charged_round_trip_usd(broker, session) == pytest.approx(max(c.round_trip_usd, hi))
    # a session the table does not split (rollover, weekend) reads the broker's pooled cell
    assert c.charged_round_trip_usd("icm", "rollover") == c.charged_round_trip_usd("icm", "all")


def test_another_brokers_measurement_is_never_used() -> None:
    c = arms.load_costs(COSTS)
    with pytest.raises(KeyError, match="no cost of record"):
        c.charged_round_trip_usd("pepperstone", "london")


def test_overshoot_of_record_is_the_upper_bound_and_none_when_untrusted() -> None:
    c = arms.load_costs(COSTS)
    assert c.of_record is not None
    icm = c.of_record.cells["icm"]["newyork"]
    assert icm.overshoot_trusted and c.overshoot_of_record_usd("icm", "newyork") == pytest.approx(icm.overshoot_ci90[1])
    assert not c.of_record.cells["vantage"]["london"].overshoot_trusted
    assert c.overshoot_of_record_usd("vantage", "london") is None       # the caller keeps the frozen scenarios


def test_without_a_table_the_frozen_round_trip_is_charged(tmp_path: Path) -> None:
    c = arms.load_costs(_write(tmp_path, None))
    assert c.of_record is None
    assert c.charged_round_trip_usd("icm", "asia") == c.round_trip_usd
    assert c.overshoot_of_record_usd("icm", "asia") is None


def test_a_table_whose_round_trip_disagrees_with_its_parts_is_refused(tmp_path: Path) -> None:
    t = _table()
    t["cells"]["icm"]["asia"]["round_trip_usd"] = 0.05
    with pytest.raises(ValueError, match="round_trip"):
        arms.load_costs(_write(tmp_path, t))


def test_an_inverted_interval_is_refused(tmp_path: Path) -> None:
    t = _table()
    t["cells"]["icm"]["london"]["round_trip_ci90"] = [0.3, 0.1]
    with pytest.raises(ValueError, match="ci90"):
        arms.load_costs(_write(tmp_path, t))


def test_a_broker_without_its_pooled_cell_is_refused(tmp_path: Path) -> None:
    t = _table()
    del t["cells"]["vantage"]["all"]
    with pytest.raises(ValueError, match="all"):
        arms.load_costs(_write(tmp_path, t))


def test_a_table_a_newer_release_wrote_still_reads(tmp_path: Path) -> None:
    """ADR 0010: read tolerantly. A field a newer release adds (top level or in a cell) is dropped, not refused."""
    t = _table()
    t["refreshed_by"] = "weekly job"
    t["cells"]["icm"]["asia"]["latency_ms"] = 800.0
    c = arms.load_costs(_write(tmp_path, t))
    assert c.of_record is not None and c.of_record.cells["icm"]["asia"].n_fills == _table()["cells"]["icm"]["asia"]["n_fills"]
