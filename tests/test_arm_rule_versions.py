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
    "absorb_fade": {1: "35735e11e6f59b3d"},
    "box_break_close": {1: "bf18a06b94315673"},
    "box_next": {1: "7a807dde912f17da"},
    "clock_anchor": {1: "af3b5efa66282f15"},
    "clock_anchor_control": {1: "38cb14a524838d19"},
    "donchian_break": {1: "76e2398c60940575"},
    "ema_pullback": {1: "6025cb3bbf0ff4f0"},
    "extreme_break": {1: "a7387ef3360a7de6"},
    "friday_long": {1: "ba7b4bdcb463b899"},
    "halfhour_persist": {1: "4e4d542704293778"},
    "htf_reclaim": {1: "ddb85f5fcccdcea1"},
    "htf_stretch_break": {1: "83041d9318daf33b"},
    "htf_stretch_break_control": {1: "df8582598af718a4"},
    "hurst_band_break": {1: "e00ce764c7b9fa25"},
    "hurst_band_break_control": {1: "54da91767bb79fb1"},
    "level_break": {1: "1e7704bc300b236d"},
    "low30_control": {1: "7da2e61575613127"},
    "low30_touch": {1: "6b5caf42edff867e"},
    "nr4_break": {1: "12475362f2fa1258"},
    "ob_retest": {1: "72efaa56c73d4dd5"},
    "opening_range_break": {1: "7bd4d9ee08341a67"},
    "pdc_break": {1: "9e7c8933e4813b5d"},
    "random_bracket": {1: "cf8051684df1018f"},
    "round_break": {1: "27e4a82daace7356"},
    "round_reject": {1: "1d48a288cd3b6460"},
    "run_fade": {1: "cfe0797b643b9898"},
    "scalp_control": {1: "ad232a6dd9350752"},
    "session_range_break": {1: "b1e3441afc32403c"},
    "session_range_control": {1: "0894aad477b57f07"},
    "session_vwap_fade": {1: "73634283fdc52b9e"},
    "spec_rule": {1: "e202fde4ecc478b2"},
    "specialist": {1: "8b28c15094127b7f"},
    "squeeze_release": {1: "feede72f6aca8043"},
    "sweep_absorb": {1: "823a5dec790f3ebe"},
    "three_horizon": {1: "50cf239cc0390fbe"},
    "tick_surge": {1: "73b0d90f9c3ac783"},
    "tsmom": {1: "be1a50c1f4e4b760"},
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


def test_the_version_covers_the_shared_helpers_each_rule_calls_and_the_common_screen() -> None:
    """Quant review 2026-11-14: hashing the rule's own function missed its shared helpers (`_screen`, the weekcut
    walk), so a change there moved every rule's output with no bump. The hash covers the rule, every arm_rules* helper
    it reaches, and the common post-filter every rule passes through."""
    from goldbot.research import arm_rules_weekcut as W
    for rule in V.RULE_VERSIONS:
        assert R._screen in V.helpers(rule) and R.screened_frame in V.helpers(rule)
    assert W.capped in V.helpers("pdc_break")                  # PDC4's time exit under the Friday cut, behind the
    assert W.capped in V.helpers("donchian_break")             # arm_rules wrapper's function-local import


def test_changing_a_shared_helper_changes_every_dependent_rules_source_sha(monkeypatch) -> None:
    from goldbot.research import arm_rules_weekcut as W
    before = {r: V.source_sha(r) for r in ("pdc_break", "random_bracket")}
    real = V._source
    monkeypatch.setattr(V, "_source", lambda f: real(f) + "# edited" if f is W.capped else real(f))
    assert V.source_sha("pdc_break") != before["pdc_break"]
    assert V.source_sha("random_bracket") == before["random_bracket"]       # does not reach capped
    monkeypatch.setattr(V, "_source", lambda f: real(f) + "# edited" if f is R._screen else real(f))
    assert V.source_sha("random_bracket") != before["random_bracket"]       # every rule passes _screen


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
