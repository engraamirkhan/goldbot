"""Futility retire (ADR 0077; prereg 2026-11-09 sections 1 and 4.7).

"Between screen_retire_floor (300) and screen_min_events (1,000) a screen retires a unit only if: (a) the one-sided
90 % upper bound of the statistic named in the card before the read ..., with day-clustered SE, is < +0.03 R; (b) the
clustered SE is <= 0.055 R; (c) both were pre-registered before the read, and the read is counted in K. Otherwise the
result is 'inconclusive (event floor)' ... Never retire on sign or net alone. ... Below 300 is 'insufficient'."
"`preregistered=False` can never return 'retire'.\""""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from goldbot.config import ResearchSettings
from goldbot.research.screen import (
    FAIL,
    INCONCLUSIVE,
    FutilityResult,
    futility_retire,
    futility_terms,
    min_events_for,
    screen_floors,
    screen_verdict,
)

Z90 = 1.2815515655446004


def test_futility_retires_only_a_tight_bound_below_three_hundredths_of_r():
    r = futility_retire(n=600, mean_r=-0.05, se_r=0.05, statistic="gross", preregistered=True)
    assert isinstance(r, FutilityResult) and r.verdict == "retire"
    assert r.ub90_r == pytest.approx(-0.05 + Z90 * 0.05) and r.se_r == 0.05 and r.n == 600 and r.statistic == "gross"


def test_futility_needs_both_the_bound_and_the_se():
    # bound below 0.03 but SE above 0.055: a noisy read cannot retire (<= 5 % chance of retiring a true +0.05 R)
    assert futility_retire(n=600, mean_r=-0.2, se_r=0.06, statistic="delta", preregistered=True).verdict == INCONCLUSIVE
    # SE fine but the bound reaches 0.03
    assert futility_retire(n=900, mean_r=0.0, se_r=0.03, statistic="gross", preregistered=True).verdict == INCONCLUSIVE
    assert futility_retire(n=900, mean_r=0.0, se_r=0.0234, statistic="gross",
                           preregistered=True).verdict == "retire"                   # UB 0.02999 < 0.03


def test_an_unregistered_futility_read_never_retires():
    for n in (300, 999, 5_000):
        r = futility_retire(n=n, mean_r=-1.0, se_r=0.01, statistic="gross", preregistered=False)
        assert r.verdict == INCONCLUSIVE


def test_below_the_floor_is_insufficient_whatever_the_numbers():
    assert futility_retire(n=299, mean_r=-1.0, se_r=0.01, statistic="gross", preregistered=True).verdict == \
        "insufficient"
    assert futility_retire(n=150, mean_r=-1.0, se_r=0.01, statistic="gross", preregistered=True,
                           floor=150).verdict == "retire"


def test_a_missing_or_degenerate_se_never_retires():
    for se in (float("nan"), 0.0, -0.01):
        assert futility_retire(n=600, mean_r=-1.0, se_r=se, statistic="gross", preregistered=True).verdict == \
            INCONCLUSIVE


def _rule(n: int, mean_r: float, t: float, futility: dict | None = None) -> dict:
    out: dict = {"gross": {"n": n, "mean_r": mean_r, "t_stat": t}, "net": {"n": n, "mean_r": mean_r - 0.2}}
    if futility is not None:
        out["futility"] = futility
    return out


def test_settings_carry_the_adr_0077_keys_and_withdraw_ruling_a():
    r = ResearchSettings()
    assert (r.screen_retire_floor, r.screen_retire_ub_r, r.screen_retire_max_se_r) == (300, 0.03, 0.055)
    assert r.screen_min_events_daily is None
    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text())
    res = doc["research"]
    assert res["screen_min_events_daily"] is None
    assert (res["screen_retire_floor"], res["screen_retire_ub_r"], res["screen_retire_max_se_r"]) == (300, 0.03, 0.055)


def test_every_unit_retires_from_three_hundred_events_and_passes_from_the_pass_floor():
    r = ResearchSettings()
    for tf in ("15m", "1h", "4h", "1d"):
        assert screen_floors(tf, r) == (300, 1000) and min_events_for(tf, r) == 300


def test_between_the_floors_the_screen_retires_only_on_the_preregistered_futility_test():
    retire, pass_n = 300, 1000
    terms = futility_terms(ResearchSettings(), statistic="gross", mean_r=-0.05, se_r=0.04, preregistered=True)
    for n in (300, 650, 999):
        assert screen_verdict(_rule(n, -0.05, -1.2, terms), retire, pass_n)["verdict"] == FAIL
        # sign and t alone never retire (Ruling A withdrawn)
        assert screen_verdict(_rule(n, -0.05, -3.0), retire, pass_n)["verdict"] == INCONCLUSIVE
        unreg = {**terms, "preregistered": False}
        assert screen_verdict(_rule(n, -0.05, -3.0, unreg), retire, pass_n)["verdict"] == INCONCLUSIVE
        # a good edge between the floors never passes
        good = futility_terms(ResearchSettings(), statistic="gross", mean_r=0.08, se_r=0.04, preregistered=True)
        assert screen_verdict(_rule(n, 0.08, 3.5, good), retire, pass_n)["verdict"] == INCONCLUSIVE
    v = screen_verdict(_rule(650, -0.05, -1.2, terms), retire, pass_n)
    assert v["futility"]["verdict"] == "retire" and v["futility"]["ub90_r"] == pytest.approx(-0.05 + Z90 * 0.04)
    noisy = futility_terms(ResearchSettings(), statistic="delta", mean_r=-0.05, se_r=0.07, preregistered=True)
    assert screen_verdict(_rule(650, -0.05, -1.2, noisy), retire, pass_n)["verdict"] == INCONCLUSIVE


def test_below_three_hundred_the_futility_test_cannot_retire_even_with_a_lower_daily_override():
    terms = futility_terms(ResearchSettings(), statistic="gross", mean_r=-0.5, se_r=0.01, preregistered=True)
    v = screen_verdict(_rule(200, -0.5, -9.0, terms), 150, 1000)
    assert v["verdict"] == INCONCLUSIVE and v["futility"]["verdict"] == "insufficient"


def test_futility_terms_come_from_the_settings_keys():
    r = ResearchSettings(screen_retire_floor=400, screen_retire_ub_r=0.02, screen_retire_max_se_r=0.05)
    t = futility_terms(r, statistic="delta", mean_r=0.0, se_r=0.01, preregistered=True)
    assert t == {"statistic": "delta", "mean_r": 0.0, "se_r": 0.01, "preregistered": True, "floor": 400,
                 "ub_r": 0.02, "max_se_r": 0.05}


def test_at_or_above_the_pass_floor_the_screen_is_unchanged():
    assert screen_verdict(_rule(1000, 0.05, 2.0), 300, 1000)["verdict"] == "pass"
    assert screen_verdict(_rule(1500, -0.05, -1.0), 300, 1000)["verdict"] == FAIL
