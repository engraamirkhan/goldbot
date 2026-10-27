"""The Sunday weekly digest (docs/ux/daily-learning-report.md section 3, lane L5a): replaces Sunday's daily, covers the
five opportunity days Mon-Fri, keeps the arms blinded (n and band only) and stays within 25 lines."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.telegram.weekly_digest import build_weekly
from tests.test_learning_report import BANNED, SEPT, Lab, _enable, _unitless, health, lines


@pytest.fixture(autouse=True)
def _no_ranking(monkeypatch):
    """The ranking section (ADR 0053) follows the report on its own budget; tests/test_ranking_text.py covers it."""
    from goldbot.telegram import ranking_text
    monkeypatch.setattr(ranking_text, "daily_lines", lambda *a, **k: [])
    monkeypatch.setattr(ranking_text, "weekly", lambda *a, **k: [])

SUN = pd.Timestamp("2026-10-25 06:45", tz="UTC")
MON = pd.Timestamp("2026-10-19 10:00", tz="UTC")
DAYS = [MON + pd.Timedelta(days=i) for i in range(5)]
MOCK_WEEKLY = """📅 Week Mon 19 – Fri 23 Oct · ended Fri 22:00 UTC (23:00 UK)
✅ Health ok now · Halts: none
Scalp lab: 1,040 signals · 318 taken · 312 filled · 305 closed −£41.20 · −9.6R
  by day: Mon −3.9R · Tue +2.2R · Wed −6.1R · Thu +1.0R · Fri −2.8R
  costs £58.10 (12.9R) · now down 2.1% (£230) from peak
Day/swing lab: 188 signals · 43 taken · 41 filled · 38 closed +£64.30 · +4.1R
  by day: Mon +1.4R · Tue −0.6R · Wed +2.9R · Thu −1.1R · Fri +1.5R
  costs £9.80 (0.6R) · now down 0.8% (£88) from peak
Learning paid +£23.10 demo this week (real money £0) · 1,402 usable labels
Arms (trades): S0 140 · S1A 96 · S1B 88 · S2A 118 · S3B 126 · S5A 140 · I2B 22 · MB30 41 · W1A 12 · +12 more
  none within 50% of a stop · none retired · each read once at 300
Learner this week: ran 5 of 5 nights · shares updated
Research this week:
  • Box break close (trial 7 of 20): closed, did not pass its pre-set rule
Not running: Vantage lab
More detail on the dashboard
👉 For you: nothing"""


def _week_lab(lab: Lab, acct: str, arms: list[tuple[str, int]], day_r: list[float], pnl: float, costs: float,
              costs_r: float, *, filled: int, taken: int, signals: int, style: str) -> None:
    """One lab's week: one net trade a day carrying that day's R (all at one stop, so the £ total is `pnl`), one trade
    carrying the week's costs at the stop that makes them `costs_r`, the rest at 0R; then the open and unfilled ones."""
    sl = abs(pnl / sum(day_r))
    queue = [a for a, n in arms for _ in range(n)]
    for i, r in enumerate(day_r):
        lab.trade(acct, queue.pop(0), DAYS[i], pnl=r * sl, stop_loss=sl)
    lab.trade(acct, queue.pop(0), DAYS[0], pnl=0.0, stop_loss=costs / costs_r, commission=-costs)
    for i, arm in enumerate(queue):
        lab.trade(acct, arm, DAYS[i % 5] + pd.Timedelta(minutes=10 + i), stop_loss=4.5)
    closed = len(day_r) + 1 + len(queue)
    lab.trades(acct, arms[0][0], DAYS[4], filled - closed, closed=False)
    lab.trades(acct, arms[0][0], DAYS[4], taken - filled, filled=False)
    lab.obs(acct, arms[0][0], MON, taken, taken=True, style=style, labelled_ns=(MON + pd.Timedelta(days=1)).value)
    lab.obs(acct, arms[0][0], MON, signals - taken, why="global_cap", style=style,
            labelled_ns=(MON + pd.Timedelta(days=1)).value)


