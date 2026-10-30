"""Arms expansion 2026-10-26, lane L5: the classic families as `specialist` arms (section 3, rows 18-20: `breakout`
compressed range on 1h and 15m, `trend` EMA50 pullback + ADX on 1h, `mean_reversion` Bollinger + RSI on 15m and 1h).

The plan's rules these tests hold the arms to:
- "each family is the specialist's own candidates at the commit the arm hash is frozen on" (section 4);
- "At 0.01 lot a position cannot be split, so the arms run the specialists' barrier form (`label_spec` stop and
  target, time exit)" (section 3): the stop is `stop_atr` x the family's barrier ATR, the target `target_atr` in ATR,
  the hold `max_bars`, never the partial or the trail of `exit_spec`;
- "The same function runs in the history matrix and live" (section 3): appending bars never changes a past candidate,
  and the bars the engine holds live (`EngineConfig.max_bars_in_memory`, ~40 trading days of 1m) are enough warm-up
  for the history candidate on every close; the engine's own exploration slice (`exploration.arm_bars`: the family's
  warm-up in trading bars of its timeframe, `exploration.lookback`) gives it, within the per-close time budget;
- "A candidate outside its GBP cap or stop band is skipped and logged with its propensity" (section 4).

A stop agrees when it is within half a broker point ($0.005): the broker quotes gold to $0.01, so two stops that close
are the same order."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine import Engine, EngineConfig, exploration
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_specialist as S
from goldbot.research import arms as A
from goldbot.research.pipeline import build_decision_frame, prepare
from goldbot.risk import RiskLimits
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter

NO = np.zeros(0, dtype=np.int64)
HALF_POINT = 0.005                                  # USD: stops this close are the same broker price
BUFFER = 60_000         # ~40 trading days of 1m bars: these warm-ups fit it, and the engine keeps at least this
CUT = pd.Timestamp("2024-01-10", tz="UTC")          # a Wednesday 00:00 UTC: whole 15m/1h/4h bars on both sides
LIVE_FROM, LIVE_TO = pd.Timestamp("2024-01-02", tz="UTC"), pd.Timestamp("2024-01-31", tz="UTC")
QUIET = 8                                           # closes without a candidate also checked live

# section 3 rows 18-20: family, decision timeframe; and the pre-registered barrier form (stop ATR, target ATR, bars)
CASES: tuple[tuple[str, A.Timeframe], ...] = (("breakout", "1h"), ("breakout", "15m"), ("trend", "1h"),
                                              ("mean_reversion", "15m"), ("mean_reversion", "1h"))
IDS = [f"{n}-{tf}" for n, tf in CASES]
BARRIER = {"breakout": (1.0, 2.0, 24), "trend": (1.25, 2.5, 48), "mean_reversion": (1.5, 1.0, 12)}

PARAMS = [pytest.param(n, tf, id=i) for (n, tf), i in zip(CASES, IDS)]
BUDGET_S = 10.0                                     # L7: every arm's rule at one 15m close, per host


@pytest.fixture(scope="module")
def ticks() -> pd.DataFrame:
    """Four months of synthetic ticks whose rate switches by the hour (a quarter of hours at full rate, the rest at
    40%), so the tick-volume trigger of `breakout` has bursts to see; seeded."""
    t = synthetic_ticks("2023-10-01", "2024-01-31", ticks_per_minute=8, seed=11)
    rng = np.random.default_rng(3)
    codes, hours = pd.factorize(t["ts_utc"].dt.floor("h"))
    keep = np.where(rng.random(len(hours)) < 0.25, 1.0, 0.4)[codes]
    return t[rng.random(len(t)) < keep].reset_index(drop=True)


@pytest.fixture(scope="module")
def b1(ticks: pd.DataFrame) -> pd.DataFrame:
    return ticks_to_1m(ticks)


def _arm(name: str, tf: A.Timeframe, arm_id: str = "X") -> A.Arm:
    return A.Arm(id=arm_id, rule="specialist", timeframe=tf, params={"specialist": name})


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """Same candidates: idx, side, target and hold exactly, the stop within half a broker point."""
    return (len(a) == len(b) and a["idx"].tolist() == b["idx"].tolist() and a["side"].tolist() == b["side"].tolist()
            and np.allclose(a["target_r"], b["target_r"], rtol=1e-12, atol=0)
            and a["max_bars"].tolist() == b["max_bars"].tolist()
            and np.allclose(a["risk_usd"], b["risk_usd"], rtol=0, atol=HALF_POINT))


def _closes(b1: pd.DataFrame, name: str, tf: A.Timeframe) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    """History's candidates (the whole series, after the arm's filters) and the closes checked live: every candidate
    between LIVE_FROM and LIVE_TO, and QUIET evenly spaced closes in that span with none."""
    ctx = _ctx(b1, tf)
    full = R.candidates(_arm(name, tf), ctx)
    close = ctx.close_ts
    span = np.flatnonzero(np.asarray((close >= LIVE_FROM) & (close < LIVE_TO)))
    hit = [int(i) for i in full["idx"] if int(i) in set(span.tolist())]
    quiet = np.setdiff1d(span, full["idx"].to_numpy())
    pick = quiet[np.linspace(0, len(quiet) - 1, QUIET).astype(int)].tolist()
    assert len(hit) >= 2, f"the fixture must give {name} {tf} candidates in the live span, else the check is vacuous"
    full = full.assign(close=close[full["idx"].to_numpy()])
    return full, [close[i] for i in sorted(hit + pick)]


def _live_check(arm: A.Arm, full: pd.DataFrame, c: pd.Timestamp, bars: pd.DataFrame, b1_live: pd.DataFrame) -> None:
    """The arm on the live bars decides on the close `c` exactly what history decided on it."""
    got = R.candidates(arm, R.RuleContext(bars=bars, b1=b1_live, blackout_lo=NO, blackout_hi=NO))
    now = got[got["idx"] == len(bars) - 1].reset_index(drop=True)
    want = full[full["close"] == c].drop(columns="close").reset_index(drop=True)
    assert len(want) == 0 or len(now) > 0, f"{c}: history has a candidate, live none"
    assert _same(now.assign(idx=0), want.assign(idx=0)), f"{c}: live {now.to_dict('records')} != " \
                                                         f"history {want.to_dict('records')}"


# ------------------------------------------------------------------------------------------------ the barrier form
@pytest.mark.parametrize(("name", "tf"), CASES, ids=IDS)
def test_the_arm_is_the_specialists_own_candidates_with_its_label_spec_stop_target_and_hold(b1, name, tf) -> None:
    ctx = _ctx(b1, tf)
    got = R.RULE_FUNCS["specialist"](_arm(name, tf), ctx)
    assert len(got) > 0, "the fixture must give candidates, else parity is vacuous"
    spec = SPECIALISTS[name](timeframe=tf)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    m, x = build_decision_frame(ctx.bars, context)
    cands = spec.candidates_in_context(m, x, context, None)
    assert spec.barrier_atr(m, context) is None                 # these three size on ATR(14) of the decision bars
    ls = spec.label_spec
    idx = cands["idx"].to_numpy(dtype=int)
    assert list(got.columns) == R.COLUMNS                       # stop, target, hold: nothing for a partial or a trail
    assert got["idx"].tolist() == idx.tolist() and got["side"].tolist() == cands["side"].tolist()
    np.testing.assert_allclose(got["risk_usd"], ls.stop_atr * atr(m, 14).to_numpy(dtype=float)[idx])
    np.testing.assert_allclose(got["target_r"], ls.target_atr / ls.stop_atr)
    assert got["max_bars"].tolist() == [ls.max_bars] * len(idx)  # triple_barrier's count, as the research labels'


@pytest.mark.parametrize("name", sorted(BARRIER))
def test_the_barrier_form_is_the_pre_registered_label_spec_and_never_the_partial_or_trail(b1, name) -> None:
    """Section 3: breakout's half at 1.0 ATR and trail, and trend's trail, are not run at 0.01 lot; the target is the
    label_spec's whole target_atr (breakout 2.0 ATR, not the 1.0 ATR partial)."""
    for tf in [t for n, t in CASES if n == name]:
        spec = SPECIALISTS[name](timeframe=tf)
        ls = spec.label_spec
        assert (ls.stop_atr, ls.target_atr, ls.max_bars) == BARRIER[name]
        got = R.RULE_FUNCS["specialist"](_arm(name, tf), _ctx(b1, tf))
        assert set(got["target_r"]) == {BARRIER[name][1] / BARRIER[name][0]}
        ex = spec.exit_spec
        if ex is not None:                                      # breakout, trend: the trail (and split) the arm drops
            assert ex.trail_atr is not None
            assert ex.scale_atr is None or ex.scale_atr < ls.target_atr


