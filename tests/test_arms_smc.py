"""Wave-1 arms of lane L3 (arms expansion 2026-10-26, sections 3, 4 and 10): `smc_structure` tvr1 (CHoCH + FVG), tvr2
(BOS continuation) and `eq_liquidity_run` through the `specialist` arm rule. Per family: parity with the family's own
`candidates_in_context` and `label_spec`, prefix invariance, warm-up inside the engine's live window ("the same function
runs in the history matrix and live"), and a candidate outside its £ cap skipped and logged with its propensity.

tvr1/tvr2 read the structure replica, whose state is pinned to the 2010 series start (B4a). The engine's exploration
pass does not carry that state (`RuleContext.spec_ctx` stays None), so a smc arm on the live window refuses and the
engine journals `exploration_rule_error` and sends nothing: fail closed (NO-GO for wave 1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine import Engine, EngineConfig, exploration
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from goldbot.research.pipeline import build_decision_frame
from goldbot.risk import RiskLimits
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter

NO = np.zeros(0, dtype=np.int64)
EQ = ("eq_liquidity_run", {"preset": "prereg"})
SMC = (("smc_structure", {"preset": "tvr1"}), ("smc_structure", {"preset": "tvr2"}))
CUT = pd.Timestamp("2024-01-10", tz="UTC")              # a Wednesday 00:00 UTC
SMC_CUT = pd.Timestamp("2010-03-10", tz="UTC")
LIVE_FROM, LIVE_TO = pd.Timestamp("2024-01-08", tz="UTC"), pd.Timestamp("2024-01-20", tz="UTC")
KEEP_1M = 60_000                                         # EngineConfig.max_bars_in_memory: the engine's 1m window


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-10-01", "2024-01-31", ticks_per_minute=1, seed=11))


@pytest.fixture(scope="module")
def b1_2010() -> pd.DataFrame:
    """Bars from the replica's pinned series start (B4a), as research gives the smc family."""
    return ticks_to_1m(synthetic_ticks("2010-01-03", "2010-04-30", ticks_per_minute=1, seed=7))


def _arm(name: str, extra: dict[str, Any], arm_id: str = "X") -> A.Arm:
    return A.Arm(id=arm_id, rule="specialist", timeframe="15m", params={"specialist": name, **extra})


def _ctx(b1: pd.DataFrame, spec_ctx: dict[str, Any] | None = None) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, "15m").reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO, spec_ctx=spec_ctx)


def _own(name: str, extra: dict[str, Any], ctx: R.RuleContext) -> tuple[pd.DataFrame, np.ndarray, Any]:
    spec = SPECIALISTS[name](timeframe="15m", **SPECIALISTS[name].presets[extra["preset"]])
    context = {TF_LABEL[x]: resample_bars(ctx.b1, x) for x in context_tfs("15m")}
    m, x = build_decision_frame(ctx.bars, context)
    cands = spec.candidates_in_context(m, x, context, None)
    own = spec.barrier_atr(m, context)
    from goldbot.features.technical import atr
    a = (atr(m, 14) if own is None else own).to_numpy(dtype=float)
    return cands, a, spec.label_spec


def _assert_parity(got: pd.DataFrame, cands: pd.DataFrame, a: np.ndarray, ls: Any) -> None:
    idx = cands["idx"].to_numpy(dtype=int)
    assert got["idx"].tolist() == idx.tolist() and got["side"].tolist() == cands["side"].tolist()
    np.testing.assert_allclose(got["risk_usd"], ls.stop_atr * a[idx])
    target = cands["target_atr"].to_numpy(dtype=float) if "target_atr" in cands else np.full(len(idx), ls.target_atr)
    np.testing.assert_allclose(got["target_r"], target / ls.stop_atr)
    hold = cands["max_bars"].to_numpy(dtype=int) if "max_bars" in cands else np.full(len(idx), ls.max_bars)
    assert got["max_bars"].tolist() == hold.tolist()             # triple_barrier's count, as the research labels'


