"""D-3 of the sr-v3 card: the frozen ub95 cost of record per class (W2-2, A9; ADR 0098 R-H). A label read is a gate
read on cost only when its rows are charged EXACTLY the recorded cost of record (version + content sha256, the table
files hashed from their bytes), and only for instruments whose class is measured; a prior keeps the D-3 block (quant
A4 on #482; quant review of #515: F1-F5, F7, R1; code review of #515: 1-4)."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D
from goldbot.research import srv3_cost_record as CR

ROOT = Path(__file__).resolve().parents[2]
SUPERSEDED = "srv3-cost-bb199294fe509df0"          # era term without slippage (quant F4): never recorded
FROZEN_SPEC = D.SPEC                                 # xt-v1 (D-4, #514)
NOT_XT_V1 = D.SPEC.model_copy(update={"h_ref": {**D.H_BY_TF, "15m": D.H_BY_TF["15m"] // 4}})
FX10 = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")
TFS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
LONDON = "2015-03-02 10:00"                         # a London-session stamp (DEFAULT_SESSIONS)
BIG = 5.0                                           # an era spread (USD) that binds every broker
SHIPPED = ROOT / "config" / "research" / "srv3_cost_record"


def _rec() -> CR.CostRecord:
    (rec,) = CR.recorded().values()
    return rec


def _spread(months: pd.PeriodIndex, usd: float, sessions: tuple[str, ...] = ("asia", "london", "newyork")
            ) -> pd.DataFrame:
    return pd.DataFrame([{"session": s, "month": str(m), "ub95_usd": usd,
                          "available_utc": (m + 1).start_time.tz_localize("UTC")}
                         for s in sessions for m in months])


def _write_record(rec: CR.CostRecord, rd: Path) -> CR.CostRecord:
    rec = rec.model_copy(update={"record_sha256": CR.record_sha256(rec)})
    (rd / f"{rec.version}.yaml").write_text(yaml.safe_dump(rec.model_dump(mode="json"), sort_keys=False))
    return rec


Make = Callable[..., tuple[CR.CostRecord, dict[str, Path], pd.DataFrame]]


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Make:
    """A recorded, self-consistent copy of the shipped record over real temp table files: the gold cells in the cost
    file are the record's, the content sha256 is recomputed from the files (as the builder does)."""
    def _make(spread: pd.DataFrame | None = None, **update: Any) -> tuple[CR.CostRecord, dict[str, Path], pd.DataFrame]:
        base = _rec().model_copy(update=update)
        spread = _spread(pd.period_range("2014-01", "2015-02", freq="M"), BIG) if spread is None else spread
        d = tmp_path / f"t{len(list(tmp_path.glob('t*')))}"
        d.mkdir()
        paths = {"cost_table": d / "cost_table.parquet", "swap_table": d / "swap_table.parquet",
                 "gold_spread_table": d / "gold_spread_table.parquet"}
        cost = base.cells().assign(instrument="XAUUSD", w2_bp=DC.RT_BP)
        swap = pd.DataFrame({"instrument": ["XAUUSD"], "month": ["2015-01"]})
        cost.to_parquet(paths["cost_table"], index=False)
        swap.to_parquet(paths["swap_table"], index=False)
        spread.to_parquet(paths["gold_spread_table"], index=False)
        frames = [pd.read_parquet(p) for p in paths.values()]
        sha = CR.content_sha256(frames[0], frames[1], frames[2], base.commission_rt_usd_per_oz)
        rec = base.model_copy(update={"version": "srv3-cost-" + sha[:16], "content_sha256": sha, "files": {
            k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}})
        rd = tmp_path / "records"
        rd.mkdir(exist_ok=True)
        rec = _write_record(rec, rd)
        monkeypatch.setattr(CR, "RECORD_DIR", rd)
        return CR.recorded()[rec.version], paths, frames[2]
    return _make


@pytest.fixture
def fx(make: Make) -> tuple[CR.CostRecord, dict[str, Path], pd.DataFrame]:
    return make()


def _labels(instrument: str = "XAUUSD", n: int = 4, tf: str = "15m", start: str = LONDON,
            entry: float = 1200.0) -> pd.DataFrame:
    return pd.DataFrame({"instrument": instrument, "tf": tf,
                         "decision_utc": pd.date_range(start, periods=n, freq="1min", tz="UTC"),
                         "entry": entry, "cost_rt_bp": DC.RT_BP, "cost_version": "w2-point",
                         "exit_freeze_sha": D.EXIT_FREEZE_SHA256})


