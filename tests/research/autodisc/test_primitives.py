"""The real MaskProvider (ADR 0058, backlog 140): G1 parity with the existing rule, no lookahead, window guard.

Seeded synthetic 1m bars dated 2015 (inside the explore window); no market data needed."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.research import arm_rules as R
from goldbot.research.arms import load_arms
from goldbot.research.autodisc import ExploreFrame
from goldbot.research.autodisc.gen_combine import exit_menu, family_spec
from goldbot.research.autodisc.nulls import MaskCache
from goldbot.research.autodisc.primitives import ARMS_YAML, NoFamilyArm, PrimitiveProvider, UnsupportedLeaf
from goldbot.research.autodisc.spec import Group, Leaf
from goldbot.research.autodisc.windows import WindowViolation

NO = np.zeros(0, dtype=np.int64)
FAMILIES = ("box_break_close", "sweep_absorb", "hurst_band_break", "low30_touch")
CUT = pd.Timestamp("2015-10-14", tz="UTC")          # a Wednesday 00:00 UTC: whole 15m bars on both sides
LEAVES = [Leaf(kind="regime", name=n) for n in ("rvol_q_hi", "rvol_q_lo", "hurst_trend", "trend_strength_mid")] + [
    Leaf(kind="time", name=n) for n in ("asia", "london", "ny", "london_ny_overlap", "wed", "month_turn")]


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    t = synthetic_ticks("2015-06-01", "2015-12-20", ticks_per_minute=6, seed=5)
    return ticks_to_1m(t)


def _provider(b1: pd.DataFrame) -> PrimitiveProvider:
    return PrimitiveProvider(ExploreFrame.from_frame(b1, "ts_utc"))


def test_g1_spec_reproduces_the_family_rule(b1: pd.DataFrame) -> None:
    prov = _provider(b1)
    arm = prov.arm_for("box_break_close", "15m")
    bars = resample_bars(b1, "15m").reset_index(drop=True)
    ref = R.candidates(arm, R.RuleContext(bars=bars, b1=b1.reset_index(drop=True), blackout_lo=NO, blackout_hi=NO))
    ref = ref.drop_duplicates("idx")
    assert len(ref) > 5
    spec = family_spec("box_break_close", "15m", None, exit_menu("15m")[0])
    mask, side = MaskCache(prov).entries(spec)
    assert np.flatnonzero(mask).tolist() == ref["idx"].tolist()
    assert side[mask].tolist() == ref["side"].tolist()
    # a filter only removes entries and never changes a side
    flt = family_spec("box_break_close", "15m", Leaf(kind="time", name="london"), exit_menu("15m")[0])
    fmask, fside = MaskCache(prov).entries(flt)
    assert (fmask & ~mask).sum() == 0 and fmask.sum() < mask.sum()
    assert (fside == side).all()


def test_masks_are_point_in_time(b1: pd.DataFrame) -> None:
    rng = np.random.default_rng(1)
    after = (b1["ts_utc"] >= CUT).to_numpy()
    pert = b1.copy()
    pert["tick_count"] = pert["tick_count"].astype(float)
    for c in [c for c in pert.columns if c.startswith(("bid_", "ask_"))]:
        pert.loc[after, c] = pert.loc[after, c].to_numpy() * rng.uniform(0.8, 1.25, int(after.sum()))
    pert.loc[after, "tick_count"] = rng.uniform(0.001, 0.01, int(after.sum()))
    a, b = _provider(b1), _provider(pert)
    for tf in ("15m", "1h"):
        before = (utc_close(a, tf) <= CUT).to_numpy()
        assert before.sum() > 500
        for fam in FAMILIES:
            if fam not in a.families(tf):
                continue
            ma, sa = a.family_entries(fam, tf)
            mb, sb = b.family_entries(fam, tf)
            assert ma[before].sum() > 0 or fam != "box_break_close"
            assert (ma[before] == mb[before]).all(), fam
            assert (sa[before] == sb[before]).all(), fam
        for leaf in LEAVES:
            assert (a.predicate_mask(leaf, tf)[before] == b.predicate_mask(leaf, tf)[before]).all(), leaf.name


def utc_close(p: PrimitiveProvider, tf: str) -> pd.Series:
    return pd.Series(pd.DatetimeIndex(p.bars(tf)["visible_at"]).tz_convert("UTC"))


def test_groups_and_unsupported_leaves(b1: pd.DataFrame) -> None:
    prov = _provider(b1)
    a, b = Leaf(kind="time", name="london"), Leaf(kind="time", name="wed")
    both = prov.predicate_mask(Group(op="and", children=(a, b)), "15m")
    either = prov.predicate_mask(Group(op="or", children=(a, b)), "15m")
    assert (both == (prov.predicate_mask(a, "15m") & prov.predicate_mask(b, "15m"))).all()
    assert (either == (prov.predicate_mask(a, "15m") | prov.predicate_mask(b, "15m"))).all()
    wavelet = Leaf(kind="regime", name="wavelet_energy_hi")
    assert not prov.supports(wavelet) and prov.supports(a)
    with pytest.raises(UnsupportedLeaf):
        prov.predicate_mask(wavelet, "15m")
    with pytest.raises(NoFamilyArm):
        prov.family_entries("pdc_break", "15m")
    assert prov.session_mask("all", "15m").all()
    assert 0 < prov.session_mask("london_ny", "15m").mean() < 1


def test_rejects_bars_from_the_confirm_window() -> None:
    late = ticks_to_1m(synthetic_ticks("2017-12-20", "2018-01-05", ticks_per_minute=2, seed=1))
    with pytest.raises(WindowViolation):
        ExploreFrame.from_frame(late, "ts_utc")


def test_arms_yaml_loads() -> None:
    assert load_arms(ARMS_YAML)
