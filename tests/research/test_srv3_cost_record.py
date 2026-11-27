"""D-3 of the sr-v3 card: the frozen ub95 cost of record per class (W2-2, A9; ADR 0098 R-H). A label read is a gate
read on cost only when its rows are charged a RECORDED cost of record (version + content sha256 + file sha256s), at
least the record's own cell and ECR terms, and only for instruments whose class is measured; a prior keeps the D-3
block (quant A4 on #482; quant review of #515: F1-F5, F7)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D
from goldbot.research import srv3_cost_record as CR

ROOT = Path(__file__).resolve().parents[2]
SUPERSEDED = "srv3-cost-bb199294fe509df0"          # era term without slippage (quant F4): never recorded
FROZEN_SPEC = D.SPEC.model_copy(update={"h_ref": "frozen-D4"})
FX10 = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")
TFS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
LONDON = "2015-03-02 10:00"                         # a London-session stamp (DEFAULT_SESSIONS)


def _rec() -> CR.CostRecord:
    (rec,) = CR.recorded().values()
    return rec


def _labels(instrument: str = "XAUUSD", n: int = 4, tf: str = "15m", start: str = LONDON,
            entry: float = 1200.0) -> pd.DataFrame:
    return pd.DataFrame({"instrument": instrument, "tf": tf,
                         "decision_utc": pd.date_range(start, periods=n, freq="1min", tz="UTC"),
                         "entry": entry, "cost_rt_bp": DC.RT_BP, "cost_version": "w2-point"})


def _meta(lab: pd.DataFrame, spec: D.LabelSpec = FROZEN_SPEC) -> dict:
    return {"rows": len(lab), **D.gate_status(lab, spec)}


def _apply(lab: pd.DataFrame, meta: dict, rt: np.ndarray | None = None, spec: D.LabelSpec = FROZEN_SPEC,
           rec: CR.CostRecord | None = None) -> tuple[pd.DataFrame, dict]:
    rec = rec or _rec()
    rt = CR.charge_floor(lab, rec) + 1.0 if rt is None else rt
    return CR.apply(lab, meta, rec.version, dict(rec.files), rt_bp=rt, spec=spec)


def _spread(months: pd.PeriodIndex, usd: float) -> pd.DataFrame:
    return pd.DataFrame([{"session": s, "month": str(m), "ub95_usd": usd,
                          "available_utc": (m + 1).start_time.tz_localize("UTC")}
                         for s in ("asia", "london", "newyork") for m in months])


# ---------------------------------------------------------------------------------------------- the record
def test_record_is_frozen_complete_and_label_blind() -> None:
    rec = _rec()
    assert rec.version == "srv3-cost-" + rec.content_sha256[:16] and rec.version != SUPERSEDED
    assert rec.charge_rule == "ub95" and rec.w2_bp == DC.RT_BP
    assert set(rec.files) == {"cost_table", "swap_table", "gold_spread_table"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in (rec.content_sha256, rec.ecr_sha256, *rec.files.values()))
    assert {(c.broker, c.session, c.tf) for c in rec.gold_cells} == {
        (b, s, t) for b in ("icm", "vantage") for s in ("asia", "london", "newyork") for t in TFS}
    assert all(c.rt_bp >= rec.w2_bp and np.isfinite(c.ecr_ub95_usd) and c.slip_x_usd >= 0 for c in rec.gold_cells)
    assert "slip_x_usd" in rec.rule                                  # quant F4: the era term carries slippage
    assert rec.measured() == frozenset({"XAUUSD"})
    assert {c.instrument for c in rec.classes if c.basis == "prior"} == {*FX10, "XAGUSD"}
    assert rec.wait_charged_tfs == ()                                # quant F3: no D-15 wait cost pinned yet
    assert rec.history_months[1] < "2025-10" and rec.ecr_window_utc[0] < rec.ecr_window_utc[1]


def test_slip_x_is_the_charged_ecr_cells_slippage() -> None:
    """slip_x = max(0, ECR charged - spread - commission) of the account the cell charges (the max charged)."""
    import yaml
    rec = _rec()
    ecr = yaml.safe_load((ROOT / "config" / "engine_costs" / f"{rec.ecr_version}.yaml").read_text())
    for c in rec.gold_cells:
        accts = [a for a, b in ecr["brokers"].items() if b == c.broker]
        t = "all" if c.basis.startswith("ecr-session-all") else c.tf      # the cell the builder charged
        cand = [x for a in accts if (x := ecr["cells"].get(a, {}).get(c.session, {}).get(t)) is not None
                and x["n"] >= rec.ecr_min_fills and np.isclose(x["charged_usd"], c.ecr_ub95_usd, atol=1e-6)]
        assert cand, f"{c}: no ECR cell charges {c.ecr_ub95_usd}"
        want = max(0.0, cand[0]["charged_usd"] - cand[0]["spread_usd"] - cand[0]["commission_usd"])
        assert c.slip_x_usd == pytest.approx(want, abs=1e-6)


def test_record_pins_the_engine_cost_of_record_in_the_repo() -> None:
    for rec in CR.recorded().values():
        ecr = ROOT / "config" / "engine_costs" / f"{rec.ecr_version}.yaml"
        assert hashlib.sha256(ecr.read_bytes()).hexdigest() == rec.ecr_sha256


def test_record_is_on_an_adr_0098_recording_line_with_the_version_of_record_rule() -> None:
    adr = (ROOT / "docs/decisions/0098-objective-of-record-obj-v1.md").read_text()
    for rec in CR.recorded().values():
        assert any("Recorded" in ln and rec.content_sha256 in ln and rec.version in ln for ln in adr.splitlines()), \
            f"{rec.version}: not on an ADR 0098 R-H recording line"
    assert SUPERSEDED not in CR.recorded() and not any("Recorded" in ln and SUPERSEDED in ln for ln in adr.splitlines())
    assert ("The cost version a unit is first read under is its version of record. A read of the same unit under "
            "another cost version is a new trial (K+1), and the first read stands.") in " ".join(adr.split())


def test_verify_refuses_unrecorded_versions_and_any_file_sha_mismatch() -> None:
    rec = _rec()
    assert CR.verify(rec.version, dict(rec.files)) == rec
    with pytest.raises(CR.CostRecordRefused, match="not recorded"):
        CR.verify(SUPERSEDED, dict(rec.files))
    with pytest.raises(CR.CostRecordRefused, match="swap_table"):
        CR.verify(rec.version, {**rec.files, "swap_table": "0" * 64})
    with pytest.raises(CR.CostRecordRefused, match="gold_spread_table"):
        CR.verify(rec.version, {k: v for k, v in rec.files.items() if k != "gold_spread_table"})


# ---------------------------------------------------------------------------------------------- the per-row charge
def test_era_term_charges_spread_plus_commission_plus_slip_x_when_it_binds() -> None:
    rec = _rec()
    lab = _labels(entry=1200.0)
    big = 5.0                                                           # an era spread that binds every broker
    got = CR.gold_row_bp(lab, rec, _spread(pd.period_range("2014-01", "2015-02", freq="M"), big))
    cells = {(c.broker, c.session, c.tf): c for c in rec.gold_cells}
    want = max((big + rec.commission_rt_usd_per_oz[b] + cells[(b, "london", "15m")].slip_x_usd) / 1200.0 * 1e4
               for b in rec.commission_rt_usd_per_oz)
    assert np.allclose(got, want, rtol=0, atol=1e-9)


def test_gold_row_bp_is_point_in_time() -> None:
    """A row in month M is charged the same whether or not the spread table holds month M or later."""
    rec = _rec()
    lab = _labels(entry=1200.0)
    past = _spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.9)
    later = pd.concat([past, _spread(pd.period_range("2015-03", "2016-12", freq="M"), 4.0)], ignore_index=True)
    assert np.array_equal(CR.gold_row_bp(lab, rec, past), CR.gold_row_bp(lab, rec, later))


# ---------------------------------------------------------------------------------------------- apply (the gate)
def test_w2_point_rows_block_until_charged_at_the_record() -> None:
    lab = _labels()
    meta = _meta(lab)
    assert meta["gate_eligible"] is False and any("(D-3)" in b for b in meta["gate_block"])
    rt = CR.charge_floor(lab, _rec()) + 1.0
    out, m2 = _apply(lab, meta, rt)
    assert m2["gate_eligible"] is True and m2["gate_block"] == []
    assert (out["cost_version"] == _rec().version).all() and np.allclose(out["cost_rt_bp"], rt)
    assert m2["cost_of_record"] == {"version": _rec().version, "content_sha256": _rec().content_sha256,
                                    "measured": ["XAUUSD"], "rows_charged": len(lab), "rows_prior": 0}
    assert (lab["cost_version"] == "w2-point").all()              # the caller's frame is not mutated


def test_f1_a_charge_below_the_records_own_terms_is_refused() -> None:
    lab = _labels(tf="15m", start=LONDON, entry=1200.0)          # icm london 15m: ECR ub95 USD 1.49 -> ~12 bp
    with pytest.raises(CR.CostRecordRefused, match="below"):
        _apply(lab, _meta(lab), np.full(len(lab), DC.RT_BP))
    floor = CR.charge_floor(lab, _rec())
    assert (floor > 10).all()
    with pytest.raises(CR.CostRecordRefused):
        _apply(lab, _meta(lab), floor - 1e-3)


def test_f1_apply_verifies_version_and_files_itself() -> None:
    lab = _labels()
    rec = _rec()
    rt = CR.charge_floor(lab, rec) + 1.0
    with pytest.raises(CR.CostRecordRefused, match="not recorded"):
        CR.apply(lab, _meta(lab), SUPERSEDED, dict(rec.files), rt_bp=rt, spec=FROZEN_SPEC)
    with pytest.raises(CR.CostRecordRefused, match="cost_table"):
        CR.apply(lab, _meta(lab), rec.version, {**rec.files, "cost_table": "f" * 64}, rt_bp=rt, spec=FROZEN_SPEC)


def test_f2_every_prior_class_row_stays_blocked_whatever_its_cost_version() -> None:
    fx = _labels("EURUSD").assign(cost_version="srv3-cost-old")
    lab = pd.concat([_labels("XAUUSD"), fx], ignore_index=True)
    meta = _meta(lab)
    out, m2 = _apply(lab, meta)
    assert m2["gate_eligible"] is False
    assert any("EURUSD" in b and "prior" in b for b in m2["gate_block"])
    assert (out.loc[out["instrument"] == "EURUSD", "cost_version"] == "srv3-cost-old").all()
    assert m2["cost_of_record"]["rows_prior"] == 4


@pytest.mark.parametrize("tf", ["1m", "5m"])
def test_f3_gold_1m_5m_stay_blocked_until_the_d15_wait_cost_is_pinned(tf: str) -> None:
    lab = _labels(tf=tf)
    _, m2 = _apply(lab, _meta(lab))
    assert m2["gate_eligible"] is False
    assert f"XAUUSD {tf}: decision-to-fill wait cost not in {_rec().version} (D-15)" in m2["gate_block"]
    rec = _rec().model_copy(update={"wait_charged_tfs": (tf,)})
    with pytest.raises(CR.CostRecordRefused):                    # a record edited in memory is not the record
        CR.apply(lab, _meta(lab), rec.version, {**rec.files, "cost_table": "e" * 64},
                 rt_bp=CR.charge_floor(lab, rec) + 1, spec=FROZEN_SPEC)


def test_d4_block_stays_when_only_the_cost_is_frozen() -> None:
    lab = _labels()
    _, m2 = _apply(lab, _meta(lab, D.SPEC), spec=D.SPEC)
    assert m2["gate_eligible"] is False
    assert not any("(D-3)" in b for b in m2["gate_block"]) and sum("(D-4)" in b for b in m2["gate_block"]) == 1


def test_f5_every_other_existing_block_survives_apply() -> None:
    lab = _labels()
    meta = _meta(lab)
    meta["gate_block"] = [*meta["gate_block"], "tiers: something else blocks this read"]
    _, m2 = _apply(lab, {**meta, "gate_eligible": False})
    assert m2["gate_eligible"] is False and m2["gate_block"] == ["tiers: something else blocks this read"]


@pytest.mark.parametrize("bad", [np.full(4, np.nan), np.full(3, 30.0)])
def test_apply_refuses_a_malformed_charge(bad: np.ndarray) -> None:
    lab = _labels()
    with pytest.raises((ValueError, CR.CostRecordRefused)):
        _apply(lab, _meta(lab), bad)


def test_check_cells_matches_the_builders_table_and_names_any_drift() -> None:
    rec = _rec()
    ct = pd.DataFrame([{"instrument": "XAUUSD", **c.model_dump(include={"broker", "session", "tf", "rt_bp",
                                                                          "ecr_ub95_usd", "slip_x_usd"})}
                       for c in rec.gold_cells])
    assert CR.check_cells(ct, rec) == []
    ct.loc[0, "slip_x_usd"] = float(ct["slip_x_usd"].iloc[0]) + 0.01
    assert CR.check_cells(ct, rec) == [f"{ct.loc[0, 'broker']}:{ct.loc[0, 'session']}:{ct.loc[0, 'tf']}: slip_x_usd"]
    assert CR.check_cells(ct.iloc[1:], rec)[0].endswith("missing")
