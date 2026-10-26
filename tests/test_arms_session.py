"""Wave-1 session families as `specialist` arms (arms expansion 2026-10-26, section 3 rows 15-17, lane L4):
`session_open` (R-02; London and New York opens as strata of one arm), `intraday_momentum` (Gao, R-05; a New York arm
and a London arm) and `asia_drift` (H-04, 1h). Section 4: "each family is the specialist's own candidates at the commit
the arm hash is frozen on", "Local session clocks where a session is named, through PR #203's local window" and "A
candidate outside its £ cap or stop band is skipped and logged with its propensity".

Per family: parity with the specialist's own `candidates_in_context` and `label_spec` (and the research labels),
prefix invariance, the warm-up inside the engine's live window (the engine's own `_bars` slice and `_signal` queue),
the £ cap skip journalled with its propensity, and the candidates on their clocks across the UK switch (2026-10-25),
the gap week (2026-10-26..10-30) and the US switch (2026-11-01)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from goldbot.data.calendar import LOCAL_SESSIONS
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine import cost_probe, exploration
from goldbot.engine.exploration import Arm as EngineArm
from goldbot.engine.exploration import Pot
from goldbot.execution.broker import Tick
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from goldbot.research.pipeline import build_decision_frame, prepare
from goldbot.specialists import SPECIALISTS
from tests.test_exploration_engine import _actions, _engine, _obs, _sent, _Terminal

NO = np.zeros(0, dtype=np.int64)
# six weeks of British and US summer time, the October gap week (UK on GMT, US still on EDT), two weeks of winter
START, END = "2026-09-14", "2026-11-14"
UK_SWITCH = pd.Timestamp("2026-10-25 01:00", tz="UTC")      # London leaves BST
US_SWITCH = pd.Timestamp("2026-11-01 06:00", tz="UTC")      # New York leaves EDT (02:00 local)
CUT = pd.Timestamp("2026-10-28", tz="UTC")                  # a Wednesday 00:00 UTC, inside the gap week
WARM = pd.Timestamp("2026-09-28", tz="UTC")                 # two weeks in: every live window is full of history
# (case id, family, timeframe, the arm's params beyond `specialist`): the section 3 rows; London is the declared
# intraday_momentum variant, session_open's two opens are strata of one arm
CASES: tuple[tuple[str, str, A.Timeframe, dict[str, Any]], ...] = (
    ("session_open", "session_open", "15m", {}),
    ("intraday_momentum_newyork", "intraday_momentum", "15m", {"config": {"session": "newyork"}}),
    ("intraday_momentum_london", "intraday_momentum", "15m", {"config": {"session": "london"}}),
    ("asia_drift", "asia_drift", "1h", {}),
)
IDS = [c[0] for c in CASES]
# (stratum, case, city, decision close on its clock): PR #203 `window_local` narrowed to the family's own decision
# close; session_open's two strata are one window each
WINDOWS: tuple[tuple[str, str, str, str], ...] = (
    ("session_open_london", "session_open", "Europe/London", "08:30"),
    ("session_open_newyork", "session_open", "America/New_York", "09:00"),
    ("intraday_momentum_newyork", "intraday_momentum_newyork", "America/New_York", "12:15"),
    ("intraday_momentum_london", "intraday_momentum_london", "Europe/London", "12:15"),
)


@pytest.fixture(scope="module")
def ticks() -> pd.DataFrame:
    return synthetic_ticks(START, END, ticks_per_minute=1, seed=11)


@pytest.fixture(scope="module")
def b1(ticks: pd.DataFrame) -> pd.DataFrame:
    return ticks_to_1m(ticks)


def _arm(name: str, tf: A.Timeframe, extra: dict[str, Any], arm_id: str = "L4") -> A.Arm:
    return A.Arm(id=arm_id, rule="specialist", timeframe=tf, params={"specialist": name, **extra})


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _run(b1: pd.DataFrame, case: str) -> tuple[A.Arm, R.RuleContext, pd.DataFrame]:
    """The arm's candidates after its window, the common filters and its stop band (`arm_rules.candidates`)."""
    _, name, tf, extra = next(c for c in CASES if c[0] == case)
    arm, ctx = _arm(name, tf, extra), _ctx(b1, tf)
    return arm, ctx, R.candidates(arm, ctx)


