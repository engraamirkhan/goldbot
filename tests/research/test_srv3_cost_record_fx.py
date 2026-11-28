"""Backlog 280 (U2 pooled body, card prereg-2026-11-28-u2-pooled-body.md section 3, P3): the measured FX cost of
record (record schema 2) beside gold's untouched schema-1 record, per-class routing in `apply`, the FX per-row charge
(`fx_row_bp`), the FX swap per rollover (`fx_swap_bp`), and 280(b): an as-of spread row more than 40 days before the
decision is refused (gold and FX)."""
from __future__ import annotations

import hashlib
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
SHIPPED = ROOT / "config" / "research" / "srv3_cost_record"
TFS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
SESSIONS = ("asia", "london", "newyork")
BROKERS = ("icm", "vantage")
LONDON = "2015-03-02 10:00"


def _gold() -> CR.CostRecord:
    (rec,) = CR.recorded().values()
    return rec


def _gold_spread(months: pd.PeriodIndex, usd: float) -> pd.DataFrame:
    return pd.DataFrame([{"session": s, "month": str(m), "ub95_usd": usd,
                          "available_utc": (m + 1).start_time.tz_localize("UTC")} for s in SESSIONS for m in months])


def _fx_spread(months: pd.PeriodIndex, px: float, inst: str = "EURUSD", basis: str = "bid_ask") -> pd.DataFrame:
    return pd.DataFrame([{"instrument": inst, "session": s, "month": str(m), "ub95_px": px, "quote_basis": basis,
                          "available_utc": (m + 1).start_time.tz_localize("UTC")} for s in SESSIONS for m in months])


def _fx_cells(inst: str = "EURUSD", rt: float = 0.8, ecr: float = 0.7, slip: float = 0.1, n: int = 40
              ) -> tuple[CR.FxCell, ...]:
    return tuple(CR.FxCell(instrument=inst, broker=b, session=s, tf=t, rt_bp=rt + (0.05 if b == "vantage" else 0),
                           ecr_ub95_bp=ecr, slip_x_bp=slip, measured_n=n, session_n=n, basis="ecr")
                 for b in BROKERS for s in SESSIONS for t in TFS)


def _v2(inst: str = "EURUSD", **upd: Any) -> CR.CostRecord:
    classes = tuple(c.model_copy(update={"basis": "measured"}) if c.instrument == inst else c for c in _gold().classes)
    base = _gold().model_copy(update={"schema_version": 2, "classes": classes, "fill_cutoff_utc": CR.FILL_CUTOFF_UTC,
                                      "fx_commission_rt_bp": {inst: {"icm": 0.6, "vantage": 0.5}},
                                      "fx_cells": _fx_cells(inst), **upd})
    return base


def _write(rec: CR.CostRecord, rd: Path) -> CR.CostRecord:
    rec = rec.model_copy(update={"record_sha256": CR.record_sha256(rec)})
    (rd / f"{rec.version}.yaml").write_text(yaml.safe_dump(rec.model_dump(mode="json"), sort_keys=False))
    return rec


def _labels(inst: str = "EURUSD", n: int = 4, tf: str = "15m", start: str = LONDON, entry: float = 1.1
            ) -> pd.DataFrame:
    return pd.DataFrame({"instrument": inst, "tf": tf,
                         "decision_utc": pd.date_range(start, periods=n, freq="1min", tz="UTC"),
                         "entry": entry, "cost_rt_bp": DC.RT_BP, "cost_version": "w2-point",
                         "exit_freeze_sha": D.EXIT_FREEZE_SHA256})


