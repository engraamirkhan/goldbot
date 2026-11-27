"""D-3 of the sr-v3 card: the frozen ub95 cost of record per class (W2-2, A9; ADR 0098 R-H). A label read is a gate
read on cost only when its rows are charged a RECORDED cost of record (version + content sha256 + file sha256s), and
only for instruments whose class is measured; a prior keeps the D-3 block (quant A4 on #482)."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import srv3_cost_record as CR
from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D

ROOT = Path(__file__).resolve().parents[2]
SRV3 = "srv3-cost-bb199294fe509df0"
FROZEN_SPEC = D.SPEC.model_copy(update={"h_ref": "frozen-D4"})
FX10 = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")


def _labels(instrument: str = "XAUUSD", n: int = 4) -> pd.DataFrame:
    return pd.DataFrame({"instrument": instrument, "tf": "15m",
                         "decision_utc": pd.date_range("2015-03-02 10:00", periods=n, freq="15min", tz="UTC"),
                         "entry": 1200.0, "cost_rt_bp": DC.RT_BP, "cost_version": "w2-point"})


def _meta(lab: pd.DataFrame, spec: D.LabelSpec = FROZEN_SPEC) -> dict:
    return {"rows": len(lab), **D.gate_status(lab, spec)}


def test_srv3_record_is_frozen_complete_and_label_blind() -> None:
    rec = CR.recorded()[SRV3]
    assert rec.version == "srv3-cost-" + rec.content_sha256[:16]
    assert rec.charge_rule == "ub95" and rec.w2_bp == DC.RT_BP
    assert set(rec.files) == {"cost_table", "swap_table", "gold_spread_table"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in (rec.content_sha256, rec.ecr_sha256, *rec.files.values()))
    cells = {(c.broker, c.session, c.tf) for c in rec.gold_cells}
    assert cells == {(b, s, t) for b in ("icm", "vantage") for s in ("asia", "london", "newyork")
                     for t in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")}
    assert all(c.rt_bp >= rec.w2_bp and np.isfinite(c.ecr_ub95_usd) for c in rec.gold_cells)
    # per class: gold is measured (gate), FX10 and silver carry a prior (never a gate read)
    assert rec.measured() == frozenset({"XAUUSD"})
    assert {c.instrument for c in rec.classes if c.basis == "prior"} == {*FX10, "XAGUSD"}
    # point in time and sealed: the history spread and swap months stop before the holdout (2025-10)
    assert rec.history_months[1] < "2025-10" and rec.ecr_window_utc[0] < rec.ecr_window_utc[1]


def test_srv3_record_pins_the_engine_cost_of_record_in_the_repo() -> None:
    import hashlib
    for rec in CR.recorded().values():
        ecr = ROOT / "config" / "engine_costs" / f"{rec.ecr_version}.yaml"
        assert hashlib.sha256(ecr.read_bytes()).hexdigest() == rec.ecr_sha256


def test_srv3_record_is_on_an_adr_0098_recording_line() -> None:
    adr = (ROOT / "docs/decisions/0098-objective-of-record-obj-v1.md").read_text()
    for rec in CR.recorded().values():
        assert any("Recorded" in ln and rec.content_sha256 in ln and rec.version in ln for ln in adr.splitlines()), \
            f"{rec.version}: not on an ADR 0098 R-H recording line"


def test_verify_refuses_unrecorded_versions_and_any_file_sha_mismatch() -> None:
    rec = CR.recorded()[SRV3]
    assert CR.verify(SRV3, dict(rec.files)) == rec
    with pytest.raises(CR.CostRecordRefused, match="not recorded"):
        CR.verify("srv3-cost-0000000000000000", dict(rec.files))
    with pytest.raises(CR.CostRecordRefused, match="swap_table"):
        CR.verify(SRV3, {**rec.files, "swap_table": "0" * 64})
    with pytest.raises(CR.CostRecordRefused, match="gold_spread_table"):
        CR.verify(SRV3, {k: v for k, v in rec.files.items() if k != "gold_spread_table"})


def test_w2_point_rows_block_until_charged_at_the_record() -> None:
    lab = _labels()
    meta = _meta(lab)
    assert meta["gate_eligible"] is False and any("(D-3)" in b for b in meta["gate_block"])
    rt = np.full(len(lab), 2.5)
    out, m2 = CR.apply(lab, meta, CR.recorded()[SRV3], rt_bp=rt, spec=FROZEN_SPEC)
    assert m2["gate_eligible"] is True and m2["gate_block"] == []
    assert (out["cost_version"] == SRV3).all() and np.allclose(out["cost_rt_bp"], rt)
    assert m2["cost_of_record"] == {"version": SRV3, "content_sha256": CR.recorded()[SRV3].content_sha256,
                                    "measured": ["XAUUSD"], "rows_charged": len(lab), "rows_prior": 0}
    assert (lab["cost_version"] == "w2-point").all()              # the caller's frame is not mutated


def test_d4_block_stays_when_only_the_cost_is_frozen() -> None:
    lab = _labels()
    _, m2 = CR.apply(lab, _meta(lab, D.SPEC), CR.recorded()[SRV3], rt_bp=np.full(len(lab), 2.5), spec=D.SPEC)
    assert m2["gate_eligible"] is False
    assert not any("(D-3)" in b for b in m2["gate_block"]) and any("(D-4)" in b for b in m2["gate_block"])


def test_prior_class_rows_keep_the_d3_block() -> None:
    lab = pd.concat([_labels("XAUUSD"), _labels("EURUSD")], ignore_index=True)
    out, m2 = CR.apply(lab, _meta(lab), CR.recorded()[SRV3], rt_bp=np.full(len(lab), 2.5), spec=FROZEN_SPEC)
    assert m2["gate_eligible"] is False
    assert (out.loc[out["instrument"] == "EURUSD", "cost_version"] == "w2-point").all()
    assert any("EURUSD" in b and "prior" in b for b in m2["gate_block"])
    assert m2["cost_of_record"]["rows_prior"] == 4


@pytest.mark.parametrize("bad", [np.full(4, np.nan), np.full(4, DC.RT_BP / 2), np.full(3, 2.5)])
def test_apply_refuses_a_charge_that_is_not_a_cost_of_record(bad: np.ndarray) -> None:
    lab = _labels()
    with pytest.raises(ValueError):
        CR.apply(lab, _meta(lab), CR.recorded()[SRV3], rt_bp=bad, spec=FROZEN_SPEC)


def test_apply_refuses_an_unrecorded_record() -> None:
    lab = _labels()
    fake = CR.recorded()[SRV3].model_copy(update={"content_sha256": "1" * 64})
    with pytest.raises(CR.CostRecordRefused):
        CR.apply(lab, _meta(lab), fake, rt_bp=np.full(len(lab), 2.5), spec=FROZEN_SPEC)


def test_check_cells_matches_the_builders_table_and_names_any_drift() -> None:
    rec = CR.recorded()[SRV3]
    ct = pd.DataFrame([{"instrument": "XAUUSD", **c.model_dump(include={"broker", "session", "tf", "rt_bp",
                                                                          "ecr_ub95_usd"})} for c in rec.gold_cells])
    assert CR.check_cells(ct, rec) == []
    ct.loc[0, "rt_bp"] += 0.01
    assert CR.check_cells(ct, rec) == [f"{ct.loc[0, 'broker']}:{ct.loc[0, 'session']}:{ct.loc[0, 'tf']}: rt_bp"]
    assert CR.check_cells(ct.iloc[1:], rec)[0].endswith("missing")
