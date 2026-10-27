"""goldbot.research.arm_rules_scalp: the H-81 scalp-volume arm rules (docs/research/prereg-2026-10-27-scalp-volume.md,
amendments (a) and (b)). Causal and prefix invariant (arms-2026-10-25.md 8.0); fixed-dollar stops and targets with a
time exit, never ATR multiples; window W and the spacing as frozen; the spread gate journalled as a skip; Z7's
half-hour read on the bar's CLOSE time (amendment (b) 1); the controls' entry and side draws; live (the engine's
1m/5m minute pass, `exploration._minutes`, on its own 1m memory) equal to the history run; the shipped spec and
registry; and the 2010-2017 event counts against the prereg's census."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import EngineConfig, exploration
from goldbot.features.technical import atr
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_scalp as Z
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
NY = "America/New_York"
SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
RAW = Path("/Users/aamirkhan/tmp/ts/raw")
MEMORY = int(EngineConfig.model_fields["max_bars_in_memory"].default)
RULES = tuple(Z.RULE_TF)
CUT = pd.Timestamp("2023-12-20", tz="UTC")
# H-81 section 1 and amendment (a): arm id -> (rule, stop $, target R, hold bars); P icm-probe, D icm-demo, V vantage
ROWS: dict[str, tuple[str, float, float, int]] = {
    "Z1P": ("tick_surge", 3.0, 1.5, 15), "Z2P": ("run_fade", 3.0, 1.0, 15), "Z5P": ("extreme_break", 4.5, 1.5, 6),
    "Z8P": ("absorb_fade", 3.0, 1.0, 15), "Z7P": ("halfhour_persist", 4.5, 1.5, 6), "Z4P": ("round_reject", 3.0, 1.0, 15),
    "Z3D": ("round_break", 4.5, 1.5, 6), "Z4D": ("round_reject", 3.0, 1.0, 15), "Z6D": ("nr4_break", 4.5, 1.5, 6),
    "Z7D": ("halfhour_persist", 4.5, 1.5, 6), "Z9D": ("htf_reclaim", 4.5, 1.5, 12),
    "Z10D": ("three_horizon", 4.5, 1.5, 6), "ZCD": ("scalp_control", 4.5, 1.5, 6),
    "Z3V": ("round_break", 7.0, 1.5, 6), "Z5V": ("extreme_break", 7.0, 1.5, 6), "Z6V": ("nr4_break", 7.0, 1.5, 6),
    "Z7V": ("halfhour_persist", 7.0, 1.5, 6), "ZCV": ("scalp_control", 7.0, 1.5, 6)}
TWINS = {"VS0": "S0", "VS1A": "S1A", "VS1B": "S1B", "VS2A": "S2A", "VS2B": "S2B", "VS3A": "S3A", "VS3B": "S3B",
         "VS5A": "S5A", "VS5B": "S5B"}
PROBE = {"Z1P", "Z2P", "Z5P", "Z8P", "Z7P", "Z4P"}


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    """Eleven weeks of synthetic minutes with weekends; the synthetic feed has one tick count, so v is a seeded
    lognormal series (the rules only compare v with its own trailing median)."""
    b = ticks_to_1m(synthetic_ticks("2023-11-01", "2024-01-20", ticks_per_minute=4, seed=7)).reset_index(drop=True)
    return b.assign(tick_count=np.round(np.random.default_rng(3).lognormal(3.0, 0.6, len(b))))


def _arm(rule: str, arm_id: str | None = None, **params: Any) -> A.Arm:
    p: dict[str, Any] = {"stop_usd": 3.0, "target_r": 1.5, "hold_bars": 6, **params}
    if rule == "scalp_control":
        p.setdefault("p", 0.07)
    return A.Arm(id=arm_id or "T" + rule.replace("_", "")[:8], rule=rule, timeframe=Z.RULE_TF[rule], params=p)


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    bars = b1 if tf == "1m" else resample_bars(b1, tf)
    return R.RuleContext(bars=bars.reset_index(drop=True), b1=b1.reset_index(drop=True), blackout_lo=NO,
                         blackout_hi=NO)


@pytest.fixture(scope="module")
def ctxs(b1) -> dict[str, R.RuleContext]:
    return {tf: _ctx(b1, tf) for tf in ("1m", "5m")}


@pytest.fixture(scope="module")
def runs(ctxs) -> dict[str, pd.DataFrame]:
    return {r: Z.RULE_FUNCS[r](_arm(r), ctxs[Z.RULE_TF[r]]) for r in RULES}


# ---------------------------------------------------------------- registration
def test_every_scalp_family_is_a_known_rule_of_the_loader_and_the_rule_table() -> None:
    assert set(Z.RULE_TF) == set(A.SCALP_RULES) == set(Z.RULE_FUNCS)
    for rule in A.SCALP_RULES:
        assert rule in A.RULES and rule in R.RULE_FUNCS


def test_an_arm_on_the_wrong_timeframe_or_without_a_fixed_bracket_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="runs on 1m"):
        A.Arm(id="X", rule="tick_surge", timeframe="5m", params={"stop_usd": 3.0, "target_r": 1.5, "hold_bars": 15})
    with pytest.raises(ValueError, match="fixed bracket"):
        A.Arm(id="X", rule="nr4_break", timeframe="5m", params={"stop_atr": 1.0, "target_r": 1.5, "hold_bars": 6})
    with pytest.raises(ValueError, match="entry probability"):
        A.Arm(id="X", rule="scalp_control", timeframe="5m", params={"stop_usd": 4.5, "target_r": 1.5, "hold_bars": 6})


# ---------------------------------------------------------------- causality, brackets, window, spacing
@pytest.mark.parametrize("rule", RULES)
def test_every_rule_fires_on_the_fixture_so_no_check_below_is_vacuous(runs, rule) -> None:
    assert len(runs[rule]) >= 30


@pytest.mark.parametrize("rule", RULES)
def test_appending_bars_never_changes_a_past_candidate_or_its_spread_verdict(b1, ctxs, runs, rule) -> None:
    """8.0: "its test proves prefix invariance": the history up to CUT gives exactly the full run's candidates before
    CUT (side, bracket and the spread gate's skip), so no decision reads a bar after its own close."""
    tf = Z.RULE_TF[rule]
    pre = _ctx(b1[b1["visible_at"] <= CUT], tf)
    got = Z.RULE_FUNCS[rule](_arm(rule), pre)
    full = runs[rule]
    want = full[ctxs[tf].close_ts[full["idx"].to_numpy()] <= CUT].reset_index(drop=True)
    assert len(want) >= 10
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want, check_dtype=False)


