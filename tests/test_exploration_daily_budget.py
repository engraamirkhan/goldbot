"""The daily exploration entry budget (owner decision 2026-10-30: 150-250 demo fills a day in total; accounts.yaml
`exploration.daily`, `ops.accounts.DailyEntries`, goldbot/engine/exploration_budget.py): fills a day, an hour and a
session per account counted by signal close; paired controls admitted per pair by matched-time thinning (quant ruling
2026-11-06 Q1); unpaired per-instrument controls capped and stopped at their cost cell's 30 fills. Entries only: exits
are never counted or gated."""
from __future__ import annotations

import logging
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import Engine, entry_refusals, exploration
from goldbot.engine import exploration_budget as budget
from goldbot.engine.exploration import LEDGER, Arm, ExplorationRecord, Pot
from goldbot.engine.exploration_budget import daily_refusal, entry_kind
from goldbot.ops import accounts as acc_mod
from goldbot.ops.accounts import DailyEntries
from goldbot.research import exploration_data as ed
from goldbot.research.arm_rules import hash_u
from goldbot.research.arms import Arm as SpecArm
from tests.test_exploration_engine import (
    CLOSE,
    LONG,
    QUALIFIED,
    TICKS,
    _actions,
    _arm,
    _at,
    _rules,  # noqa: F401 - the always-long rule and the fixed loss (autouse)
    _sent,
)
from tests.test_exploration_engine import _engine as _base_engine

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.usefixtures("_rules")
START = CLOSE - pd.Timedelta(hours=6)                     # Tuesday 14:00 UTC: London and New York both open
ALL = {"asia": 100, "london": 100, "newyork": 100, "late_asia": 100}   # segment budgets that never bind


def _engine(tmp_path: Path, arms: list[Arm], daily: DailyEntries | None) -> tuple[Engine, Any]:
    """The exploration engine of tests/test_exploration_engine.py with the account's daily budget set."""
    eng, pb = _base_engine(tmp_path, arms, pots={"intraday": Pot(gbp=48, max_open=10),
                                                 "scalp": Pot(gbp=48, max_open=10)})
    assert eng.exploration is not None
    eng.exploration.cfg = eng.exploration.cfg.model_copy(update={"daily": daily})
    return eng, pb


def _passes(eng: Engine, pb: Any, n: int, start: pd.Timestamp = START) -> None:
    for k in range(n):
        exploration.on_pass(eng, _at(eng, pb, start + pd.Timedelta(minutes=15 * k)))


def _spec(arm_id: str, params: dict[str, Any], role: str = "strategy") -> SpecArm:
    return SpecArm(id=arm_id, rule=LONG, timeframe="1m", max_open=1, params=params, status="run",
                   qualification=QUALIFIED, role=role)  # type: ignore[arg-type]


@pytest.fixture
def _sessions_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session budgets that never bind: the day's and the hour's are under test."""
    monkeypatch.setattr(budget, "session_budgets", lambda *a, **k: dict(ALL))


def _rec(i: int, arm: str, at: pd.Timestamp, *, filled: bool = True, symbol: str = "XAUUSD",
         account: str = "icm-probe") -> ExplorationRecord:
    return ExplorationRecord(client_order_id=f"o{i}", account_id=account, arm_id=arm, family="f", tf="15m", side=1,
                             lots=0.01, signal_utc=at, sent_utc=pd.Timestamp("2030-01-01", tz="UTC"), atr_usd=1.0,
                             stop_usd=1.0, target_usd=1.0, hold_s=60, position_id=i if filled else None, symbol=symbol)


def _history(tmp_path: Path, fills: dict[str, list[pd.Timestamp]], signals: dict[str, list[pd.Timestamp]]) -> None:
    """Earlier days' ledger fills (by signal close) and control candidates (exploration_obs) of icm-probe."""
    db = exploration.ledger_db(str(tmp_path))
    i = 1000
    with db.write() as conn:
        for arm, ts in fills.items():
            for t in ts:
                i += 1
                LEDGER.upsert(conn, _rec(i, arm, t))
    for arm, ts in signals.items():
        for t in ts:
            i += 1
            ed.record_candidate(db, candidate_id=f"h{i}", account_id="icm-probe", broker="icm", arm_id=arm,
                                family="random_bracket", style="intraday", tf="15m", side=1, decision_utc=t,
                                stop_dist=5.0, target_dist=10.0, max_hold_s=3600, taken=True, is_control=True,
                                mu=1.0, branch="random", u=0.5)


# ------------------------------------------------------------------------------------------------ the rule alone
def test_an_unpaired_per_instrument_control_is_thin_and_gold_or_learning_paired_controls_are_not():
    edge = Arm(spec=_spec("e", {"stop_usd": 3.0}), style="scalp")
    gold = Arm(spec=_spec("S0", {"stop_usd": 3.0}), style="scalp", control_share=0.25)
    twin = Arm(spec=_spec("LCS5A", {"stop_usd": 3.0, "control": "learning:S5A"}), style="scalp", control_share=0.05)
    lbc = Arm(spec=_spec("LBC-US500", {"instrument": "US500", "stop_atr": 2.0, "control": "learning:LB-US500"}),
              style="scalp", control_share=0.02)
    thin = Arm(spec=_spec("S0-US500", {"instrument": "US500", "stop_atr": 2.0}), style="scalp", control_share=0.01)
    assert [entry_kind(a) for a in (edge, gold, twin, lbc, thin)] == ["edge", "control", "control", "control", "thin"]


