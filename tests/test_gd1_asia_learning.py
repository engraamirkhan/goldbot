"""GD-1 A1 on demo as a learning arm (docs/research/prereg-amendment-2026-11-20-gd1-asia-learning.md; card
prereg-2026-11-20-gold-daily.md, H-98, trial #3440): long gold on the 1h close at 00:00 UTC every weekday, time exit
07:00 UTC on the wall clock, a $48 catastrophic stop, 0.01 lot through the exploration fence, on icm-demo (GD1D) and
icm-probe (GD1P); each with a replay-only control long for the same 7 hours at a seeded hour 07:00-13:00 UTC (GD1CD,
GD1CP, never an order); the quant's kill rule as one-off checks of each arm's own fills (cost > 1.1 bp at 40, mean net
<= -4 bp at 120, mean net < +3.5 bp at 250) beside H-61's swing stop and the learning class's loss cap."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.db.connection import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_clock_hold as G
from goldbot.research import arm_stops
from goldbot.research import arms as A
from goldbot.research import exploration_data as ed
from goldbot.telegram import labs
from tests.test_daily_review import HOSTS, _run
from tests.test_exploration_engine import _at, _engine, _sent
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401 - the fixed loss at every stop (autouse)
)

ROOT = Path(__file__).resolve().parents[1]
PREREG = "docs/research/prereg-amendment-2026-11-20-gd1-asia-learning.md"
NO = np.zeros(0, dtype=np.int64)
ARMS = {"GD1D": ("GD1CD", "icm-demo"), "GD1P": ("GD1CP", "icm-probe")}
CONTROL_HOURS = [7, 8, 9, 10, 11, 12, 13]
TUE_00 = pd.Timestamp("2025-03-04 00:00", tz="UTC")              # a Tuesday 1h close inside the test ticks
pytestmark = pytest.mark.usefixtures("_rules")


def _params(**kw: Any) -> dict[str, Any]:
    return {"entry_utc": "00:00", "weekdays": [0, 1, 2, 3, 4], "side": 1, "stop_usd": 48.0, "target_r": 50.0,
            "hold_bars": 7, **kw}


def _control_params(**kw: Any) -> dict[str, Any]:
    base = _params()
    del base["entry_utc"]
    return {**base, "entry_hours_utc": CONTROL_HOURS, "control": "learning:GDX", **kw}


def _arm(arm_id: str = "GDX", **kw: Any) -> A.Arm:
    return A.Arm(id=arm_id, rule="clock_hold", timeframe="1h", params=_params(**kw))


def _control(arm_id: str = "GDXC", **kw: Any) -> A.Arm:
    return A.Arm(id=arm_id, rule="clock_hold_control", timeframe="1h", params=_control_params(**kw), role="control",
                 control_mode="replay_only")


@pytest.fixture(scope="module")
def ctx() -> R.RuleContext:
    b1 = ticks_to_1m(synthetic_ticks("2023-09-04", "2023-11-04", ticks_per_minute=1, seed=11))
    return R.RuleContext(bars=resample_bars(b1, "1h").reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


# ------------------------------------------------------------------------------------------------ the rules
def test_the_arm_is_long_on_the_1h_close_at_0000_utc_of_every_weekday_with_its_stop_no_target_and_a_7_bar_hold(
        ctx) -> None:
    got = G.clock_hold(_arm(), ctx)
    closes = ctx.close_ts[got["idx"].to_numpy()]
    assert len(got) > 30
    assert set(closes.hour) == {0} and set(closes.minute) == {0} and set(closes.dayofweek) <= {0, 1, 2, 3, 4}
    days_with_a_0000_close = {d for d, h in zip(ctx.close_ts.normalize(), ctx.close_ts.hour, strict=True)
                              if h == 0 and d.dayofweek < 5}
    assert set(closes.normalize()) == days_with_a_0000_close                 # every weekday, no filter, no signal
    assert set(got["side"]) == {1} and set(got["risk_usd"]) == {48.0} and set(got["target_r"]) == {50.0}
    assert set(got["max_bars"]) == {6}           # triple_barrier_1m's count: the exit is the close of bar idx + 7


def test_the_arm_reads_no_later_bar_and_appending_bars_never_changes_a_past_candidate(ctx) -> None:
    full = G.clock_hold(_arm(), ctx)
    cut = 600
    part = R.RuleContext(bars=ctx.bars.iloc[:cut].reset_index(drop=True), b1=ctx.b1, blackout_lo=NO, blackout_hi=NO)
    early = G.clock_hold(_arm(), part)
    assert early.equals(full[full["idx"] < cut].reset_index(drop=True))


def test_a_tier1_blackout_at_0000_drops_that_days_candidate_as_for_every_arm(ctx) -> None:
    day = G.clock_hold(_arm(), ctx)
    first = ctx.close_ts[int(day["idx"].iloc[0])]
    lo = np.array([(first - pd.Timedelta(minutes=15)).value], dtype=np.int64)
    hi = np.array([(first + pd.Timedelta(minutes=30)).value], dtype=np.int64)
    blacked = R.RuleContext(bars=ctx.bars, b1=ctx.b1, blackout_lo=lo, blackout_hi=hi)
    kept = R.candidates(_arm(), blacked)
    assert first not in set(ctx.close_ts[kept["idx"].to_numpy()]) and len(kept) == len(day) - 1


def test_the_control_is_long_once_a_weekday_at_a_seeded_hour_from_0700_to_1300_with_the_arms_bracket(ctx) -> None:
    got = G.clock_hold_control(_control(), ctx)
    closes = ctx.close_ts[got["idx"].to_numpy()]
    assert len(got) > 30 and closes.normalize().is_unique                     # one a weekday
    assert set(closes.hour) <= set(CONTROL_HOURS) and set(closes.minute) == {0}
    assert set(closes.dayofweek) <= {0, 1, 2, 3, 4}
    assert set(got["side"]) == {1} and set(got["risk_usd"]) == {48.0} and set(got["target_r"]) == {50.0}
    assert set(got["max_bars"]) == {6}
    for at in closes:                            # the draw: hash_u of the control id and the UTC day, restart-safe
        assert at.hour == G.drawn_hour("GDXC", at.normalize(), CONTROL_HOURS)
    assert G.clock_hold_control(_control(), ctx).equals(got)


def test_the_control_draws_every_declared_hour_and_its_draw_depends_on_its_own_id() -> None:
    days = pd.date_range("2026-11-23", periods=400, freq="D", tz="UTC")
    a = [G.drawn_hour("GD1CD", d, CONTROL_HOURS) for d in days]
    b = [G.drawn_hour("GD1CP", d, CONTROL_HOURS) for d in days]
    assert set(a) == set(CONTROL_HOURS) and set(b) == set(CONTROL_HOURS)
    assert a != b
    counts = np.bincount(a, minlength=24)[CONTROL_HOURS]
    assert counts.min() > 400 / 7 * 0.6                                       # roughly uniform, no hour starved


def test_rows_the_engine_cannot_run_as_registered_are_refused_at_load() -> None:
    with pytest.raises(ValueError, match="1h"):
        A.Arm(id="GDX", rule="clock_hold", timeframe="15m", params=_params())
    with pytest.raises(ValueError, match="hold_bars"):
        _arm(hold_bars=6)
    with pytest.raises(ValueError, match="side"):
        _arm(side=-1)
    with pytest.raises(ValueError, match="target_r"):
        _arm(target_r=2.0)
    with pytest.raises(ValueError, match="weekdays"):
        _arm(weekdays=[0, 5])
    with pytest.raises(ValueError, match="entry_utc"):
        _arm(entry_utc="00:30")
    with pytest.raises(ValueError, match="rollover"):
        _arm(entry_utc="15:00")                                   # 15:00 + 7 h runs into the 20:45 UTC rollover
    with pytest.raises(ValueError, match="rollover"):
        _control(entry_hours_utc=[7, 14])
    with pytest.raises(ValueError, match="entry_hours_utc"):
        _control(entry_hours_utc=[])


def test_the_control_matches_the_arms_bracket_and_a_different_stop_or_hold_is_no_match() -> None:
    assert A.control_match_refusal(_arm(), _control()) is None
    assert "stop" in str(A.control_match_refusal(_arm(), _control(stop_usd=40.0)))
    assert A.control_match_refusal(_arm(), _control(hold_bars=7, target_r=50.0)) is None


# ------------------------------------------------------------------------------------------------ the spec rows
def _rows() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((ROOT / "config" / "exploration_arms.yaml").read_text())["arms"]
    return {r["id"]: r for r in raw if r["rule"] in G.CLOCK_HOLD_RULES}


def test_the_spec_has_two_learning_arms_each_with_its_own_replay_only_control(tmp_path) -> None:
    rows = _rows()
    assert set(rows) == set(ARMS) | {c for c, _ in ARMS.values()}
    spec = {k: A.Arm.model_validate(r) for k, r in rows.items()}
    for arm_id, (control_id, _) in ARMS.items():
        a, c = spec[arm_id], spec[control_id]
        block = A.Learning.model_validate(a.learning)
        assert a.role == "learning" and a.forward_only and a.max_open == 1 and a.timeframe == "1h"
        assert a.params == _params(prereg="prereg-amendment-2026-11-20-gd1-asia-learning.md#rules")
        assert block.prereg == PREREG and (ROOT / PREREG).is_file()
        assert block.control == control_id and A.control_match_refusal(a, c) is None
        assert (block.since, block.until) == ("2026-11-23", "2027-01-04")
        assert block.loss_cap_gbp == 800.0                     # the class's 20 x its £40 per-trade cap
        assert c.role == "control" and c.control_mode == "replay_only" and c.forward_only
        assert A.dedicated_control_of(c) == (arm_id,)
        assert set(c.params["entry_hours_utc"]) == set(CONTROL_HOURS)
    reg = tmp_path / "registry.jsonl"
    reg.write_text("\n".join(json.dumps({"config_hash": A.Learning.model_validate(spec[a].learning).k_id,
                                         "family": A.LEARNING_FAMILY}) for a in ARMS) + "\n")
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump({"arms": list(rows.values())}))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(A, "_now", lambda: pd.Timestamp("2026-11-22 23:00", tz="UTC"))
        early = {a.id: a for a in A.load_arms(path, registry=reg)}
        assert all("not yet" in early[a].entry_refused for a in ARMS), "no fill before its registration day"
        mp.setattr(A, "_now", lambda: pd.Timestamp("2026-11-23 00:00", tz="UTC"))
        loaded = {a.id: a for a in A.load_arms(path, registry=reg)}
        assert all(a.entry_refused == "" for a in loaded.values()), {k: a.entry_refused for k, a in loaded.items()}
        mp.setattr(A, "_now", lambda: pd.Timestamp("2027-01-05 00:00", tz="UTC"))
        late = {a.id: a for a in A.load_arms(path, registry=reg)}
        assert all("ended" in late[a].entry_refused for a in ARMS)
    assert all("does not resolve" in a.entry_refused for a in A.load_arms(path, registry=tmp_path / "none.jsonl")
               if a.role == "learning"), "without its K row the arm is refused (fail closed)"


def test_each_arm_and_its_control_are_registered_on_their_own_demo_probe_only_account_only_at_the_swing_cap() -> None:
    accounts = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)
    for arm_id, (control_id, host) in ARMS.items():
        on = {k for k, acc in accounts.items() if acc.exploration and {arm_id, control_id} & set(acc.exploration.arms)}
        assert on == {host}
        acc = accounts[host]
        assert acc.mode == "demo" and acc.probe_only and acc.exploration is not None and acc.exploration.enabled
        arm, ctl = acc.exploration.arms[arm_id], acc.exploration.arms[control_id]
        assert (arm.style, arm.max_loss, arm.sizing, arm.adds) == ("swing", 40.0, "min_lot", 0)
        assert ctl.style == "swing" and ctl.control_share is not None
        assert "swing" in acc.exploration.pots and acc.exploration.learning_max_open >= 1
        # the $48 stop at 0.01 lot (1 oz) plus the ~0.8 bp round trip fits the ADR 0043 swing cap with room for GBP
        # at 0.80 per USD (0.752 today): the cap refuses, never clamps
        assert 48.0 * 0.80 + 0.8e-4 * 4500 * 0.80 < arm.max_loss


def test_the_stop_rules_register_the_quants_checks_per_arm_and_the_controls_stop_with_their_arm() -> None:
    rules = arm_stops.load_stop_rules()
    for arm_id, (control_id, _) in ARMS.items():
        u = rules.units[rules.unit_of(arm_id) or ""]
        assert u.arms == (arm_id,) and (u.stop_r, u.within) == (-10.0, 40) and u.source == "GD1"
        assert [(s.at, s.cost_bp_gt, s.mean_bp_le, s.mean_bp_lt) for s in u.safety] == [
            (40, 1.1, None, None), (120, None, -4.0, None), (250, None, None, 3.5)]
        assert rules.follows[control_id] == arm_id


def test_the_amendment_names_each_arm_its_k_id_its_control_and_the_kill_numbers() -> None:
    text = (ROOT / PREREG).read_text()
    spec = {a.id: a for a in A.load_arms(ROOT / "config" / "exploration_arms.yaml")}
    for arm_id, (control_id, host) in ARMS.items():
        block = A.Learning.model_validate(spec[arm_id].learning)
        assert arm_id in text and control_id in text and host in text and block.k_id in text
        ctl_k = A.learning_k_id(control_id, A.arm_hash(spec[control_id]), PREREG)
        assert ctl_k in text
    for phrase in ("1.1 bp", "40 fills", "-4 bp", "120 fills", "+3.5 bp", "250 fills", "replay_only",
                   "time_exit_ev_unsupported", "H-98",
                   # review round 2026-11-20: quant C1, C2 and point 4
                   "no kill check falls due in this run", "a renewal adds", "Continue is not", "reads about 0.1-0.3 bp low",
                   "never compares"):
        assert phrase in text, phrase


# ------------------------------------------------------------------------------------------------ in the engine
def _engine_arm(spec: A.Arm) -> exploration.Arm:
    return exploration.Arm(spec=spec, style="swing", control_share=0.01 if spec.role == "control" else None)


def _gd1_engine(tmp_path: Path, spec: A.Arm, cap: float = 50.0) -> tuple[Any, Any]:
    """The exploration test engine with both accounts' demo ceiling for a minimum-lot entry (accounts.yaml
    `risk_overrides.ceilings.min_lot_risk_cap` 0.01, ADR 0032): the $48 stop at 0.01 lot fits it. The paper account is
    in USD, so the arm's cap is $50 here; the GBP 40 cap of the accounts is tested on the accounts above."""
    eng, pb = _engine(tmp_path, [_engine_arm(spec)], caps={spec.id: cap})
    eng.gate.limits = eng.gate.limits.model_copy(update={"min_lot_risk_cap": 0.01})
    return eng, pb