@pytest.mark.parametrize(("name", "tf"), CASES, ids=IDS)
def test_the_arm_matches_the_research_labels_stop_and_target_on_every_labelled_candidate(b1, name, tf) -> None:
    """research.pipeline.prepare: the stop is stop_atr x atr_sig and the target target_atr, per labelled candidate."""
    arm = _arm(name, tf)
    ctx = _ctx(b1, tf)
    got = R.RULE_FUNCS["specialist"](arm, ctx).set_index("idx")
    lab = prepare(S.build_specialist(arm), ctx.bars, {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}).labels
    assert len(lab) > 0
    row = got.loc[lab["idx"].to_numpy()]
    assert row["side"].tolist() == lab["side"].tolist()
    np.testing.assert_allclose(row["risk_usd"], lab["stop_atr"] * lab["atr_sig"])
    np.testing.assert_allclose(row["target_r"], lab["target_atr"] / lab["stop_atr"])


# ------------------------------------------------------------------------------------------------ history = live
@pytest.mark.parametrize(("name", "tf"), CASES, ids=IDS)
def test_appending_bars_never_changes_a_past_candidate(b1, name, tf) -> None:
    """Live sees the bars up to now; the history run sees them all. Every candidate decided by the cut is the same."""
    arm = _arm(name, tf)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    full_ctx = _ctx(b1, tf)
    full, part = R.RULE_FUNCS["specialist"](arm, full_ctx), R.RULE_FUNCS["specialist"](arm, _ctx(head, tf))
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