@pytest.fixture
def v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A recorded, self-consistent schema-2 record over real temp table files (EURUSD measured)."""
    def _make(fx_spread: pd.DataFrame | None = None, **upd: Any):
        base = _v2(**upd)
        d = tmp_path / f"t{len(list(tmp_path.glob('t*')))}"
        d.mkdir()
        paths = {k: d / f"{k}.parquet" for k in CR.TABLE_FILES_V2}
        cost = pd.concat([base.cells().assign(instrument="XAUUSD"),
                          pd.DataFrame([c.model_dump() for c in base.fx_cells])], ignore_index=True)
        frames = {"cost_table": cost, "swap_table": pd.DataFrame({"instrument": ["XAUUSD"], "month": ["2015-01"]}),
                  "gold_spread_table": _gold_spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.5),
                  "fx_spread_table": _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.0002)
                  if fx_spread is None else fx_spread,
                  "fx_swap_spec": _swap_spec()}
        for k, f in frames.items():
            f.to_parquet(paths[k], index=False)
        frames = {k: pd.read_parquet(p) for k, p in paths.items()}
        sha = CR.content_sha256_v2(frames, base.commission_rt_usd_per_oz, base.fx_commission_rt_bp)
        rec = base.model_copy(update={"version": "srv3-cost-" + sha[:16], "content_sha256": sha, "files": {
            k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}})
        rd = tmp_path / "records"
        rd.mkdir(exist_ok=True)
        rec = _write(rec, rd)
        monkeypatch.setattr(CR, "RECORD_DIR", rd)
        return CR.recorded()[rec.version], paths, frames
    return _make


def _swap_spec(captured: str = "2015-02-20", long_pts: float = -6.0, short_pts: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame([{"broker": b, "instrument": "EURUSD", "captured_utc": pd.Timestamp(captured, tz="UTC"),
                          "swap_mode": "points", "swap_long_points": long_pts * (2 if b == "vantage" else 1),
                          "swap_short_points": short_pts, "point": 1e-5, "swap_triple_weekday": 3,
                          "rollover_tz": "Etc/GMT-2"} for b in BROKERS])


# ---------------------------------------------------------------------------------------------- gold stays as is
def test_gold_record_bytes_and_self_hash_are_unchanged() -> None:
    rec = _gold()
    assert rec.schema_version == 1 and rec.fx_cells == () and rec.dry_run is False
    assert rec.record_sha256 == CR.record_sha256(rec) == \
        "416adbd878e2f3eab59512cc1c89ca3c2c2b263a64a9a67337afbccbec80bdc9"
    text = (SHIPPED / f"{rec.version}.yaml").read_bytes()
    assert b"schema_version" not in text and b"fx_cells" not in text


def test_a_schema_1_record_carrying_fx_fields_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = yaml.safe_load((SHIPPED / f"{_gold().version}.yaml").read_text())
    d["fx_cells"] = [c.model_dump() for c in _fx_cells()[:1]]
    rd = tmp_path / "r"
    rd.mkdir()
    (rd / f"{d['version']}.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    monkeypatch.setattr(CR, "RECORD_DIR", rd)
    with pytest.raises(CR.CostRecordRefused, match="schema 1"):
        CR.recorded()


# ---------------------------------------------------------------------------------------------- 280(b) staleness
def test_280b_gold_row_charges_on_a_table_frozen_within_40_days_and_refuses_a_stale_one() -> None:
    rec = _gold()
    lab = _labels("XAUUSD", start="2027-02-03 10:00", entry=2600.0)
    fresh = _gold_spread(pd.period_range("2026-10", "2026-12", freq="M"), 0.5)    # 2026-12 available 2027-01-01
    assert np.isfinite(CR.gold_row_bp(lab, rec, fresh)).all()                     # 33 days: charges
    stale = _gold_spread(pd.period_range("2025-01", "2025-09", freq="M"), 0.5)    # available 2025-10-01
    with pytest.raises(CR.CostRecordRefused, match="40 days"):
        CR.gold_row_bp(lab, rec, stale)


def test_280b_history_gold_charges_are_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every history row sees the previous month (at most 31 days old): the 40-day rule never binds there."""
    rec = _gold()
    sp = _gold_spread(pd.period_range("2003-05", "2025-09", freq="M"), 0.37)
    lab = pd.concat([_labels("XAUUSD", n=3, start=s, entry=e, tf=t) for s, e, t in
                     (("2003-07-31 23:58", 350.0, "15m"), ("2012-02-29 14:00", 1700.0, "1h"),
                      ("2025-09-30 20:00", 3800.0, "4h"))], ignore_index=True)
    got = CR.gold_row_bp(lab, rec, sp)
    monkeypatch.setattr(CR, "STALE_DAYS", 10_000)                # the rule off: the same bytes
    assert got.tobytes() == CR.gold_row_bp(lab, rec, sp).tobytes()
    # and the shipped arithmetic: the record's own era term on these rows (unchanged formula)
    cells = {(c.broker, c.session, c.tf): c for c in rec.gold_cells}
    sess = CR._sessions(lab)
    want = np.array([max(rec.w2_bp, *(max(cells[(b, s, t)].rt_bp, cells[(b, s, t)].ecr_ub95_usd / e * 1e4,
                                          (0.37 + c + cells[(b, s, t)].slip_x_usd) / e * 1e4)
                                      for b, c in sorted(rec.commission_rt_usd_per_oz.items())))
                     for s, t, e in zip(sess, lab["tf"], lab["entry"], strict=True)])
    assert np.allclose(got, want, rtol=0, atol=1e-12)