def test_the_arm_sends_a_minimum_lot_long_at_the_0000_close_through_the_riskgate_with_its_wide_stop(tmp_path) -> None:
    eng, pb = _gd1_engine(tmp_path, _arm("GD1D"))
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy
    tick = _at(eng, pb, TUE_00)
    exploration.on_pass(eng, tick)
    [order] = _sent(eng)
    assert order["action"] == "exploration:executed:auto", eng.decisions
    [(intent, gd)] = seen
    assert intent.purpose == "exploration" and intent.arm_id == "GD1D" and gd.allowed
    assert "time_exit_ev_unsupported" not in gd.reasons                     # the fence, not an EV judgement
    assert intent.side == 1 and intent.volume_max == intent.volume_min == 0.01
    [tr] = eng.open.values()
    assert tr.open_price is not None and tr.sl is not None and tr.tp is not None
    assert tr.open_price - tr.sl == pytest.approx(48.0, abs=0.5)            # the catastrophic stop at the broker
    assert tr.tp - tr.open_price > 2_000.0                                  # no target: 50 R, out of reach


def test_the_position_is_closed_at_0700_utc_on_the_wall_clock_even_when_its_bars_are_missing(tmp_path,
                                                                                            monkeypatch) -> None:
    eng, pb = _gd1_engine(tmp_path, _arm("GD1D"))
    exploration.on_pass(eng, _at(eng, pb, TUE_00))
    [pid] = list(eng.open)
    eng.open[pid].bars_held = 0                                   # every bar of the hold missed: the count never ends
    closes: list[tuple[int, str]] = []
    monkeypatch.setattr(eng, "_try_close", lambda p, why, now, **kw: closes.append((p, why)))
    seven = pd.Timestamp("2025-03-04 07:00", tz="UTC")
    exploration.exits(eng, seven - pd.Timedelta(seconds=1))
    assert not closes
    exploration.exits(eng, seven)
    assert closes == [(pid, "time_exit")]
    assert not exploration._censored(eng.exploration.arms["GD1D"], "time_exit", eng.open[pid])