# ------------------------------------------------------------------------------------------------ eq_liquidity_run
def test_eq_liquidity_run_arm_takes_the_familys_own_candidates_stop_target_and_hold(b1) -> None:
    """Stop 0.75 x ATR14 (barrier_atr), the candidate's own target_atr in R, 16 bars: as the research labels."""
    ctx = _ctx(b1)
    got = R.RULE_FUNCS["specialist"](_arm(*EQ), ctx)
    assert len(got) > 0, "the fixture must give candidates, else parity is vacuous"
    cands, a, ls = _own(*EQ, ctx)
    _assert_parity(got, cands, a, ls)
    assert got["target_r"].nunique() > 1                 # per-candidate targets (0.73-1.67 R), not one nominal value


def test_eq_liquidity_run_appending_bars_never_changes_a_past_candidate(b1) -> None:
    arm = _arm(*EQ)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    full_ctx = _ctx(b1)
    full, part = R.RULE_FUNCS["specialist"](arm, full_ctx), R.RULE_FUNCS["specialist"](arm, _ctx(head))
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


def test_eq_liquidity_run_on_the_engines_live_window_decides_exactly_as_the_full_history(b1) -> None:
    """No train/serve skew: at every 15m close the engine's window (`exploration._bars` for a specialist arm, inside
    the engine's ~40 trading days of 1m) gives the history run's candidate on that bar, or none where it has none.
    The family's ATR200 equal-level threshold needs far more than the 200-bar arm lookback: on 200 bars the live arm
    found none of the history's candidates."""
    arm = _arm(*EQ)
    ctx = _ctx(b1)
    full = R.candidates(arm, ctx)                         # the window, common filters and stop band, as live
    close = ctx.close_ts
    hist = {close[i]: (s, r) for i, s, r in zip(full["idx"], full["side"], full["risk_usd"], strict=True)}
    span = np.flatnonzero(np.asarray((close >= LIVE_FROM) & (close < LIVE_TO)))
    want = [c for c in close[span] if c in hist]
    assert want, "the span must hold history candidates, else the check is vacuous"
    quiet = [close[i] for i in span[::40] if close[i] not in hist]
    for c in [*want, *quiet]:
        complete = b1[b1["visible_at"] <= c].tail(KEEP_1M)
        bars = exploration._bars(complete, "15m", c, whole=exploration.whole_window(arm))
        assert bars is not None
        live = R.candidates(arm, R.RuleContext(
            bars=bars, b1=complete[complete["ts_utc"] >= pd.Timestamp(bars["ts_utc"].iloc[0])].reset_index(drop=True),
            blackout_lo=NO, blackout_hi=NO))
        last = live[live["idx"] == len(bars) - 1]
        got = [(int(s), float(r)) for s, r in zip(last["side"], last["risk_usd"], strict=True)]
        exp = [] if c not in hist else [(int(hist[c][0]), float(hist[c][1]))]
        assert len(got) == len(exp) and all(g[0] == e[0] and g[1] == pytest.approx(e[1], rel=1e-9)
                                            for g, e in zip(got, exp, strict=True)), c


def test_only_specialist_arms_read_the_whole_window_the_written_rules_keep_their_lookback() -> None:
    assert exploration.whole_window(_arm(*EQ))
    assert not exploration.whole_window(A.Arm(id="Y", rule="random_bracket", timeframe="15m",
                                              params={"minutes": [0], "stop_atr": 1.0, "target_r": 2.0,
                                                      "hold_bars": 4}))


# ------------------------------------------------------------------------------------------------ smc tvr1 / tvr2
@pytest.mark.parametrize(("name", "extra"), SMC)
def test_smc_arm_takes_the_familys_own_candidates_from_the_series_start(b1_2010, name, extra) -> None:
    ctx = _ctx(b1_2010)
    got = R.RULE_FUNCS["specialist"](_arm(name, extra), ctx)
    assert len(got) > 0
    cands, a, ls = _own(name, extra, ctx)
    _assert_parity(got, cands, a, ls)


@pytest.mark.parametrize(("name", "extra"), SMC)
def test_smc_appending_bars_never_changes_a_past_candidate(b1_2010, name, extra) -> None:
    arm = _arm(name, extra)
    head = b1_2010[np.asarray(utc_index(b1_2010["ts_utc"]) < SMC_CUT)].reset_index(drop=True)
    full_ctx = _ctx(b1_2010)
    full, part = R.RULE_FUNCS["specialist"](arm, full_ctx), R.RULE_FUNCS["specialist"](arm, _ctx(head))
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= SMC_CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