def test_280b_fx_row_refuses_a_stale_spread_month() -> None:
    rec = _v2()
    lab = _labels(start="2027-02-03 10:00")
    with pytest.raises(CR.CostRecordRefused, match="40 days"):
        CR.fx_row_bp(lab, rec, _fx_spread(pd.period_range("2025-01", "2025-09", freq="M"), 0.0002))


# ---------------------------------------------------------------------------------------------- the FX charge
def test_fx_row_bp_is_the_worse_broker_of_cell_ecr_and_era_floored_at_w2() -> None:
    rec = _v2()
    lab = _labels(entry=1.1)
    sp = _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.0002)
    got = CR.fx_row_bp(lab, rec, sp)
    era = {b: 0.0002 / 1.1 * 1e4 + rec.fx_commission_rt_bp["EURUSD"][b] + 0.1 for b in BROKERS}
    want = max(max(0.8 + (0.05 if b == "vantage" else 0), 0.7, era[b]) for b in BROKERS)
    assert np.allclose(got, max(DC.RT_BP, want), rtol=0, atol=1e-12)
    tiny = CR.fx_row_bp(lab, _v2(fx_cells=_fx_cells(rt=0.1, ecr=0.1, slip=0.0),
                                 fx_commission_rt_bp={"EURUSD": {"icm": 0.0, "vantage": 0.0}}),
                        _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), 1e-7))
    assert np.allclose(tiny, DC.RT_BP)


def test_fx_row_bp_is_point_in_time() -> None:
    rec = _v2()
    lab = _labels()
    past = _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.0002)
    later = pd.concat([past, _fx_spread(pd.period_range("2015-03", "2016-01", freq="M"), 0.01)], ignore_index=True)
    assert np.array_equal(CR.fx_row_bp(lab, rec, past), CR.fx_row_bp(lab, rec, later))


def test_fx_row_bp_refuses_a_bid_only_month_and_a_missing_month() -> None:
    rec = _v2()
    lab = _labels()
    with pytest.raises(CR.CostRecordRefused, match="bid_only"):
        CR.fx_row_bp(lab, rec, _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), np.nan, basis="bid_only"))
    with pytest.raises(CR.CostRecordRefused, match="no finite era spread"):
        CR.fx_row_bp(lab, rec, _fx_spread(pd.period_range("2015-05", "2015-08", freq="M"), 0.0002))


def test_fx_row_bp_refuses_a_prior_instrument() -> None:
    with pytest.raises(CR.CostRecordRefused, match="USDJPY.*not a measured FX"):
        CR.fx_row_bp(_labels("USDJPY", entry=110.0), _v2(),
                     _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.02, "USDJPY"))