@pytest.mark.parametrize(("name", "tf"), CASES, ids=IDS)
def test_the_warm_up_fits_the_engines_forty_day_1m_buffer(b1, name, tf) -> None:
    """On the engine's live 1m bars alone (the last `max_bars_in_memory` minutes, every decision bar of them) the arm
    decides on each close what history decided: the same candidate, or none."""
    arm = _arm(name, tf)
    full, closes = _closes(b1, name, tf)
    for c in closes:
        complete = b1[b1["visible_at"] <= c]
        assert len(complete) > BUFFER, "the history must be longer than the buffer, else the check is vacuous"
        buf = complete.tail(BUFFER).reset_index(drop=True)
        bars = resample_bars(buf, tf)
        _live_check(arm, full, c, bars[bars["visible_at"] <= c].reset_index(drop=True), buf)


@pytest.mark.parametrize(("name", "tf"), PARAMS)
def test_the_engines_exploration_window_gives_the_history_candidate_on_every_close(b1, name, tf) -> None:
    """The bars `exploration.step` hands the arm at a close (`arm_bars`: its family's warm-up of trading bars, a
    weekend inside it included, and the 1m bars from the first of them) give the history candidate, or none."""
    arm = _arm(name, tf)
    full, closes = _closes(b1, name, tf)
    for c in closes:
        complete = b1[b1["visible_at"] <= c].tail(BUFFER)
        got = exploration.arm_bars(complete, arm, c)
        assert got is not None
        _live_check(arm, full, c, *got)


@pytest.mark.parametrize(("name", "tf"), PARAMS)
def test_a_specialist_arms_warm_up_is_its_familys_and_stays_inside_the_engines_buffer(name, tf) -> None:
    """Bounded: never the whole 40-day buffer (L7: ~30 s a close), these five within half of it, the cap within it;
    any other arm keeps LOOKBACK."""
    n = exploration.lookback(_arm(name, tf))
    assert exploration.LOOKBACK <= n <= BUFFER // 2 * 60 // tf_seconds(tf)
    assert BUFFER <= EngineConfig.model_fields["max_bars_in_memory"].default == exploration.LOOKBACK_MAX_1M
    rb = A.Arm(id="R", rule="random_bracket", timeframe=tf,
               params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4, "minutes": [0]})
    assert exploration.lookback(rb) == exploration.LOOKBACK


def test_all_five_classic_arms_decide_a_close_inside_the_time_budget(b1) -> None:
    """L7's budget: every wave-1 rule at one 15m close within 10 s per host; these five take at most half of it."""
    c = pd.Timestamp("2024-01-23 12:00", tz="UTC")
    complete = b1[b1["visible_at"] <= c].tail(BUFFER)
    t0 = time.perf_counter()
    for name, tf in CASES:
        arm = _arm(name, tf)
        got = exploration.arm_bars(complete, arm, c)
        assert got is not None
        R.candidates(arm, R.RuleContext(bars=got[0], b1=got[1], blackout_lo=NO, blackout_hi=NO))
    assert time.perf_counter() - t0 < BUDGET_S / 2