def _meta(lab: pd.DataFrame, spec: D.LabelSpec = FROZEN_SPEC) -> dict:
    return {"rows": len(lab), **D.gate_status(lab, spec)}


def _charge(lab: pd.DataFrame, rec: CR.CostRecord, spread: pd.DataFrame) -> np.ndarray:
    """The record's own charge on measured rows; prior rows get any value (they are never stamped)."""
    meas = lab["instrument"].isin(sorted(rec.measured())).to_numpy()
    rt = np.full(len(lab), 9.0)
    rt[meas] = CR.gold_row_bp(lab[meas], rec, spread)
    return rt


def _apply(fx: tuple, lab: pd.DataFrame, meta: dict, rt: np.ndarray | None = None,
           spec: D.LabelSpec = FROZEN_SPEC) -> tuple[pd.DataFrame, dict]:
    rec, paths, spread = fx
    rt = _charge(lab, rec, spread) if rt is None else rt
    return CR.apply(lab, meta, rec.version, paths, rt_bp=rt, spec=spec)


# ---------------------------------------------------------------------------------------------- code review of #515
def test_cr1_the_shipped_record_passes_its_self_hash_and_a_tampered_cell_is_refused(tmp_path: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch
                                                                                    ) -> None:
    rec = _rec()
    assert rec.record_sha256 == CR.record_sha256(rec)
    for field, edit in (("gold_cells", lambda d: d["gold_cells"][0].update(rt_bp=d["gold_cells"][0]["rt_bp"] + 1)),
                        ("w2_bp", lambda d: d.update(w2_bp=0.1))):
        d = yaml.safe_load((SHIPPED / f"{rec.version}.yaml").read_text())
        edit(d)
        rd = tmp_path / field
        rd.mkdir()
        (rd / f"{rec.version}.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
        monkeypatch.setattr(CR, "RECORD_DIR", rd)
        with pytest.raises(CR.CostRecordRefused, match="record_sha256"):
            CR.recorded()


def test_cr1_verify_recomputes_the_content_sha_and_the_cells_from_the_file_bytes(make: Make) -> None:
    rec, paths, _ = make()
    assert CR.verify(rec.version, paths) == rec
    # a record whose cells were re-hashed after an edit still disagrees with the hashed cost table
    cells = list(rec.gold_cells)
    cells[0] = cells[0].model_copy(update={"rt_bp": cells[0].rt_bp + 1.0})
    forged = _write_record(rec.model_copy(update={"gold_cells": tuple(cells)}), CR.RECORD_DIR)
    with pytest.raises(CR.CostRecordRefused, match="cells"):
        CR.verify(forged.version, paths)
    # a commission edit (re-hashed record) no longer reproduces the content sha256 of the files
    forged = _write_record(rec.model_copy(update={"commission_rt_usd_per_oz": {"icm": 0.0, "vantage": 0.0}}),
                           CR.RECORD_DIR)
    with pytest.raises(CR.CostRecordRefused, match="content sha256"):
        CR.verify(forged.version, paths)


def test_cr2_a_row_without_a_prior_spread_month_is_refused() -> None:
    rec = _rec()
    sp = _spread(pd.period_range("2003-05", "2004-12", freq="M"), 0.5)
    lab = _labels(start="2003-03-03 10:00")
    with pytest.raises(CR.CostRecordRefused, match=r"4 gold rows.*2003-03-03 10:00"):
        CR.gold_row_bp(lab, rec, sp)


def test_cr2_a_spread_table_with_other_session_names_is_refused(make: Make) -> None:
    rec = _rec()
    sp = _spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.5, ("asia", "London", "newyork"))
    with pytest.raises(CR.CostRecordRefused, match="session"):
        CR.gold_row_bp(_labels(), rec, sp)
    rec2, paths, _ = make(spread=sp)
    with pytest.raises(CR.CostRecordRefused, match="session"):
        CR.verify(rec2.version, paths)