def test_each_control_pairs_the_arms_its_rows_and_the_quant_ruling_name():
    arms = {"LBC-US500": Arm(spec=_spec("LBC-US500", {"instrument": "US500", "stop_atr": 2.0,
                                                     "control": "learning:LB-US500"}, "control"), style="scalp",
                             control_share=0.02)}
    got = budget.pairs(arms)
    assert got["LBC-US500"] == ("LB-US500",) and got["F1C"] == ("F1A",) and got["G1C"] == ("G1A",)
    assert got["ZCD"] == ("Z10D", "Z3D", "Z9D") and set(got["I0"]) >= {"OBR15"}


def test_the_day_and_the_hour_budgets_refuse_every_kind_once_spent():
    d = DailyEntries(max_per_day=3, max_per_hour=2)
    kw: dict[str, Any] = {"segment": 1, "budgets": ALL}
    assert daily_refusal(None, "edge", {"day": 10**6}, **kw) is None                 # no budget configured: no bound
    assert daily_refusal(d, "edge", {"day": 2, "hour": 1}, **kw) is None
    assert daily_refusal(d, "edge", {"day": 3, "hour": 0}, **kw) == "daily_entries"
    assert daily_refusal(d, "edge", {"day": 2, "hour": 2}, **kw) == "hourly_entries"


def test_a_saturated_asia_does_not_stop_the_first_new_york_fill():
    d = DailyEntries(max_per_day=30)
    budgets = {"asia": 10, "london": 10, "newyork": 5, "late_asia": 5}
    n = {"day": 10}
    assert daily_refusal(d, "edge", n, segment=0, budgets=budgets) == "session_entries"
    assert daily_refusal(d, "edge", n, segment=2, budgets=budgets) is None


def test_the_utc_days_segments_run_asia_london_new_york_then_the_asia_after_the_new_york_close():
    day = pd.Timestamp("2025-03-03", tz="UTC")                 # GMT and EST: London 08:00, NY 13:30-22:00 UTC
    hours = [0.5, 5, 9, 15, 23]
    assert [budget.day_segment(day + pd.Timedelta(hours=h)) for h in hours] == [0, 0, 1, 2, 3]


def test_a_sessions_unspent_budget_rolls_forward_to_the_next_session_of_the_day_and_never_past_the_day():
    """Fix 2 of the 2026-11-09 volume diagnosis: an early session that left fills unspent passes them on (cumulative
    budget through the signal's segment); a busy early session still never spends a later one's (Q3)."""
    d = DailyEntries(max_per_day=70)
    budgets = {"asia": 20, "london": 20, "newyork": 20, "late_asia": 10}
    # Asia spent 4 of its 20: London may take its own 20 and Asia's unspent 16
    assert daily_refusal(d, "edge", {"day": 39}, segment=1, budgets=budgets) is None
    assert daily_refusal(d, "edge", {"day": 40}, segment=1, budgets=budgets) == "session_entries"
    # quant C4: the early Asia never spends the late Asia's own share
    assert daily_refusal(d, "edge", {"day": 20}, segment=0, budgets=budgets) == "session_entries"
    # the Asia after the New York close takes what the day left, and the day's cap binds
    assert daily_refusal(d, "edge", {"day": 69}, segment=3, budgets=budgets) is None
    assert daily_refusal(d, "edge", {"day": 70}, segment=3, budgets=budgets) == "daily_entries"


@pytest.mark.parametrize("budgets", [{"asia": 20, "london": 20, "newyork": 20, "late_asia": 10},
                                     {"asia": 40, "london": 40, "newyork": 40, "late_asia": 40}])
@pytest.mark.parametrize("wanted", [(2, 50, 50, 50), (0, 0, 0, 100), (100, 100, 100, 100), (5, 1, 0, 3)])
def test_rolled_session_budgets_never_admit_more_fills_than_max_per_day(budgets, wanted):
    """Whatever each segment's signals want, the day's admitted fills stay <= max_per_day and <= the budgets' sum, and
    a want the late Asia can still take is never left on the table."""
    d = DailyEntries(max_per_day=70)
    n = {"day": 0}
    for seg, want in enumerate(wanted):
        for _ in range(want):
            if daily_refusal(d, "edge", n, segment=seg, budgets=budgets) is None:
                n["day"] += 1
    cap = min(d.max_per_day, sum(budgets.values()))
    assert n["day"] <= cap
    if wanted[3] >= cap:
        assert n["day"] == cap


def test_session_budgets_follow_the_trailing_edge_signal_shares_and_split_equally_with_none(tmp_path):
    d = DailyEntries(max_per_day=90)
    db = exploration.ledger_db(str(tmp_path))
    ex = SimpleNamespace(db=db, budget_cache={})
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe", broker_name="icm", weekend_window=None))
    day = START.floor("D")
    arms = {"e": _arm("e", style="scalp")}
    assert budget.session_budgets(eng, ex, d, day, arms) == {  # type: ignore[arg-type]
        "asia": 23, "london": 23, "newyork": 22, "late_asia": 22}
    t_a, t_b = day - pd.Timedelta(hours=23), day - pd.Timedelta(hours=5)       # Monday 01:00 and 19:00 UTC
    assert budget.session_of(t_a) != budget.session_of(t_b)
    for i, at in enumerate([t_a] * 6 + [t_b] * 3):
        ed.record_candidate(db, candidate_id=f"c{i}-{at.value}", account_id="icm-probe", broker="icm", arm_id="e",
                            family="f", style="scalp", tf="1m", side=1, decision_utc=at + pd.Timedelta(minutes=i),
                            stop_dist=3.0, target_dist=3.0, max_hold_s=60, taken=True)
    budget.SHARES.clear()
    got = budget.session_budgets(eng, ex, d, day, arms)  # type: ignore[arg-type]
    # every segment keeps one fill and the rest is split by largest remainder: the sum is the day's
    assert got == {"asia": 58, "london": 1, "newyork": 30, "late_asia": 1}
    assert sum(got.values()) == d.max_per_day


