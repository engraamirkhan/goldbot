"""The execution/cost calibration controls (docs/research/prereg-amendment-2026-11-10-execution-controls.md, an
amendment to prereg-amendment-2026-11-06-control-thinning.md): VEX15/VEX1H on vantage-demo, IEX15/IEX1H on icm-demo;
VCAL5 was considered and dropped (quant ruling 2026-11-09: no row anywhere, no K). "Purpose:
execution and cost measurement; never read for edge, never paired". Each is a gold `random_bracket` control with no
pairing, so the daily budget admits it with propensity 1.0 (bounded only by the day, hour and session budgets); it is
outside N-1a's scope (`learning_icm_only` refuses gold learning arms and the prereg's fixed LC*/ZCD set only), so it
runs on vantage; and its frozen row's hash is `research.arms.arm_hash`'s. Its stops: no R stop (a control is the
null), the Z 4 cost-stop shape."""
from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from goldbot.engine import entry_refusals, exploration, refusals_novel
from goldbot.engine import exploration_budget as budget
from goldbot.engine.exploration import SPEC_PATH, Arm
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_stops
from goldbot.research.arms import arm_hash, load_arms
from tests.test_arm_stops import _db, _fills
from tests.test_refusals_novel import TUE, _eng, _sig

VANTAGE = ("VEX15", "VEX1H")
ICM = ("IEX15", "IEX1H")
DROPPED = "VCAL5"                     # quant ruling 2026-11-09: dropped, K +0 (amendment 2.1)
ALL = (*VANTAGE, *ICM)
# the amendment's geometry, per id: (timeframe, stop, target_r, hold_bars, max_open)
GEOMETRY = {
    "VEX15": ("15m", {"stop_atr": 1.5, "min_stop_usd": 3.0, "max_stop_usd": 22.6}, 2.0, 16, 2),
    "IEX15": ("15m", {"stop_atr": 1.5, "min_stop_usd": 3.0, "max_stop_usd": 22.6}, 2.0, 16, 2),
    "VEX1H": ("1h", {"stop_atr": 1.5, "min_stop_usd": 3.0, "max_stop_usd": 39.8}, 2.0, 8, 1),
    "IEX1H": ("1h", {"stop_atr": 1.5, "min_stop_usd": 3.0, "max_stop_usd": 39.8}, 2.0, 8, 1),
}
# the planned hashes (research plan 2026-11-09 section 4), frozen in the rows
HASHES = {"VEX15": "c05155207165e963", "VEX1H": "c0514a7202094915", "IEX15": "0d096cf24fbe20dd",
          "IEX1H": "05c6633e6297a37d"}
# retired 2026-11-13, its cost cell complete at 38 fills (profitability-2026-11-12.md (a)); IEX1H 2026-11-14 under the
# research-director ruling 2026-11-13 (gold-only demo, IEX* named; tests/test_gold_only_demo_1113.py); VEX15/VEX1H run on
RETIRED = {"IEX15": "2026-11-13", "IEX1H": "2026-11-14"}


@pytest.fixture(scope="module")
def accs() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def _running(acc: acc_mod.Account) -> dict[str, Arm]:
    cfg = exploration.config_for(acc)
    assert cfg is not None
    return {a.arm_id: a for a in cfg.arms}


# ------------------------------------------------------------------------------------------------ the frozen rows
def test_each_execution_control_is_a_frozen_gold_random_bracket_control_whose_hash_is_arm_hash():
    rows = {a.id: a for a in load_arms(SPEC_PATH)}            # check_hash: an edited row would raise here
    for arm_id in ALL:
        row = rows[arm_id]
        assert (row.rule, row.role, row.status, row.forward_only) == ("random_bracket", "control", "run", True)
        assert row.config_hash == arm_hash(row) == HASHES[arm_id]
        assert row.retired_day == RETIRED.get(arm_id) and row.learning is None and "control" not in row.params


@pytest.mark.parametrize("arm_id", ALL)
def test_each_execution_control_has_the_amendments_geometry(arm_id):
    row = {a.id: a for a in load_arms(SPEC_PATH, check_hash=False)}[arm_id]
    tf, stop, target_r, hold, max_open = GEOMETRY[arm_id]
    assert (row.timeframe, row.params["target_r"], row.params["hold_bars"], row.max_open) == (tf, target_r, hold,
                                                                                               max_open)
    assert {k: row.params[k] for k in stop} == stop


def test_the_dropped_vcal5_has_no_spec_row_and_no_stop_rule():
    """Quant ruling 2026-11-09: vantage-demo fills stops and targets at the level (its exit leg measures nothing) and
    its entry leg duplicates VEX15/VEX1H, so VCAL5 is not registered anywhere and adds no K."""
    assert DROPPED not in {a.id for a in load_arms(SPEC_PATH, check_hash=False)}
    rules = arm_stops.load_stop_rules()
    assert DROPPED not in rules.none and DROPPED not in rules.cost_stop.arms


def test_the_execution_control_ids_are_new_and_their_order_keys_clash_with_no_other_arm():
    ids = [a.id for a in load_arms(SPEC_PATH, check_hash=False)]
    assert len(ids) == len(set(ids))
    keys = {i: hashlib.sha1(i.encode()).hexdigest()[:5] for i in ids}
    assert len(set(keys.values())) == len(keys)


# ------------------------------------------------------------------------------------------------ hosts
def test_vex_runs_on_vantage_demo_and_iex_on_icm_demo_only(accs):
    on = {aid: set(acc.exploration.arms) for aid, acc in accs.items()}
    assert set(VANTAGE) <= on["vantage-demo"] and set(ICM) <= on["icm-demo"]
    assert all(DROPPED not in arms for arms in on.values())
    for aid, arms in on.items():
        assert not (set(VANTAGE) & arms) or aid == "vantage-demo"
        assert not (set(ICM) & arms) or aid == "icm-demo"


