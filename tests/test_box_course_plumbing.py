"""Research plumbing the box/price-action course families need (docs/research/box-mfi-families-2026-10-18.md):
a per-candidate time exit (pooled rungs keep each signal timeframe's own exit), an entry at the next bar's open (H-45
"E: entry at the **next** 1h bar's open"), candidate-borne features (H-40's flags of the candidate's own box and
side), a discovery that screens exactly those pre-registered groups ("K_eff = groups screened (21)") and the screen's
cost-gate reading (research director ruling 2026-10-18, item 2). Every default path is unchanged."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier, triple_barrier_delayed
from goldbot.research.discovery import DiscoveryConfig, discovery_pool
from goldbot.research.pipeline import Prepared, prepare
from goldbot.research.screen import cost_gate, screen_lines, screen_verdict
from goldbot.specialists.base import Specialist


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2024-01-01", "2024-02-15", ticks_per_minute=1, seed=3))


@pytest.fixture(scope="module")
def h1(b1: pd.DataFrame) -> pd.DataFrame:
    return resample_bars(b1, "1h").reset_index(drop=True)


SPEC = BarrierSpec(target_atr=5000.0, stop_atr=5000.0, max_bars=20, name="t")     # out of reach: every label times out


def test_a_candidate_with_its_own_max_bars_is_held_exactly_that_many_bars_and_others_keep_the_spec(h1):
    atr = pd.Series(1.0, index=h1.index)
    sig = pd.DataFrame({"idx": [10, 100], "side": [1, -1], "max_bars": [5, 40]})
    out = triple_barrier(h1, sig, SPEC, atr)
    assert out["barrier_hit"].tolist() == ["time", "time"]
    assert (out["t_exit"] - out["t_entry"]).tolist() == [5, 40]
    plain = triple_barrier(h1, sig[["idx", "side"]], SPEC, atr)
    assert (plain["t_exit"] - plain["t_entry"]).tolist() == [20, 20]


def test_the_delayed_label_honours_a_per_candidate_max_bars(b1, h1):
    atr = pd.Series(1.0, index=h1.index)
    sig = pd.DataFrame({"idx": [10], "side": [1], "max_bars": [7]})
    out = triple_barrier_delayed(h1, b1, sig, SPEC, atr, pd.Timedelta(0))
    assert int(out["t_exit"].iloc[0] - out["t_entry"].iloc[0]) == 7


def test_a_zero_delay_label_fills_at_the_next_bars_first_1m_open(b1, h1):
    atr = pd.Series(1.0, index=h1.index)
    out = triple_barrier_delayed(h1, b1, pd.DataFrame({"idx": [10], "side": [1]}), SPEC, atr, pd.Timedelta(0))
    nxt = pd.Timestamp(h1["ts_utc"].iloc[11])
    first = b1[pd.to_datetime(b1["ts_utc"], utc=True) == nxt].iloc[0]
    assert out["entry"].iloc[0] == first["ask_open"] and int(out["t_entry"].iloc[0]) == 11


class _NextOpen(Specialist):
    family = "t_next_open"
    timeframe = "1h"
    entry_at_next_open = True
    candidate_features = ("flag_a",)

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [30, 60], "side": [1, -1], "flag_a": [1.0, 0.0], "max_bars": [4, 4]})

    @property
    def label_spec(self) -> BarrierSpec:
        return SPEC


def _frame(h1: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    from goldbot.data.resample import mid
    m = mid(h1).reset_index(drop=True)
    x = pd.DataFrame({"ts_utc": m["ts_utc"], "f1": np.arange(len(m), dtype=float)})
    x.attrs["feature_version"] = "t"
    return m, x


def test_prepare_refuses_a_next_open_family_without_its_1m_bars_and_fills_at_the_next_open_with_them(b1, h1):
    spec = _NextOpen()
    with pytest.raises(ValueError, match="1m bars"):
        prepare(spec, h1, frame=_frame(h1))
    prep = prepare(spec, h1, frame=_frame(h1), bars_1m=b1)
    first = b1[pd.to_datetime(b1["ts_utc"], utc=True) == pd.Timestamp(h1["ts_utc"].iloc[31])].iloc[0]
    assert prep.labels["entry"].iloc[0] == first["ask_open"]


def test_prepare_adds_the_candidate_features_at_each_labelled_candidate(b1, h1):
    prep = prepare(_NextOpen(), h1, frame=_frame(h1), bars_1m=b1)
    assert prep.feats["flag_a"].tolist() == [1.0, 0.0]
    assert prep.feats["f1"].tolist() == [30.0, 60.0]


def _prep(feats: pd.DataFrame, spec: Any) -> Prepared:
    return Prepared(spec=spec, labels=pd.DataFrame(), gross=pd.DataFrame(), feats=feats, feature_version="t", n_bars=1)


def test_a_candidate_pool_is_exactly_the_declared_candidate_features_however_sparse():
    feats = pd.DataFrame({"f1": [1.0, 2.0, 3.0], "flag_a": [np.nan, np.nan, 1.0], "side": [1, -1, 1]})
    spec = _NextOpen()
    assert discovery_pool(_prep(feats, spec), (), "candidate") == ["flag_a"]
    assert discovery_pool(_prep(feats, spec), ()) == ["f1"]                       # the frame pool: 80% rule as before
    with pytest.raises(ValueError, match="flag_b"):
        discovery_pool(_prep(feats.drop(columns="flag_a"), type("S", (_NextOpen,), {"candidate_features": ("flag_b",)})()),
                       (), "candidate")


def test_a_candidate_pool_takes_its_groups_from_the_specialist_and_only_in_column_mode():
    from goldbot.research.discovery import candidate_groups
    spec = type("S", (_NextOpen,), {"candidate_features": ("a1", "a2", "b"), "candidate_feature_groups": {"a1": "a", "a2": "a"}})()
    assert candidate_groups(spec, "column") == {"a1": "a", "a2": "a", "b": "b"}
    with pytest.raises(ValueError, match="group mode"):
        candidate_groups(spec, "family")
    with pytest.raises(ValueError, match="no candidate features"):
        candidate_groups(type("T", (_NextOpen,), {"candidate_features": ()})(), "column")


def test_the_pool_setting_leaves_every_existing_discovery_config_hash_unchanged():
    assert "pool" not in DiscoveryConfig().model_dump()
    assert DiscoveryConfig(pool="candidate").model_dump()["pool"] == "candidate"


def _research_pass() -> Any:
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py"
    spec = importlib.util.spec_from_file_location("research_pass_box_course", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_screen_only_trial_reports_what_the_account_can_take_at_the_minimum_lot():
    """The course families are screen-only (`screening`), so `lot_fit=true` must report on the screened candidates
    (RD ruling item 4: "those are skipped, never upsized"): unfit trades are counted, never sized up."""
    from goldbot.research.lot_fit import LotFitSpec
    rp = _research_pass()
    labels = pd.DataFrame({"entry": [2000.0, 2000.0], "ret": [0.002, -0.001], "risk": [0.001, 0.02]})
    lf = rp.screen_lot_fit([_prep(pd.DataFrame(), None).model_copy(update={"labels": labels})], LotFitSpec())
    assert lf is not None and lf["all_candidates"]["n"] == 2 and lf["all_candidates"]["n_unfit"] == 1
    assert rp.screen_lot_fit([], None) is None
    from goldbot.research.metrics import expectancy
    e = expectancy(np.array([0.002, -0.001]), np.array([0.001, 0.001]))
    text = rp.render_screen_failed(screen_verdict({"gross": e, "net": e}), {"lookahead_columns": []},
                                   {"specialist": "box_retest", "from_year": 2010, "to_year": 2025, "n_1m": 1,
                                    "n_dec": 1, "tf": "1h", "trial": 1, "n": 2, "seconds": 1.0, "lot_fit": lf})
    assert "Lot fit" in text


def _scr(mean_r: float) -> dict[str, Any]:
    return screen_verdict({"gross": {"n": 2000, "mean_r": mean_r, "t_stat": 3.0}, "net": {"n": 2000, "mean_r": 0.0}})


def test_the_cost_gate_retires_a_rule_whose_gross_mean_r_is_below_the_gate_and_reports_it():
    low, high = cost_gate(_scr(0.40), 0.85), cost_gate(_scr(0.90), 0.85)
    assert low["retire"] and not high["retire"]
    assert low["threshold_r"] == 0.85 and low["gross_mean_r"] == pytest.approx(0.40)
    text = "\n".join(screen_lines({**_scr(0.40), "cost_gate": low}))
    assert "Cost gate" in text and "RETIRE" in text
    assert "Cost gate" not in "\n".join(screen_lines(_scr(0.40)))                 # no gate, no line