@pytest.mark.parametrize("rule", RULES)
def test_every_candidate_carries_the_rows_fixed_dollar_stop_target_and_time_exit(ctxs, rule) -> None:
    """Section 0: "Stop and target are fixed prices set at entry ... The time exit is at the close of the hold-th
    decision bar"; "Stops are fixed dollars per broker, not ATR multiples" (Vantage's $7.00 here)."""
    arm = _arm(rule, stop_usd=7.0, target_r=1.5, hold_bars=12)
    kept, _ = R.screened(arm, ctxs[Z.RULE_TF[rule]])
    assert len(kept) and set(kept["risk_usd"]) == {7.0} and set(kept["target_r"]) == {1.5}
    assert set(kept["max_bars"]) == {11}                      # triple_barrier_1m's count: the close of bar t + 12
    queued = exploration._pending(exploration.Arm(spec=arm, style="scalp"), kept.head(3), pd.Timestamp("2024-01-02 10:00", tz="UTC"))
    assert {(p.stop_usd, p.target_usd, p.hold_bars) for p in queued} == {(7.0, 10.5, 12)}
    assert "skip" not in kept.columns


@pytest.mark.parametrize("rule", RULES)
def test_every_candidate_decides_inside_window_w_off_the_rollover_and_the_friday_close(ctxs, runs, rule) -> None:
    """W: "from 19:00 New York ... to 16:00 New York, Monday to Friday trading days"; the common filters' clock part."""
    close = ctxs[Z.RULE_TF[rule]].close_ts[runs[rule]["idx"].to_numpy()]
    ny = close.tz_convert(NY)
    nm = ny.hour * 60 + ny.minute
    assert (((nm >= 19 * 60) | (nm < 16 * 60))).all()
    assert ((ny + pd.Timedelta(hours=7)).dayofweek < 5).all()
    um = close.hour * 60 + close.minute
    assert not ((um >= 20 * 60 + 45) & (um <= 22 * 60 + 15)).any()
    assert not ((close.dayofweek == 4) & (um >= 19 * 60)).any()


