"""D-12-xt (quant ruling on PR #514): the rho re-measurement at the xt-v1 exit freeze.

- the file records `exit_freeze_sha` = dense_labels.EXIT_FREEZE_SHA256 and the geometry it was measured under;
- labels come from the factory (`label_rows` under SPEC), stamped xt-v1, usable rows only;
- binding: rho_u,day and rho_u,inst (both levels) = max(D-12 recorded, re-run) per cell; tau_a / lambda_a re-run only;
- a file so produced passes `check_rho_measurement(gate_read=True)`, `require_power_read_rho` and
  `sr_v3.rho_inputs(body="pooled", freeze_sha=...)`."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import PowerFirstSettings, check_rho_measurement
from goldbot.research import data_sufficiency as DS
from goldbot.research import dense_labels as D
from goldbot.research import sr_v3 as S

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("measure_rho_xt", ROOT / "scripts" / "research" / "measure_rho_xt.py")
assert _spec is not None and _spec.loader is not None
X: Any = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("measure_rho_xt", X)
_spec.loader.exec_module(X)

CLASSES = ("gold", "metals", "fx10", "pooled", "gold+fx10")
BANDS = ("le60m", "1h-4h", "gt4h")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _minutes(days: int = 4, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 1440
    ts = pd.date_range("2015-03-02", periods=n, freq="1min", tz="UTC")
    mid = 1500 + np.cumsum(rng.normal(0, 0.3, n))
    hi, lo = mid + np.abs(rng.normal(0, 0.2, n)), mid - np.abs(rng.normal(0, 0.2, n))
    op = np.clip(mid + rng.normal(0, 0.05, n), lo, hi)
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
                         "bid_open": op - .1, "bid_high": hi - .1, "bid_low": lo - .1, "bid_close": mid - .1,
                         "ask_open": op + .1, "ask_high": hi + .1, "ask_low": lo + .1, "ask_close": mid + .1,
                         "tick_count": 10.0, "spread_mean": .2, "spread_max": .2, "source": "dukascopy",
                         "bid_only": False})


# ------------------------------------------------------------------------------------------------ geometry and labels
def test_the_file_records_the_xt_v1_freeze_and_its_geometry() -> None:
    g = X.geometry_record()
    assert g["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256 and g["freeze_id"] == "xt-v1"
    assert g["h_ref_s"] == g["h_by_tf_s"] and set(g["h_ref_factor"].values()) == {1.0}       # h_ref = H
    assert g["h_by_tf_s"]["1m"] == 3_600 and g["h_by_tf_s"]["4h"] == 5 * 86_400
    assert (g["heads"]["T2"]["stop_k"], g["heads"]["T2"]["target_k"]) == (1.0, 2.0)
    assert (g["heads"]["T6"]["stop_k"], g["heads"]["T6"]["target_k"]) == (2.0, None)
    assert "1.5 x median 1m TR of the 1440 minutes" in g["floor"]
    assert {X.BAND_OF[n] for _, _, n in X.GEOMETRIES} == set(BANDS)
    assert X.BAND_OF["1m H=1h T2"] == "le60m" and X.BAND_OF["5m H=4h T6"] == "1h-4h" and X.BAND_OF["15m H=8h T2"] == "gt4h"
    out = X.assemble(binding=_binding(), by_source={}, recorded_path="d12.json", recorded_sha="0" * 64,
                     recorded={"rho_x": {"k": 1}}, meta=_meta())
    assert out["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256 and out["geometry"]["freeze_id"] == "xt-v1"
    assert out["rho_x"] == {"k": 1} and out["binds_with"]["path"] == "d12.json"


def test_labels_are_the_factory_xt_v1_labels_and_refuse_another_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _minutes()
    mm = D.Minutes(m)
    bars = D.decision_bars(mm, "1m")
    dec = X.grid_decisions(bars["close_ns"].to_numpy(), "1m")
    lab = X.label_xt(mm, "XAUUSD", "1m", dec, "dukascopy", False, bars, None)
    assert len(lab) == 0                                    # no tiers: ungraded, never usable
    raw = D.label_rows(mm, pd.DataFrame({"instrument": "XAUUSD", "tf": "1m", "phase": 0, "group_id": 0,
                                         "decision_utc": D._to_utc(dec), "available_utc": D._to_utc(dec),
                                         "window_from_utc": D._to_utc(dec), "source": "dukascopy",
                                         "bid_only": False}), "1m", bars=bars)
    monkeypatch.setattr(D, "attach_tiers", lambda lab, tiers, columns: lab.assign(usable=lab["label_ok"]))
    lab = X.label_xt(mm, "XAUUSD", "1m", dec, "dukascopy", False, bars, None)
    assert set(lab["template"]) == {"T2", "T6"} and (lab["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256).all()
    want = raw[raw["template"].isin(["T2", "T6"]) & raw["label_ok"]].reset_index(drop=True)
    pd.testing.assert_series_equal(lab["gross_r"], want["gross_r"])       # the factory's values, not a re-derivation
    o = X.outcomes(lab, "XAUUSD@dukascopy", (0.5, 1.0), "T2", [1, 2])
    assert len(o) and {"long_gross", "short_net", "rand_gross", "both_gross"} <= set(o.columns)
    assert (o["long_net"] < o["long_gross"]).all()
    monkeypatch.setattr(D, "label_rows", lambda *a, **k: raw.assign(exit_freeze_sha="0" * 64))
    with pytest.raises(RuntimeError, match="not xt-v1"):
        X.label_xt(mm, "XAUUSD", "1m", dec, "dukascopy", False, bars, None)


def test_the_control_grid_never_overlaps_and_uses_hold_h() -> None:
    ts = pd.date_range("2015-03-02 00:00", "2015-03-31", freq="1min", tz="UTC", inclusive="left")
    close = (ts + pd.Timedelta(minutes=1)).as_unit("ns")
    from goldbot.data.timeutil import epoch_ns
    ns = epoch_ns(pd.DatetimeIndex(close))
    g1 = X.grid_decisions(ns, "1m")
    day = X.M.session_day(pd.DatetimeIndex(pd.to_datetime(g1, unit="ns", utc=True)))
    within = pd.Series(g1).groupby(day).diff().dropna()
    assert within.min() >= 3_600 * 10**9 and 20 <= len(g1) / 29 <= 25    # a session day's candidates never overlap
    h4 = ns[(ns // 10**9) % (4 * 3600) == 0]
    g4 = X.grid_decisions(h4, "4h")
    assert len(g4) and np.diff(g4).min() >= 5 * 86_400 * 10**9                         # H = 5 d: never overlaps
    w = pd.date_range("2015-01-04", periods=60, freq="7D", tz="UTC")            # weekly closes on Sundays
    gw = X.grid_decisions(epoch_ns(w), "1w")
    assert len(gw) == 20 and np.diff(gw).min() >= 20 * 86_400 * 10**9                  # H = 20 d: every 3rd week
    assert X.thin(np.array([0, 10, 60, 125]) * 10**9, 60).tolist() == [0, 2, 3]


# ------------------------------------------------------------------------------------------------ the binding rule
def _cell(v: float | None) -> dict[str, Any]:
    return {"grid": v, "families": None, "families_pooled": None, "max": v}


def _binding(rerun_day: float = 0.1, rerun_inst: float = 0.3, rerun_tau: float = 0.05) -> dict[str, Any]:
    rr = {"rho_u_day": {f"{c}|{b}": _cell(rerun_day) for c in CLASSES for b in BANDS},
          "rho_u_inst_day_mean": {f"{c}|{b}": _cell(rerun_inst) for c in CLASSES if c != "gold" for b in BANDS},
          "rho_u_inst_vs_gold_day_mean": {f"{c}|{b}": _cell(rerun_inst) for c in ("metals", "pooled", "gold+fx10")
                                          for b in BANDS},
          "tau_a": {f"{c}|{b}": {"tau_a": rerun_tau, "lambda_a": 1.35 / rerun_tau ** 2} for c in CLASSES if c != "gold"
                    for b in BANDS}}
    rec = {"rho_u_day": {"gold|le60m": {"max": 0.2}, "fx10|le60m": {"max": 0.01}},
           "rho_u_inst_day_mean": {"fx10|le60m": {"max": 0.44}, "pooled|gt4h": {"max": 0.1}},
           "rho_u_inst_vs_gold_day_mean": {"pooled|le60m": {"max": 0.5}},
           "tau_a": {"pooled|le60m": {"tau_a": 0.5}, "fx10|le60m": {"tau_a": 0.001}}}
    return dict(X.bind(rr, rec))


def test_rho_binds_at_max_of_recorded_and_rerun_and_tau_takes_the_rerun_only() -> None:
    b = _binding()
    assert b["rho_u_day"]["gold|le60m"]["max"] == 0.2                     # recorded higher
    assert b["rho_u_day"]["fx10|le60m"]["max"] == 0.1                     # re-run higher
    assert b["rho_u_day"]["gold|le60m"]["rho_value"] == 0.2
    assert b["rho_u_day"]["gold|le60m"]["d12_recorded"] == 0.2 and b["rho_u_day"]["gold|le60m"]["rerun"] == 0.1
    assert b["rho_u_inst_day_mean"]["fx10|le60m"]["max"] == 0.44          # class day-mean level
    assert b["rho_u_inst_day_mean"]["pooled|gt4h"]["max"] == 0.3
    assert b["rho_u_inst_vs_gold_day_mean"]["pooled|le60m"]["max"] == 0.5  # vs-gold level
    t = b["tau_a"]["pooled|le60m"]
    assert t["tau_a"] == 0.05 and t["d12_recorded_tau_a"] == 0.5 and math.isclose(t["lambda_a"], 1.35 / 0.05 ** 2)
    assert b["tau_a"]["fx10|le60m"]["tau_a"] == 0.05                       # a lower recorded tau is not used either
    assert X.bind({"rho_u_day": {"gold|le60m": _cell(1.4)}}, {})["rho_u_day"]["gold|le60m"]["max"] == 1.0
    sbs = {(r["class"], r["bucket"]): r for r in X.side_by_side(b)}
    assert sbs[("gold", "le60m")]["rho_u_day"] == {"d12": 0.2, "rerun": 0.1, "binding": 0.2}
    assert sbs[("pooled", "le60m")]["tau_a"] == {"d12": 0.5, "rerun": 0.05, "binding": 0.05}


# ------------------------------------------------------------------------------------------------ produced file accepted
def _meta() -> dict[str, Any]:
    return {"boot": 10, "pc1": {}, "gsign": {}, "script": {"path": "scripts/research/measure_rho_xt.py"},
            "data": {}}


def _d12(tmp_path: Path, le60: dict[str, float]) -> Path:
    f = tmp_path / "d12.json"
    f.write_text(json.dumps({"rho_day_le_60m": le60}))
    return f


def _produce(tmp_path: Path, rerun_le60: float, d12_le60: float) -> tuple[Path, str, Path]:
    """A D-12-xt file written by the script's own `assemble` + `write`, from a cells table whose le-60m within-day
    upper bound is `rerun_le60`, binding with a D-12 file whose le-60m bound is `d12_le60` for every class."""
    d12 = _d12(tmp_path, dict.fromkeys(CLASSES, d12_le60))
    rows = []
    for c in CLASSES:
        for band, geom in (("le60m", "1m H=1h T2"), ("1h-4h", "5m H=4h T2"), ("gt4h", "15m H=8h T2")):
            rows.append({"kind": "out", "class": c, "key": f"out|{geom}|long_net|raw|{c}", "source": "grid",
                         "geometry": geom, "outcome": "long_net", "orientation": "raw", "band": band, "head": "T2",
                         "tf": geom.split()[0], "days": 100, "within_day.point": rerun_le60 / 2,
                         "within_day.hi95": rerun_le60})
    tab = pd.DataFrame(rows)
    rr = {"rho_u_day": {f"{c}|{b}": _cell(rerun_le60) for c in CLASSES for b in BANDS},
          "rho_u_inst_day_mean": {f"{c}|{b}": _cell(0.3) for c in CLASSES if c != "gold" for b in BANDS},
          "rho_u_inst_vs_gold_day_mean": {}, "tau_a": {f"{c}|{b}": {"tau_a": 0.05} for c in CLASSES if c != "gold"
                                                       for b in BANDS}}
    rec = {"rho_u_day": {f"{c}|le60m": {"max": d12_le60} for c in CLASSES}}
    out = X.assemble(binding=X.bind(rr, rec), by_source={}, recorded_path=str(d12), recorded_sha=_sha(d12),
                     recorded={"rho_x": {}}, meta=_meta())
    js, _ = X.write(tmp_path / "out", "rho-xt", out, tab)
    return js, _sha(js), d12


@pytest.mark.parametrize(("rerun", "d12"), [(0.12, 0.08), (0.07, 0.15)])
def test_a_produced_file_passes_every_gate_read_entry_point(tmp_path: Path, rerun: float, d12: float) -> None:
    js, sha, _ = _produce(tmp_path, rerun, d12)
    d = json.loads(js.read_text())
    assert d["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256
    assert d["rho_day_le_60m"] == dict.fromkeys(CLASSES, max(rerun, d12))
    check_rho_measurement(str(js), max(rerun, d12), sha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)
    with pytest.raises(ValueError, match="below the measured binding"):
        check_rho_measurement(str(js), min(rerun, d12), sha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)
    cfg = {"tfs": ["1m", "5m", "1h"],
           "units": {"fx10": {"learn": [1000] * 3, "judge": [500] * 3,
                              "card_pooled": {"k": 10, "rho_inst": 0.5,
                                              "rho_inst_from": {"file": str(js), "key": "fx10", "stat": "max"}}}},
           "pooled": {"gold_fx": {"rho_inst_from": {"file": str(js), "key": "gold+fx10", "stat": "max"}}}}
    power = PowerFirstSettings.model_validate({"rho_day_le_60m": max(0.05, rerun, d12), "rho_day_measurement": str(js),
                                               "rho_day_measurement_sha256": sha})
    DS.require_power_read_rho(cfg, power)
    assert DS.pre_xt_v1_rho(cfg) == []
    ri = S.rho_inputs(js, body="pooled", members=["XAUUSD", "XAGUSD", "EURUSD"], tf="1m",
                      freeze_sha=D.EXIT_FREEZE_SHA256)
    assert math.isclose(ri.rho_u_inst, 0.3) and math.isclose(ri.lambda_a, S.RESIDUAL_VAR / 0.05 ** 2)


def test_binds_with_must_match_its_sha(tmp_path: Path) -> None:
    js, sha, d12 = _produce(tmp_path, 0.07, 0.15)
    check_rho_measurement(str(js), 0.15, sha)
    d12.write_text(json.dumps({"rho_day_le_60m": dict.fromkeys(CLASSES, 0.01)}))       # the D-12 file moved
    with pytest.raises(ValueError, match="binds_with"):
        check_rho_measurement(str(js), 0.15, sha)


def test_the_written_cells_file_holds_only_aggregate_columns(tmp_path: Path) -> None:
    js, _, _ = _produce(tmp_path, 0.1, 0.1)
    cells = Path(json.loads(js.read_text())["cells_file"]["path"])
    cells = cells if cells.is_absolute() else ROOT / cells
    with gzip.open(cells, "rt") as fh:
        header = fh.readline().strip().split(",")
    from tests.test_long_history import RHO_CELL_ID_COLUMNS, RHO_CELL_STATS
    stats = {f"{s}.{p}" for s in RHO_CELL_STATS for p in ("point", "lo95", "hi95")}
    assert set(header) <= RHO_CELL_ID_COLUMNS | stats
