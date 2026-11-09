"""EVAL-2 graded verdicts (prereg 2026-11-09 sections 1, 2 and 4.8; method v6 sections 1b, 3 and 7).

"The owner sees rungs and £, not 0/N." CONFIRM-SEEN / HOLDOUT-SEEN by "excess overlap e = o - b >= 15 pp".
"DSR K = 3,471 + 17,862 = 21,333 (sensitivity 20,151). Reported, not gated, before L5.\""""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from goldbot.research.eval2.confirm import L3Result
from goldbot.research.eval2.hier import L2Decision
from goldbot.research.eval2.verdict import (
    DSR_K,
    DSR_K_SENSITIVITY,
    OVERLAP_SEEN_PP,
    FamilyVerdict,
    HoldoutStatus,
    Verdict,
    dsr_at_k,
    excess_overlap_pp,
    family_verdict,
    holdout_status,
    is_confirm_seen,
)


@dataclass(frozen=True)
class Fwd:
    """The fields of Lane 5A's ForwardState that the verdict reads."""
    family: str = "C03"
    n_signals: int = 120
    n_fills: int = 60
    e_exec: float = 1.0
    exit_mix_ok: bool = True
    e_futility: float = 1.0
    e_edge: float = 2.0
    status: str = "ON TRACK"


def _l3(rejected: bool = True, cells: tuple[str, ...] = ("C03|a",), lb: float = 0.05) -> L3Result:
    return L3Result("C03", "test_old", 1, 0.05, 0.01, 0.02, lb, {"years_pos_ge3": True}, 0.05, None, rejected,
                    cells if rejected else ())


L2_CARRY = L2Decision("C03", ("C03|a", "C03|b"), (), ())
L2_NONE = L2Decision("C03", (), ("C03|a",), ("C03|b",))


def _v(**kw: Any) -> FamilyVerdict:
    base: dict[str, Any] = dict(family="C03", probe_no_signal=False, l2=None, l3=None, l4=None,
                                holdout=HoldoutStatus.FRESH, confirm_seen_cells=(), gbp_per_r=50.0, signals_per_day=0.5)
    base.update(kw)
    return family_verdict(**base)


def test_the_verdict_names_are_the_frozen_strings():
    assert [v.value for v in Verdict] == ["NOT TESTED", "INSUFFICIENT (power)", "NO SIGNAL", "COST-BOUND",
                                         "EXIT-DRAG", "TRADABLE ON HISTORY", "CONFIRM-SEEN", "CONFIRMED", "ON TRACK",
                                         "DRIFTING", "KILLED", "LIVE-READY"]
    assert [h.value for h in HoldoutStatus] == ["HOLDOUT-SEEN", "HOLDOUT-FRESH"]


def test_the_ladder_reads_rung_by_rung():
    assert _v(probe_no_signal=None).verdict is Verdict.NOT_TESTED
    assert _v(probe_no_signal=True, l2=L2_CARRY).verdict is Verdict.NO_SIGNAL
    assert _v(l2=L2_NONE).verdict is Verdict.COST_BOUND
    assert _v(l2=L2_CARRY).verdict is Verdict.TRADABLE
    assert _v(l2=L2_CARRY, l3=_l3(rejected=False)).verdict is Verdict.TRADABLE
    assert _v(l2=L2_CARRY, l3=_l3()).verdict is Verdict.CONFIRMED
    assert _v(l2=L2_CARRY, l3=_l3(), l4=Fwd()).verdict is Verdict.ON_TRACK
    assert _v(l2=L2_CARRY, l3=_l3(), l4=Fwd(status="WAITING")).verdict is Verdict.CONFIRMED
    assert _v(l2=L2_CARRY, l3=_l3(), l4=Fwd(status="DRIFTING")).verdict is Verdict.DRIFTING