def test_apportion_reserves_one_fill_per_segment_and_never_passes_max_per_day():
    """Code review LOW / trading-safety L1: 1000:1:1 of 10 gave 12 under the per-session floor."""
    got = budget.apportion(10, {"asia": 1000, "london": 1, "newyork": 1})
    assert got == {"asia": 7, "london": 1, "newyork": 1, "late_asia": 1}
    assert budget.apportion(2, {"asia": 5, "newyork": 9}) == {"asia": 1, "london": 0, "newyork": 1, "late_asia": 0}


def test_apportion_sums_to_max_per_day_for_any_candidate_counts():
    """Trading-safety L1, property: every segment >= 1 and the four sum to max_per_day (max_per_day >= 4)."""
    rng = random.Random(1109)
    for _ in range(2000):
        m = rng.randint(4, 400)
        by = {s: rng.choice([0, 0, rng.randint(1, 5), rng.randint(1, 10**5)]) for s in budget.SEGMENTS}
        got = budget.apportion(m, by)
        assert sum(got.values()) == m and min(got.values()) >= 1, (m, by, got)


def test_every_book_of_an_account_reads_one_split_computed_once_a_day(tmp_path, monkeypatch):
    """Code review MEDIUM, quant C3(b), trading-safety L2: the split is cached per (ledger, account, UTC day, active
    arm set), not per book, so a second book reads the same shares without re-reading the candidates; another active
    set is a new split."""
    eng, ex, day = _icm_demo_history(tmp_path)
    arms = {"Z1D": _arm("Z1D", style="scalp")}
    calls: list[str] = []
    real = entry_refusals.static_refusal

    def counted(*a: Any) -> Any:
        calls.append("x")
        return real(*a)
    monkeypatch.setattr(entry_refusals, "static_refusal", counted)
    first = budget.session_budgets(eng, ex, DailyEntries(max_per_day=70), day, arms)
    n = len(calls)
    other_book = SimpleNamespace(db=ex.db, budget_cache={})
    assert budget.session_budgets(eng, other_book, DailyEntries(max_per_day=70), day, arms) == first  # type: ignore[arg-type]
    assert len(calls) == n > 0
    budget.session_budgets(eng, other_book, DailyEntries(max_per_day=70), day,  # type: ignore[arg-type]
                           {**arms, "Z2D": _arm("Z2D", style="scalp")})
    assert len(calls) > n


def test_a_static_refusal_that_raises_excludes_the_candidate_and_warns_once_per_read(tmp_path, monkeypatch, caplog):
    """Quant C3(c), code review LOW: fail closed (the candidate takes no share), one warning per refusal per read."""
    def boom(*_a: Any) -> bool:
        raise RuntimeError("unreadable")
    monkeypatch.setattr(entry_refusals, "REFUSALS", (entry_refusals.EntryRefusal(name=entry_refusals.WINDOW_0207,
                                                                                   applies=boom),))
    eng, ex, day = _icm_demo_history(tmp_path)
    with caplog.at_level(logging.WARNING):
        got = budget.session_budgets(eng, ex, DailyEntries(max_per_day=70), day,
                                     {"Z1D": _arm("Z1D", style="scalp")})
    assert got == {"asia": 18, "london": 18, "newyork": 17, "late_asia": 17}      # no candidate counted: equal
    warned = [r for r in caplog.records if "refusal_error:window_0207" in r.getMessage()]
    assert len(warned) == 1


def test_a_slow_session_split_read_is_warned(tmp_path, monkeypatch, caplog):
    """Trading-safety L3: the read's duration is logged, and a warning above 1 s."""
    eng, ex, day = _icm_demo_history(tmp_path)
    clock = iter([0.0, 1.5])
    monkeypatch.setattr(budget, "_clock", lambda: next(clock))
    with caplog.at_level(logging.INFO, logger="goldbot.engine.exploration_budget"):
        budget.session_budgets(eng, ex, DailyEntries(max_per_day=70), day,
                               {"Z1D": _arm("Z1D", style="scalp")})
    assert [r.levelno for r in caplog.records if "session split" in r.getMessage()] == [logging.WARNING]


def test_aux_migration_27_indexes_the_candidates_by_account_kind_and_decision_time(tmp_path):
    db = exploration.ledger_db(str(tmp_path))
    with db.connection() as conn:
        cols = [r[2] for r in conn.execute("PRAGMA index_info(exploration_obs_account_kind)")]
    assert cols == ["account_id", "is_control", "decision_ns"]


def _icm_demo_history(tmp_path: Path, arm: str = "Z1D") -> tuple[Any, Any, pd.Timestamp]:
    """icm-demo's Monday (GMT/EST: Asia to 08:00, London to 13:30, New York to 22:00 UTC) edge candidates of `arm`:
    Asia 2 at 00:30, 10 between 02:00 and 07:00 (the window_0207 gold block), 2 at 23:00; London 4; New York 6."""
    db = exploration.ledger_db(str(tmp_path))
    mon = START.floor("D") - pd.Timedelta(days=1)
    hours = [0.5] * 2 + [2 + k * 0.5 for k in range(10)] + [23] * 2 + [9] * 4 + [15] * 6
    for i, h in enumerate(hours):
        ed.record_candidate(db, candidate_id=f"d{arm}{i}", account_id="icm-demo", broker="icm", arm_id=arm, family="f",
                            style="scalp", tf="1m", side=1, decision_utc=mon + pd.Timedelta(hours=h, minutes=i % 3),
                            stop_dist=3.0, target_dist=3.0, max_hold_s=60, taken=False,
                            blocked_reason="window_0207" if 2 <= h < 7 else None)
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-demo", broker_name="icm", weekend_window=None))
    return eng, SimpleNamespace(db=db, budget_cache={}), START.floor("D")


