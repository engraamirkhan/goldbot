"""BACKLOG 105: a rule-code version on each exploration arm row (goldbot/research/arm_rule_versions.py).

PINS holds, per rule, the sha256[:16] of its implementation's source for every version it has had. A rule whose source
changes fails `test_every_rule_source_matches_the_pin_of_its_version` until its version is bumped in RULE_VERSIONS (with
a dated note in docs/research/arms-2026-10-25.md "Rule-code versions") and the new source is pinned under the new
version here; a past version's pin is never edited."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from goldbot.research import arm_rule_versions as V
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from goldbot.research import daily_review as dr
from goldbot.research import exploration_data as ex
from tests.test_daily_review import HOSTS, LONDON, _db, _run

PINS: dict[str, dict[int, str]] = {
    "absorb_fade": {1: "460fec90f4727017"},
    "box_break_close": {1: "c321a10af6ce00a0"},
    "box_next": {1: "1d4b2a2f2a18e3dc"},
    "clock_anchor": {1: "45303ca696e52bcc"},
    "clock_anchor_control": {1: "df02eb7611d24f66"},
    "donchian_break": {1: "c5495642a600c84b"},
    "ema_pullback": {1: "1ed029cc64862fba"},
    "extreme_break": {1: "5ac50e23593ae76d"},
    "friday_long": {1: "33db754be06654ce"},
    "halfhour_persist": {1: "56739104de4c708c"},
    "htf_reclaim": {1: "8a7231aab274f9c9"},
    "htf_stretch_break": {1: "2341399d818413a9"},
    "htf_stretch_break_control": {1: "99596995ab8a4241"},
    "hurst_band_break": {1: "c38cd53f4839baab"},
    "hurst_band_break_control": {1: "371882e1c12df97e"},
    "level_break": {1: "e5422ac431379449"},
    "low30_control": {1: "5a670ac762983bf3"},
    "low30_touch": {1: "edbb0e7e35e7b5ba"},
    "nr4_break": {1: "9068726d1f91840e"},
    "ob_retest": {1: "ebe55716358d85f5"},
    "opening_range_break": {1: "9982fd1cfac424d9"},
    "pdc_break": {1: "d8224403ed3c50af"},
    "random_bracket": {1: "456a1e9b3011641b"},
    "round_break": {1: "5162cf1c9babc1fc"},
    "round_reject": {1: "d7f16179ef5d4a0a"},
    "run_fade": {1: "26545f69de302cab"},
    "scalp_control": {1: "adad7e8760b09f6a"},
    "session_range_break": {1: "b05f7872081146ea"},
    "session_range_control": {1: "81034cf39f041892"},
    "session_vwap_fade": {1: "9982a7f4a164f192"},
    "spec_rule": {1: "d8b86dc0868bfe85"},
    "specialist": {1: "0c31b1c9497ea0ca"},
    "squeeze_release": {1: "71317a2c853e0c98"},
    "sweep_absorb": {1: "f0c4293742f985f2"},
    "three_horizon": {1: "133d7bf654e915c4"},
    "tick_surge": {1: "9f4823a326feeef8"},
    "tsmom": {1: "542feb3ba6304916"},
}


def unbumped() -> list[str]:
    """Rules whose current source is not the pin of their current version."""
    return sorted(r for r in V.RULE_VERSIONS if PINS.get(r, {}).get(V.RULE_VERSIONS[r]) != V.source_sha(r))


def test_every_rule_carries_a_version_and_a_pin() -> None:
    assert set(V.RULE_VERSIONS) == set(R.RULE_FUNCS) == set(PINS)


def test_every_rule_source_matches_the_pin_of_its_version() -> None:
    assert unbumped() == [], ("rule code changed without a version bump: bump RULE_VERSIONS (dated note in "
                              "docs/research/arms-2026-10-25.md) and pin the new source under the new version")


def test_a_source_change_without_a_bump_fails_and_the_bump_with_its_pin_passes(monkeypatch) -> None:
    real = V.source_sha
    monkeypatch.setattr(V, "source_sha", lambda r: "edited" if r == "random_bracket" else real(r))
    assert unbumped() == ["random_bracket"]
    monkeypatch.setitem(V.RULE_VERSIONS, "random_bracket", 2)
    monkeypatch.setitem(PINS, "random_bracket", {**PINS["random_bracket"], 2: "edited"})
    assert unbumped() == []


def test_the_arm_hash_and_the_frozen_arm_ids_are_unchanged_by_the_versions() -> None:
    """No re-freeze: the version is not part of an arm's identity (`arm_hash`), so the frozen arms keep theirs."""
    arms = A.load_arms(ex.SPEC_PATHS[0])                     # check_hash: every frozen arm's hash still its own
    before = {a.id: A.arm_hash(a) for a in arms}
    assert arms and before == {a.id: a.config_hash for a in arms}
    with pytest.MonkeyPatch.context() as m:
        for r in V.RULE_VERSIONS:
            m.setitem(V.RULE_VERSIONS, r, 99)
        assert {a.id: A.arm_hash(a) for a in arms} == before


def _cand(db: object, cid: str, version: int | None, arm: str = "S1A", minutes: int = 0) -> None:
    ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker=HOSTS["icm-probe"], arm_id=arm,  # type: ignore[arg-type]
                        family="f", style="scalp", tf="1m", side=1,
                        decision_utc=LONDON + pd.Timedelta(minutes=minutes), stop_dist=2.0, target_dist=2.0,
                        max_hold_s=600, taken=True, branch="random", u=0.5, mu=1.0, arm_rule_version=version)


def test_the_readers_can_filter_on_the_rule_version(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _cand(db, "c1", 1)
    _cand(db, "c2", 2, minutes=1)
    _cand(db, "c3", None, minutes=2)                              # recorded before the column: no version
    assert [None if pd.isna(v) else int(v) for v in ex.read_obs(db, broker="icm")["arm_rule_version"]] == [1, 2, None]
    assert list(ex.read_obs(db, broker="icm", rule_version=2)["candidate_id"]) == ["c2"]


def test_a_version_bump_inside_an_arms_life_is_reported_in_the_daily_review(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for i in range(3):
        _cand(db, f"a{i}", 1, minutes=i)
    _cand(db, "a3", 2, minutes=3)
    _cand(db, "b0", 1, arm="S2A", minutes=4)                      # one version only: not reported
    review = _run(tmp_path)
    assert review.rule_versions == {"S1A": {"1": 3, "2": 1}}
    assert "Review: rule code changed inside an arm's life (rows per version): S1A v1 3, v2 1" in dr.report_lines(review)
