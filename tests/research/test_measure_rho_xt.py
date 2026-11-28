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
    assert "rho_x" not in out and out["rho_x_from"]["path"] == "d12.json" and out["binds_with"]["path"] == "d12.json"
    assert out["geometry"]["behaviour_sha"] == D.exit_freeze().behaviour_sha


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


TFS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
INST_BY_TF = {"1m": .17, "5m": .16, "15m": .20, "30m": .19, "1h": .21, "2h": .27, "4h": .26, "1d": .38, "1w": .44}
DAY_BY_TF = {"1m": .06, "5m": .07, "15m": .45, "30m": .27}
TAU_BY_TF = {"1m": .09, "5m": .087, "15m": .09942, "30m": .1, "1h": .12, "2h": .117, "4h": .243, "1d": .258, "1w": .2}


def _d12_record(le60: float, inst: float = 0.185, day: float = 0.1) -> dict[str, Any]:
    b = {"rho_u_day": {f"{c}|{bk}": {"max": le60 if bk == "le60m" else day} for c in CLASSES for bk in BANDS},
         "rho_u_inst_day_mean": {f"{c}|{bk}": {"max": inst} for c in CLASSES if c != "gold" for bk in BANDS},
         "rho_u_inst_vs_gold_day_mean": {f"{c}|{bk}": {"max": inst} for c in ("metals", "pooled", "gold+fx10")
                                         for bk in BANDS},
         "tau_a": {f"{c}|{bk}": {"tau_a": 0.5} for c in CLASSES if c != "gold" for bk in BANDS}}
    return {"binding": b, "rho_day_le_60m": dict.fromkeys(CLASSES, max(0.05, le60)),
            "rho_x": {"fold_2010_11|15m|pooled|ret_16": {"day": {"upper95": 0.3}, "inst_agg": {"upper95": 0.2}}}}


def _cells(rerun_le60: float) -> pd.DataFrame:
    rows = []
    for c in CLASSES:
        for tf in TFS:
            geom = X.geometry_name(tf, "T2")
            r: dict[str, Any] = {"kind": "out", "class": c, "key": f"out|{geom}|long_gross|raw|{c}", "source": "grid",
                                 "geometry": geom, "outcome": "long_gross", "orientation": "raw",
                                 "band": X.BAND_OF[geom], "head": "T2", "tf": tf, "days": 100}
            wd = rerun_le60 if tf == "1m" else DAY_BY_TF.get(tf)
            if wd is not None:
                r.update({"within_day.point": wd / 2, "within_day.hi95": wd})
            if c != "gold":
                r.update({"cross_agg.point": INST_BY_TF[tf] / 2, "cross_agg.hi95": INST_BY_TF[tf],
                          "between_inst_sd.point": TAU_BY_TF[tf] / 2, "between_inst_sd.hi95": TAU_BY_TF[tf]})
            if c in ("metals", "pooled", "gold+fx10"):
                r.update({"vs_gold_agg.point": INST_BY_TF[tf] / 2, "vs_gold_agg.hi95": INST_BY_TF[tf]})
            rows.append(r)
    return pd.DataFrame(rows)


def _produce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rerun_le60: float = 0.07,
             d12_le60: float = 0.15) -> tuple[Path, str]:
    """A D-12-xt file written by the script's own `finish` + `write` from a synthetic cells table, binding with a D-12
    file at the repo-relative path d12.json under a repo root patched to `tmp_path`."""
    import goldbot.config as C
    monkeypatch.setattr(C, "ROOT", tmp_path)
    C._rho_day_by_tf.cache_clear()
    d12 = tmp_path / "d12.json"
    rec = _d12_record(d12_le60)
    d12.write_text(json.dumps(rec))
    out = X.finish(_cells(rerun_le60), recorded=rec, recorded_path="d12.json", recorded_sha=_sha(d12), meta=_meta())
    js, _ = X.write(tmp_path / "out", "rho-xt", out, _cells(rerun_le60))
    return js, _sha(js)