def test_after_a_restart_without_its_order_row_the_wall_clock_exit_runs_from_the_positions_open_time(
        tmp_path, monkeypatch) -> None:
    """Trading-safety LOW: an adopted position whose order id (the signal close) is lost exits 7 hours after it
    opened, never later; the bar count stays the backstop."""
    eng, pb = _gd1_engine(tmp_path, _arm("GD1D"))
    exploration.on_pass(eng, _at(eng, pb, TUE_00))
    [pid] = list(eng.open)
    tr = eng.open[pid]
    tr.client_order_id, tr.bars_held = None, 0                   # adopted: no order row, every bar of the hold missed
    tr.opened_utc = TUE_00 + pd.Timedelta(seconds=40)
    closes: list[tuple[int, str]] = []
    monkeypatch.setattr(eng, "_try_close", lambda p, why, now, **kw: closes.append((p, why)))
    due = TUE_00 + pd.Timedelta(hours=7, seconds=40)
    exploration.exits(eng, due - pd.Timedelta(seconds=1))
    assert not closes
    exploration.exits(eng, due)
    assert closes == [(pid, "time_exit")]


def test_at_an_extreme_exchange_rate_the_entry_is_refused_never_resized_or_given_a_wider_stop(tmp_path) -> None:
    """Trading-safety LOW: below about 1.20 USD per GBP the $48 stop at 0.01 lot is over the GBP 40 cap; the cap
    refuses the entry (it never clamps). Here the paper account is in USD: a cap just under the $48 stop stands for it."""
    assert 48.0 / 1.18 + 0.3 > 40.0 > 48.0 / 1.33 + 0.3                    # GBP/USD 1.18 refused, 1.33 (0.752) fits
    eng, pb = _gd1_engine(tmp_path, _arm("GD1D"), cap=47.0)
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy
    exploration.on_pass(eng, _at(eng, pb, TUE_00))
    assert not _sent(eng) and not pb.positions() and not eng.open
    assert [d["action"] for d in eng.decisions if str(d.get("action", "")).startswith("exploration_refused:")] == [
        "exploration_refused:exploration_arm_cap"]
    assert all(i.volume_min == i.volume_max == 0.01 for i, _ in seen)       # never a smaller lot
    assert all(d["stop_usd"] == 48.0 for d in eng.decisions if d.get("arm") == "GD1D")   # never another stop