@pytest.mark.parametrize("rule", [r for r in RULES if r in Z.GAP])
def test_a_family_fires_at_most_once_per_its_frozen_number_of_bars(runs, rule) -> None:
    assert (np.diff(runs[rule]["idx"].to_numpy()) >= Z.GAP[rule]).all()


# ---------------------------------------------------------------- the spread gate
def test_a_wide_spread_at_the_decision_minute_skips_the_candidate_as_spread_wide(b1) -> None:
    """Section 0: "skip the candidate when the spread at decision is above 2 x the median spread of the account's own
    last 60 minutes. Each skip is journalled `spread_wide`": it leaves the kept frame for the skipped one with that
    reason, and a wide minute just after the decision changes nothing."""
    arm = _arm("scalp_control")
    ctx = _ctx(b1, "5m")
    kept, _ = R.screened(arm, ctx)
    victim = kept.iloc[len(kept) // 2]
    close = ctx.close_ts[int(victim["idx"])]
    minute = b1.index[b1["visible_at"] == close][0]
    for at, gated in ((minute + 1, False), (minute, True)):
        wide = b1.copy()
        wide.loc[at, "spread_mean"] = 2.01 * float(b1["spread_mean"].iloc[minute - 60:minute].median())
        k2, s2 = R.screened(arm, _ctx(wide, "5m"))
        assert (int(victim["idx"]) in set(s2["idx"])) is gated
        assert (int(victim["idx"]) in set(k2["idx"])) is not gated
        if gated:
            assert set(s2.loc[s2["idx"] == victim["idx"], "reason"]) == {Z.SPREAD_WIDE}


def test_a_spread_at_exactly_twice_the_median_is_not_wide(b1) -> None:
    ctx = _ctx(b1, "5m")
    close = ctx.close_ts[600:601]
    minute = b1.index[b1["visible_at"] == close[0]][0]
    at = b1.copy()
    med = float(b1["spread_mean"].iloc[minute - 60:minute].median())
    at.loc[minute, "spread_mean"] = 2.0 * med
    assert not Z.spread_wide(_ctx(at, "5m"), close)[0]
    at.loc[minute, "spread_mean"] = 2.0 * med + 1e-6
    assert Z.spread_wide(_ctx(at, "5m"), close)[0]


# ---------------------------------------------------------------- family specifics
@pytest.mark.parametrize(("arm_id", "slots", "labels"), [("Z7D", [0, 30], {25, 55}), ("Z7P", [15, 45], {10, 40})])
def test_z7_decides_on_the_half_hour_close_never_on_the_bar_labelled_by_its_open(ctxs, arm_id, slots, labels) -> None:
    """Amendment (b) 1: "τ is the 5m bar's close time. Dukascopy bars are labelled by their open, so history adds the
    bar length before any clock test": the :00/:30 decision is the bar labelled :25/:55 (Z7P: :15/:45, labels :10/:40)."""
    ctx = ctxs["5m"]
    c = Z.halfhour_persist(_arm("halfhour_persist", slots_ny=slots), ctx)
    idx = c["idx"].to_numpy()
    assert set(pd.DatetimeIndex(ctx.bars["ts_utc"].iloc[idx]).tz_convert(NY).minute) == labels
    assert set(Z.decision_close(ctx.bars, "5m")[idx].tz_convert(NY).minute) == set(slots)


def _z7_side_brute(ctx: R.RuleContext, i: int) -> int:
    """Independent Z7: the same New York half-hour on each of the 10 weekdays before the trading day."""
    close = Z.decision_close(ctx.bars, "5m")
    px = dict(zip(close, mid(ctx.bars)["close"].astype(float), strict=True))
    tau = close[i].tz_convert(NY)
    day = (tau + pd.Timedelta(hours=7)).normalize().tz_localize(None)
    total, present = 0.0, 0
    for k in range(1, 11):
        d = pd.Timestamp(np.busday_offset(np.datetime64(day.date()), -k, roll="forward"))
        cal = d - pd.Timedelta(days=1) if tau.hour * 60 + tau.minute >= 17 * 60 else d
        t0 = pd.Timestamp(cal.year, cal.month, cal.day, tau.hour, tau.minute, tz=NY).tz_convert("UTC")
        t1 = t0 + pd.Timedelta(minutes=30)
        if t0 in px and t1 in px:
            total += px[t1] - px[t0]
            present += 1
    return int(np.sign(total)) if present >= 8 else 0


def test_z7s_side_is_the_sign_of_the_same_half_hours_return_summed_over_the_previous_ten_trading_days(ctxs) -> None:
    ctx = ctxs["5m"]
    c = Z.halfhour_persist(_arm("halfhour_persist"), ctx)
    for j in np.linspace(0, len(c) - 1, 12).astype(int):
        assert int(c["side"].iloc[j]) == _z7_side_brute(ctx, int(c["idx"].iloc[j]))
    # a half-hour close with fewer than 8 of its 10 days present (the first two weeks of the fixture) never fires
    assert ctx.close_ts[int(c["idx"].iloc[0])] >= pd.Timestamp("2023-11-13", tz="UTC")


def test_z4p_trades_the_five_dollar_offset_handles_and_z4d_the_ten_dollar_ones(ctxs) -> None:
    """Amendment (a): Z4P is "Z4's rule at the $5-offset handles (prices = 5.00 mod 10.00)": every rejection is at a
    level of that class, so the two strata never share a level."""
    ctx = ctxs["1m"]
    m = mid(ctx.bars)
    hi, lo, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    for off in (0.0, 5.0):
        cands = Z.round_reject(_arm("round_reject", handle_usd=10.0, handle_offset_usd=off), ctx)
        assert len(cands) >= 30
        for i, side in zip(cands["idx"].to_numpy(dtype=int), cands["side"].to_numpy(dtype=int), strict=True):
            prev = float(c[i - 1])
            # the touched level: the first price of the class (x = off mod 10) beyond the previous close
            level = prev + (off - prev) % 10 if side < 0 else prev - (prev - off) % 10
            assert level != prev and round(level % 10, 6) in (off, 10.0 if off == 0 else off)
            assert (hi[i] >= level and c[i] < level) if side < 0 else (lo[i] <= level and c[i] > level)


def test_z1_continues_a_surge_bar_and_z8_fades_the_move_a_surge_bar_absorbs(ctxs) -> None:
    """Z1 side = sign(c_t - o_t); Z8 side = -sign(c_{t-1} - c_{t-6}); they are disjoint by the body condition."""
    ctx = ctxs["1m"]
    m = mid(ctx.bars)
    o, c = m["open"].to_numpy(dtype=float), m["close"].to_numpy(dtype=float)
    z1, z8 = Z.tick_surge(_arm("tick_surge"), ctx), Z.absorb_fade(_arm("absorb_fade"), ctx)
    assert (z1["side"].to_numpy() == np.sign(c - o)[z1["idx"]]).all()
    i8 = z8["idx"].to_numpy()
    assert (z8["side"].to_numpy() == -np.sign(c[i8 - 1] - c[i8 - 6])).all()
    assert not set(z1["idx"]) & set(z8["idx"])


def test_z9_follows_only_the_last_closed_1h_bars_trend(b1, ctxs) -> None:
    """A 1h bar still forming at the decision close never votes: its last minute rewritten changes no candidate whose
    decision precedes that bar's close."""
    ctx = ctxs["5m"]
    base = Z.htf_reclaim(_arm("htf_reclaim"), ctx)
    i = int(base["idx"].iloc[len(base) // 2])
    close = ctx.close_ts[i]
    hour_end = close.ceil("h")
    if hour_end == close:
        hour_end += pd.Timedelta(hours=1)
    later = b1.copy()
    sel = (later["visible_at"] > close) & (later["visible_at"] <= hour_end)
    for k in ("bid_close", "ask_close", "bid_high", "ask_high"):
        later.loc[sel, k] = later.loc[sel, k] + 500.0
    moved = Z.htf_reclaim(_arm("htf_reclaim"), R.RuleContext(bars=ctx.bars, b1=later, blackout_lo=NO, blackout_hi=NO))
    assert int(moved.loc[moved["idx"] == i, "side"].iloc[0]) == int(base.loc[base["idx"] == i, "side"].iloc[0])


def test_the_controls_enter_on_their_own_draw_and_take_both_sides_from_a_separate_key(ctxs) -> None:
    """ZCD: "enter when hash_u(id, close) < p". Its side is drawn on `id:side` (module docstring): `hash_side(id,
    close)` is the entry draw < 0.5, which would make every control entry long."""
    ctx = ctxs["5m"]
    arm = _arm("scalp_control", arm_id="ZCD", stop_usd=4.5, p=0.07)
    c = Z.scalp_control(arm, ctx)
    close = Z.decision_close(ctx.bars, "5m")
    ok = np.flatnonzero(Z.in_w(close))
    assert c["idx"].tolist() == [int(i) for i in ok if R.hash_u("ZCD", close[int(i)]) < 0.07]
    assert c["side"].tolist() == [R.hash_side("ZCD:side", close[int(i)]) for i in c["idx"]]
    assert set(c["side"]) == {-1, 1} and 0.4 < (c["side"] > 0).mean() < 0.6
    assert {R.hash_side("ZCD", close[int(i)]) for i in c["idx"]} == {1}      # the literal reading: long only


# ---------------------------------------------------------------- live == history (the engine's 1m/5m pass)
def _live(b1: pd.DataFrame, arm: A.Arm, close: pd.Timestamp) -> list[tuple[int, float, float, int]]:
    """What `exploration._minutes` queues for `arm` at `close` from the engine's 1m memory (the last
    max_bars_in_memory complete minutes, no unbuilt ticks)."""
    mem = b1[b1["visible_at"] <= close].tail(MEMORY).reset_index(drop=True)
    ex = SimpleNamespace(arms={arm.id: exploration.Arm(spec=arm, style="scalp")}, retired=frozenset(), last_bar={},
                         queue=deque(), last_minute=None, deferred={}, needs_context=frozenset(), held_back={},
                         netting=None, ahead_seen=None)
    eng = SimpleNamespace(exploration=ex, cfg=SimpleNamespace(live_clock=False, account_id="t"), bars_1m=mem,
                          ticks=[], decisions=[])
    exploration._minutes(eng, ex, close + pd.Timedelta(seconds=exploration.GRACE_S + 0.5))  # type: ignore[arg-type]
    assert not [d for d in eng.decisions if d.get("action") == "exploration_rule_error"], eng.decisions
    assert exploration.step(eng, close)  # type: ignore[arg-type]       # the 15m+ pass never runs a 1m/5m arm
    return [(p.side, p.stop_usd, p.target_usd, p.hold_bars) for p in ex.queue]


@pytest.mark.parametrize("rule", RULES)
def test_the_engines_minute_pass_queues_every_history_candidate_and_no_other(b1, ctxs, runs, rule) -> None:
    arm = _arm(rule)
    tf = Z.RULE_TF[rule]
    ctx = ctxs[tf]
    kept, _ = R.screened(arm, ctx)
    first = pd.Timestamp(b1["visible_at"].iloc[(exploration.lookback(arm) + 1) * tf_seconds(tf) // 60 + 1440])
    close = ctx.close_ts
    fired = [close[int(i)] for i in kept["idx"] if close[int(i)] >= first]
    assert len(fired) >= 4, "the fixture must give candidates after the lookback fills, else this is vacuous"
    rows = {close[int(i)]: (int(side), float(risk), float(risk * tr), int(mb) + 1)
            for i, side, risk, tr, mb in zip(*(kept[k].to_numpy() for k in ("idx", "side", "risk_usd", "target_r",
                                                                           "max_bars")), strict=True)}
    rng = np.random.default_rng(11)
    later = np.flatnonzero(np.asarray(close >= first) & Z.in_w(close))
    quiet = [close[int(i)] for i in rng.choice(later, 20, replace=False) if close[int(i)] not in rows][:4]
    picks = [fired[int(j)] for j in np.linspace(0, len(fired) - 1, 4).round().astype(int)]
    for at in picks:
        assert _live(b1, arm, at) == [rows[at]], at
    for at in quiet:
        assert _live(b1, arm, at) == [], f"live queues at {at}, history has no candidate"


def test_every_declared_lookback_fits_the_engines_memory_and_reaches_z7s_ten_trading_days() -> None:
    for rule, n in Z.LOOKBACK_BARS.items():
        assert (n + 1) * tf_seconds(Z.RULE_TF[rule]) // 60 <= MEMORY, rule
        assert exploration.lookback(_arm(rule)) == n
    assert Z.LOOKBACK_BARS["halfhour_persist"] > 11 * 288          # a weekday holds at most 288 5m bars


# ---------------------------------------------------------------- the shipped spec and registry
@pytest.fixture(scope="module")
def spec() -> dict[str, A.Arm]:
    return {a.id: a for a in A.load_arms(SPEC)}                  # hash-checked on load


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_the_shipped_spec_freezes_the_27_h81_ids_with_the_prereg_brackets(spec) -> None:
    """Section 5 + amendments (a) and (b) 5: 25 + Z7P/Z4P = 27 ids, each hashed (an edit needs a new id)."""
    assert len(set(ROWS) | set(TWINS)) == 27 and set(ROWS) | set(TWINS) <= set(spec)
    for arm_id, (rule, stop, target, hold) in ROWS.items():
        a = spec[arm_id]
        assert (a.rule, a.timeframe, a.status) == (rule, Z.RULE_TF[rule], "run"), arm_id
        assert (a.params["stop_usd"], a.params["target_r"], a.params["hold_bars"]) == (stop, target, hold), arm_id
        assert a.forward_only and a.config_hash == A.arm_hash(a)
    assert spec["Z7P"].params["slots_ny"] == [15, 45] and spec["Z7D"].params["slots_ny"] == [0, 30]
    assert spec["Z4P"].params["handle_offset_usd"] == 5.0 and "handle_offset_usd" not in spec["Z4D"].params
    assert (spec["ZCD"].params["p"], spec["ZCV"].params["p"]) == (0.07, 0.05)


def test_no_h81_arm_runs_in_the_h60_history_matrix(spec) -> None:
    """Amendment (b) 4: the only history run is the m_eff correlation run; the H-60 matrix never reads their returns."""
    assert not (set(ROWS) | set(TWINS)) & set(A.runnable_ids(SPEC))


def test_the_vantage_s_twins_are_frozen_blocked_on_their_cost_gate_with_their_originals_bodies(spec) -> None:
    for vid, orig in TWINS.items():
        a = spec[vid]
        assert a.twin_of == orig and a.status == "blocked" and "$0.24" in a.note and "200" in a.note
        assert (a.rule, a.timeframe, a.params, a.max_open) == (spec[orig].rule, spec[orig].timeframe,
                                                               spec[orig].params, spec[orig].max_open)


def test_icm_probe_registers_its_six_strata_in_the_unchanged_scalp_pot_at_the_scalp_cap(accounts) -> None:
    ex = accounts["icm-probe"].exploration
    assert {a for a in ex.arms if a in ROWS} == PROBE
    assert {(ex.arms[a].style, ex.arms[a].max_loss, ex.arms[a].control_share) for a in PROBE} == {("scalp", 6.5, None)}
    assert (ex.pots["scalp"].gbp, ex.pots["scalp"].max_open, ex.max_open_total) == (138, 12, 17)
    from goldbot.engine.exploration import config_for
    cfg = config_for(accounts["icm-probe"])
    assert cfg is not None and PROBE <= {a.arm_id for a in cfg.arms} and not cfg.dropped


def test_every_registered_h81_arm_is_a_scalp_arm_on_a_host_with_a_scalp_pot(accounts) -> None:
    """The read's stop rule is per style (-30 R in 300 for scalp, telegram.labs ARM_STOP): a scalp arm registered
    under another style would be retired by the wrong rule. A host without a scalp pot carries none of them."""
    for aid, acc in accounts.items():
        for arm_id, reg in acc.exploration.arms.items():
            if arm_id in ROWS or arm_id in TWINS:
                assert reg.style == "scalp" and "scalp" in acc.exploration.pots, (aid, arm_id)
                assert reg.max_loss <= 6.5


def test_live_accounts_stay_disabled_and_every_login_is_null() -> None:
    accs = acc_mod.load_accounts(logins=False)
    assert all(not a.enabled for a in accs.values() if a.mode == "live")
    raw = (SPEC.parent / "accounts.yaml").read_text()
    assert all(line.split("login:")[1].split("#")[0].strip() == "null"
               for line in raw.splitlines() if line.strip().startswith("login:"))


# ---------------------------------------------------------------- 2010-2017 event counts against the census
def _load_1m() -> pd.DataFrame:
    parts = [pd.read_parquet(RAW / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(2010, 2018)]
    d = pd.concat(parts, ignore_index=True)
    for k in ("ts_utc", "visible_at"):
        d[k] = pd.to_datetime(d[k], utc=True).astype("datetime64[ns, UTC]")
    d = d.sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
    return d[d["ts_utc"] < pd.Timestamp("2018-01-01", tz="UTC")].reset_index(drop=True)


@pytest.mark.integration          # slow: 8 years of 1m bars (no `slow` marker in this repo)
@pytest.mark.skipif(not RAW.exists(), reason="local 2010-2017 data (data-v1) not present")
def test_history_counts_2010_2017_reproduce_the_prereg_census() -> None:
    """Section 1: "Fire rates are events per trading day on 2010-2017, window W, before the blackout and spread gate";
    the census's day is a Monday-Friday UTC date and its round-number grid is "scaled to the 2010-17 price level, so
    that handle/ATR5m equals today's $10 / $4.43". Z7's 40.9 counts every half-hour close in W (an upper bound: the
    8-of-10 rule and S = 0 skip a few); amendment (a): Z7P at the offset grid, Z4P "about 28 fills" at 0.85 admission."""
    b1 = _load_1m()
    ctx = {"1m": _ctx(b1, "1m"), "5m": _ctx(b1, "5m")}
    close = ctx["1m"].close_ts
    days = len(pd.Index(close[close.dayofweek < 5].date).unique())
    hs = 10.0 * float(atr(mid(ctx["5m"].bars), 14).median()) / 4.43
    census = {"tick_surge": 13.8, "run_fade": 13.9, "round_break": 30.9, "round_reject": 32.5, "extreme_break": 6.8,
              "nr4_break": 13.5, "absorb_fade": 4.2, "htf_reclaim": 7.4, "three_horizon": 7.3}
    got: dict[str, float] = {}
    for rule, rate in census.items():
        extra: dict[str, Any] = {"handle_usd": hs} if rule.startswith("round") else {}
        got[rule] = len(Z.RULE_FUNCS[rule](_arm(rule, **extra), ctx[Z.RULE_TF[rule]])) / days
        assert 0.95 * rate <= got[rule] <= 1.05 * rate, (rule, got[rule], rate)
    for slots in ([0, 30], [15, 45]):
        got[f"z7{slots}"] = len(Z.halfhour_persist(_arm("halfhour_persist", slots_ny=slots), ctx["5m"])) / days
        assert 0.93 * 40.9 <= got[f"z7{slots}"] <= 40.9 * 1.01
    z4p = len(Z.round_reject(_arm("round_reject", handle_usd=hs, handle_offset_usd=hs / 2), ctx["1m"])) / days
    assert 0.9 * 28 / 0.85 <= z4p <= 1.1 * 28 / 0.85
    for arm_id, p, rate in (("ZCD", 0.07, 17.0), ("ZCV", 0.05, 12.0)):
        n = len(Z.scalp_control(_arm("scalp_control", arm_id=arm_id, p=p), ctx["5m"])) / days
        assert 0.93 * rate <= n <= 1.07 * rate, (arm_id, n)
    print(got, z4p)