def test_cr2_a_nan_spread_ub95_is_refused() -> None:
    sp = _spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.5)
    sp.loc[(sp["session"] == "london") & (sp["month"] == "2015-02"), "ub95_usd"] = np.nan
    with pytest.raises(CR.CostRecordRefused, match="4 gold rows"):
        CR.gold_row_bp(_labels(), _rec(), sp)


def test_cr4_a_nat_decision_is_refused_clearly() -> None:
    lab = _labels()
    lab.loc[1, "decision_utc"] = pd.NaT
    with pytest.raises(CR.CostRecordRefused, match="decision_utc"):
        CR.gold_row_bp(lab, _rec(), _spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.5))


def test_apply_refuses_a_record_that_measures_anything_but_gold(make: Make) -> None:
    classes = tuple(c.model_copy(update={"basis": "measured"}) if c.instrument == "XAGUSD" else c
                    for c in _rec().classes)
    rec, paths, spread = make(classes=classes)
    lab = _labels()
    with pytest.raises(CR.CostRecordRefused, match="gold"):
        CR.apply(lab, _meta(lab), rec.version, paths, rt_bp=CR.gold_row_bp(lab, rec, spread), spec=FROZEN_SPEC)


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
        assert any("Recorded" in ln and rec.content_sha256 in ln and rec.version in ln and rec.record_sha256 in ln for ln in adr.splitlines()), \
            f"{rec.version}: not on an ADR 0098 R-H recording line"
    assert SUPERSEDED not in CR.recorded() and not any("Recorded" in ln and SUPERSEDED in ln for ln in adr.splitlines())
    assert ("The cost version a unit is first read under is its version of record. A read of the same unit under "
            "another cost version is a new trial (K+1), and the first read stands.") in " ".join(adr.split())


def test_verify_hashes_the_files_itself_and_refuses_unrecorded_versions_or_other_bytes(fx: tuple,
                                                                                         tmp_path: Path) -> None:
    rec, paths, _ = fx
    assert CR.verify(rec.version, paths) == rec
    with pytest.raises(CR.CostRecordRefused, match="not recorded"):
        CR.verify(SUPERSEDED, paths)
    other = tmp_path / "other_spread.parquet"
    _spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.5).to_parquet(other, index=False)
    with pytest.raises(CR.CostRecordRefused, match="gold_spread_table"):
        CR.verify(rec.version, {**paths, "gold_spread_table": other})
    with pytest.raises(CR.CostRecordRefused, match="swap_table"):
        CR.verify(rec.version, {k: v for k, v in paths.items() if k != "swap_table"})


# ---------------------------------------------------------------------------------------------- the per-row charge
def test_era_term_charges_spread_plus_commission_plus_slip_x_when_it_binds() -> None:
    rec = _rec()
    lab = _labels(entry=1200.0)
    got = CR.gold_row_bp(lab, rec, _spread(pd.period_range("2014-01", "2015-02", freq="M"), BIG))
    cells = {(c.broker, c.session, c.tf): c for c in rec.gold_cells}
    want = max((BIG + rec.commission_rt_usd_per_oz[b] + cells[(b, "london", "15m")].slip_x_usd) / 1200.0 * 1e4
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
def test_w2_point_rows_block_until_charged_at_the_record(fx: tuple) -> None:
    rec, _, spread = fx
    lab = _labels()
    meta = _meta(lab)
    assert meta["gate_eligible"] is False and any("(D-3)" in b for b in meta["gate_block"])
    rt = CR.gold_row_bp(lab, rec, spread)
    out, m2 = _apply(fx, lab, meta, rt)
    assert m2["gate_eligible"] is True and m2["gate_block"] == []
    assert (out["cost_version"] == rec.version).all() and np.allclose(out["cost_rt_bp"], rt)
    assert m2["cost_of_record"] == {"version": rec.version, "content_sha256": rec.content_sha256,
                                    "measured": ["XAUUSD"], "rows_charged": len(lab), "rows_prior": 0}
    assert (lab["cost_version"] == "w2-point").all()              # the caller's frame is not mutated


def test_r1_only_the_records_exact_charge_is_stamped(fx: tuple) -> None:
    rec, _, spread = fx
    lab = _labels(tf="15m", start=LONDON, entry=1200.0)
    exact = CR.gold_row_bp(lab, rec, spread)
    floor = CR.charge_floor(lab, rec)                              # no slip_x / era term: a diagnostic only
    assert (exact > floor + 1).all()                               # the era term binds in this fixture
    for bad in (np.full(len(lab), DC.RT_BP), floor, exact + 1.0, exact - 1.0):
        with pytest.raises(CR.CostRecordRefused, match=r"rows differ.*largest gap"):
            _apply(fx, lab, _meta(lab), bad)


def test_r1_apply_hashes_the_files_itself(fx: tuple, tmp_path: Path) -> None:
    rec, paths, spread = fx
    lab = _labels()
    rt = CR.gold_row_bp(lab, rec, spread)
    with pytest.raises(CR.CostRecordRefused, match="not recorded"):
        CR.apply(lab, _meta(lab), SUPERSEDED, paths, rt_bp=rt, spec=FROZEN_SPEC)
    forged = tmp_path / "forged_spread.parquet"
    _spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.1).to_parquet(forged, index=False)
    with pytest.raises(CR.CostRecordRefused, match="gold_spread_table"):
        CR.apply(lab, _meta(lab), rec.version, {**paths, "gold_spread_table": forged}, rt_bp=rt, spec=FROZEN_SPEC)