def test_window_0207_hours_no_longer_count_in_the_session_shares_so_asia_falls_and_london_new_york_rise(tmp_path):
    """Cause 4 of the 2026-11-09 volume diagnosis: Asia's 02:00-07:00 gold candidates are refused at entry
    (`entry_refusals.window_0207`), so they take no share. Counted, the split was Asia 41 / London 12 / New York 17 of
    70 (14:4:6); from the admissible candidates (early Asia 2, London 4, New York 6, late Asia 2) it is 11/20/29/10,
    and the sum is max_per_day."""
    eng, ex, day = _icm_demo_history(tmp_path)
    d = DailyEntries(max_per_day=70)
    got = budget.session_budgets(eng, ex, d, day, {"Z1D": _arm("Z1D", style="scalp")})
    assert got == {"asia": 11, "london": 20, "newyork": 29, "late_asia": 10}
    assert sum(got.values()) == d.max_per_day


def test_an_arm_outside_window_0207_keeps_its_asia_hours_in_the_shares(tmp_path):
    """A non-gold arm is not in the block's scope: its 02:00-07:00 candidates still count (12:4:6:2 of 70)."""
    eng, ex, day = _icm_demo_history(tmp_path, arm="LB-US500")
    lb = Arm(spec=_spec("LB-US500", {"instrument": "US500", "stop_atr": 2.0}), style="scalp")
    got = budget.session_budgets(eng, ex, DailyEntries(max_per_day=70), day, {"LB-US500": lb})
    assert got == {"asia": 34, "london": 12, "newyork": 18, "late_asia": 6}


def test_retired_arms_candidates_take_no_session_share(tmp_path):
    """A retired arm can no longer enter, so its trailing candidates (10 in Asia here) leave Asia its floor of one:
    the pass reads the shares from its active arms only."""
    db = exploration.ledger_db(str(tmp_path))
    mon = START.floor("D") - pd.Timedelta(days=1)
    for i, (arm, h) in enumerate([("R", 0.5)] * 10 + [("e", 9)] + [("e", 15)] * 3):
        ed.record_candidate(db, candidate_id=f"r{i}", account_id="icm-probe", broker="icm", arm_id=arm, family="f",
                            style="intraday", tf="15m", side=1, decision_utc=mon + pd.Timedelta(hours=h, minutes=i),
                            stop_dist=5.0, target_dist=10.0, max_hold_s=3600, taken=True)
    gone = _arm("R", max_open=10)
    gone = gone.model_copy(update={"spec": gone.spec.model_copy(update={"retired_day": "2026-11-06",
                                                                        "retired_rule": "t"})})
    eng, pb = _engine(tmp_path, [_arm("e", max_open=10), gone], DailyEntries(max_per_day=30))
    assert eng.exploration is not None
    ps = exploration._Pass(eng, eng.exploration, _at(eng, pb, START).ts_utc)
    assert ps.sessions == {"asia": 1, "london": 8, "newyork": 20, "late_asia": 1}


@pytest.mark.parametrize("why", ["entry_refused", "unqualified"])
def test_entry_refused_and_unqualified_arms_candidates_take_no_session_share(tmp_path, why):
    """Quant C3(a): an arm the pass will not let enter (spec `entry_refused`, or a qualification that fails) is no
    admissible candidate either."""
    db = exploration.ledger_db(str(tmp_path))
    mon = START.floor("D") - pd.Timedelta(days=1)
    for i, (arm, h) in enumerate([("R", 0.5)] * 10 + [("e", 9)] + [("e", 15)] * 3):
        ed.record_candidate(db, candidate_id=f"q{i}", account_id="icm-probe", broker="icm", arm_id=arm, family="f",
                            style="intraday", tf="15m", side=1, decision_utc=mon + pd.Timedelta(hours=h, minutes=i),
                            stop_dist=5.0, target_dist=10.0, max_hold_s=3600, taken=True)
    out = _arm("R", max_open=10)
    change = {"entry_refused": "refused in a test"} if why == "entry_refused" else {
        "qualification": {**QUALIFIED, "verdict": "fail"}}
    out = out.model_copy(update={"spec": out.spec.model_copy(update=change)})
    eng, pb = _engine(tmp_path, [_arm("e", max_open=10), out], DailyEntries(max_per_day=30))
    assert eng.exploration is not None
    ps = exploration._Pass(eng, eng.exploration, _at(eng, pb, START).ts_utc)
    assert ps.sessions == {"asia": 1, "london": 8, "newyork": 20, "late_asia": 1}


def test_a_session_refusal_stops_the_whole_pass_only_for_signals_of_the_current_segment():
    """Code review LOW: a 07:59 Asia signal refused for its session must not block the pass's 08:00 London signal."""
    now = pd.Timestamp("2025-03-04 08:00:05", tz="UTC")
    since = now.floor("D")
    asia, london = pd.Timestamp("2025-03-04 07:59", tz="UTC"), pd.Timestamp("2025-03-04 08:00", tz="UTC")
    assert not budget.pass_wide("session_entries", asia, now, since)
    assert budget.pass_wide("session_entries", london, now, since)
    assert budget.pass_wide("daily_entries", asia, now, since) and budget.pass_wide("hourly_entries", asia, now, since)
    assert not budget.pass_wide("thin_controls", london, now, since)


