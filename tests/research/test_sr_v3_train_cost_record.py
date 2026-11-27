"""The sr-v3 trainer charges gold rows the D-3 cost of record and re-derives the gate through it (quant F6 on #515),
and a pooled Stage 0 refuses a rho file that is not measured at the xt-v1 exit freeze (code review of #514, D-12-xt)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D
from goldbot.research import sr_v3 as V
from goldbot.research import sr_v3_store as S
from goldbot.research import sr_v3_train as T
from goldbot.research import srv3_cost_record as CR
from tests.research.test_srv3_cost_record import BIG, LONDON, _labels, _meta, _spread, _write_record

ROOT = Path(__file__).resolve().parents[2]
RHO = ROOT / "docs" / "research" / "rho-measurement-2026-11.json"


def _recorded_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, spread_file: bool = True
                       ) -> tuple[CR.CostRecord, Path]:
    """A recorded copy of the shipped record over temp tables the trainer's CostTable can read (swap with rates)."""
    (base,) = CR.recorded().values()
    d = tmp_path / "costs"
    d.mkdir()
    paths = {"cost_table": d / "cost_table.parquet", "swap_table": d / "swap_table.parquet",
             "gold_spread_table": d / "gold_spread_table.parquet"}
    base.cells().assign(instrument=V.GOLD, w2_bp=DC.RT_BP).to_parquet(paths["cost_table"], index=False)
    pd.DataFrame({"instrument": [V.GOLD], "month": ["2015-03"], "long_per_night": [-1e-4],
                  "short_per_night": [-1e-4]}).to_parquet(paths["swap_table"], index=False)
    _spread(pd.period_range("2014-01", "2015-02", freq="M"), BIG).to_parquet(paths["gold_spread_table"], index=False)
    frames = [pd.read_parquet(p) for p in paths.values()]
    sha = CR.content_sha256(frames[0], frames[1], frames[2], base.commission_rt_usd_per_oz)
    rec = base.model_copy(update={"version": "srv3-cost-" + sha[:16], "content_sha256": sha, "files": {
        k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}})
    rd = tmp_path / "records"
    rd.mkdir()
    rec = _write_record(rec, rd)
    monkeypatch.setattr(CR, "RECORD_DIR", rd)
    meta = d / "cost_table.json"
    meta.write_text(json.dumps({"commission_rt_usd_per_oz": dict(rec.commission_rt_usd_per_oz)}))
    files: dict[str, Any] = {}
    for k, p in paths.items():
        if k == "gold_spread_table" and not spread_file:
            continue
        files[k], files[f"{k}_sha256"] = str(p), hashlib.sha256(p.read_bytes()).hexdigest()
    man = tmp_path / "manifest-costs.json"
    man.write_text(json.dumps({"costs": {"path": str(meta), "cost_version": rec.version, "files": files}}))
    return CR.recorded()[rec.version], man


def test_cost_table_keeps_the_table_paths_and_charges_gold_exactly_the_record(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    rec, man = _recorded_manifest(tmp_path, monkeypatch)
    table = S.cost_table(man)
    assert set(table.paths) == set(CR.TABLE_FILES)
    lab = _labels(n=6)
    spread = pd.read_parquet(table.paths["gold_spread_table"])
    want = CR.gold_row_bp(lab, rec, spread)
    got = table.rt_bp(lab)
    assert np.allclose(got, want, rtol=0, atol=1e-9)              # slip_x included: the record's own charge
    assert (got > DC.RT_BP).all()


def test_store_charge_of_record_lifts_d3_for_gold_and_keeps_1m_blocked(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    rec, man = _recorded_manifest(tmp_path, monkeypatch)
    store = S.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tmp_path, costs=S.cost_table(man))
    lab = _labels(n=4).drop(columns=["instrument", "tf"])
    meta = _meta(lab.assign(instrument=V.GOLD, tf="15m"))
    assert not meta["gate_eligible"]                                   # w2-point rows: D-3 blocks
    out, m = store._charge_of_record(lab, meta, V.GOLD, "15m")
    assert m["gate_eligible"], m["gate_block"]
    assert (out["cost_version"] == rec.version).all()
    _, m1 = store._charge_of_record(_labels(n=4, tf="1m").drop(columns=["instrument", "tf"]),
                                    _meta(_labels(n=4, tf="1m")), V.GOLD, "1m")
    assert not m1["gate_eligible"] and any("(D-15)" in b for b in m1["gate_block"])


def test_store_without_the_spread_table_refuses_rather_than_dropping_the_gold_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, man = _recorded_manifest(tmp_path, monkeypatch, spread_file=False)
    with pytest.raises(CR.CostRecordRefused):
        S.cost_table(man)


def test_store_without_costs_keeps_the_w2_point_block(tmp_path: Path) -> None:
    store = S.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tmp_path)
    lab = _labels(n=3).drop(columns=["instrument", "tf"])
    meta = _meta(lab.assign(instrument=V.GOLD, tf="15m"))
    out, m = store._charge_of_record(lab, meta, V.GOLD, "15m")
    assert out is lab and m == meta and not m["gate_eligible"]


def test_a_recorded_table_loaded_without_the_store_keeps_the_legacy_charge_and_apply_refuses_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec, man = _recorded_manifest(tmp_path, monkeypatch)
    bare = T.CostTable.from_manifest(man)                         # no gold_charge: never stamped as the record
    lab = _labels(n=4)
    with pytest.raises(CR.CostRecordRefused):
        CR.apply(lab, _meta(lab), rec.version, bare.paths, rt_bp=bare.rt_bp(lab), spec=D.SPEC)


def test_the_cost_record_module_is_part_of_the_trainers_code_version() -> None:
    assert "goldbot/research/srv3_cost_record.py" in T.CODE_FILES


def test_pooled_stage0_refuses_a_rho_file_without_the_xt_v1_freeze(tmp_path: Path) -> None:
    d = json.loads(RHO.read_text())
    assert d.get("exit_freeze_sha") != D.EXIT_FREEZE_SHA256            # the D-12 file predates xt-v1
    members = sorted(V.POOLED_MEMBERS)[:3]
    with pytest.raises(ValueError, match="xt-v1"):
        V.rho_inputs(RHO, body="pooled", members=members, tf="1h", freeze_sha=D.EXIT_FREEZE_SHA256)
    with pytest.raises(ValueError, match="xt-v1"):                   # no sha from the caller: fail closed
        V.rho_inputs(RHO, body="pooled", members=members, tf="1h")
    V.rho_inputs(RHO, body="gold", members=[V.GOLD], tf="1h")       # gold reads no rho_u,inst / tau_a: allowed
    xt = tmp_path / "rho.json"
    xt.write_text(json.dumps({**d, "exit_freeze_sha": D.EXIT_FREEZE_SHA256}))
    V.rho_inputs(xt, body="pooled", members=members, tf="1h", freeze_sha=D.EXIT_FREEZE_SHA256)


def test_gold_rows_in_the_learn_window_are_charged_at_a_london_stamp() -> None:
    assert pd.Timestamp(LONDON).year in V.LEARN_YEARS