def _closes(ctx: R.RuleContext, c: pd.DataFrame) -> pd.DatetimeIndex:
    return ctx.close_ts[c["idx"].to_numpy()]


def _local_hhmm(ts: pd.DatetimeIndex, tz: str) -> list[str]:
    return list(ts.tz_convert(ZoneInfo(tz)).strftime("%H:%M"))


# ------------------------------------------------------------------------------------------------ parity
@pytest.mark.parametrize(("case", "name", "tf", "extra"), CASES, ids=IDS)
def test_the_arm_is_the_specialists_own_candidates_with_its_label_spec_stop_target_and_hold(
        b1: pd.DataFrame, case: str, name: str, tf: A.Timeframe, extra: dict[str, Any]) -> None:
    arm, ctx = _arm(name, tf, extra), _ctx(b1, tf)
    got = R.RULE_FUNCS["specialist"](arm, ctx)
    spec = SPECIALISTS[name](timeframe=tf, **extra.get("config", {}))
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    m, x = build_decision_frame(ctx.bars, context)
    cands = spec.candidates_in_context(m, x, context, None)
    assert len(cands) >= 10, "the fixture must give candidates, else parity is vacuous"
    assert spec.barrier_atr(m, context) is None                 # all three size on ATR(14) of the decision bars
    ls, idx = spec.label_spec, cands["idx"].to_numpy(dtype=int)
    assert got["idx"].tolist() == idx.tolist() and got["side"].tolist() == cands["side"].tolist()
    np.testing.assert_allclose(got["risk_usd"], ls.stop_atr * atr(m, 14).to_numpy()[idx])
    np.testing.assert_allclose(got["target_r"], ls.target_atr / ls.stop_atr)
    assert set(got["max_bars"]) == {ls.max_bars}                 # triple_barrier's count, as the research labels'
    assert R.candidates(arm, ctx).equals(got)                    # no window, filter or band drops one of these


@pytest.mark.parametrize(("case", "name", "tf", "extra"), CASES, ids=IDS)
def test_the_arm_matches_the_research_labels_stop_and_target_on_every_labelled_candidate(
        b1: pd.DataFrame, case: str, name: str, tf: A.Timeframe, extra: dict[str, Any]) -> None:
    """research.pipeline.prepare: the stop is stop_atr x atr_sig and the target target_atr, per labelled candidate
    (asia_drift's research sample rule `complete_windows` labels a subset; the arm does not apply it: declared)."""
    arm, ctx = _arm(name, tf, extra), _ctx(b1, tf)
    got = R.RULE_FUNCS["specialist"](arm, ctx).set_index("idx")
    spec = SPECIALISTS[name](timeframe=tf, **extra.get("config", {}))
    lab = prepare(spec, ctx.bars, {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}).labels
    assert len(lab) >= 10
    row = got.loc[lab["idx"].to_numpy()]
    assert row["side"].tolist() == lab["side"].tolist()
    np.testing.assert_allclose(row["risk_usd"], lab["stop_atr"] * lab["atr_sig"])
    np.testing.assert_allclose(row["target_r"], lab["target_atr"] / lab["stop_atr"])


def test_intraday_momentum_exits_at_its_own_session_close_on_the_local_clock() -> None:
    """Gao: enter at mid-session, exit at the session close. triple_barrier exits at the close of bar idx + 1 +
    max_bars, so the decision close + (max_bars + 1) bars is 16:00 New York and 16:30 London, whatever the season."""
    for session, close in (("newyork", "16:00"), ("london", "16:30")):
        arm = _arm("intraday_momentum", "15m", {"config": {"session": session}})
        spec = SPECIALISTS["intraday_momentum"](timeframe="15m", session=session)
        tz = LOCAL_SESSIONS[session].tz
        for day in ("2026-10-21", "2026-10-28", "2026-11-04"):           # summer, gap week, winter
            decision = pd.Timestamp(f"{day} 12:15", tz=tz).tz_convert("UTC")
            exit_at = decision + (spec.label_spec.max_bars + 1) * pd.Timedelta(minutes=15)
            assert _local_hhmm(pd.DatetimeIndex([exit_at]), tz) == [close], (session, day)
        assert arm.params["config"]["session"] == session