# ---------------------------------------------------------------------------------------------- membership (card P3)
def test_a_measured_fx_instrument_needs_both_brokers_at_min_fills(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """'the measured broker is never charged alone': one broker below 30 fills in every session keeps it prior."""
    thin = tuple(c.model_copy(update={"session_n": 29}) if c.broker == "vantage" else c for c in _fx_cells())
    alone = tuple(c for c in _fx_cells() if c.broker == "icm")
    recs = [(_v2(fx_cells=thin), "fewer than 30"), (_v2(fx_cells=alone), "both brokers")]
    for rec, why in recs:
        rd = tmp_path / why.replace(" ", "_")
        rd.mkdir()
        _write(rec, rd)
        monkeypatch.setattr(CR, "RECORD_DIR", rd)
        with pytest.raises(CR.CostRecordRefused, match=why):
            CR.recorded()


def test_a_dry_run_record_is_never_of_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rd = tmp_path / "r"
    rd.mkdir()
    _write(_v2(dry_run=True), rd)
    monkeypatch.setattr(CR, "RECORD_DIR", rd)
    with pytest.raises(CR.CostRecordRefused, match="dry run"):
        CR.recorded()


def test_a_fill_cutoff_after_the_card_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rd = tmp_path / "r"
    rd.mkdir()
    _write(_v2(fill_cutoff_utc="2026-12-20T00:00:00+00:00"), rd)
    monkeypatch.setattr(CR, "RECORD_DIR", rd)
    with pytest.raises(CR.CostRecordRefused, match="fill_cutoff"):
        CR.recorded()


# ---------------------------------------------------------------------------------------------- apply routes per class
def test_apply_routes_gold_and_fx_rows_to_their_own_charge(v2) -> None:
    rec, paths, frames = v2()
    lab = pd.concat([_labels("XAUUSD", entry=1200.0), _labels("EURUSD"), _labels("USDJPY", entry=110.0)],
                    ignore_index=True)
    rt = np.full(len(lab), 9.0)
    g, f = (lab["instrument"] == "XAUUSD").to_numpy(), (lab["instrument"] == "EURUSD").to_numpy()
    rt[g] = CR.gold_row_bp(lab[g], rec, frames["gold_spread_table"])
    rt[f] = CR.fx_row_bp(lab[f], rec, frames["fx_spread_table"])
    out, m = CR.apply(lab, {"rows": len(lab), **D.gate_status(lab)}, rec.version, paths, rt_bp=rt)
    assert (out.loc[g | f, "cost_version"] == rec.version).all()
    assert (out.loc[~(g | f), "cost_version"] == "w2-point").all()
    assert m["cost_of_record"]["measured"] == ["EURUSD", "XAUUSD"] and m["cost_of_record"]["rows_prior"] == 4
    assert any("USDJPY" in b and "prior" in b for b in m["gate_block"])
    ok, m2 = CR.apply(lab[g | f].reset_index(drop=True), {"rows": int((g | f).sum()), **D.gate_status(lab[g | f])},
                      rec.version, paths, rt_bp=rt[g | f])
    assert m2["gate_eligible"] is True


def test_apply_refuses_an_fx_row_not_at_the_records_charge(v2) -> None:
    rec, paths, frames = v2()
    lab = _labels("EURUSD")
    exact = CR.fx_row_bp(lab, rec, frames["fx_spread_table"])
    for bad in (exact - 0.01, exact + 0.01):
        with pytest.raises(CR.CostRecordRefused, match="rows differ"):
            CR.apply(lab, {"rows": 4, **D.gate_status(lab)}, rec.version, paths, rt_bp=bad)


def test_fx_1m_5m_stay_blocked_on_the_wait_cost(v2) -> None:
    rec, paths, frames = v2()
    lab = _labels("EURUSD", tf="1m")
    _, m = CR.apply(lab, {"rows": 4, **D.gate_status(lab)}, rec.version, paths,
                    rt_bp=CR.fx_row_bp(lab, rec, frames["fx_spread_table"]))
    assert f"EURUSD 1m: decision-to-fill wait cost not in {rec.version} (D-15)" in m["gate_block"]


def test_v2_verify_hashes_every_table_including_the_fx_files(v2, tmp_path: Path) -> None:
    rec, paths, _ = v2()
    assert CR.verify(rec.version, paths) == rec
    other = tmp_path / "fx_other.parquet"
    _fx_spread(pd.period_range("2014-01", "2015-02", freq="M"), 0.0009).to_parquet(other, index=False)
    with pytest.raises(CR.CostRecordRefused, match="fx_spread_table"):
        CR.verify(rec.version, {**paths, "fx_spread_table": other})
    with pytest.raises(CR.CostRecordRefused, match="fx_swap_spec"):
        CR.verify(rec.version, {k: v for k, v in paths.items() if k != "fx_swap_spec"})


def test_v2_refuses_holdout_fx_spread_months(v2) -> None:
    from goldbot.data.holdout_guard import HoldoutSealed
    rec, paths, _ = v2(fx_spread=_fx_spread(pd.period_range("2025-08", "2025-11", freq="M"), 0.0002))
    with pytest.raises(HoldoutSealed):
        CR.verify(rec.version, paths)


def test_v2_fx_cells_drift_from_the_cost_table_is_refused(v2, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    rec, paths, _ = v2()
    cells = list(rec.fx_cells)
    cells[0] = cells[0].model_copy(update={"rt_bp": cells[0].rt_bp + 1})
    forged = _write(rec.model_copy(update={"fx_cells": tuple(cells)}), CR.RECORD_DIR)
    with pytest.raises(CR.CostRecordRefused, match="fx cells"):
        CR.verify(forged.version, paths)


# ---------------------------------------------------------------------------------------------- FX swap per rollover
def _swap_rows(start: str, end: str, side: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"instrument": ["EURUSD"], "side": [side], "entry": [1.1],
                         "decision_utc": [pd.Timestamp(start, tz="UTC")],
                         "horizon_end_utc": [pd.Timestamp(end, tz="UTC")]})


def test_fx_swap_charges_each_rollover_crossed_triple_on_the_brokers_triple_day() -> None:
    rec, spec = _v2(), _swap_spec()
    night = 12e-5 / 1.1 * 1e4                       # vantage long pays 12 points x 1e-5 (the worse broker)
    # server tz GMT+2: rollover at 22:00 UTC. Mon 2015-03-02 10:00 -> Tue 10:00 crosses Mon's rollover once
    assert CR.fx_swap_bp(_swap_rows("2015-03-02 10:00", "2015-03-03 10:00"), rec, spec)[0] == pytest.approx(night)
    # Wed's rollover (MT5 weekday 3) is charged three times
    assert CR.fx_swap_bp(_swap_rows("2015-03-04 10:00", "2015-03-05 10:00"), rec, spec)[0] == pytest.approx(3 * night)
    assert CR.fx_swap_bp(_swap_rows("2015-03-02 10:00", "2015-03-02 21:59"), rec, spec)[0] == 0.0
    # a full week: 5 rollovers Mon..Fri with Wed triple = 7 nights (weekend nights are inside the triple)
    assert CR.fx_swap_bp(_swap_rows("2015-03-02 10:00", "2015-03-07 10:00"), rec, spec)[0] == pytest.approx(7 * night)
    # a credit is never banked: short receives 1 point at both brokers -> charge 0
    assert CR.fx_swap_bp(_swap_rows("2015-03-02 10:00", "2015-03-03 10:00", -1), rec, spec)[0] == 0.0


def test_fx_swap_is_point_in_time_on_the_capture() -> None:
    rec = _v2()
    with pytest.raises(CR.CostRecordRefused, match="no broker swap spec captured"):
        CR.fx_swap_bp(_swap_rows("2014-12-01 10:00", "2014-12-02 10:00"), rec, _swap_spec("2015-01-05"))
    with pytest.raises(CR.CostRecordRefused, match="40 days"):
        CR.fx_swap_bp(_swap_rows("2015-05-04 10:00", "2015-05-05 10:00"), rec, _swap_spec("2015-02-20"))
    with pytest.raises(CR.CostRecordRefused, match="swap_mode"):
        CR.fx_swap_bp(_swap_rows("2015-03-02 10:00", "2015-03-03 10:00"), rec,
                      _swap_spec().assign(swap_mode="interest"))