def test_f2_every_prior_class_row_stays_blocked_whatever_its_cost_version(fx: tuple) -> None:
    other = _labels("EURUSD").assign(cost_version="srv3-cost-old")
    lab = pd.concat([_labels("XAUUSD"), other], ignore_index=True)
    out, m2 = _apply(fx, lab, _meta(lab))
    assert m2["gate_eligible"] is False
    assert any("EURUSD" in b and "prior" in b for b in m2["gate_block"])
    assert (out.loc[out["instrument"] == "EURUSD", "cost_version"] == "srv3-cost-old").all()
    assert m2["cost_of_record"]["rows_prior"] == 4


@pytest.mark.parametrize("tf", ["1m", "5m"])
def test_f3_gold_1m_5m_stay_blocked_until_the_d15_wait_cost_is_pinned(fx: tuple, tf: str) -> None:
    lab = _labels(tf=tf)
    _, m2 = _apply(fx, lab, _meta(lab))
    assert m2["gate_eligible"] is False
    assert f"XAUUSD {tf}: decision-to-fill wait cost not in {fx[0].version} (D-15)" in m2["gate_block"]


def test_d4_block_stays_when_only_the_cost_is_frozen(fx: tuple) -> None:
    lab = _labels()
    _, m2 = _apply(fx, lab, _meta(lab, NOT_XT_V1), spec=NOT_XT_V1)
    assert m2["gate_eligible"] is False
    assert not any("(D-3)" in b for b in m2["gate_block"]) and sum("(D-4)" in b for b in m2["gate_block"]) == 1


def test_f5_every_other_existing_block_survives_apply(fx: tuple) -> None:
    lab = _labels()
    meta = _meta(lab)
    meta["gate_block"] = [*meta["gate_block"], "tiers: something else blocks this read"]
    _, m2 = _apply(fx, lab, {**meta, "gate_eligible": False})
    assert m2["gate_eligible"] is False and m2["gate_block"] == ["tiers: something else blocks this read"]


@pytest.mark.parametrize("bad", [np.full(4, np.nan), np.full(3, 30.0)])
def test_apply_refuses_a_malformed_charge(fx: tuple, bad: np.ndarray) -> None:
    lab = _labels()
    with pytest.raises((ValueError, CR.CostRecordRefused)):
        _apply(fx, lab, _meta(lab), bad)


def test_check_cells_matches_the_builders_table_and_names_any_drift() -> None:
    rec = _rec()
    ct = pd.DataFrame([{"instrument": "XAUUSD", **c.model_dump(include={"broker", "session", "tf", "rt_bp",
                                                                          "ecr_ub95_usd", "slip_x_usd"})}
                       for c in rec.gold_cells])
    assert CR.check_cells(ct, rec) == []
    ct.loc[0, "slip_x_usd"] = float(ct["slip_x_usd"].iloc[0]) + 0.01
    assert CR.check_cells(ct, rec) == [f"{ct.loc[0, 'broker']}:{ct.loc[0, 'session']}:{ct.loc[0, 'tf']}: slip_x_usd"]
    assert CR.check_cells(ct.iloc[1:], rec)[0].endswith("missing")