def _cfg(js: Path) -> dict[str, Any]:
    return {"tfs": ["1m", "5m", "15m", "1h", "4h", "1w"],
            "units": {"fx10": {"learn": [1000] * 6, "judge": [500] * 6,
                               "card_pooled": {"k": 10, "rho_inst": 0.5,
                                               "rho_inst_from": {"file": str(js), "key": "fx10", "stat": "max"}}}},
            "pooled": {"gold_fx": {"rho_inst_from": {"file": str(js), "key": "gold+fx10", "stat": "max",
                                                     "sha256": _sha(js)}}}}


def test_binding_by_tf_is_the_d12_floor_and_the_cumulative_rerun_max(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant ruling 1 on #525: rho_u,inst(tf) = max(D-12 bucket binding, cumulative max of the re-run over holds <=
    tf); rho_u,day(tf) = max(D-12 bucket, re-run at tf), undefined at H >= 1 d; tau_a(tf) = re-run at tf only."""
    js, _ = _produce(tmp_path, monkeypatch)
    bt = json.loads(js.read_text())["binding_by_tf"]
    inst = {tf: bt["rho_u_inst_day_mean"][f"pooled|{tf}"]["max"] for tf in TFS}
    assert inst == {"1m": .185, "5m": .185, "15m": .2, "30m": .2, "1h": .21, "2h": .27, "4h": .27, "1d": .38, "1w": .44}
    assert bt["rho_u_inst_vs_gold_day_mean"]["pooled|4h"]["max"] == .27
    day = {tf: bt["rho_u_day"][f"gold|{tf}"]["max"] for tf in TFS}
    assert day["1m"] == .15 and day["5m"] == .1 and day["15m"] == .45 and day["30m"] == .27
    assert all(day[tf] is None and bt["rho_u_day"][f"gold|{tf}"]["design_effect_1"] for tf in ("1h", "4h", "1d", "1w"))
    assert bt["tau_a"]["pooled|15m"]["tau_a"] == .09942 and bt["tau_a"]["pooled|4h"]["tau_a"] == .243


def test_rho_inputs_and_measured_rho_read_the_per_tf_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    js, _ = _produce(tmp_path, monkeypatch)
    ri = S.rho_inputs(js, body="pooled", members=["XAUUSD", "XAGUSD", "EURUSD"], tf="15m",
                      freeze_sha=D.EXIT_FREEZE_SHA256)
    assert math.isclose(ri.lambda_a, S.RESIDUAL_VAR / 0.09942 ** 2) and math.isclose(ri.rho_u_inst, 0.2)
    assert ri.rho_u_day == 0.45 and ri.rho_x_day == {"ret_16": 0.3}         # rho_x followed through rho_x_from
    assert S.rho_inputs(js, body="pooled", members=["XAUUSD", "XAGUSD"], tf="1d",
                        freeze_sha=D.EXIT_FREEZE_SHA256).rho_u_day == S.RHO_DAY_PRIOR     # design effect 1: the prior
    cfg = _cfg(js)
    rho = DS.measured_rho(cfg, cfg["pooled"]["gold_fx"]["rho_inst_from"], gate_read=True)
    assert rho == {"1m": .185, "5m": .185, "15m": .2, "1h": .21, "4h": .27, "1w": .44}


def test_the_shipped_bodies_read_the_committed_per_tf_binding() -> None:
    """Quant F1 on #525, on the committed file and the shipped sufficiency config."""
    import yaml

    from goldbot.config import ROOT as CROOT
    ri = S.rho_inputs(XT_JSON, body="pooled", members=["XAUUSD", "XAGUSD", "EURUSD"], tf="15m",
                      freeze_sha=D.EXIT_FREEZE_SHA256)
    assert math.isclose(ri.lambda_a, S.RESIDUAL_VAR / 0.09942 ** 2, rel_tol=1e-9)
    cfg = yaml.safe_load((CROOT / "config" / "data" / "sufficiency.yaml").read_text())
    rho = DS.measured_rho(cfg, cfg["pooled"]["gold_fx"]["rho_inst_from"], gate_read=True)
    assert rho["15m"] == 0.31666