def test_asia_drift_is_long_only_with_no_reachable_target_and_flat_at_seven_utc(b1: pd.DataFrame) -> None:
    """H-04 as pre-registered: long at 00:00 UTC, the time exit at 07:00 UTC, the 50 ATR non-target (33.3 R at a
    1.5 ATR stop) that seven hourly bars cannot reach."""
    _, ctx, c = _run(b1, "asia_drift")
    assert set(c["side"]) == {1}
    np.testing.assert_allclose(c["target_r"], 50.0 / 1.5)
    exit_at = _closes(ctx, c) + (c["max_bars"].to_numpy() + 1) * pd.Timedelta(hours=1)
    assert set(exit_at.strftime("%H:%M")) == {"07:00"}


# ------------------------------------------------------------------------------------------------ point in time
@pytest.mark.parametrize(("case", "name", "tf", "extra"), CASES, ids=IDS)
def test_appending_bars_never_changes_a_past_candidate(
        b1: pd.DataFrame, case: str, name: str, tf: A.Timeframe, extra: dict[str, Any]) -> None:
    """Live sees the bars up to now; the history run sees them all. Every candidate decided by the cut is the same."""
    arm = _arm(name, tf, extra)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    full_ctx, head_ctx = _ctx(b1, tf), _ctx(head, tf)
    full, part = R.candidates(arm, full_ctx), R.candidates(arm, head_ctx)
    assert len(part) >= 5
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


def _live(b1: pd.DataFrame, arm: A.Arm, through: pd.Timestamp) -> list[exploration.Pending]:
    """What the engine queues at the close `through`: its own live window (`exploration._bars`, LOOKBACK bars of the
    arm's timeframe and at least LOOKBACK_1M minutes, cut at the close) and its own `_signal`."""
    complete = b1[b1["visible_at"] <= through]
    bars = exploration._bars(complete, arm.timeframe, through)
    assert bars is not None, through
    window_1m = complete[complete["ts_utc"] >= pd.Timestamp(bars["ts_utc"].iloc[0])].reset_index(drop=True)
    eng = SimpleNamespace(decisions=[])
    ex = SimpleNamespace(retired=set(), last_bar={}, queue=[])
    exploration._signal(cast(Any, eng), cast(Any, ex), EngineArm(spec=arm, style="intraday"), bars, window_1m, through)
    assert not eng.decisions, eng.decisions                    # no exploration_rule_error
    return list(ex.queue)


# The engine's live window is wall-clock (`exploration._bars`: LOOKBACK x the bar's seconds, at least LOOKBACK_1M
# minutes), not a bar count, and its 1d context is resampled from that window. Measured on this fixture (lane L4):
# - session_open: 200 15m bars hold about 3 daily bars, so its `d1_atr14` (ATR(14) of 1d) is NaN live; the specialist's
#   Asian-range filter `asia_rng < 0.8 x d1_atr14` is then False (a NaN comparison; its `fillna(True)` never sees a
#   NaN), so live never queues a candidate history takes: 16 of 16 missed.
# - after a weekend the window holds 34-69 15m bars, so Wilder ATR(14) has not forgotten its start: intraday_momentum's
#   Monday stop is up to 1.2% (London) / 0.25% (New York) off history's. Tuesday to Friday agree within 1e-4.
WINDOW_GAP = "engine live window (exploration._bars) is wall-clock and too short for the family's warm-up (see above)"
LIVE_CASES = [pytest.param(*c, id=c[0], marks=pytest.mark.xfail(strict=True, reason=WINDOW_GAP))
              if c[0] == "session_open" else pytest.param(*c, id=c[0]) for c in CASES]
