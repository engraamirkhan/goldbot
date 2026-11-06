"""The daily exploration entry budget (owner decision 2026-10-30: 150-250 demo fills a day in total; accounts.yaml
`exploration.daily`, `ops.accounts.DailyEntries`): fills a day and an hour per account, at most one random-control
fill per strategy or learning fill, the unpaired per-instrument controls thinned first. Entries only: exits are never
counted or gated."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import Engine, exploration
from goldbot.engine.exploration import Arm, Pot, daily_refusal, entry_kind
from goldbot.ops import accounts as acc_mod
from goldbot.ops.accounts import DailyEntries
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


def _sent_at_signal(tmp_path: Path) -> None:
    """The ledger's sent time (`ts_ns`, the host clock at send: wall time in a test) set to each order's signal close,
    as it is live, where the host clock and the bars agree."""
    with exploration.ledger_db(str(tmp_path)).write() as conn:
        for rec in exploration.LEDGER.select(conn):
            conn.execute("UPDATE exploration_trades SET ts_ns = ? WHERE client_order_id = ?",
                         (rec.signal_utc.value, rec.client_order_id))


def _spec(arm_id: str, params: dict[str, Any]) -> SpecArm:
    return SpecArm(id=arm_id, rule=LONG, timeframe="1m", max_open=1, params=params, status="run",
                   qualification=QUALIFIED)


# ------------------------------------------------------------------------------------------------ the rule alone
def test_an_unpaired_per_instrument_control_is_thin_and_gold_or_learning_paired_controls_pair_first():
    edge = Arm(spec=_spec("e", {"stop_usd": 3.0}), style="scalp")
    gold = Arm(spec=_spec("S0", {"stop_usd": 3.0}), style="scalp", control_share=0.25)
    twin = Arm(spec=_spec("LCS5A", {"stop_usd": 3.0, "control": "learning:S5A"}), style="scalp", control_share=0.05)
    lbc = Arm(spec=_spec("LBC-US500", {"instrument": "US500", "stop_atr": 2.0, "control": "learning:LB-US500"}),
              style="scalp", control_share=0.02)
    thin = Arm(spec=_spec("S0-US500", {"instrument": "US500", "stop_atr": 2.0}), style="scalp", control_share=0.01)
    assert [entry_kind(a) for a in (edge, gold, twin, lbc, thin)] == ["edge", "control", "control", "control", "thin"]


def test_the_day_and_the_hour_budgets_refuse_every_kind_once_spent():
    d = DailyEntries(max_per_day=3, max_per_hour=2)
    assert daily_refusal(None, "edge", {"day": 10**6}) is None                       # no budget configured: no bound
    assert daily_refusal(d, "edge", {"day": 2, "hour": 1}) is None
    assert daily_refusal(d, "edge", {"day": 3, "hour": 0}) == "daily_entries"
    assert daily_refusal(d, "edge", {"day": 2, "hour": 2}) == "hourly_entries"


def test_a_control_fills_only_while_it_stays_at_most_one_per_edge_fill_and_thin_controls_take_their_share_only():
    d = DailyEntries(max_per_day=100, controls_per_edge=1.0, thin_control_share=0.25)
    assert daily_refusal(d, "control", {"day": 0}) == "control_ratio"               # no edge fill yet: no control
    assert daily_refusal(d, "control", {"day": 1, "edge": 1}) is None
    assert daily_refusal(d, "control", {"day": 2, "edge": 1, "control": 1}) == "control_ratio"
    assert daily_refusal(d, "thin", {"day": 4, "edge": 4}) is None                   # 1 <= 0.25 x 4
    assert daily_refusal(d, "thin", {"day": 5, "edge": 4, "thin": 1}) == "thin_controls"
    assert daily_refusal(d, "control", {"day": 5, "edge": 4, "thin": 1}) is None     # the paired controls still pair


# ------------------------------------------------------------------------------------------------ in the engine
def test_the_engine_sends_no_more_exploration_fills_than_the_days_budget_and_records_the_rest_blocked(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=2))
    _passes(eng, pb, 4)
    assert len(_sent(eng)) == 2 and len(eng.open) == 2
    assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:daily_entries"}
    from goldbot.research.exploration_data import read_obs
    obs = read_obs(exploration.ledger_db(str(tmp_path)))
    assert sorted(obs["blocked_reason"].fillna("taken")) == ["daily_entries", "daily_entries", "taken", "taken"], obs


def test_the_days_count_is_read_from_the_ledger_so_a_restart_never_resets_the_budget(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=2))
    _passes(eng, pb, 2)
    assert len(_sent(eng)) == 2
    eng2, pb2 = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=2))
    _passes(eng2, pb2, 1, START + pd.Timedelta(minutes=45))
    assert not _sent(eng2) and _actions(eng2, "exploration_refused:daily_entries")


def test_the_hourly_budget_spreads_the_day_and_the_next_hour_sends_again(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=10)], DailyEntries(max_per_day=50, max_per_hour=1))
    _passes(eng, pb, 3)                                      # 14:00, 14:15, 14:30: one hour
    assert len(_sent(eng)) == 1 and _actions(eng, "exploration_refused:hourly_entries")
    _sent_at_signal(tmp_path)
    _passes(eng, pb, 1, START + pd.Timedelta(hours=1))
    assert len(_sent(eng)) == 2


def test_a_control_waits_for_its_edge_fill_and_then_pairs_one_for_one(tmp_path):
    ctrl = _arm("c", max_open=10, control_share=0.5)
    eng, pb = _engine(tmp_path, [ctrl], DailyEntries(max_per_day=50))
    _passes(eng, pb, 2)
    assert not _sent(eng) and {d["action"] for d in _actions(eng, "exploration_refused:")} == \
        {"exploration_refused:control_ratio"}
    eng2, pb2 = _engine(tmp_path / "paired", [_arm("e", max_open=10), ctrl], DailyEntries(max_per_day=50))
    _passes(eng2, pb2, 3)
    arms = [d["arm"] for d in _sent(eng2)]
    assert arms.count("e") == 3 and 1 <= arms.count("c") <= 3


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


# ------------------------------------------------------------------------------------------------ PD+PO 2026-11-06
def test_an_unpaired_per_instrument_control_stops_for_good_once_its_cost_cell_has_30_fills():
    d = DailyEntries(max_per_day=100)
    busy = {"day": 10, "edge": 10}
    assert daily_refusal(d, "thin", busy, arm_fills=29) is None
    assert daily_refusal(d, "thin", busy, arm_fills=30) == "thin_cell_complete"
    assert daily_refusal(d, "control", busy, arm_fills=500) is None              # gold and paired controls: no stop
    assert daily_refusal(d.model_copy(update={"thin_control_fills": None}), "thin", busy, arm_fills=500) is None


def test_the_ledger_counts_each_unpaired_controls_fills_ever_and_todays_by_kind(tmp_path):
    from types import SimpleNamespace

    from goldbot.engine.exploration import ARM_FILLS, LEDGER, ExplorationRecord, entries_today
    db = exploration.ledger_db(str(tmp_path))
    thin = Arm(spec=_spec("S0-US500", {"instrument": "US500", "stop_atr": 2.0}), style="scalp", control_share=0.01)
    edge = Arm(spec=_spec("e", {"stop_usd": 3.0}), style="scalp")
    day = START.floor("D")

    def rec(i: int, arm: str, at: pd.Timestamp, filled: bool = True) -> ExplorationRecord:
        return ExplorationRecord(client_order_id=f"o{i}", account_id="icm-probe", arm_id=arm, family="f", tf="1m",
                                 side=1, lots=0.01, signal_utc=at, sent_utc=at, atr_usd=1.0, stop_usd=1.0,
                                 target_usd=1.0, hold_s=60, position_id=i if filled else None)

    with db.write() as conn:
        for i in range(31):                                  # 31 fills of the control, 30 on earlier days
            LEDGER.upsert(conn, rec(i, "S0-US500", day - pd.Timedelta(days=1) if i < 30 else START))
        LEDGER.upsert(conn, rec(100, "e", START))
        LEDGER.upsert(conn, rec(101, "e", START, filled=False))                 # not filled: never counted
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    n = entries_today(eng, SimpleNamespace(db=db), day, START + pd.Timedelta(minutes=5),  # type: ignore[arg-type]
                      {"S0-US500": thin, "e": edge})
    assert (n["day"], n["hour"], n["thin"], n["edge"], n[ARM_FILLS + "S0-US500"]) == (2, 2, 1, 1, 31)
    assert ARM_FILLS + "e" not in n


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
    keep = ["S0", "S0R", "ZCD", "Z3D", "Z9D", "Z10D", "LCZ3D", "F1C", "GS1C",             *(a for a in rows if a.startswith("LBC-") and not rows["LB-" + a[4:]].retired)]
    assert all(a in rows and not rows[a].retired for a in keep)