def test_rho_day_for_reads_the_class_binding_at_the_tf_from_the_pinned_xt_file() -> None:
    """Quant F2 on #525: max(0.25, the binding rho_u,day of the class at that tf); gold at 15m (H = 8 h) >= 0.44691."""
    from goldbot.config import load_settings
    pf = load_settings().research.power_first
    assert pf.rho_xt_measurement == f"docs/research/{X.STEM}.json"
    assert pf.rho_day_for(8 / 24, "gold") >= 0.44691
    assert pf.rho_day_for(8 / 24) == 0.25 and pf.rho_day_for(10.0, "gold") == 0.25     # no class / H >= 1 d: prior
    assert pf.rho_day_for(60 / 1440, "fx10") >= 0.25                                    # ruling 2: le60m stays 0.25
    with pytest.raises(ValueError, match="unknown rho class"):
        pf.rho_day_for(8 / 24, "crypto")
    with pytest.raises(ValueError, match="together"):
        PowerFirstSettings.model_validate({"rho_xt_measurement": pf.rho_xt_measurement})
    with pytest.raises(ValueError, match="differs"):
        PowerFirstSettings.model_validate({"rho_xt_measurement": pf.rho_xt_measurement,
                                           "rho_xt_measurement_sha256": "0" * 64})


def test_a_pinned_sha_and_the_cells_file_guard_every_gate_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant F3 on #525: rho_inst_from pins the sha256; a gate read recomputes the per-tf binding from the cells."""
    js, _ = _produce(tmp_path, monkeypatch)
    cfg = _cfg(js)
    DS.require_power_read_rho(cfg, PowerFirstSettings())
    bad = {**cfg["pooled"]["gold_fx"]["rho_inst_from"], "sha256": "0" * 64}
    with pytest.raises(ValueError, match="sha256 pinned"):
        DS.measured_rho(cfg, bad)
    d = json.loads(js.read_text())
    d["binding_by_tf"]["rho_u_inst_day_mean"]["gold+fx10|15m"]["max"] = 0.1          # edited below the cells
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(d))
    src = {"file": str(tampered), "key": "gold+fx10", "stat": "max"}
    assert DS.measured_rho(cfg, src)["15m"] == 0.1                                   # fit-only reads the record
    with pytest.raises(ValueError, match="recomputed"):
        DS.measured_rho(cfg, src, gate_read=True)
    del d["binding_by_tf"]
    tampered.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="binding_by_tf"):
        DS.measured_rho(cfg, src, gate_read=True)