MONDAY_CASES = [pytest.param(*c, id=c[0], marks=pytest.mark.xfail(strict=True, reason=WINDOW_GAP))
                if c[0] != "asia_drift" else pytest.param(*c, id=c[0]) for c in CASES]


def _live_vs_history(b1: pd.DataFrame, case: str, mondays: bool) -> None:
    """At every close on the family's decision clock times (from WARM on; Mondays alone, or Tuesday to Friday): the
    engine's live window queues exactly history's candidate (side, hold, stop and target within 1e-4) or nothing."""
    arm, ctx, full = _run(b1, case)
    closes = _closes(ctx, full)
    at = pd.Series(np.arange(len(full)), index=closes)
    clock = set(closes.strftime("%H:%M"))
    ts = ctx.close_ts
    every = ts[(ts >= WARM) & np.isin(ts.strftime("%H:%M"), list(clock)) & ((ts.dayofweek == 0) == mondays)]
    assert len(every) >= 7 and int(np.isin(every, closes).sum()) >= 3, "the check would be vacuous"
    for through in every:
        live = _live(b1, arm, through)
        if through not in at.index:
            assert live == [], (case, through)                  # no extra trade
            continue
        hist = full.iloc[int(at[through])]
        assert len(live) == 1, (case, through, "missed")        # no missed trade
        p = live[0]
        assert (p.side, p.hold_bars) == (int(hist["side"]), int(hist["max_bars"]) + 1), (case, through)
        assert p.stop_usd == pytest.approx(float(hist["risk_usd"]), rel=1e-4), (case, through)
        assert p.target_usd == pytest.approx(float(hist["target_r"] * hist["risk_usd"]), rel=1e-4), (case, through)


@pytest.mark.parametrize(("case", "name", "tf", "extra"), LIVE_CASES)
def test_the_engines_live_window_gives_the_history_candidate_at_every_decision_close_tuesday_to_friday(
        b1: pd.DataFrame, case: str, name: str, tf: A.Timeframe, extra: dict[str, Any]) -> None:
    """Warm-up: on the engine's live window alone the arm queues, at each close where history has a candidate, that
    candidate with its side, stop, target and hold, and nothing at the same clock times where history has none (a
    missed or an extra trade both break history = live). session_open fails it (comment above): NO-GO."""
    _live_vs_history(b1, case, mondays=False)


@pytest.mark.parametrize(("case", "name", "tf", "extra"), MONDAY_CASES)
def test_the_engines_live_window_gives_the_history_candidate_on_mondays_after_the_weekend_gap(
        b1: pd.DataFrame, case: str, name: str, tf: A.Timeframe, extra: dict[str, Any]) -> None:
    """The same on Mondays, when the wall-clock window spans the weekend and holds the fewest bars. asia_drift (1h,
    a 201 h window) passes; the 15m families fail on the stop (comment above) until the window counts bars."""
    _live_vs_history(b1, case, mondays=True)


# ------------------------------------------------------------------------------------------------ the £ cap
def _tick(eng: Any, pb: Any, ticks: pd.DataFrame, close: pd.Timestamp) -> Tick:
    """The engine just after `close`: its 1m bars before it, the close processed, a tick 2 s later."""
    before = ticks[ticks["ts_utc"] < close]
    eng.bars_1m = ticks_to_1m(before)
    px = float(before["bid"].iloc[-1])
    tick = Tick(ts_utc=close + pd.Timedelta(seconds=2), bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    eng._last_tick = tick
    eng._refresh_account(tick)
    eng._closed_through = close
    return tick


@pytest.mark.parametrize("case", [pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=WINDOW_GAP))
                                  if c == "session_open" else c for c in IDS])