def test_the_replay_only_control_never_reaches_the_riskgate_and_its_candidate_is_recorded(tmp_path,
                                                                                         monkeypatch) -> None:
    ctl = _control("GD1CD", control="GD1D")   # undedicated here: a dedicated control stops without its arm
    hour = G.drawn_hour("GD1CD", TUE_00, CONTROL_HOURS)
    eng, pb = _gd1_engine(tmp_path, ctl)
    calls: list[Any] = []
    monkeypatch.setattr(eng.gate, "check", lambda *a, **k: calls.append(a))
    exploration.on_pass(eng, _at(eng, pb, TUE_00 + pd.Timedelta(hours=hour)))
    assert not calls and not _sent(eng) and not pb.positions()
    assert [d["action"] for d in eng.decisions if str(d.get("action", "")).startswith("exploration_refused:")] == [
        "exploration_refused:replay_only"]


# ------------------------------------------------------------------------------------------------ the kill rule
START = pd.Timestamp("2026-09-01 00:00", tz="UTC")


def _fills(sd: Path, arm: str, n: int, *, pnl: float = 0.3, spread: float = 0.2, slip: float = 0.0,
           commission: float | None = -0.06, start: pd.Timestamp = START, tag: str = "", host: str = "icm-demo",
           stop_loss: float = 36.0) -> None:
    """`n` admissible GD-1 closes of `arm`, one a day from `start` (entry 00:00, exit 07:00 UTC), at $4,000 and 0.75
    GBP per USD: 1 bp of the 1 oz notional is GBP 0.30; spread and commission (GBP) set the realised round trip."""
    db = Database.open(sd, "aux")
    for i in range(n):
        at = start + pd.Timedelta(days=i)
        cid = f"{arm}{tag}-{i}"
        ed.record_candidate(db, candidate_id=cid, account_id=host, broker=HOSTS[host], arm_id=arm, family="clock_hold",
                            style="swing", tf="1h", side=1, decision_utc=at, stop_dist=48.0, target_dist=2400.0,
                            max_hold_s=7 * 3600, taken=True, is_control=False, blocked_reason=None, branch="random",
                            u=0.5, mu=1.0)
        rec = ExplorationRecord(client_order_id=cid, account_id=host, arm_id=arm, family="clock_hold", tf="1h", side=1,
                                lots=0.01, signal_utc=at, sent_utc=at, atr_usd=48.0, stop_usd=48.0, target_usd=2400.0,
                                hold_s=7 * 3600, requested_price=4000.0, spread_usd=spread, fill_price=4000.0 + slip,
                                stop_loss=stop_loss, cost=0.2, exit_utc=at + pd.Timedelta(hours=7), exit_price=4001.0,
                                exit_reason="time_exit", swap=0.0, commission=commission, pnl=pnl, censored=False,
                                fx_acct_per_usd=0.75)
        with db.write() as conn:
            exploration.LEDGER.upsert(conn, rec)


