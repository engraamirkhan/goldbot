"""Discovery grammar v2 (ADR 0058 amendment 2026-11-02): gate leaves and the r4h side are point in time, the macro and
calendar rows are cut at the explore cutoff, the exit list of 5 (break-even trail included), the v2 neighbours, the
absolute net-R floor, the declared K, and the live interpreter's refusal of every v2-only primitive.

Seeded synthetic 1m bars, macro and calendar rows dated 2015 (inside the explore window); no market data needed."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.labels.triple_barrier import triple_barrier
from goldbot.research.autodisc import ExploreFrame
from goldbot.research.autodisc.evaluate import Evaluation, Stat
from goldbot.research.autodisc.gen_combine import MAX_BARS
from goldbot.research.autodisc.gen_gp import (
    GPConfig,
    Resolver,
    catalogue,
    neighbours,
    run_gp,
    side_families,
    v2_exit_menu,
)
from goldbot.research.autodisc.grammar_v2 import (
    R4H,
    V2_MACRO,
    V2_REGIME,
    V2_TIME,
    PrimitiveProviderV2,
)
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.nightly import explore_failures, passes_net_floor
from goldbot.research.autodisc.outcomes import barrier_exit, build_tensor, v2_menu
from goldbot.research.autodisc.primitives import PrimitiveProvider, UnsupportedLeaf
from goldbot.research.autodisc.spec import ExitTemplate, Group, Leaf, StrategySpec, Timeframe
from goldbot.research.autodisc.windows import EXPLORE_CUTOFF

CUT = pd.Timestamp("2015-09-16", tz="UTC")        # a Wednesday 00:00 UTC
V2_LEAVES = ([Leaf(kind="regime", name=n) for n in V2_REGIME] + [Leaf(kind="time", name=n) for n in V2_TIME]
             + [Leaf(kind="macro", name=n) for n in V2_MACRO])


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2015-01-05", "2015-12-20", ticks_per_minute=1, seed=11))


def _macro(seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2014-10-01", "2015-12-31")
    rows = []
    for series, lvl, sd in (("real_yield_10y", 0.5, 0.03), ("broad_dollar", 115.0, 0.3), ("gold_vix", 18.0, 0.6)):
        v = lvl + np.cumsum(rng.normal(0, sd, len(days)))
        for d, x in zip(days, v, strict=True):
            avail = pd.Timestamp(d, tz="UTC") + pd.Timedelta(days=1, hours=23)
            rows.append({"series": series, "series_id": series, "value_date": d, "value": float(x), "vintage": "fr",
                         "available_utc": avail, "ts_utc": avail})
    return pd.DataFrame(rows)


def _calendar() -> pd.DataFrame:
    days = pd.bdate_range("2015-01-06", "2015-12-18")[::7]
    ev = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=13, minutes=30) for d in days]
    return pd.DataFrame({"event_id": [f"e{i}" for i in range(len(ev))], "event_type": "nfp", "tier": 1,
                         "release_id": 0, "event_utc": ev, "status": "scheduled",
                         "available_utc": [e - pd.Timedelta(days=20) for e in ev], "ts_utc": ev, "vintage": "v",
                         "source": "synthetic"})


def _prov(b1: pd.DataFrame, macro: pd.DataFrame | None = None, cal: pd.DataFrame | None = None) -> PrimitiveProviderV2:
    return PrimitiveProviderV2(ExploreFrame.from_frame(b1, "ts_utc"), macro=_macro() if macro is None else macro,
                               calendar=_calendar() if cal is None else cal, min_fit_rows=60, gvz_rank_obs=60)


def test_every_v2_leaf_is_defined_somewhere(b1: pd.DataFrame) -> None:
    p = _prov(b1)
    for tf in ("1h", "4h"):
        for leaf in V2_LEAVES:
            m = p.predicate_mask(leaf, tf)
            assert m.dtype == bool and len(m) == len(p.bars(tf))
            assert m.any(), (tf, leaf.name)
        m, side = p.family_entries(R4H, tf)
        assert set(np.unique(side[m])) == {-1, 1}


def test_v2_masks_and_r4h_are_point_in_time(b1: pd.DataFrame) -> None:
    rng = np.random.default_rng(1)
    after = (b1["ts_utc"] >= CUT).to_numpy()
    pert = b1.copy()
    for c in [c for c in pert.columns if c.startswith(("bid_", "ask_"))]:
        pert.loc[after, c] = pert.loc[after, c].to_numpy() * rng.uniform(0.8, 1.25, int(after.sum()))
    macro = _macro()
    m_after = pd.to_datetime(macro["available_utc"], utc=True) >= CUT
    macro_p = macro.copy()
    macro_p.loc[m_after, "value"] = macro_p.loc[m_after, "value"] * rng.uniform(-3, 3, int(m_after.sum()))
    cal = _calendar()
    late = pd.DataFrame({**cal.iloc[0].to_dict(), "event_id": "late", "event_utc": CUT + pd.Timedelta(hours=13),
                         "available_utc": CUT + pd.Timedelta(hours=1), "ts_utc": CUT}, index=[0])
    cal_p = pd.concat([cal, late], ignore_index=True)
    a, b = _prov(b1, macro, cal), _prov(pert, macro_p, cal_p)
    for tf in ("1h", "4h", "1d"):
        before = (pd.to_datetime(a.bars(tf)["visible_at"], utc=True) <= CUT).to_numpy()
        assert before.sum() > 50
        ca, cb = a.v2_columns(tf), b.v2_columns(tf)
        for col in ca.columns:
            assert np.array_equal(ca[col].to_numpy()[before], cb[col].to_numpy()[before], equal_nan=True), (tf, col)
            assert np.isfinite(ca[col].to_numpy()[before]).any(), (tf, col)      # the test reads real values
        for leaf in V2_LEAVES:
            assert (a.predicate_mask(leaf, tf)[before] == b.predicate_mask(leaf, tf)[before]).all(), (tf, leaf.name)
        sa, sb = a.family_entries(R4H, tf)[1], b.family_entries(R4H, tf)[1]
        assert (sa[before] == sb[before]).all(), tf


def test_macro_and_calendar_rows_are_cut_at_the_explore_cutoff(b1: pd.DataFrame) -> None:
    macro = _macro()
    leak = macro.iloc[[0]].assign(value_date=pd.Timestamp("2018-01-02"),
                                  available_utc=pd.Timestamp("2018-01-03 23:00", tz="UTC"))
    cal = _calendar()
    cal_leak = cal.iloc[[0]].assign(event_id="x", event_utc=pd.Timestamp("2018-01-05 13:30", tz="UTC"),
                                    available_utc=pd.Timestamp("2017-12-01", tz="UTC"))
    p = _prov(b1, pd.concat([macro, leak], ignore_index=True), pd.concat([cal, cal_leak], ignore_index=True))
    assert p._macro is not None and pd.to_datetime(p._macro["available_utc"], utc=True).max() < EXPLORE_CUTOFF
    assert pd.to_datetime(p._macro["value_date"], utc=True).max() < EXPLORE_CUTOFF
    assert p._cal is not None and pd.to_datetime(p._cal["event_utc"], utc=True).max() < EXPLORE_CUTOFF
    assert len(p._macro) == len(macro) and len(p._cal) == len(cal)


def test_vocabulary_v2_only_in_the_v2_provider_and_r4h_is_never_an_entry_leaf(b1: pd.DataFrame) -> None:
    p = _prov(b1)
    for leaf in V2_LEAVES:
        assert p.supports(leaf)
        with pytest.raises(UnsupportedLeaf):              # the live interpreter (arm_rules_spec) checks with v1's
            PrimitiveProvider.check_supported(leaf)
    r4h = Leaf(kind="structure", name="family_rule", params={"rule": R4H})
    assert not p.supports(r4h)
    assert p.supports(Leaf(kind="regime", name="rvol_q_hi"))          # v1 leaves stay
    cat = [leaf for leaf, _ in catalogue(("box_next", R4H), "v2")]
    assert r4h not in cat and all(x in cat for x in V2_LEAVES)
    assert all(x.kind != "macro" for x, _ in catalogue(("box_next",), "v1"))
    cfg = GPConfig(timeframe="1h", families=("box_next",), grammar="v2")
    assert side_families(cfg) == ("box_next", R4H)
    assert side_families(cfg.model_copy(update={"grammar": "v1"})) == ("box_next",)


def test_v2_exit_list_of_five_matches_the_tensor_menu() -> None:
    for tf in ("1h", "4h", "1d"):
        menu = v2_exit_menu(tf)
        assert len(menu) == 5 and {e.stop for e in menu} == {"atr_1.0"}
        names = [barrier_exit(e, MAX_BARS[tf]).name for e in menu]
        assert names == [x.name for x in v2_menu(MAX_BARS[tf])]
    be = barrier_exit(ExitTemplate(stop="atr_1.0", target="be_trail"), 10)
    pol = be.policy()
    assert be.target_atr is None and pol is not None and pol.trail_atr == pol.trail_after_atr == 1.0
    assert barrier_exit(ExitTemplate(stop="atr_1.0", target="3R"), 10).target_atr == 3.0
    assert barrier_exit(ExitTemplate(stop="atr_1.0", target="2R"), 10).policy() is None


def _bars(path: list[tuple[float, float, float]]) -> pd.DataFrame:
    ts = pd.date_range("2015-03-02 08:00", periods=len(path), freq="1h", tz="UTC")
    d: dict[str, object] = {"ts_utc": ts}
    for side in ("bid", "ask"):
        off = 0.0 if side == "bid" else 0.1
        d[f"{side}_high"] = [h + off for h, _, _ in path]
        d[f"{side}_low"] = [lo + off for _, lo, _ in path]
        d[f"{side}_close"] = [c + off for _, _, c in path]
    return pd.DataFrame(d)


def test_be_trail_moves_the_stop_to_break_even_after_one_r() -> None:
    # long entry at the ask close of bar 0 (100.1); ATR 1: stop 99.1. Bar 1 reaches bid 101.2 (> +1 R), arming the trail
    # at 101.2 - 1 = 100.2 >= entry; bar 2 falls to 99.5: out at the trail (about break-even), never at the 99.1 stop
    bars = _bars([(100.0, 100.0, 100.0), (101.2, 100.3, 101.0), (101.0, 99.5, 99.6), (99.8, 99.0, 99.2)])
    ex = barrier_exit(ExitTemplate(stop="atr_1.0", target="be_trail"), 3)
    lab = triple_barrier(bars, pd.DataFrame({"idx": [0], "side": [1]}), ex.barrier_spec(), pd.Series([1.0] * 4),
                         policy=ex.policy())
    row = lab.iloc[0]
    assert row["barrier_hit"] == "trail" and row["t_exit"] == 2
    assert row["exit"] == pytest.approx(100.2) and row["ret"] > 0
    plain = barrier_exit(ExitTemplate(stop="atr_1.0", target="time", time_bars=3), 3)
    lab2 = triple_barrier(bars, pd.DataFrame({"idx": [0], "side": [1]}), plain.barrier_spec(), pd.Series([1.0] * 4))
    assert lab2.iloc[0]["barrier_hit"] == "stop"                     # without the trail the same path stops out


def _spec(entry: Leaf | Group, flt: Leaf | None = None, target: str = "2R") -> StrategySpec:
    return StrategySpec(timeframe="4h", side_rule="with",
                        side_primitive=Leaf(kind="structure", name="family_rule", params={"rule": R4H}),
                        entry=entry, filter=flt, exit=ExitTemplate(stop="atr_1.0", target=target))  # type: ignore[arg-type]


def test_v2_neighbours_move_v2_ladders_and_targets_but_never_the_stop() -> None:
    s = _spec(Group(op="and", children=(Leaf(kind="regime", name="har_mid"), Leaf(kind="time", name="london"))),
              Leaf(kind="macro", name="gvz_lo"))
    nb = neighbours(s, "v2")
    got = {(n.entry.model_dump_json(), n.filter.model_dump_json() if n.filter else "", n.exit.target) for n in nb}
    assert len(nb) == 5                     # har lo/hi, gvz mid, target 1R/3R
    assert all(n.exit.stop == "atr_1.0" for n in nb)
    assert {n.exit.target for n in nb} == {"1R", "2R", "3R"}
    assert any("har_lo" in e for e, _, _ in got) and any("har_hi" in e for e, _, _ in got)
    assert any("gvz_mid" in f for _, f, _ in got)
    assert len(neighbours(_spec(Leaf(kind="macro", name="ry_up"), target="be_trail"), "v2")) == 0
    assert len(neighbours(_spec(Leaf(kind="regime", name="nr4"), target="3R"), "v2")) == 2       # nr7, 2R


def _ev(mean_r: float | None) -> Evaluation:
    return Evaluation(spec_hash="h", exit_name="s1_t2R", n_events=400, mean_r=mean_r, t_stat=1.0, time_in_market=0.1,
                      floors_passed=True, floor_failures=[], long=Stat(n=200), short=Stat(n=200), fold_t=[1.0] * 4,
                      fitness=1.0, drift_baseline_r=0.0, excess_r=0.04, stress_mean_r=0.01, stress_passed=True,
                      input_digest="d", excess_fold_t=[1.0] * 4, excess_fitness=1.0)


def test_absolute_net_r_floor() -> None:
    assert passes_net_floor(_ev(0.03), None)
    assert not passes_net_floor(_ev(0.03), 0.05) and passes_net_floor(_ev(0.05), 0.05)
    assert not passes_net_floor(_ev(None), 0.05)
    assert explore_failures(_ev(0.03)) == []
    assert explore_failures(_ev(0.03), 0.05) == ["net mean R 0.030 < floor +0.050"]


def test_a_v2_search_ledgers_version_2_and_stays_in_the_grammar(b1: pd.DataFrame, tmp_path) -> None:
    p = _prov(b1)
    tf: Timeframe = "4h"
    from goldbot.features.technical import atr
    t = build_tensor(ExploreFrame.from_frame(p.bars(tf), "ts_utc"), atr(p.mid_bars(tf), 14), v2_menu(MAX_BARS[tf]))
    fams = tuple(f for f in ("box_next", "tsmom") if f in p.families(tf))
    cfg = GPConfig(timeframe=tf, population=6, generations=1, seed=4, families=fams, grammar="v2", min_events=5)
    ledger = SearchLedger(tmp_path / "l.db", mode="synthetic")
    run = run_gp(t, p, cfg, ledger, resolver=Resolver(p))
    assert run.evaluated and ledger.k_search() == len(run.evaluated)
    versions = {json.loads(e.payload)["generator_version"] for e in ledger.entries() if e.kind == "spec"}
    assert versions == {"2"}
    menu = set(v2_exit_menu(tf))
    for c in run.evaluated:
        assert c.spec.exit in menu
        assert p.supports(c.spec.entry) and (c.spec.filter is None or p.supports(c.spec.filter))