def test_binds_with_is_required_repo_relative_complete_and_sha_checked(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant F4 on #525."""
    js, sha = _produce(tmp_path, monkeypatch)
    check_rho_measurement(str(js), 0.15, sha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)
    d = json.loads(js.read_text())

    def made(name: str, dd: dict[str, Any]) -> tuple[str, str]:
        f = tmp_path / name
        f.write_text(json.dumps(dd))
        return str(f), _sha(f)
    p, s = made("v3.json", {k: v for k, v in d.items() if k != "binds_with"})
    with pytest.raises(ValueError, match="must name binds_with"):
        check_rho_measurement(p, 0.15, s)
    pre = {k: v for k, v in d.items() if k not in ("binds_with", "exit_freeze_sha")} | {"schema": "rho_day_measurement/v3"}
    p, s = made("v3b.json", pre)
    with pytest.raises(ValueError, match="must name binds_with"):
        check_rho_measurement(p, 0.15, s)
    prior = tmp_path / "nogold.json"
    rec = _d12_record(0.15)
    del rec["rho_day_le_60m"]["gold"]
    prior.write_text(json.dumps(rec))
    p, s = made("nogold-xt.json", d | {"binds_with": {"path": "nogold.json", "sha256": _sha(prior)}})
    with pytest.raises(ValueError, match="'gold' has no finite bound"):
        check_rho_measurement(p, 0.15, s)
    p, s = made("abs.json", d | {"binds_with": {"path": str(tmp_path / "d12.json"), "sha256": _sha(tmp_path / "d12.json")}})
    with pytest.raises(ValueError, match="repo-relative"):
        check_rho_measurement(p, 0.15, s)
    (tmp_path / "d12.json").write_text(json.dumps(_d12_record(0.01)))                 # the D-12 file moved
    with pytest.raises(ValueError, match="binds_with"):
        check_rho_measurement(str(js), 0.15, sha)


@pytest.mark.parametrize(("rerun", "d12"), [(0.12, 0.08), (0.07, 0.15)])
def test_the_le60m_bound_is_max_of_d12_and_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rerun: float,
                                                 d12: float) -> None:
    js, sha = _produce(tmp_path, monkeypatch, rerun, d12)
    d = json.loads(js.read_text())
    assert d["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256 and "rho_x" not in d
    assert d["rho_day_le_60m"] == dict.fromkeys(CLASSES, max(rerun, d12))
    check_rho_measurement(str(js), max(rerun, d12), sha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)
    with pytest.raises(ValueError, match="below the measured binding"):
        check_rho_measurement(str(js), min(rerun, d12), sha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)


def test_the_written_cells_file_holds_only_aggregate_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    js, _ = _produce(tmp_path, monkeypatch)
    cells = Path(json.loads(js.read_text())["cells_file"]["path"])
    with gzip.open(cells, "rt") as fh:
        header = fh.readline().strip().split(",")
    from tests.test_long_history import RHO_CELL_ID_COLUMNS, RHO_CELL_STATS
    stats = {f"{s}.{p}" for s in RHO_CELL_STATS for p in ("point", "lo95", "hi95")}
    assert set(header) <= RHO_CELL_ID_COLUMNS | stats


# ------------------------------------------------------------------------------------------------ the committed file
XT_JSON = ROOT / "docs" / "research" / f"{X.STEM}.json"


def test_the_committed_xt_file_is_from_the_committed_script_and_passes_the_gate_read() -> None:
    """CI tie (as D-12's): the committed D-12-xt JSON records the committed script's sha256, its cells file's sha256,
    the D-12 file it binds with, the xt-v1 freeze and its behaviour sha; it stays under the 800 kB cap; the shipped
    sufficiency config reads it for its pooled bodies and the one gate-read entry point accepts it."""
    import yaml

    from goldbot.config import ROOT as CROOT
    d = json.loads(XT_JSON.read_text())
    assert XT_JSON.stat().st_size < 800_000
    assert d["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256
    assert d["geometry"]["behaviour_sha"] == D.exit_freeze().behaviour_sha                    # quant F5 on #525
    assert d["script"]["path"] == "scripts/research/measure_rho_xt.py"
    assert d["script"]["sha256"] == _sha(ROOT / d["script"]["path"]), "measure_rho_xt.py changed: --rebind it (D-12-xt)"
    assert d["script"]["uses"]["scripts/research/measure_rho.py"] == _sha(ROOT / "scripts/research/measure_rho.py")
    assert d["cells_file"]["sha256"] == _sha(ROOT / d["cells_file"]["path"])
    assert d["binds_with"]["sha256"] == _sha(ROOT / d["binds_with"]["path"])
    assert d["rho_x_from"]["sha256"] == _sha(ROOT / d["rho_x_from"]["path"]) and "rho_x" not in d
    assert d["bootstrap"]["B"] == 2000
    bound = max(d["rho_day_le_60m"].values())
    check_rho_measurement(f"docs/research/{X.STEM}.json", bound, _sha(XT_JSON), gate_read=True,
                          freeze_sha=D.EXIT_FREEZE_SHA256)
    cfg = yaml.safe_load((CROOT / "config" / "data" / "sufficiency.yaml").read_text())
    assert DS.pre_xt_v1_rho(cfg) == []
    assert all(src.get("sha256") == _sha(XT_JSON) for _, src in DS._rho_sources(cfg))
    DS.require_power_read_rho(cfg, PowerFirstSettings())
    bt = d["binding_by_tf"]["rho_u_inst_day_mean"]                                          # the ruling's figures
    assert [bt[f"gold+fx10|{tf}"]["max"] for tf in ("15m", "30m", "1h", "2h", "4h", "1d", "1w")] == \
        [0.31666] * 5 + [pytest.approx(0.410, abs=1e-3), pytest.approx(0.510, abs=1e-3)]
    assert [bt[f"pooled|{tf}"]["max"] for tf in ("15m", "30m", "1h", "2h", "4h", "1d", "1w")] == pytest.approx(
        [0.198, 0.203, 0.211, 0.269, 0.269, 0.380, 0.443], abs=1e-3)


def test_power_cards_take_the_class_rho_day_at_their_tf() -> None:
    """Quant R1 on #525 re-verify: evaluate() and the verdict check pass the card's class to rho_day_for."""
    from goldbot.config import load_settings
    from goldbot.research import power_first as P
    from tests.research.test_power_first import card
    st = load_settings()
    v = P.evaluate(card(tf="15m", hold_days=8 / 24, instruments=["XAUUSD"]), st)
    assert v.rho_day == pytest.approx(0.44691)
    cls = st.research.power_first.rho_classes()                    # the pinned file's `classes`, never a copy
    assert cls["metals"] == ("XAUUSD", "XAGUSD") and len(cls["pooled"]) == 12
    of = lambda xs: P.rho_class_of(xs, cls)                         # noqa: E731
    assert of(["XAUUSD@dukascopy"]) == "gold" and of(["XAUUSD", "XAGUSD"]) == "metals" and of(["XAGUSD"]) == "metals"
    assert of(["EURUSD", "GBPUSD"]) == "fx10" and of(["XAUUSD", "EURUSD"]) == "gold+fx10"
    assert of(["XAUUSD", "XAGUSD", "EURUSD"]) == "pooled" and of(["XAGUSD", "EURUSD"]) == "pooled"
    assert of(["XAUUSD", "US500"]) is None and P.rho_class_of(["XAUUSD"], {}) is None    # outside / no file: prior
    metals = P.evaluate(card(tf="15m", hold_days=8 / 24, instruments=["XAUUSD", "XAGUSD"]), st)
    assert metals.rho_day == pytest.approx(0.29928)                 # quant R1b on #525
    with pytest.raises(ValueError, match="looser"):                           # a verdict on the 0.25 prior is refused
        P._check_given(v.model_copy(update={"rho_day": 0.25}), card(tf="15m", hold_days=8 / 24,
                                                                       instruments=["XAUUSD"]), st)


def test_pooled_rho_inputs_recompute_the_per_tf_binding_from_the_cells(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant R2 on #525 re-verify: a pooled Stage 0 read verifies binding_by_tf against the cells file."""
    js, _ = _produce(tmp_path, monkeypatch)
    S.rho_inputs(js, body="pooled", members=["XAUUSD", "EURUSD"], tf="15m", freeze_sha=D.EXIT_FREEZE_SHA256)
    d = json.loads(js.read_text())
    d["binding_by_tf"]["rho_u_inst_day_mean"]["gold+fx10|15m"]["max"] = 0.1
    bad = tmp_path / "tampered.json"
    bad.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="recomputed"):
        S.rho_inputs(bad, body="pooled", members=["XAUUSD", "EURUSD"], tf="15m", freeze_sha=D.EXIT_FREEZE_SHA256)