@pytest.mark.parametrize(("name", "extra"), SMC)
def test_smc_arm_without_the_carried_replica_state_refuses_rather_than_trade_a_tail(b1, name, extra) -> None:
    """B4a: the replica's events depend on the series start; on a window that does not start it and no carried
    state (`spec_ctx`), the rule refuses instead of giving values the history never had."""
    with pytest.raises(ValueError, match="pinned"):
        R.candidates(_arm(name, extra), _ctx(b1.tail(KEEP_1M)))


# ------------------------------------------------------------------------------------------------ the engine
class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


def _engine(tmp_path: Path, spec: A.Arm, cap: float) -> tuple[Engine, PaperBroker]:
    arm = Arm(spec=spec, style="intraday")
    cfg = ExplorationConfig(enabled=True, budget_share=0.9, max_open_total=18,
                            pots={"scalp": Pot(gbp=50, max_open=8), "intraday": Pot(gbp=48, max_open=5),
                                  "swing": Pot(gbp=40, max_open=1)}, arms=(arm,))
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(risk_per_trade=0.002, multiplier_bounds=(1.0, 1.0), max_positions=50, probe_only=True,
                        exploration_arms={spec.id: cap}, daily_loss_limit_abs=220.0)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=str(tmp_path), owner_user_id=111, probe_only=True,
                              exploration_config=cfg, cost_probe=False,
                              cost_probe_scheduled=acc_mod.ScheduledProbe(enabled=False)),
                 pb, [], {}, ApprovalCenter({111}), limits=limits, live_shares={})
    return eng, pb


def _close_at(eng: Engine, pb: PaperBroker, b1: pd.DataFrame, close: pd.Timestamp) -> Tick:
    eng.bars_1m = b1[b1["visible_at"] <= close].tail(KEEP_1M).reset_index(drop=True)
    px = float(eng.bars_1m["bid_close"].iloc[-1])
    tick = Tick(ts_utc=close + pd.Timedelta(seconds=2), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    eng._last_tick = tick
    eng._refresh_account(tick)
    eng._closed_through = close
    return tick


def test_eq_liquidity_run_candidate_above_its_pound_cap_is_skipped_and_logged_with_its_propensity(tmp_path, b1) -> None:
    """Section 4: "A candidate outside its £ cap or stop band is skipped and logged with its propensity"."""
    spec = _arm(*EQ, arm_id="EQX")
    ctx = _ctx(b1)
    full = R.RULE_FUNCS["specialist"](spec, ctx)
    close = ctx.close_ts[full["idx"].to_numpy()]
    c = next(t for t in close if LIVE_FROM <= t < LIVE_TO and t.dayofweek < 4 and 1 <= t.hour < 19)
    eng, pb = _engine(tmp_path, spec, cap=0.01)
    tick = _close_at(eng, pb, b1, c)
    assert exploration.step(eng, c)
    [pending] = list(eng.exploration.queue)                                   # type: ignore[union-attr]
    assert pending.arm_id == "EQX" and pending.bar_ts == c
    exploration.on_pass(eng, tick)
    assert not pb.positions()
    [refused] = [d for d in eng.decisions if str(d.get("action", "")).startswith("exploration_refused:")]
    assert refused["action"] == "exploration_refused:arm_cap" and "propensity" in refused
    with exploration.ledger_db(str(tmp_path)).connection() as conn:
        rows = conn.execute("SELECT arm_id, taken, mu, blocked_reason FROM exploration_obs").fetchall()
    assert rows == [("EQX", 0, 0.0, "arm_cap")]


@pytest.mark.parametrize(("name", "extra"), SMC)
def test_the_engine_journals_a_smc_arm_without_replica_state_and_sends_nothing(tmp_path, b1, name, extra) -> None:
    """Fail closed while the exploration pass carries no replica state: the arm's rule error is journalled, no signal
    is queued, no order is sent."""
    c = pd.Timestamp("2024-01-09 14:00", tz="UTC")
    eng, pb = _engine(tmp_path, _arm(name, extra, arm_id="SMX"), cap=40.0)
    tick = _close_at(eng, pb, b1, c)
    assert exploration.step(eng, c)
    exploration.on_pass(eng, tick)
    assert not eng.exploration.queue and not pb.positions()                  # type: ignore[union-attr]
    assert [d["action"] for d in eng.decisions if d.get("agent") == "explore:SMX"] == ["exploration_rule_error"]