def test_a_signal_of_the_previous_utc_day_is_refused_as_daily_entries_and_never_stops_the_pass():
    """Trading-safety L4: today's counts start at midnight, so a signal closed before it is refused, alone."""
    d = DailyEntries(max_per_day=70)
    now = pd.Timestamp("2025-03-04 00:00:05", tz="UTC")
    late = pd.Timestamp("2025-03-03 23:59", tz="UTC")
    assert daily_refusal(d, "edge", {"day": 0}, segment=3, budgets=ALL, prior_day=True) == "daily_entries"
    assert not budget.pass_wide("daily_entries", late, now, now.floor("D"))


def test_1m_and_5m_entries_stop_at_the_scalp_day_cap_and_other_timeframes_go_on():
    """Quant C2, PD ruling 5(d): at most `scalp_day_cap` (default 30) fills a day per account from 1m/5m arms."""
    d = DailyEntries(max_per_day=100)
    assert d.scalp_day_cap == 30
    kw: dict[str, Any] = {"segment": 1, "budgets": ALL}
    assert daily_refusal(d, "edge", {"day": 40, "fast": 29}, fast=True, **kw) is None
    assert daily_refusal(d, "edge", {"day": 40, "fast": 30}, fast=True, **kw) == "scalp_day_cap"
    assert daily_refusal(d, "control", {"day": 40, "fast": 30}, fast=True, **kw) == "scalp_day_cap"
    assert daily_refusal(d, "edge", {"day": 40, "fast": 30}, fast=False, **kw) is None
    assert daily_refusal(d.model_copy(update={"scalp_day_cap": None}), "edge", {"day": 40, "fast": 99}, fast=True,
                         **kw) is None


def test_unpaired_per_instrument_controls_take_their_share_of_the_day_and_stop_at_their_cost_cells_30_fills():
    d = DailyEntries(max_per_day=100, thin_control_share=0.25)
    kw: dict[str, Any] = {"segment": 1, "budgets": ALL}
    assert daily_refusal(d, "thin", {"day": 10, "thin": 24}, **kw) is None
    assert daily_refusal(d, "thin", {"day": 10, "thin": 25}, **kw) == "thin_controls"
    assert daily_refusal(d, "thin", {"day": 10}, cell_fills=29, **kw) is None
    assert daily_refusal(d, "thin", {"day": 10}, cell_fills=30, **kw) == "thin_cell_complete"
    assert daily_refusal(d, "control", {"day": 10}, cell_fills=500, **kw) is None     # gold and paired: no such stop
    assert daily_refusal(d.model_copy(update={"thin_control_fills": None}), "thin", {"day": 10}, cell_fills=500,
                         **kw) is None


def test_the_cost_cell_of_an_instrument_counts_every_arms_fills_of_it_lb_and_lbc_too(tmp_path):
    db = exploration.ledger_db(str(tmp_path))
    with db.write() as conn:
        for i, arm in enumerate(["S0-US500", "I0-US500", "LB-US500", "LBC-US500", "LB-US500"]):
            LEDGER.upsert(conn, _rec(i, arm, START, symbol="US500"))
        LEDGER.upsert(conn, _rec(10, "LB-US500", START, symbol="US500", filled=False))     # never filled
        LEDGER.upsert(conn, _rec(11, "S0", START))
    ex = SimpleNamespace(db=db, budget_cache={})
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    assert budget.cell_fills(eng, ex, START) == {"US500": 5, "XAUUSD": 1}  # type: ignore[arg-type]


def test_todays_counts_are_by_signal_close_never_the_hosts_send_time(tmp_path):
    """Code review LOW-1: a replay's sends happen at the host's wall clock (2030 here); its day is its signals'."""
    db = exploration.ledger_db(str(tmp_path))
    thin = Arm(spec=_spec("S0-US500", {"instrument": "US500", "stop_atr": 2.0}), style="scalp", control_share=0.01)
    edge = Arm(spec=_spec("e", {"stop_usd": 3.0}), style="scalp")
    day = START.floor("D")
    with db.write() as conn:
        LEDGER.upsert(conn, _rec(1, "S0-US500", day - pd.Timedelta(hours=1)))        # yesterday by signal
        LEDGER.upsert(conn, _rec(2, "S0-US500", START, symbol="US500"))
        LEDGER.upsert(conn, _rec(3, "e", START))
        LEDGER.upsert(conn, _rec(4, "e", START, filled=False))
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    n = budget.entries_today(eng, SimpleNamespace(db=db), day, START + pd.Timedelta(minutes=5),  # type: ignore[arg-type]
                             {"S0-US500": thin, "e": edge})
    assert (n["day"], n["hour"], n["thin"], n["edge"], n.get("fast", 0)) == (2, 2, 1, 1, 2)


# ------------------------------------------------------------------------------------------------ in the engine
@pytest.mark.usefixtures("_sessions_open")
def test_the_engine_sends_no_more_exploration_fills_than_the_days_budget_and_records_the_rest_blocked(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=2))
    _passes(eng, pb, 4)
    assert len(_sent(eng)) == 2 and len(eng.open) == 2
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:daily_entries"}
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    assert sorted(obs["blocked_reason"].fillna("taken")) == ["daily_entries", "daily_entries", "taken", "taken"], obs


@pytest.mark.usefixtures("_sessions_open")
def test_the_engine_refuses_fast_entries_past_the_scalp_day_cap_and_records_them_blocked(tmp_path, monkeypatch):
    """The engine path: fills counted from the ledger and this pass, the refusal journalled and recorded blocked (the
    test's 15m arm stands in for a fast one; FAST_TFS itself is pinned by the rule's test above)."""
    monkeypatch.setattr(budget, "FAST_TFS", frozenset({"15m"}))
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=50, scalp_day_cap=2))
    _passes(eng, pb, 4)
    assert len(_sent(eng)) == 2
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:scalp_day_cap"}
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    assert set(obs["blocked_reason"].dropna()) == {"scalp_day_cap"}