def _status(sd: Path, arm: str) -> labs.ArmStatus:
    with Database.open(sd, "aux").connection() as conn:
        return {s.arm_id: s for s in labs.arm_statuses(conn, ["icm-demo", "icm-probe"])}[arm]


def test_the_realised_round_trip_is_the_spread_crossed_the_adverse_entry_slippage_and_the_commission_in_bp() -> None:
    rec = ExplorationRecord(client_order_id="x", account_id="icm-demo", arm_id="GD1D", family="clock_hold", tf="1h",
                            side=1, lots=0.01, signal_utc=START, sent_utc=START, atr_usd=48.0, stop_usd=48.0,
                            target_usd=2400.0, hold_s=25200, requested_price=4000.0, spread_usd=0.2,
                            fill_price=4000.04, commission=-0.06, fx_acct_per_usd=0.75)
    # (0.2 spread + 0.04 slippage + 0.06 / 0.75 commission per oz) / 4000.04 x 1e4
    assert arm_stops.realised_cost_bp(rec) == pytest.approx(0.32 / 4000.04 * 1e4)
    short = rec.model_copy(update={"side": -1})
    assert arm_stops.realised_cost_bp(short) == pytest.approx(0.24 / 4000.04 * 1e4)    # a better fill is cheaper
    assert arm_stops.realised_cost_bp(rec.model_copy(update={"commission": None})) is None
    assert arm_stops.realised_cost_bp(rec.model_copy(update={"spread_usd": None})) is None


