"""Stop the bleeding (docs/research/prereg-amendment-2026-11-20-stop-the-bleeding.md; owner goal 2026-11-20, system
audit 2026-11-20):

* "Every random control is replay_only": no control on any trading account sends a broker order; each keeps its
  comparison role as a replay twin (candidates recorded and labelled by the r2 replay, never retired). One dated
  exception: GS1C, paired 1:1 with GS1, until GS1's owner-experiment end, which the engine enforces itself.
* "Controls <= 1:1 to signal arms, per account" (A4): broker-traded controls never outnumber the account's signal arms.
* "nr4_break 5m stays retired" (Z6D/Z6V, -0.333 R n 56 on both brokers).
* "No gold 5m entry decided in Asia, [00:00, 07:00) UTC" (`entry_refusals.ASIA_5M`; -0.167 R n 512, t -6.2, gross).
Entries only: exits, stops, targets, holds and the RiskGate are untouched.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from goldbot.engine import entry_refusals, exploration
from goldbot.engine.entry_refusals import ASIA_5M, WINDOW_0207
from goldbot.engine.exploration import Arm
from goldbot.ops import accounts as acc_mod
from goldbot.research.arms import dedicated_control_of, owner_experiment_until
from tests.test_entry_refusals import TUE, _at_utc, _cfg_engine, _obs, _sig, _spec_arm, _spy_gate
from tests.test_exploration_engine import _actions, _arm, _at, _engine, _sent
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)

# the controls that were broker-traded until this amendment (VEX15/VEX1H/IEX1H: -GBP 471 on 99 fills 2026-11-13..20);
# S0R and the red-box RBC* too, so the mode alone, not a rule or a read count, keeps every control off the broker
TURNED = ("VEX15", "VEX1H", "IEX1H", "S0R", "RBC15", "RBC1H", "RBC4H")
# amendment section 1: a control paired 1:1 with a signal arm whose read ends on a fixed day stays broker-traded until
# that day (changing its fills to shadow fills in the read's last week would change its matched-control method)
PAIRED = {"GS1C": ("icm-demo", "GS1")}
# the ADR 0062 weekend lane (vantage-247): its controls measure a broker-made weekend quote, which a replay cannot;
# the account is disabled. Enabling it while these are broker-traded fails this test, so it needs a dated decision
WEEKEND_LANE = ("vantage-247", frozenset({"WKS0", "WKI0"}))
RELEASE_DAY = "2026-11-20"


@pytest.fixture(scope="module")
def accs() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def _arms(acc: acc_mod.Account) -> list[Arm]:
    cfg = exploration.config_for(acc)
    return [] if cfg is None else list(cfg.arms)


def _may_enter(a: Arm) -> bool:
    """Configured to take new entries: status run and not retired (a refusal stamped at load can lift, so it counts)."""
    return a.spec.status == "run" and not a.spec.retired


def _broker_controls(acc: acc_mod.Account) -> set[str]:
    return {a.arm_id for a in _arms(acc) if a.spec.role == "control" and _may_enter(a)
            and a.spec.control_mode != "replay_only"}


def _paired_until(name: str, acc: acc_mod.Account, arm_id: str, today: str) -> bool:
    """`arm_id` is PAIRED on `name` with its signal arm configured there, and `today` is on or before that arm's end."""
    if arm_id not in PAIRED or PAIRED[arm_id][0] != name:
        return False
    signal = PAIRED[arm_id][1]
    until = owner_experiment_until(signal)
    return until is not None and today <= until and any(a.arm_id == signal and _may_enter(a) for a in _arms(acc))


def _unexcused(accs: dict[str, acc_mod.Account], today: str) -> list[tuple[str, str]]:
    return sorted((name, x) for name, acc in accs.items() if acc.enabled for x in _broker_controls(acc)
                  if not _paired_until(name, acc, x, today))


# ------------------------------------------------------------------------------------------------ controls
def test_no_control_on_any_enabled_account_can_send_a_broker_order_except_a_paired_one_until_its_arms_end(accs):
    """Checked on the release day (an injected date, never the wall clock: trading-safety review of e0bd500b)."""
    assert _unexcused(accs, RELEASE_DAY) == []