@pytest.mark.usefixtures("_sessions_open")
def test_the_days_count_is_read_from_the_ledger_so_a_restart_never_resets_the_budget(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=2))
    _passes(eng, pb, 2)
    assert len(_sent(eng)) == 2
    eng2, pb2 = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=2))
    _passes(eng2, pb2, 1, START + pd.Timedelta(minutes=45))
    assert not _sent(eng2) and _actions(eng2, "exploration_refused:daily_entries")


@pytest.mark.usefixtures("_sessions_open")
def test_the_hourly_budget_spreads_the_day_and_the_next_hour_sends_again(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=50, max_per_hour=1))
    _passes(eng, pb, 3)                                      # 14:00, 14:15, 14:30: one hour
    assert len(_sent(eng)) == 1 and _actions(eng, "exploration_refused:hourly_entries")
    _passes(eng, pb, 1, START + pd.Timedelta(hours=1))
    assert len(_sent(eng)) == 2


def test_control_admitted_before_first_edge_fill(tmp_path, monkeypatch):
    """Quant Q1: a paired control is admitted by its pair's trailing matched-time rate, never "after an edge fill":
    its arm filled in this (session, hour) cell on earlier days as often as it signalled there, so p = 1 today before
    the arm's first fill; p is the candidate's recorded propensity."""
    monkeypatch.setitem(budget.EXTRA_PAIRS, "c", ("e",))
    before = [START - pd.Timedelta(days=d) for d in (1, 7)]                 # Monday, last Tuesday: same cell
    _history(tmp_path, fills={"e": before}, signals={"c": before})
    eng, pb = _engine(tmp_path, [_arm("c", max_open=10, control_share=0.5), _arm("e", max_open=10, side=-1)],
                      DailyEntries(max_per_day=50))
    eng.exploration.queue.clear()                                            # type: ignore[union-attr]
    exploration.on_pass(eng, _at(eng, pb, START))
    first = [d["arm"] for d in _sent(eng)]
    assert first[0] == "c" or "c" in first, eng.decisions
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    today = obs[(obs["arm_id"] == "c") & (obs["decision_utc"] == START)]
    assert list(today["mu"]) == [1.0] and bool(today["taken"].iloc[0])


def test_control_allowance_is_per_pair(tmp_path, monkeypatch):
    """Quant Q1: not pooled. c1's arm filled in this cell as often as c1 signalled (p = 1); c2's arm filled only in
    another hour (p = 0 here). Today's fills of e1 never admit c2."""
    monkeypatch.setitem(budget.EXTRA_PAIRS, "c1", ("e1",))
    monkeypatch.setitem(budget.EXTRA_PAIRS, "c2", ("e2",))
    cell = [START - pd.Timedelta(days=1)]
    elsewhere = [START - pd.Timedelta(days=1, hours=5)]
    _history(tmp_path, fills={"e1": cell, "e2": elsewhere}, signals={"c1": cell, "c2": cell})
    # e2 is configured and active: c2's p = 0 is "its arms never filled in this cell", not "no active arm" (quant C1)
    eng, pb = _engine(tmp_path, [_arm("c1", max_open=10, control_share=0.3), _arm("c2", max_open=10, control_share=0.3),
                                 _arm("e1", max_open=10), _arm("e2", max_open=10)], DailyEntries(max_per_day=50))
    exploration.on_pass(eng, _at(eng, pb, START))
    sent = {d["arm"] for d in _sent(eng)}
    assert {"c1", "e1"} <= sent and "c2" not in sent
    [thinned] = [d for d in _actions(eng, "exploration_refused:control_thinned")]
    assert thinned["arm"] == "c2" and thinned["admission_p"] == 0.0
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    c2 = obs[(obs["arm_id"] == "c2") & (obs["decision_utc"] == START)]
    assert list(c2["blocked_reason"]) == ["control_thinned"] and list(c2["mu"]) == [0.0]


def test_a_thinned_draw_is_the_seeded_hash_of_the_control_and_its_signal_close():
    """p in (0, 1): admitted exactly when hash_u(control, signal close) < p (restart-safe, identical in a replay)."""
    u = hash_u("c", START)
    assert 0.0 <= u < 1.0 and hash_u("c", START) == u


def test_an_unreadable_ledger_sends_no_entry_and_says_so(tmp_path, monkeypatch):
    """Trading-safety LOW-2: the budget's read raising refuses every entry of the pass (fail closed)."""
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=50))

    def boom(*a: Any, **k: Any) -> dict[str, int]:
        raise OSError("disk")
    monkeypatch.setattr(budget, "entries_today", boom)
    exploration.on_pass(eng, _at(eng, pb, START))
    assert not _sent(eng) and not eng.open and _actions(eng, "exploration_refused:ledger_unreadable")


def test_a_position_open_when_the_budget_is_spent_still_exits_at_its_own_hold(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("s", tf="1m", style="scalp", hold_bars=5, max_open=10)],
                      DailyEntries(max_per_day=1))
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, tick.model_copy(update={"ts_utc": minute + pd.Timedelta(seconds=2)}))
    [pid] = list(eng.open)
    opened = eng.open[pid].opened_utc
    assert opened is not None
    eng._retry_closes(opened + pd.Timedelta(seconds=300))
    assert pid not in eng.open and not pb.positions()