def test_an_arm_is_killed_at_its_40th_fill_when_its_realised_round_trip_is_above_1_1_bp(tmp_path) -> None:
    _fills(tmp_path, "GD1D", 39, spread=0.40, slip=0.04)                    # 1.3 bp a round trip
    assert _status(tmp_path, "GD1D").band != "at_stop"
    _fills(tmp_path, "GD1D", 1, spread=0.40, slip=0.04, start=START + pd.Timedelta(days=45), tag="b")
    st = _status(tmp_path, "GD1D")
    assert (st.band, st.stop_at, st.rule) == ("at_stop", 40, "GD1")
    review = _run(tmp_path)
    assert {r.arm_id for r in review.retired} >= {"GD1D", "GD1CD"}         # the control stops with its arm
    with Database.open(tmp_path, "aux").connection() as conn:
        [(rule,)] = conn.execute("SELECT rule FROM exploration_arm_status WHERE arm_id = 'GD1D' AND broker = 'icm'"
                                 ).fetchall()
    assert "gd1-asia-learning" in str(rule)


def test_a_round_trip_at_or_below_1_1_bp_runs_on_and_an_unreadable_cost_waits_for_40_readable_fills(tmp_path) -> None:
    _fills(tmp_path, "GD1P", 40, host="icm-probe")                          # 0.7 bp
    assert _status(tmp_path, "GD1P").band != "at_stop"
    _fills(tmp_path, "GD1D", 1, spread=0.40, commission=None, tag="u")     # no commission read: no cost figure
    _fills(tmp_path, "GD1D", 39, spread=0.40, slip=0.04, start=START + pd.Timedelta(days=1))
    assert _status(tmp_path, "GD1D").band != "at_stop"
    _fills(tmp_path, "GD1D", 1, spread=0.40, slip=0.04, start=START + pd.Timedelta(days=60), tag="b")
    st = _status(tmp_path, "GD1D")
    assert (st.band, st.stop_at) == ("at_stop", 41)