def mock_week(sd: Path) -> Lab:
    lab = Lab(sd, SUN).engine("icm-probe", 10_720, 10_950).engine("icm-demo", 10_912, 11_000)
    _week_lab(lab, "icm-probe", [("S0", 137), ("S1A", 96), ("S1B", 67), ("S2A", 5)], [-3.9, 2.2, -6.1, 1.0, -2.8],
              -41.20, 58.10,
              12.9, filled=312, taken=318, signals=1_040, style="scalp")
    _week_lab(lab, "icm-demo", [("MB30", 28), ("X03", 5), ("X04", 5)], [1.4, -0.6, 2.9, -1.1, 1.5], 64.30, 9.80, 0.6,
              filled=41, taken=43, signals=188, style="intraday")
    lab.obs("icm-probe", "S0", SEPT, 1, control=True)
    lab.obs("icm-probe", "S2A", SEPT, 174, labelled_ns=(MON + pd.Timedelta(days=2)).value)   # labelled this week
    lab.trades("icm-probe", "S0", SEPT, 3)
    for arm, n in (("S1B", 21), ("S2A", 113), ("S3B", 126), ("S5A", 140)):
        lab.trades("icm-probe", arm, SEPT, n)
    for arm, n in (("MB30", 13), ("I2B", 22), ("W1A", 12), ("W1B", 5), ("W2", 5)):
        lab.trades("icm-demo", arm, SEPT, n, stop_loss=15.0)
    for i in range(1, 11):
        lab.trades("icm-probe", f"X{i:02d}", SEPT, 10)
    lab.weights(pd.Timestamp("2026-10-18 21:30", tz="UTC"), {"S1A": 0.20})
    for i, d in enumerate(DAYS):
        lab.weights(d.normalize() + pd.Timedelta(hours=21, minutes=30), {"S1A": 0.21 + i / 100})
    (sd / "research_registry.jsonl").write_text("\n".join(json.dumps(r) for r in (
        {"trial": 7, "ts": "2026-10-12T00:00:00+00:00", "status": "evaluated", "family": "box_break_close"},
        {"trial": 8, "ts": "2026-10-21T09:00:00+00:00", "status": "verdict", "family": "box_break_close",
         "verdict_of": {"trial": 7, "ts": "2026-10-12T00:00:00+00:00"}, "decision": "stop",
         "reason": "no edge after costs, see owner@example.com"},
        {"trial": 6, "ts": "2026-10-10T00:00:00+00:00", "status": "verdict", "family": "trend",
         "verdict_of": {"trial": 5}, "decision": "continue", "reason": "last week"})) + "\n")
    return lab


def weekly(sd: Path, now: pd.Timestamp = SUN, **kw: Any) -> str:
    kw.setdefault("settings", load_settings())
    kw.setdefault("roadmap", sd / "no-roadmap.md")
    kw.setdefault("report", health())
    kw.setdefault("registry_path", sd / "research_registry.jsonl")
    return build_weekly(sd, now, **kw)


def test_the_weekly_digest_reproduces_the_spec_mock_for_every_line_with_a_source(tmp_path: Path, monkeypatch) -> None:
    """The spec's section 3.1 mock, less what has no recorded source yet (reported, not invented): the health failure
    history, the worst drawdown of the week (needs-field 5), the broker cost comparison, the H-62 model outcome and
    next week's news event. The verdict's free-text reason never reaches the chat. The spec's accounts are pinned
    (Vantage listed, not enabled), so the shipped config switching it on never changes the mock."""
    from types import SimpleNamespace

    from goldbot.ops import accounts as acc_mod
    monkeypatch.setattr(acc_mod, "account_ids", lambda *a, **k: SimpleNamespace(
        listed={"icm-probe", "icm-demo", "vantage-demo"}, enabled={"icm-probe", "icm-demo"}))
    mock_week(tmp_path)
    assert weekly(tmp_path) == MOCK_WEEKLY


def test_the_weekly_arms_block_is_blinded(tmp_path: Path) -> None:
    lab = Lab(tmp_path, SUN).engine("icm-probe", 10_000, 10_000)
    lab.trades("icm-probe", "S1B", MON, 7, pnl=4.995, stop_loss=4.5)
    lab.trades("icm-probe", "S2A", MON, 4, pnl=-4.995, stop_loss=4.5)
    lab.weights(MON - pd.Timedelta(days=1), {"S1B": 0.2, "S2A": 0.2})
    lab.weights(MON + pd.Timedelta(hours=11), {"S1B": 0.123456, "S2A": 0.276544})
    text = weekly(tmp_path)
    for figure in ("7.77", "7.8R", "4.44", "4.4R", "1.11", "0.12", "12.3", "0.28", "27.7"):
        assert figure not in text, figure
    arm_lines = [x for x in text.splitlines() if "Arms" in x or "within 50%" in x or "Learner" in x]
    assert arm_lines and not any(BANNED.search(x) or "share " in x for x in arm_lines)


