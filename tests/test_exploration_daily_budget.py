"""The daily exploration entry budget (owner decision 2026-10-30: 150-250 demo fills a day in total; accounts.yaml
`exploration.daily`, `ops.accounts.DailyEntries`, goldbot/engine/exploration_budget.py): fills a day, an hour and a
session per account counted by signal close; paired controls admitted per pair by matched-time thinning (quant ruling
2026-11-06 Q1); unpaired per-instrument controls capped and stopped at their cost cell's 30 fills. Entries only: exits
are never counted or gated."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import Engine, exploration
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
ALL = {"asia": 100, "london": 100, "newyork": 100}         # session budgets that never bind


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
    kw: dict[str, Any] = {"session": "london", "budgets": ALL}
    assert daily_refusal(None, "edge", {"day": 10**6}, **kw) is None                 # no budget configured: no bound
    assert daily_refusal(d, "edge", {"day": 2, "hour": 1}, **kw) is None
    assert daily_refusal(d, "edge", {"day": 3, "hour": 0}, **kw) == "daily_entries"
    assert daily_refusal(d, "edge", {"day": 2, "hour": 2}, **kw) == "hourly_entries"


def test_a_saturated_asia_does_not_stop_the_first_new_york_fill():
    d = DailyEntries(max_per_day=30)
    budgets = {"asia": 10, "london": 10, "newyork": 10}
    n = {"day": 10, "session:asia": 10}
    assert daily_refusal(d, "edge", n, session="asia", budgets=budgets) == "session_entries"
    assert daily_refusal(d, "edge", n, session="newyork", budgets=budgets) is None


def test_session_budgets_follow_the_trailing_edge_signal_shares_and_split_equally_with_none(tmp_path):
    d = DailyEntries(max_per_day=90)
    db = exploration.ledger_db(str(tmp_path))
    ex = SimpleNamespace(db=db, budget_cache={})
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    day = START.floor("D")
    assert budget.session_budgets(eng, ex, d, day) == {"asia": 30, "london": 30, "newyork": 30}  # type: ignore[arg-type]
    t_a, t_b = day - pd.Timedelta(hours=20), day - pd.Timedelta(hours=5)       # Monday 04:00 and 19:00 UTC
    assert budget.session_of(t_a) != budget.session_of(t_b)
    for i, at in enumerate([t_a] * 6 + [t_b] * 3):
        ed.record_candidate(db, candidate_id=f"c{i}-{at.value}", account_id="icm-probe", broker="icm", arm_id="e",
                            family="f", style="scalp", tf="1m", side=1, decision_utc=at + pd.Timedelta(minutes=i),
                            stop_dist=3.0, target_dist=3.0, max_hold_s=60, taken=True)
    ex.budget_cache = {}
    got = budget.session_budgets(eng, ex, d, day)  # type: ignore[arg-type]
    rest = ({"asia", "london", "newyork"} - {budget.session_of(t_a), budget.session_of(t_b)}).pop()
    assert (got[budget.session_of(t_a)], got[budget.session_of(t_b)], got[rest]) == (60, 30, 1)


def test_unpaired_per_instrument_controls_take_their_share_of_the_day_and_stop_at_their_cost_cells_30_fills():
    d = DailyEntries(max_per_day=100, thin_control_share=0.25)
    kw: dict[str, Any] = {"session": "london", "budgets": ALL}
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
    assert (n["day"], n["hour"], n["thin"], n["edge"], n["session:" + budget.session_of(START)]) == (2, 2, 1, 1, 2)


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
    # ZCD still pairs Z3D, Z9D and Z10D on icm-demo; ZCV paired only Z6V/Z7V (A5 sign-off section 7)
    keep = ["S0", "S0R", "ZCD", "Z3D", "Z9D", "Z10D", "LCZ3D", "F1C", "GS1C",
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
        assert migrate(conn, "aux") == [23, 24]
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