def test_a_forward_kill_wins_over_every_history_rung():
    for kw in ({"l2": L2_CARRY, "l3": _l3()}, {"l2": L2_CARRY}, {"probe_no_signal": True}):
        assert _v(l4=Fwd(status="KILLED"), **kw).verdict is Verdict.KILLED


def test_on_track_needs_a_confirmed_family():
    """L4 is conformance, not discovery: an unconfirmed family with a green forward state stays on its history rung."""
    assert _v(l2=L2_CARRY, l4=Fwd()).verdict is Verdict.TRADABLE


def test_a_family_confirmed_only_through_confirm_seen_cells_reads_confirm_seen():
    """CONFIRM-SEEN cells are 'decided on TEST-OLD + the forward holdout + L4': the rung says so."""
    v = _v(l2=L2_CARRY, l3=_l3(cells=("C03|a",)), confirm_seen_cells=("C03|a",))
    assert v.verdict is Verdict.CONFIRM_SEEN and v.confirm_seen_cells == ("C03|a",)
    mixed = _v(l2=L2_CARRY, l3=_l3(cells=("C03|a", "C03|b")), confirm_seen_cells=("C03|a",))
    assert mixed.verdict is Verdict.CONFIRMED


def test_the_verdict_is_never_live_ready():
    """L5 is the owner's: no combination of history and forward rungs reads LIVE-READY here."""
    assert _v(l2=L2_CARRY, l3=_l3(), l4=Fwd(e_edge=50.0)).verdict is not Verdict.LIVE_READY


def test_the_gbp_view_carries_the_l3_lower_bound_and_leaves_what_it_cannot_derive_nan():
    v = _v(l2=L2_CARRY, l3=_l3(lb=0.04), gbp_per_r=50.0, signals_per_day=0.5)
    assert v.net_gbp_day_lo80 == pytest.approx(0.04 * 50.0 * 0.5)
    assert math.isnan(v.net_gbp_day) and math.isnan(v.net_gbp_day_hi80)
    assert math.isnan(_v(l2=L2_CARRY).net_gbp_day_lo80)
    assert v.holdout is HoldoutStatus.FRESH and v.reason


def test_excess_overlap_marks_seen_at_15_pp_or_more():
    assert OVERLAP_SEEN_PP == 15.0
    assert excess_overlap_pp(0.30, 0.15) == pytest.approx(15.0)
    assert is_confirm_seen(trigger_seen=False, o=0.30, b=0.15) is True          # exactly 15 pp: SEEN
    assert is_confirm_seen(trigger_seen=False, o=0.299, b=0.15) is False
    assert is_confirm_seen(trigger_seen=True, o=0.0, b=0.0) is True
    assert holdout_status(trigger_matches_holdout_rule=False, o=0.27, b=0.11) is HoldoutStatus.SEEN    # 16 pp
    assert holdout_status(trigger_matches_holdout_rule=False, o=0.20, b=0.09) is HoldoutStatus.FRESH   # 11 pp
    assert holdout_status(trigger_matches_holdout_rule=True, o=0.0, b=0.0) is HoldoutStatus.SEEN
    with pytest.raises(ValueError):
        excess_overlap_pp(1.2, 0.1)


def test_dsr_is_reported_at_the_registry_plus_snooping_k_and_its_sensitivity():
    assert (DSR_K, DSR_K_SENSITIVITY) == (21_333, 20_151) == (3_471 + 17_862, 3_471 + 16_680)
    x = np.random.default_rng(0).normal(0.05, 1.0, 2_000)
    r = dsr_at_k(x)
    from goldbot.research.catalogue.stats import deflated_sharpe
    assert r["k"] == 21_333 and r["dsr"] == pytest.approx(deflated_sharpe(x, 21_333)["dsr"])
    assert r["k_sensitivity"] == 20_151
    assert r["dsr_sensitivity"] == pytest.approx(deflated_sharpe(x, 20_151)["dsr"])
    assert r["dsr_sensitivity"] >= r["dsr"]