# ------------------------------------------------------------------------------------------------ the configuration
def test_the_demo_accounts_daily_budgets_and_the_probe_plan_total_at_most_250_fills_a_day():
    """Owner decision 2026-10-30 (demo 150-250 fills a day): every exploring account carries a budget, and the
    budgets plus the cost probes' daily plan stay inside 250."""
    raw = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    exploring = {k: v for k, v in raw.items() if v.get("enabled") and (v.get("exploration") or {}).get("enabled")}
    assert exploring and all((v["exploration"].get("daily") or {}).get("max_per_day") for v in exploring.values())
    budgets = sum(v["exploration"]["daily"]["max_per_day"] for v in exploring.values())
    probes = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["daily_review"]["probe_plan"]
    assert budgets + sum(probes.values()) <= 250
    for acc in acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False).values():
        if acc.enabled and acc.exploration.enabled:
            d = acc.exploration.daily
            assert d is not None and d.controls_per_edge <= 1.0 and acc.mode == "demo"
            cfg = exploration.config_for(acc)
            assert cfg is not None and cfg.daily == d


@pytest.mark.parametrize("bad", [{"max_per_day": 0}, {"max_per_day": 5, "max_per_hour": 0},
                                 {"max_per_day": 5, "controls_per_edge": 0}, {"max_per_day": 5, "thin_control_share": 2}])
def test_a_daily_budget_out_of_bounds_is_refused_at_load(bad):
    with pytest.raises(ValueError):
        DailyEntries.model_validate(bad)


def test_the_kill_list_and_a5_retire_their_rows_and_the_paired_controls_stay():
    """Performance-analyst kill list 2026-11-06 (A2: H-80 R2 failed; XBB15 < 2 fills a session day), the unpaired gold
    controls (PD+PO 2026-11-06) and kill rule A5 (quant-reviewer signed 2026-11-06) with its learning twins: retired
    rows (no new entry, every exit kept, K unchanged). The paired controls keep running."""
    from goldbot.research.arms import load_arms
    rows = {a.id: a for a in load_arms(ROOT / "config" / "exploration_arms.yaml")}
    gone = [f"{f}{tf}" for f in ("BXR", "BXT", "BXF") for tf in ("15", "30", "1H", "4H")] + \
        ["XBB15", "I0", "VI0", "C1H", "VC1H", "W0", "VW0", "ZCV"]
    a5 = ["S5A", "S5B", "Z4D", "Z4P", "Z6D", "Z6V", "Z7D", "Z7P", "Z7V",
          "LCS5A", "LCS5B", "LCZ4D", "LCZ4P", "LCZ6D", "LCZ6V", "LCZ7D", "LCZ7P", "LCZ7V"]
    assert all(str(rows[a].retired_day) == "2026-11-06" and rows[a].retired_rule for a in [*gone, *a5])
    assert all(str(rows[a].retired_rule).startswith("A5 history-bounded entries, signed 2026-11-06") for a in a5)
    # ZCV paired only Z6V/Z7V (A5 sign-off section 7); ZCD and F1C retire on 2026-11-13 (profitability-2026-11-12.md
    # (a): Z9D and Z10D keep their dedicated LCZ9D and LCZ10D)
    assert all(str(rows[a].retired_day) == "2026-11-13" for a in ("ZCD", "F1C"))
    # Z3D and LCZ3D stop on 2026-11-13 (prereg-amendment-2026-11-13-h90-cost-budget.md: cost > gross/3)
    assert all(str(rows[a].retired_day) == "2026-11-13" for a in ("Z3D", "LCZ3D"))
    keep = ["S0", "S0R", "Z9D", "Z10D", "LCZ9D", "LCZ10D", "GS1C",
            *(a for a in rows if a.startswith("LBC-") and not rows["LB-" + a[4:]].retired)]
    assert all(a in rows and not rows[a].retired for a in keep)


def test_aux_migrations_23_24_backfill_each_rows_signal_close_and_index_it(tmp_path):
    """Code review LOW-1 / trading-safety LOW-1: an older ledger row gets its signal close from its body, indexed."""
    import sqlite3

    from goldbot.db.migrations import AUX, migrate
    rec = _rec(1, "e", START)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        migrate(conn, "aux", migrations=AUX[:22])
        conn.execute("INSERT INTO exploration_trades (client_order_id, account_id, arm_id, position_id, ts_ns, closed, "
                     "exit_ns, body) VALUES (?, ?, ?, ?, ?, 0, NULL, ?)",
                     (rec.client_order_id, rec.account_id, rec.arm_id, 1, 0, rec.model_dump_json()))
        conn.commit()
        assert migrate(conn, "aux", migrations=AUX[:24]) == [23, 24]
        assert conn.execute("SELECT signal_ns FROM exploration_trades").fetchone()[0] == START.value
        assert "exploration_trades_signal" in {r[1] for r in conn.execute("PRAGMA index_list(exploration_trades)")}


# ------------------------------------------------------------------------------------------------ round 3 (quant d, C2)
def _ctx(tmp_path: Path) -> tuple[Any, Any]:
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    return eng, SimpleNamespace(db=exploration.ledger_db(str(tmp_path)), budget_cache={})


