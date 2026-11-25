"""cost(x) known at decision (ADR 0098 R-I; ADR 0102 point 3 as amended by the quant review of PR #461)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import decision_cost as C
from goldbot.research import history_replay as H

DEC = pd.Timestamp("2014-03-04 20:00", tz="UTC")       # a Tuesday, two hours before the 22:00 UTC rollover


def _three_exits() -> pd.DataFrame:
    """Same arm, side, decision, max hold, entry and risk; a gapped stop, an overnight time exit and a target."""
    return pd.DataFrame({
        "arm": "Z1P", "side": 1.0, "decision_utc": DEC, "max_hold_s": 30 * 3600.0, "entry": 1340.0, "risk_usd": 4.0,
        "label_exit_utc": [DEC + pd.Timedelta(minutes=5), DEC + pd.Timedelta(hours=26), DEC + pd.Timedelta(hours=1)],
        "barrier_hit": ["stop", "time", "target"], "r_mid": [-1.4, 0.3, 2.0], "r": [-1.6, 0.2, 1.9]})


def _priors() -> C.CostPriors:
    return C.CostPriors(p_stop={"Z1P": 0.4}, night_share={"Z1P": 0.5}, p_stop_pooled=0.3, night_share_pooled=0.4,
                        n_rows=10, labelled_before="2014-01-01T00:00:00+00:00")


def test_the_w2_constants_are_the_replays():
    assert (C.RT_BP, C.OVERSHOOT_BP) == (H.RT_BP, H.OVERSHOOT_BP)


def test_cost_x_is_identical_for_a_gapped_stop_an_overnight_hold_and_a_target_exit():
    cx = C.cost_x(_three_exits(), _priors())
    assert np.ptp(cx) == 0.0 and cx[0] > 0


def test_cost_x_charges_the_expected_overshoot_and_the_expected_nights():
    rows = _three_exits().iloc[:1]
    k = 1e-4 * 1340.0 / 4.0
    nights = C.nights_full_hold(rows)[0]
    assert nights >= 1
    rate = abs(C.swap_spec().usd_per_oz(np.array([1.0]))[0]) / C.swap_spec().ref_price_usd
    want = k * (C.RT_BP + C.OVERSHOOT_BP * 0.4) + nights * 0.5 * k * 1e4 * rate
    assert C.cost_x(rows, _priors())[0] == pytest.approx(want)


def test_an_arm_unseen_on_learn_takes_the_pooled_priors():
    rows = _three_exits().assign(arm="NEW").iloc[:1]
    k = 1e-4 * 1340.0 / 4.0
    nights = C.nights_full_hold(rows)[0]
    rate = abs(C.swap_spec().usd_per_oz(np.array([1.0]))[0]) / C.swap_spec().ref_price_usd
    assert C.cost_x(rows, _priors())[0] == pytest.approx(k * (C.RT_BP + C.OVERSHOOT_BP * 0.3)
                                                         + nights * 0.4 * k * 1e4 * rate)


def test_the_legacy_back_out_recovers_k_exactly_for_gapped_stops_and_overnight_holds():
    rows = _three_exits()
    k = 1e-4 * 1340.0 / 4.0
    sw = C.swap_spec()
    held = C.nights_held(rows)
    signed = sw.usd_per_oz(np.ones(3)) / sw.ref_price_usd
    stop = np.array([1.0, 0.0, 0.0])
    rows["r"] = rows["r_mid"] - k * (C.RT_BP + C.OVERSHOOT_BP * stop - 1e4 * held * signed)
    legacy = rows.drop(columns=["entry", "risk_usd", "barrier_hit"])
    assert held[1] >= 1 and legacy["r_mid"].iloc[0] < -1          # an overnight hold and a gapped stop
    assert np.allclose(C.legacy_k(legacy), k)
    assert np.allclose(C.row_k(rows), k)


def test_priors_are_per_arm_and_refuse_a_label_still_open_at_the_block_start():
    a = _three_exits()
    b = _three_exits().assign(arm="Z9D", barrier_hit="target")
    pri = C.priors(pd.concat([a, b]), before=pd.Timestamp("2014-04-01", tz="UTC"))
    assert pri.p_stop == {"Z1P": pytest.approx(1 / 3), "Z9D": 0.0}
    with pytest.raises(ValueError, match="closes at or after"):
        C.priors(a, before=DEC + pd.Timedelta(hours=2))


ZOO = Path(os.environ.get("GOLDBOT_MODEL_ZOO_DIR", str(Path.home() / "tmp/history-replay/model-zoo")))


@pytest.mark.integration
@pytest.mark.skipif(not (ZOO / "learn_oof.parquet").exists(), reason="H-101 Stage L cache is local-only")
def test_on_h101_rows_cost_x_is_uncorrelated_with_gross_within_arm():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "obj_v1_h101_recompute", Path(__file__).resolve().parents[2] / "scripts/research/obj_v1_h101_recompute.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    j, _ = mod.frame(ZOO)
    d = j[["arm"]].assign(cx=j["cost"], g=np.clip(j["gross"], -10, 10))
    dm = d.groupby("arm")[["cx", "g"]].transform(lambda x: x - x.mean())
    assert abs(np.corrcoef(dm["cx"], dm["g"])[0, 1]) < 0.01