@pytest.mark.parametrize(("host", "ids"), [("vantage-demo", VANTAGE), ("icm-demo", ICM)])
def test_the_engine_runs_each_execution_control_and_refuses_only_the_retired_iex_rows(accs, host, ids):
    cfg = exploration.config_for(accs[host])
    assert cfg is not None
    running = {a.arm_id for a in cfg.arms}
    assert set(ids) <= running and not set(ids) & set(cfg.dropped)
    assert not set(ids) & set(cfg.hash_refused) and exploration.spec_retired(cfg) & set(ids) == set(ids) & set(RETIRED)
    assert not set(ids) & set(exploration.entry_refused(cfg))    # a control needs no qualification


# ------------------------------------------------------------------------------------------------ the daily budget
@pytest.mark.parametrize(("host", "ids"), [("vantage-demo", VANTAGE), ("icm-demo", ICM)])
def test_an_execution_control_is_an_unpaired_gold_control_admitted_with_propensity_one(accs, host, ids):
    arms = _running(accs[host])
    paired = budget.pairs(arms)
    for arm_id in ids:
        arm = arms[arm_id]
        assert budget.entry_kind(arm) == "control"                      # gold: not a thin per-instrument control
        assert arm_id not in paired, f"{arm_id} is read against an arm"
        assert all(arm_id not in v for v in paired.values()), f"{arm_id} is an arm some control is read against"
        # the engine's admission (`exploration._send`): an unpaired control's p is control_propensity with no arms
        arms_of: tuple[str, ...] = ()
        assert budget.control_propensity(None, None, arm_id, arms_of, TUE, TUE, 1.0) == 1.0  # type: ignore[arg-type]


def test_an_execution_control_never_spends_the_thin_control_share(accs):
    daily = accs["vantage-demo"].exploration.daily
    assert daily is not None
    full_thin = {"day": 0, "thin": int(daily.thin_control_share * daily.max_per_day)}
    assert budget.daily_refusal(daily, "control", full_thin, segment=1, budgets={}) is None
    assert budget.daily_refusal(daily, "thin", full_thin, segment=1, budgets={}) == "thin_controls"


# ------------------------------------------------------------------------------------------------ N-1a routing
@pytest.mark.parametrize("arm_id", VANTAGE)
def test_learning_icm_only_does_not_refuse_an_execution_control_on_vantage(accs, tmp_path, arm_id):
    spec = {a.id: a for a in load_arms(SPEC_PATH, check_hash=False)}[arm_id]
    arm = Arm(spec=spec, style="intraday", control_share=0.04)
    eng = _eng(tmp_path, [arm], broker="vantage")
    s = _sig(arm, TUE + pd.Timedelta(hours=10))                         # 10:00 UTC: inside every window
    assert not refusals_novel.in_scope(eng, arm)
    assert not refusals_novel.learning_icm_only(eng, arm, s)
    assert entry_refusals.first_refusal(eng, arm, s) is None


# ------------------------------------------------------------------------------------------------ stops
def test_an_execution_control_has_no_r_stop_and_carries_the_z4_cost_stop():
    rules = arm_stops.load_stop_rules()
    for arm_id in ALL:
        assert arm_id in rules.none and arm_id in rules.cost_stop.arms
        assert rules.unit_of(arm_id) is None and arm_id not in rules.follows and arm_id not in rules.h61


@pytest.mark.parametrize(("cost", "want"), [(0.05, "ok"), (0.15, "halved"), (0.25, "suspended")])
def test_an_execution_controls_cost_state_reads_the_z4_bands_from_30_fills(tmp_path, cost, want):
    _fills(_db(tmp_path), "VEX15", 30, host="vantage-demo", pnl=0.5, cost=cost, days=5, control=True)
    with _db(tmp_path).connection() as conn:
        assert arm_stops.cost_states(conn, ["VEX15"]) == {"VEX15": want}


# ------------------------------------------------------------------------------------------------ quant conditions
END = pd.Timestamp("2026-12-09", tz="UTC")       # amendment 4 rule 3: the sample stop's end date


def test_no_catalogued_arm_is_measured_against_an_execution_control():
    """Quant C3 (2026-11-10): never paired. A future row naming one as its control would leak edge reads into it."""
    from goldbot.api.strategy_labels import load_catalog
    leaks = {k: a.control_arm for k, a in load_catalog().arms.items() if a.control_arm in ALL}
    assert leaks == {}


def test_the_only_controls_carrying_the_cost_stop_are_the_four_execution_controls():
    """Quant C3: the review retires a cost-suspended control only through `cost_stop.arms`; no other control is in it."""
    controls = {a.id for a in load_arms(SPEC_PATH, check_hash=False) if a.role == "control"}
    assert set(arm_stops.load_stop_rules().cost_stop.arms) & controls == set(ALL)


def _unretired_after_end(today: pd.Timestamp) -> set[str]:
    rows = {a.id: a for a in load_arms(SPEC_PATH, check_hash=False)}
    return {i for i in ALL if not rows[i].retired_day} if today.normalize() > END else set()


def test_the_execution_controls_are_retired_by_their_2026_12_09_end():
    """Quant C3 dated tripwire: from 2026-12-10 CI fails while any of the four rows has no retired_day."""
    assert _unretired_after_end(pd.Timestamp.now(tz="UTC")) == set(), "retire the execution controls (amendment 4)"


def test_the_end_date_tripwire_fires_on_2026_12_10_and_not_on_the_end_day():
    assert _unretired_after_end(END) == set()
    assert _unretired_after_end(END + pd.Timedelta(days=1)) == set(ALL) - set(RETIRED)   # the rows as committed