def test_an_arm_is_killed_at_its_120th_fill_when_its_mean_net_is_at_or_below_minus_4_bp(tmp_path) -> None:
    _fills(tmp_path, "GD1D", 119, pnl=-1.2)                                 # -4.0 bp each
    assert _status(tmp_path, "GD1D").band != "at_stop"
    _fills(tmp_path, "GD1D", 1, pnl=-1.2, start=START + pd.Timedelta(days=200), tag="b")
    st = _status(tmp_path, "GD1D")
    assert (st.band, st.stop_at) == ("at_stop", 120)


def test_a_mean_net_above_minus_4_bp_at_120_fills_runs_on(tmp_path) -> None:
    _fills(tmp_path, "GD1D", 120, pnl=-1.17)                                # -3.9 bp
    assert _status(tmp_path, "GD1D").band != "at_stop"


def test_at_250_fills_the_arm_continues_only_with_a_mean_net_of_at_least_3_5_bp(tmp_path) -> None:
    _fills(tmp_path, "GD1D", 250, pnl=1.02)                                 # +3.4 bp
    st = _status(tmp_path, "GD1D")
    assert (st.band, st.stop_at) == ("at_stop", 250)
    _fills(tmp_path, "GD1P", 250, pnl=1.05, host="icm-probe")               # +3.5 bp
    assert _status(tmp_path, "GD1P").band != "at_stop"


def test_h61s_swing_stop_still_binds_the_arm(tmp_path) -> None:
    _fills(tmp_path, "GD1D", 10, pnl=-36.0)                                 # -1 R each, -10 R within 40
    st = _status(tmp_path, "GD1D")
    assert (st.band, st.stop_at) == ("at_stop", 10)


def test_a_check_is_judged_once_on_its_first_fills_and_a_later_recovery_does_not_undo_it(tmp_path) -> None:
    _fills(tmp_path, "GD1D", 120, pnl=-1.5)                                 # -5 bp at 120: killed there
    _fills(tmp_path, "GD1D", 130, pnl=3.0, start=START + pd.Timedelta(days=200), tag="b")
    st = _status(tmp_path, "GD1D")
    assert (st.band, st.stop_at) == ("at_stop", 120)


def _rows_bp(bps: list[float | None]) -> list[arm_stops.Fill]:
    return [arm_stops.Fill(r=0.0, at=START + pd.Timedelta(days=i), counted=True, arm="X", bp=b, cost_bp=b)
            for i, b in enumerate(bps)]


def test_a_legacy_single_mapping_check_fires_only_when_all_its_first_fills_are_readable() -> None:
    """Code review LOW: WFRI's form (one mapping) keeps its rule: the mean over the first `at` fills, judged only when
    every one of them is readable; a missing figure among them means no fire, then or later."""
    unit = arm_stops.StopUnit.model_validate({"arms": ["X"], "stop_r": -10.0, "within": 40, "source": "TS",
                                              "safety": {"at": 3, "mean_bp_le": -20.0, "cum_r_le": -10.0}})
    assert len(unit.safety) == 1 and not unit.safety[0].readable_only
    assert arm_stops.unit_stop(unit, _rows_bp([-30.0, -30.0, -30.0])).stop_at == 3
    assert arm_stops.unit_stop(unit, _rows_bp([-30.0, None, -30.0, -30.0])).band != "at_stop"


def test_a_list_form_check_waits_for_its_first_readable_figures() -> None:
    unit = arm_stops.StopUnit.model_validate({"arms": ["X"], "stop_r": -10.0, "within": 40, "source": "GD1",
                                              "safety": [{"at": 3, "mean_bp_le": -20.0}, {"at": 3, "cost_bp_gt": 1.1}]})
    assert all(s.readable_only for s in unit.safety)
    assert arm_stops.unit_stop(unit, _rows_bp([-30.0, None, -30.0])).band != "at_stop"       # 2 readable: not due
    st = arm_stops.unit_stop(unit, _rows_bp([-30.0, None, -30.0, -30.0]))
    assert (st.band, st.stop_at) == ("at_stop", 4)