def test_a_learning_loss_is_worded_as_a_cost_per_usable_label(tmp_path: Path) -> None:
    lab = Lab(tmp_path, SUN).engine("icm-probe", 10_000, 10_000)
    for arm in ("S1A", "S1B", "S2A"):
        lab.trade("icm-probe", arm, MON, pnl=-4.0)
    lab.obs("icm-probe", "S1A", MON, 8, taken=True, labelled_ns=MON.value)
    assert "Learning cost £12.00 demo (real money £0) · £1.50 per usable label (8)" in weekly(tmp_path)


def test_a_week_total_is_withheld_when_any_of_its_days_was(tmp_path: Path) -> None:
    """Quant 2026-10-26: a week total minus the dailies shown would give a withheld day's figure by subtraction."""
    lab = Lab(tmp_path, SUN).engine("icm-probe", 10_000, 10_000)
    for arm in ("S1A", "S1B", "S2A"):
        lab.trade("icm-probe", arm, MON, pnl=-4.0)
    lab.trade("icm-probe", "S1A", DAYS[2], pnl=7.0)                     # Wednesday: one arm only
    text = weekly(tmp_path)
    assert "by day: Mon −2.7R · Tue none · Wed — · Thu none · Fri none" in text
    assert "4 closed — (a day under 3 arms)" in text and "Learning: — demo this week (real money £0)" in text
    assert "5.00" not in text and "−£12" not in text


def test_no_research_result_and_an_unreadable_registry_are_one_line_each(tmp_path: Path) -> None:
    Lab(tmp_path, SUN).engine("icm-probe", 10_000, 10_000)
    assert "Research this week: no new result" in weekly(tmp_path)
    (tmp_path / "research_registry.jsonl").write_text("{not json\n")
    assert "Research this week: could not read the registry" in weekly(tmp_path)


def test_a_learner_that_never_ran_this_week_is_a_warning(tmp_path: Path) -> None:
    Lab(tmp_path, SUN).engine("icm-probe", 10_000, 10_000)
    assert "⚠️ Learner did not run this week: equal shares in use" in weekly(tmp_path)


def test_a_paused_lab_says_so_in_the_weekly(tmp_path: Path) -> None:
    lab = Lab(tmp_path, SUN).engine("icm-probe", 10_041, 10_950)
    lab.obs("icm-probe", "S1A", pd.Timestamp("2026-10-22 14:20", tz="UTC"), 2, why="paused_dd")
    assert "⏸ Scalp lab paused since Thu 22 Oct 14:20 UTC: down 8.3% (£909) from peak" in weekly(tmp_path)


def test_the_worst_week_fits_twenty_five_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, "icm-probe", "icm-demo", "vantage-demo")
    mock_week(tmp_path)
    lab = Lab(tmp_path, SUN).engine("vantage-demo", 9_625, 10_950, stage="halted")
    lab.trades("icm-probe", "S3B", SEPT, 31, pnl=-4.5, stop_loss=4.5)
    (tmp_path / "control.json").write_text(json.dumps({"halted": True, "ts": SUN.timestamp() - 3600}))
    text = weekly(tmp_path, report=health(41, ("bridge_heartbeat", "fail")))
    got = lines(text)
    assert len(got) <= 25, text
    assert got[1].startswith("🛑 Halts: owner halt") and "Attention: fail bridge_heartbeat" in got[1]
    assert any("1 retired" in x for x in got) and got[-1].startswith("👉 For you:")


def test_every_weekly_number_carries_a_unit_and_no_identifier_appears(tmp_path: Path) -> None:
    mock_week(tmp_path)
    text = weekly(tmp_path)
    assert _unitless(text) == []
    for secret in ("icm-probe", "icm-demo", "vantage-demo", "example.com"):
        assert secret not in text