@pytest.mark.parametrize("control", sorted(PAIRED))
def test_the_engine_itself_refuses_a_paired_broker_control_from_the_minute_after_its_arms_end(accs, control):
    """The end binds in the running engine, not in CI: `exploration.refresh_owner_experiments` (once a minute, no
    restart) refuses the owner experiment past OWNER_EXPERIMENTS' day and every control whose params.control names it,
    whatever its control_mode. Real spec rows; open positions keep their exits (the arm stays configured)."""
    from types import SimpleNamespace
    account, signal = PAIRED[control]
    rows = {a.arm_id: a for a in _arms(accs[account])}
    until = owner_experiment_until(signal)
    assert until is not None and rows[control].spec.params.get("control") == signal

    def refused_at(ts: str) -> frozenset[str]:
        ex = SimpleNamespace(arms={a: SimpleNamespace(spec=rows[a].spec) for a in (signal, control)},
                             retired=frozenset(), unqualified={}, owner_read=None, queue=[])
        exploration.refresh_owner_experiments(SimpleNamespace(decisions=[]), ex,  # type: ignore[arg-type]
                                              pd.Timestamp(ts, tz="UTC"))
        return frozenset(ex.retired)

    assert control not in refused_at(f"{until} 23:59")
    assert {signal, control} <= refused_at(f"{pd.Timestamp(until) + pd.Timedelta(days=1):%Y-%m-%d} 00:01")


def test_gs1c_is_retired_with_gs1_before_its_exception_would_lapse(accs):
    """Owner priority 2026-11-22 (only arms with evidence place orders): GS1 and its random twin GS1C are retired on
    2026-11-22, before the owner experiment's 2026-11-27 end, so no broker-traded control is left to excuse."""
    until = owner_experiment_until("GS1")
    assert until == "2026-11-27"
    rows = {a.arm_id: a for acc in accs.values() for a in _arms(acc)}
    assert rows["GS1"].spec.retired_day == rows["GS1C"].spec.retired_day == "2026-11-22"
    after = (pd.Timestamp(until) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert _unexcused(accs, until) == [] and _unexcused(accs, after) == []


@pytest.mark.parametrize("arm_id", TURNED)
def test_each_formerly_broker_traded_control_is_a_replay_twin_not_a_retirement(accs, arm_id):
    """A retirement records no candidates, so it would leave no twin (amendment 2026-11-15, "Why"). A control dedicated
    to learning arms is retired only once every arm it serves is (RBC15 with RB15, red-box review 2026-11-20): it has
    nothing left to be a twin of."""
    rows = {a.arm_id: a for acc in accs.values() for a in _arms(acc)}
    spec = rows[arm_id].spec
    serves = dedicated_control_of(spec)
    assert spec.control_mode == "replay_only"
    assert not spec.retired or (serves and all(rows[s].spec.retired for s in serves)), arm_id


def test_broker_traded_controls_never_outnumber_signal_arms_on_any_enabled_account(accs):
    for name, acc in accs.items():
        if not acc.enabled:
            continue
        signals = [a for a in _arms(acc) if _may_enter(a) and a.spec.role != "control"]
        assert len(_broker_controls(acc)) <= len(signals), (name, _broker_controls(acc))


def test_no_account_keeps_a_broker_traded_control(accs):
    """Owner priority 2026-11-22: GS1C retired with GS1, the weekend lane's WKS0/WKI0 replay-only (the lane stays off)."""
    name, _ids = WEEKEND_LANE
    broker = {n: _broker_controls(acc) for n, acc in accs.items()}
    assert {n: x for n, x in broker.items() if x} == {}
    assert not accs[name].enabled


@pytest.mark.parametrize("arm_id", TURNED)
def test_a_signal_of_a_configured_control_never_reaches_the_riskgate_or_an_order(tmp_path, accs, arm_id):
    """The configured mode of the real row, on the engine's own path: recorded `replay_only`, never sent."""
    mode = {a.arm_id: a for acc in accs.values() for a in _arms(acc)}[arm_id].spec.control_mode
    test_arm = _arm(arm_id, control_share=0.05)
    spec = test_arm.spec.model_copy(update={"role": "control", "control_mode": mode})
    eng, pb = _engine(tmp_path, [test_arm.model_copy(update={"spec": spec}), _arm("e")])
    seen = _spy_gate(eng)
    exploration.on_pass(eng, _at(eng, pb, _at_utc(TUE, "12:00")))
    assert {d["arm"] for d in _sent(eng)} == {"e"}
    assert all(i[0].arm_id != arm_id for i in seen)
    assert [d["arm"] for d in _actions(eng, "exploration_refused:replay_only")] == [arm_id]


# ------------------------------------------------------------------------------------------------ nr4_break 5m
def test_every_nr4_break_5m_arm_stays_retired():
    from goldbot.research.arms import load_arms
    rows = [a for a in load_arms(exploration.SPEC_PATH) if a.rule == "nr4_break" and a.timeframe == "5m"]
    assert {a.id for a in rows} >= {"Z6D", "Z6V"}
    assert all(a.retired for a in rows), [a.id for a in rows if not a.retired]


# ------------------------------------------------------------------------------------------------ 5m Asia
def _five(arm_id: str = "A", **kw: Any) -> Arm:
    arm = _spec_arm(arm_id, **kw)
    return arm.model_copy(update={"spec": arm.spec.model_copy(update={"timeframe": "5m"})})


@pytest.mark.parametrize("hms, refused", [("23:59:59", False), ("00:00:00", True), ("01:59:59", True),
                                          ("04:30:00", True), ("06:59:59", True), ("07:00:00", False),
                                          ("12:00:00", False), ("21:00:00", False)])
def test_asia_5m_refuses_a_gold_5m_signal_decided_from_0000_inclusive_to_0700_exclusive(hms, refused):
    arm = _five()
    assert entry_refusals.asia_5m(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, hms))) is refused


