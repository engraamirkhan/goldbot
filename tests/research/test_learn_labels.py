"""The learn-only label route for SEQ-1 and RL-X1 (goldbot/research/learn_labels.py): every read goes through
dense_labels.load_labels on learn 2010-01..2017-12, purpose train, phase 0, never a booked confirm/test_old read; a
sealed store stays sealed; every read names its procedure and writes its consumption row to the ledger (quant L1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research import dense_labels as D
from goldbot.research import learn_labels as LL
from goldbot.research import windows as WN
from tests.research.test_dense_labels import CORE6, _minutes, _store, _tiers


def test_a_year_outside_learn_is_refused_before_any_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[Any] = []
    monkeypatch.setattr(D, "load_labels", lambda *a, **k: calls.append(k))
    for y in (2009, 2018, 2025):
        with pytest.raises(ValueError, match="learn"):
            LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="XAUUSD", tf="1h", years=[y], tiers=None, columns=CORE6, keys={})
    assert calls == []


def test_the_read_is_a_train_read_of_the_learn_window_without_a_booked_read_under_its_procedure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def spy(cache: Path, **k: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        seen.update(k)
        return pd.DataFrame(columns=list(D.COLUMNS)), {}

    monkeypatch.setattr(D, "load_labels", spy)
    LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="XAUUSD", tf="1h", years=[2012], tiers=None, columns=CORE6, keys={})
    assert seen["window"] == WN.SPANS[WN.Window.LEARN] and seen["purpose"] == "train"
    assert seen["phases"] == (0,) and seen.get("booked_read") is None
    assert seen["procedure"] == "seq-1" and seen["ledger"] == tmp_path / "reads.jsonl"


def test_a_sealed_store_stays_sealed(tmp_path: Path):
    m = _minutes("2015-03-02", 6 * 1440)
    _, k = _store(tmp_path, m, "1h", 0, "dukascopy")
    tiers = _tiers(tmp_path, "XAUUSD", ["2015-02", "2015-03"])      # sha not recorded in ADR 0085 A4
    with pytest.raises(D.SealedStore):
        LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="XAUUSD", tf="1h", years=[2015], tiers=tiers, columns=CORE6,
                      keys={(2015, 0): k}, proxy_source="dukascopy:XAUUSD")


def test_rows_of_a_readable_store_carry_a_uniqueness_weight_per_event(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch):
    m = _minutes("2015-03-02", 6 * 1440).assign(source="histdata:EURUSD")
    _, k = _store(tmp_path, m, "1h", 0, "histdata:EURUSD")
    tiers = _tiers(tmp_path, "EURUSD", ["2014-09", "2014-10", "2014-11", "2014-12", "2015-01",
                                         "2015-02", "2015-03"])
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset({tiers.sha256}))   # fixture store only
    lab, meta = LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="EURUSD", tf="1h", years=[2015], tiers=tiers, columns=CORE6,
                              keys={(2015, 0): k}, templates=("T2",))
    assert len(lab) and set(lab["template"]) == {"T2"} and lab["label_ok"].all()
    assert ((lab["w"] > 0) & (lab["w"] <= 1)).all()
    per_event = lab.groupby("decision_utc")["w"].nunique()
    assert (per_event == 1).all()                      # both sides of one bar share one event weight
    assert lab.drop_duplicates("decision_utc")["w"].median() < 0.2   # hourly bars, 1-day horizons: ~24 overlap
    assert meta["learn_only"] is True and meta["procedure"] == "seq-1"
    rows = WN.read_ledger(tmp_path / "reads.jsonl")
    assert [r["id"] for r in rows if r.get("type") == "consumption"] == ["seq-1/EURUSD/1h/train"]


def _fake_rows(n: int = 4, stamp: str = D.EXIT_FREEZE_SHA256) -> pd.DataFrame:
    dec = pd.date_range("2014-03-03 10:00", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"instrument": "XAUUSD", "tf": "1h", "phase": 0, "decision_utc": dec,
                         "entry_utc": dec, "horizon_end_utc": dec + pd.Timedelta(hours=24), "label_ok": True,
                         "entry": 1300.0, "cost_version": "w2-point", "cost_rt_bp": 2.0, "exit_freeze_sha": stamp})


def _spy_load(rows: pd.DataFrame, meta: dict[str, Any]):
    def load(cache: Path, **k: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        return rows.copy(), dict(meta)
    return load


def test_rows_not_built_under_the_exit_freeze_xt_v1_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "load_labels", _spy_load(_fake_rows(stamp="0" * 64), {}))
    with pytest.raises(LL.NotExitFreeze, match="xt-v1"):
        LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "r.jsonl", instrument="XAUUSD", tf="1h",
                      years=[2014], tiers=None, columns=CORE6, keys={})


def test_a_read_with_costs_is_charged_through_the_one_cost_of_record_route_of_sr_v3_store(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from goldbot.research import sr_v3_store as ST
    seen: dict[str, Any] = {}

    class Costs:                       # the CostTable surface load_learn uses beside the charge
        def swap_rate(self, rows: pd.DataFrame) -> Any:
            assert set(rows["instrument"]) == {"XAUUSD"} and set(rows["tf"]) == {"1h"}
            return np.full(len(rows), 3e-5)

    sentinel = Costs()

    def charge(lab: pd.DataFrame, meta: dict[str, Any], costs: Any, instrument: str, tf: str
               ) -> tuple[pd.DataFrame, dict[str, Any]]:
        seen.update(rows=len(lab), costs=costs, instrument=instrument, tf=tf)
        return lab.assign(cost_version="srv3-cost-test", cost_rt_bp=9.0), {**meta, "gate_block": []}

    monkeypatch.setattr(D, "load_labels", _spy_load(_fake_rows(), {"gate_block": ["D-3"]}))
    monkeypatch.setattr(ST, "charge_of_record", charge)
    lab, meta = LL.load_learn(tmp_path, procedure="rl-x1", ledger=tmp_path / "r.jsonl", instrument="XAUUSD",
                              tf="1h", years=[2014], tiers=None, columns=CORE6, keys={}, costs=sentinel)
    assert seen == {"rows": 4, "costs": sentinel, "instrument": "XAUUSD", "tf": "1h"}
    assert (lab["cost_version"] == "srv3-cost-test").all() and (lab["cost_rt_bp"] == 9.0).all()
    assert meta["gate_block"] == [] and meta["learn_only"] is True and "w" in lab.columns
    assert (lab["swap_rate"] == 3e-5).all()          # the swap of record RL-X1 charges per night (quant 5)


def test_a_read_without_costs_keeps_the_point_cost_and_its_d3_block(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "load_labels", _spy_load(_fake_rows(), {"gate_block": ["D-3"]}))
    lab, meta = LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "r.jsonl", instrument="XAUUSD",
                              tf="1h", years=[2014], tiers=None, columns=CORE6, keys={})
    assert (lab["cost_version"] == "w2-point").all() and meta["gate_block"] == ["D-3"]


def test_the_trainer_store_charges_through_the_same_module_level_route(monkeypatch: pytest.MonkeyPatch):
    from goldbot.research import sr_v3_store as ST
    calls: list[Any] = []

    def charge(lab: pd.DataFrame, meta: dict[str, Any], costs: Any, i: str, t: str
               ) -> tuple[pd.DataFrame, dict[str, Any]]:
        calls.append((costs, i, t))
        return lab, meta

    monkeypatch.setattr(ST, "charge_of_record", charge)
    st = ST.StoreData(labels=Path("."), features=Path("."), tiers_path=Path("t.csv"), ledger=Path("r.jsonl"))
    st._charge_of_record(_fake_rows(), {}, "XAUUSD", "1h")
    assert calls == [(None, "XAUUSD", "1h")]


def test_xt_keys_picks_the_one_xt_v1_partition_per_year_and_skips_one_built_under_another_freeze(tmp_path: Path):
    import json
    m = _minutes("2015-03-02", 6 * 1440)
    _, k = _store(tmp_path, m, "1h", 0, "dukascopy")
    assert LL.xt_keys(tmp_path, instrument="XAUUSD", tf="1h", years=[2015]) == {(2015, 0): k}
    man = next((tmp_path / D.SPEC.spec_id / "XAUUSD" / "1h" / "p0" / "2015").glob("*.json"))
    man.write_text(json.dumps({**json.loads(man.read_text()), "exit_freeze_sha": "0" * 64}))
    assert LL.xt_keys(tmp_path, instrument="XAUUSD", tf="1h", years=[2015]) == {}
    with pytest.raises(ValueError, match="learn"):
        LL.xt_keys(tmp_path, instrument="XAUUSD", tf="1h", years=[2018])
