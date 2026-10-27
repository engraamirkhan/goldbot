"""scripts/profile_arm_rules.py (lane L7): the host's arm list, the sampled closes and one timed close."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prof():
    spec = importlib.util.spec_from_file_location("profile_arm_rules", ROOT / "scripts" / "profile_arm_rules.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["profile_arm_rules"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2024-03-04", "2024-03-13", ticks_per_minute=1, seed=7))


def test_host_cases_cover_config_and_wave1(prof) -> None:
    icm = prof.host_cases("icm-demo")
    labels = {c.label for c in icm}
    assert {"I0", "MB15", "W1A"} <= labels                       # today's 15m+ arms of the host
    assert not {"S0", "S1A"} & labels                            # sub-15m arms run on their own minute path
    assert sum(c.wave1 for c in icm) == 10 and "C1H" in labels   # 7 families + 2 variants + the 1h control
    van = prof.host_cases("vantage-demo")
    assert sum(c.wave1 for c in van) == 9 and "VC1H" in {c.label for c in van}
    assert len(prof.host_cases("icm-demo", wave1=False)) == len(icm) - 10


def test_sample_closes_are_on_15m(prof, bars) -> None:
    closes = prof.sample_closes(bars, 5)
    assert len(closes) == 5 and all(t.minute % 15 == 0 and t.second == 0 for t in closes)
    assert closes == sorted(closes) and closes[-1] <= pd.Timestamp(bars["visible_at"].iloc[-1])


def test_profile_times_every_due_arm(prof, bars) -> None:
    cases = [c for c in prof.host_cases("icm-demo") if c.label in {"I0", "C1H", "mean_reversion@15m"}]
    hour = [t for t in prof.sample_closes(bars, 40) if t.minute == 0][-1:]
    r = prof.profile(bars, cases, hour, full=False, host="icm-demo")
    assert len(r.per_close_s) == 1 and set(r.per_arm_s) == {"I0", "C1H", "mean_reversion@15m"}
    assert not r.errors
    s = r.summary(cpu_factor=2.0)
    assert s["max_s_brain"] == pytest.approx(2.0 * s["max_s"], abs=2e-3) and s["budget_s"] == prof.BUDGET_S


def test_minute_cases_are_the_sub15m_arms(prof) -> None:
    probe = prof.minute_cases("icm-probe")
    labels = {c.label for c in probe}
    assert {"S0", "S1A", "S5A"} <= labels and all(c.tf in ("1m", "5m") for c in probe)
    demo = {c.label for c in prof.minute_cases("icm-demo")}      # icm-demo carries the H-81 scalp sleeve (2026-10-27)
    assert demo and all(c.tf in ("1m", "5m") for c in prof.minute_cases("icm-demo"))
    two = prof.minute_cases("icm-probe", replicate=2)
    assert len(two) == 2 * len(probe) and "S0#1" in {c.label for c in two}


def test_minute_pass_times_due_arms_and_the_quarter_sum(prof, bars) -> None:
    cases = [c for c in prof.minute_cases("icm-probe") if c.label in {"S0", "S1A"}]
    minutes = prof.sample_minutes(bars, 15)
    assert len(minutes) == 15 and all(t.second == 0 for t in minutes)
    r = prof.profile_minutes(bars, cases, minutes, host="icm-probe")
    assert len(r.per_close_s) == 15 and not r.errors
    assert len(r.per_arm_s["S0"]) == 15                          # the 1m arm every minute
    assert len(r.per_arm_s["S1A"]) == sum(t.minute % 5 == 0 for t in minutes)   # the 5m arm on the fives
    s = r.summary(cpu_factor=1.0, budget_s=prof.MINUTE_BUDGET_S, at="p99")
    assert s["budget_s"] == 2.0 and s["within_budget"] is (s["p99_s_brain"] <= 2.0)
    quarters = [t for t in minutes if t.minute % 15 == 0]
    fifteen = prof.HostResult(host="icm-probe", arms=1, wave1=0, per_close_s=[1.0] * len(quarters),
                              close_ts=quarters)
    q = prof.quarter_pass(fifteen, r, cpu_factor=2.0)
    assert q["quarter_closes"] == len(quarters) >= 1
    worst = max(r.per_close_s[minutes.index(t)] for t in quarters)
    assert q["max_s"] == pytest.approx(1.0 + worst, abs=2e-3)