@pytest.mark.parametrize("tf", ["1m", "15m", "1h"])
def test_asia_5m_touches_no_other_timeframe(tf):
    arm = _spec_arm("A")
    arm = arm.model_copy(update={"spec": arm.spec.model_copy(update={"timeframe": tf})})
    assert not entry_refusals.asia_5m(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "01:00")))


@pytest.mark.parametrize("arm", [_five("S0-XAUEUR", role="control", instrument="XAUEUR"),
                                 _five("G1A", role="cost", cost_read="O-12")])
def test_asia_5m_has_the_scope_of_window_0207_no_cross_and_no_cost_arm(arm):
    assert not entry_refusals.asia_5m(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, "01:00")))


def test_asia_5m_leaves_the_weekend_lane_alone():
    arm = _five("WKS0", role="control")
    assert not entry_refusals.asia_5m(_cfg_engine(weekend=True), arm, _sig(arm, _at_utc(TUE, "01:00")))


@pytest.mark.parametrize("hms, reason", [("01:00", ASIA_5M), ("03:00", WINDOW_0207), ("08:00", None)])
def test_window_0207_keeps_naming_its_hours_and_asia_5m_names_the_rest_of_asia(hms, reason):
    arm = _five()
    static = tuple(r for r in entry_refusals.REFUSALS if r.name in (WINDOW_0207, ASIA_5M))   # in shipped order
    got = entry_refusals.first_refusal(_cfg_engine(), arm, _sig(arm, _at_utc(TUE, hms)), static)
    assert (got.reason if got else None) == reason


def test_asia_5m_is_static_so_the_daily_budget_counts_only_the_signals_it_lets_through():
    arm = _five()
    got = entry_refusals.static_refusal(_cfg_engine(), arm, _at_utc(TUE, "01:00"))
    assert ASIA_5M in entry_refusals.STATIC and got is not None and got.reason == ASIA_5M


@pytest.mark.parametrize("close, sent", [("00:55", False), ("07:05", True)])   # off the quarter: sent this pass
def test_the_engine_shadows_a_refused_5m_asia_signal_and_the_riskgate_never_sees_it(tmp_path, close, sent):
    eng, pb = _engine(tmp_path, [_arm("a", tf="5m")])
    seen = _spy_gate(eng)
    exploration.on_pass(eng, _at(eng, pb, _at_utc(TUE, close)))
    assert bool(_sent(eng)) is sent and bool(seen) is sent
    if not sent:
        [row] = _obs(tmp_path)
        assert row["taken"] == 0 and row["blocked_reason"] == ASIA_5M
        assert {d["action"] for d in _actions(eng, "exploration_refused:")} == {"exploration_refused:" + ASIA_5M}