def test_an_atr_control_arm_keeps_its_history_stop_on_mondays_after_the_weekend(b1) -> None:
    """W2b, 2026-10-26: on the old wall-clock window a Monday held few bars and every ATR arm's stop drifted (up to
    3e-4). Counted in bars, an I0-style control (stop 1.5 x ATR of its 15m bars) on Monday equals history."""
    arm = A.Arm(id="I0X", rule="random_bracket", timeframe="15m",
                params={"minutes": [0], "stop_atr": 1.5, "target_r": 2.0, "hold_bars": 8})
    ctx = _ctx(b1, "15m")
    full = R.candidates(arm, ctx)
    full = full.assign(close=ctx.close_ts[full["idx"].to_numpy()])
    monday = full[(full["close"] >= LIVE_FROM) & (full["close"] < LIVE_TO) & (full["close"].dt.dayofweek == 0)
                  & (full["close"].dt.hour < 12)]
    assert len(monday) >= 4
    for c in monday["close"]:
        got = exploration.arm_bars(b1[b1["visible_at"] <= c].tail(BUFFER), arm, c)
        assert got is not None
        _live_check(arm, full, c, *got)


# ------------------------------------------------------------------------------------------------ the GBP cap
class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


def _send_at(path: Path, ticks: pd.DataFrame, b1: pd.DataFrame, arm: Arm, close: pd.Timestamp,
             cap: float) -> Engine:
    """A demo probe-only engine with this one arm (per-trade cap `cap`, the pot and budget wide), just after `close`:
    its live 1m buffer, the close processed, a quote 2 s later, one exploration pass."""
    path.mkdir()
    from tests.test_exploration_engine import qualified
    arm = qualified(arm)
    cfg = ExplorationConfig(enabled=True, budget_share=0.9, max_open_total=18,
                            pots={"intraday": Pot(gbp=10_000, max_open=5)}, arms=(arm,))
    pb = _Terminal(equity=100_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=50, probe_only=True,
                        exploration_arms={arm.arm_id: cap}, daily_loss_limit_abs=20_000.0)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg, cost_probe=False,
                              cost_probe_scheduled=acc_mod.ScheduledProbe(enabled=False)),
                 pb, [], {}, ApprovalCenter({111}), limits=limits, live_shares={})
    eng.bars_1m = b1[b1["visible_at"] <= close].tail(BUFFER).reset_index(drop=True)
    px = float(ticks.loc[ticks["ts_utc"] < close, "bid"].iloc[-1])
    tick = Tick(ts_utc=close + pd.Timedelta(seconds=2), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    eng._last_tick = tick
    eng._refresh_account(tick)
    eng._closed_through = close
    exploration.on_pass(eng, tick)
    return eng


def _blocked(path: Path) -> list[tuple[Any, ...]]:
    with exploration.ledger_db(str(path)).connection() as conn:
        return conn.execute("SELECT arm_id, taken, branch, mu, blocked_reason, stop_dist FROM exploration_obs").fetchall()


@pytest.mark.parametrize(("name", "tf"), PARAMS)
def test_a_candidate_over_the_arms_gbp_cap_is_skipped_and_logged_with_its_propensity(tmp_path, ticks, b1, name,
                                                                                     tf) -> None:
    """Section 4: the history candidate reaches the engine with its own stop; with the per-trade cap a penny below its
    loss at the stop plus the round trip it is refused `arm_cap`, journalled with its propensity and recorded blocked
    (mu 0); a penny above, it is sent at that stop."""
    full, _ = _closes(b1, name, tf)
    wk = full[(full["close"] >= LIVE_FROM) & (full["close"] < LIVE_TO)]
    wk = wk[(wk["close"].dt.dayofweek <= 1) & (wk["close"].dt.hour.between(2, 17))]    # far from the Friday cut
    assert len(wk) > 0
    row = wk.iloc[0]
    close = pd.Timestamp(row["close"])
    arm = Arm(spec=_arm(name, tf, arm_id=f"L5-{name}-{tf}"), style="intraday")
    wide = _send_at(tmp_path / "wide", ticks, b1, arm, close, cap=1e9)
    [sent] = [d for d in wide.decisions if str(d.get("action", "")).startswith("exploration:")]
    assert sent["action"] == "exploration:executed:auto", wide.decisions
    assert sent["stop_usd"] == pytest.approx(row["risk_usd"], abs=HALF_POINT)
    need = float(sent["stop_loss"]) + float(sent["cost"])
    for cap, refused in ((need - 0.01, True), (need + 0.01, False)):
        eng = _send_at(tmp_path / f"cap{cap:.2f}", ticks, b1, arm, close, cap=cap)
        no = [d for d in eng.decisions if str(d.get("action", "")).startswith("exploration_refused:")]
        if refused:
            [d] = no
            assert d["action"] == "exploration_refused:arm_cap" and "propensity" in d and not eng.open
            [obs] = _blocked(tmp_path / f"cap{cap:.2f}")
            assert obs[:5] == (arm.arm_id, 0, "blocked", 0.0, "arm_cap")
            assert obs[5] == pytest.approx(row["risk_usd"], abs=HALF_POINT)
        else:
            assert not no and len(eng.open) == 1