def test_s0_is_thinned_at_its_gold_scalp_arms_matched_rate(tmp_path):
    """Quant (d): S0 and S0R are paired with the account's active gold scalp edge arms that name no other non-learning
    control (icm-probe: Z1P, Z2P, Z5P, ...). Z1P filled once where S0 signalled twice in the cell: p = 0.5, recorded as
    today's S0 candidate's propensity, admitted or thinned."""
    arms = {"S0": _arm("S0", style="scalp", control_share=0.25), "S0R": _arm("S0R", style="scalp", control_share=0.05),
            "Z1P": _arm("Z1P", style="scalp"), "I9": _arm("I9", style="intraday"), "F1A": _arm("F1A", style="scalp")}
    got = budget.pairs(arms)
    assert got["S0"] == got["S0R"] == ("Z1P",)                    # F1A pairs F1C; an intraday arm is not S0's
    before = [START - pd.Timedelta(days=1), START - pd.Timedelta(days=7)]
    _history(tmp_path, fills={"Z1P": before[:1]}, signals={"S0": before})
    eng, pb = _engine(tmp_path, [arms["S0"], arms["Z1P"]], DailyEntries(max_per_day=50))
    exploration.on_pass(eng, _at(eng, pb, START))
    obs = ed.read_obs(exploration.ledger_db(str(tmp_path)))
    today = obs[(obs["arm_id"] == "S0") & (obs["decision_utc"] == START)]
    assert list(today["mu"]) == [0.5]
    assert bool(today["taken"].iloc[0]) == (hash_u("S0", START) < 0.5)


def test_a_pairs_p_for_day_d_is_unchanged_when_fills_and_signals_on_or_after_d_are_added(tmp_path):
    day = START.floor("D")
    _history(tmp_path, fills={"e": [START - pd.Timedelta(days=1)]},
             signals={"c": [START - pd.Timedelta(days=1), START - pd.Timedelta(days=7)]})
    eng, ex = _ctx(tmp_path)
    p = budget.control_propensity(eng, ex, "c", ("e",), START, day, 1.0)
    assert p == 0.5
    with ex.db.write() as conn:                        # today's and later rows: outside day D's trailing window
        for i, t in enumerate([START, START + pd.Timedelta(days=1)]):
            LEDGER.upsert(conn, _rec(5000 + i, "e", t))
    _history(tmp_path, fills={}, signals={"c": [START + pd.Timedelta(minutes=1)]})
    ex.budget_cache = {}
    assert budget.control_propensity(eng, ex, "c", ("e",), START, day, 1.0) == p


def test_a_pair_whose_active_arms_never_filled_in_the_window_is_cold_p_1(tmp_path):
    _history(tmp_path, fills={"e": [START - pd.Timedelta(days=30)]},          # before the 10 trading days
             signals={"c": [START - pd.Timedelta(days=1)]})
    eng, ex = _ctx(tmp_path)
    assert budget.control_propensity(eng, ex, "c", ("e",), START, START.floor("D"), 1.0) == 1.0


def test_a_pair_with_every_arm_retired_is_p_0_and_thinned(tmp_path, monkeypatch):
    monkeypatch.setitem(budget.EXTRA_PAIRS, "c", ("e",))
    gone = _arm("e", max_open=10)
    gone = gone.model_copy(update={"spec": gone.spec.model_copy(update={"retired_day": "2026-11-06",
                                                                        "retired_rule": "t"})})
    eng, pb = _engine(tmp_path, [_arm("c", max_open=10, control_share=0.5), gone], DailyEntries(max_per_day=50))
    exploration.on_pass(eng, _at(eng, pb, START))
    assert not _sent(eng)
    [thinned] = _actions(eng, "exploration_refused:control_thinned")
    assert thinned["arm"] == "c" and thinned["admission_p"] == 0.0


def test_a_fractional_p_admits_exactly_when_hash_u_is_below_it(tmp_path, monkeypatch):
    """p = 0.5 in the 14:00 cell (one fill of e for two signals of c): each of today's four quarter-hour signals of c
    is admitted iff hash_u(c, its close) < 0.5."""
    monkeypatch.setitem(budget.EXTRA_PAIRS, "c", ("e",))
    before = [START - pd.Timedelta(days=1), START - pd.Timedelta(days=7)]
    _history(tmp_path, fills={"e": before[:1]}, signals={"c": before})
    eng, pb = _engine(tmp_path, [_arm("c", max_open=10, control_share=0.5), _arm("e", max_open=10, side=-1)],
                      DailyEntries(max_per_day=50))
    closes = [START + pd.Timedelta(minutes=15 * k) for k in range(4)]
    for t in closes:
        exploration.on_pass(eng, _at(eng, pb, t))
    admitted = {pd.Timestamp(d["ts"]).floor("min") for d in _sent(eng) if d["arm"] == "c"}
    assert admitted == {t for t in closes if hash_u("c", t) < 0.5}
    assert any(hash_u("c", t) >= 0.5 for t in closes) or admitted == set(closes)


def test_a_filled_row_written_without_signal_ns_during_a_rollback_still_counts(tmp_path):
    """Trading-safety LOW (round 3): an older release leaves signal_ns NULL; its send time counts instead."""
    db = exploration.ledger_db(str(tmp_path))
    rec = _rec(1, "e", START)
    with db.write() as conn:
        LEDGER.upsert(conn, rec)
        conn.execute("UPDATE exploration_trades SET signal_ns = NULL, ts_ns = ?", (START.value,))
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    edge = Arm(spec=_spec("e", {"stop_usd": 3.0}), style="scalp")
    n = budget.entries_today(eng, SimpleNamespace(db=db), START.floor("D"), START, {"e": edge})  # type: ignore[arg-type]
    assert n["day"] == 1 and n["edge"] == 1
    eng2, ex = _ctx(tmp_path)
    _history(tmp_path, fills={}, signals={"c": [START, START + pd.Timedelta(minutes=15)]})
    day_after = START.floor("D") + pd.Timedelta(days=1)                    # the NULL row's fill is in the window: 1 / 2
    assert budget.control_propensity(eng2, ex, "c", ("e",), START + pd.Timedelta(days=1), day_after, 1.0) == 0.5