def test_a_candidate_whose_loss_at_its_stop_exceeds_the_arms_pound_cap_is_skipped_and_journalled_with_its_propensity(
        tmp_path: Path, ticks: pd.DataFrame, b1: pd.DataFrame, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    """Section 4: "A candidate outside its £ cap or stop band is skipped and logged with its propensity". The same
    candidate under a cap above its loss and cost is sent, so the cap alone skips it. Never resized, never a tighter
    stop. Through the engine (`exploration.on_pass` at the history candidate's close), so session_open, which never
    queues on the live window, fails here too."""
    monkeypatch.setattr(cost_probe, "stop_loss", lambda eng, side, price, distance, lots: distance)  # £1 per $1
    arm, ctx, full = _run(b1, case)
    closes = _closes(ctx, full)
    pick = np.flatnonzero((closes >= WARM) & np.isin(closes.dayofweek, [1, 2, 3]))[0]   # no weekend gap or hold
    close, risk = closes[pick], float(full["risk_usd"].iloc[pick])
    # synthetic ATRs are wide ($20-70 stops): the pot and the account's risk per trade are raised so only the arm cap binds
    pots = {"intraday": Pot(gbp=1_000.0, max_open=5)}

    def terminal() -> _Terminal:
        return _Terminal(equity=200_000, adverse_slip_points=0.0)

    sent_eng, sent_pb = _engine(tmp_path / "sent", [EngineArm(spec=arm, style="intraday")], pots=pots,
                                caps={arm.id: 2 * risk + 10.0}, pb=terminal())
    exploration.on_pass(sent_eng, _tick(sent_eng, sent_pb, ticks, close))
    assert len(_sent(sent_eng)) == 1, sent_eng.decisions
    cap = risk / 2
    eng, pb = _engine(tmp_path / "cap", [EngineArm(spec=arm, style="intraday")], pots=pots, caps={arm.id: cap},
                      pb=terminal())
    exploration.on_pass(eng, _tick(eng, pb, ticks, close))
    assert not _sent(eng) and not pb.positions()
    [refused] = _actions(eng, "exploration_refused:")
    assert refused["action"] == "exploration_refused:arm_cap" and refused["arm"] == arm.id
    assert refused["stop_usd"] == pytest.approx(risk, rel=1e-4) and refused["stop_loss"] > cap   # live-window ATR
    assert "propensity" in refused
    [obs] = _obs(tmp_path / "cap")
    assert obs[:4] == (arm.id, 0, "blocked", 0.0) and obs[5] == "arm_cap"


# ------------------------------------------------------------------------------------------------ DST
def _regime(ts: pd.DatetimeIndex) -> np.ndarray:
    """summer (both on summer time), gap (UK on GMT, US on EDT), winter (both on standard time)."""
    return np.where(ts < UK_SWITCH, "summer", np.where(ts < US_SWITCH, "gap", "winter"))


def test_session_open_decides_thirty_minutes_after_each_local_open_in_every_regime_and_the_gap_week(
        b1: pd.DataFrame) -> None:
    """R-02 on local clocks: the decision closes on the second 15m bar after 08:00 London (stratum London) or 08:30
    New York (stratum New York): 07:30/13:00 UTC in summer, 08:30/13:00 in the gap week, 08:30/14:00 in winter. Each
    candidate is in exactly one stratum, and every (stratum, regime) cell has candidates."""
    _, ctx, c = _run(b1, "session_open")
    closes = _closes(ctx, c)
    london = np.array(_local_hhmm(closes, "Europe/London")) == "08:30"
    newyork = np.array(_local_hhmm(closes, "America/New_York")) == "09:00"
    assert (london ^ newyork).all()
    utc = {("london" if lo else "newyork", reg, hhmm)
           for lo, reg, hhmm in zip(london, _regime(closes), closes.strftime("%H:%M"), strict=True)}
    assert utc == {("london", "summer", "07:30"), ("london", "gap", "08:30"), ("london", "winter", "08:30"),
                   ("newyork", "summer", "13:00"), ("newyork", "gap", "13:00"), ("newyork", "winter", "14:00")}


@pytest.mark.parametrize(("case", "tz", "summer", "gap", "winter"), [
    ("intraday_momentum_newyork", "America/New_York", "16:15", "16:15", "17:15"),
    ("intraday_momentum_london", "Europe/London", "11:15", "12:15", "12:15"),
])
def test_intraday_momentum_decides_at_mid_session_on_its_own_city_clock_every_weekday(
        b1: pd.DataFrame, case: str, tz: str, summer: str, gap: str, winter: str) -> None:
    """Gao: one decision a weekday at 12:15 local (half of 08:30-16:00 New York, of 08:00-16:30 London, rounded down
    to a whole 15m bar), so its UTC hour moves with that city alone: New York only on 2026-11-01, London only on
    2026-10-25. No weekday is missed or doubled across either switch or the gap week."""
    _, ctx, c = _run(b1, case)
    closes = _closes(ctx, c)
    assert set(_local_hhmm(closes, tz)) == {"12:15"}
    by = dict(zip(_regime(closes), closes.strftime("%H:%M"), strict=True))
    assert set(zip(_regime(closes), closes.strftime("%H:%M"), strict=True)) == {
        ("summer", summer), ("gap", gap), ("winter", winter)}, by
    days = closes.tz_convert(ZoneInfo(tz)).normalize()
    weekdays = pd.bdate_range(START, pd.Timestamp(END) - pd.Timedelta(days=1), tz=ZoneInfo(tz))
    assert days.is_unique and list(days) == list(weekdays)


def test_asia_drift_stays_on_utc_across_both_switches_so_its_london_hour_moves(b1: pd.DataFrame) -> None:
    """H-04 is pre-registered on UTC ("UTC has no DST"): 00:00 UTC every weekday, which is 01:00 London in summer and
    00:00 from 2026-10-25; New York's switch does not move it. No weekday is missed or doubled (the fixture's first
    Monday has no 23:00 bar before it: no decision bar, no entry)."""
    _, ctx, c = _run(b1, "asia_drift")
    closes = _closes(ctx, c)
    assert set(closes.strftime("%H:%M")) == {"00:00"}
    london = dict(zip(_regime(closes), _local_hhmm(closes, "Europe/London"), strict=False))
    assert set(zip(_regime(closes), _local_hhmm(closes, "Europe/London"), strict=True)) == {
        ("summer", "01:00"), ("gap", "00:00"), ("winter", "00:00")}, london
    weekdays = pd.bdate_range(pd.Timestamp(START) + pd.Timedelta(days=1), pd.Timestamp(END) - pd.Timedelta(days=1),
                              tz="UTC")
    assert closes.is_unique and list(closes) == list(weekdays)


@pytest.mark.parametrize(("stratum", "case", "tz", "hhmm"), WINDOWS, ids=[w[0] for w in WINDOWS])
def test_a_local_window_on_the_decision_close_keeps_its_stratum_across_the_switches_where_a_utc_window_would_not(
        b1: pd.DataFrame, stratum: str, case: str, tz: str, hhmm: str) -> None:
    """Section 4 through PR #203: a `window_local` on the family's decision close keeps exactly that stratum's
    candidates in summer, the gap week and winter; the same window frozen at its summer UTC hour keeps the summer
    ones and drops every one after its city's switch."""
    arm, ctx, bare = _run(b1, case)
    mine = bare[np.array(_local_hhmm(_closes(ctx, bare), tz)) == hhmm].reset_index(drop=True)
    assert set(_regime(_closes(ctx, mine))) == {"summer", "gap", "winter"}, stratum
    at = pd.Timestamp(f"2026-10-21 {hhmm}", tz=tz)                  # a summer date: the frozen UTC hours
    before = (at - pd.Timedelta(minutes=10)).strftime("%H:%M")       # [from, to] inclusive: the decision close alone
    local = A.Arm.model_validate({**arm.model_dump(), "params": {**arm.params, "window_local": {
        "from": [tz, before], "to": [tz, hhmm]}}})
    pd.testing.assert_frame_equal(R.candidates(local, ctx), mine)
    utc = [(at - pd.Timedelta(minutes=10)).tz_convert("UTC").strftime("%H:%M"), at.tz_convert("UTC").strftime("%H:%M")]
    frozen = A.Arm.model_validate({**arm.model_dump(), "params": {**arm.params, "window_utc": utc}})
    switch = UK_SWITCH if tz == "Europe/London" else US_SWITCH
    ours = _closes(ctx, mine)
    assert _closes(ctx, R.candidates(frozen, ctx)).equals(ours[ours < switch])
