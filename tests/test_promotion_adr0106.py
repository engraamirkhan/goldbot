"""ADR 0106 decision 6 (challenger beats the live model 30-60 days after costs) and decision 2 (as-if real-money
sizing) in the champion/challenger promotion gates (goldbot/research/promotion.py)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from goldbot.research.as_if_sizing import AsIfReport
from goldbot.research.promotion import (
    MAX_PAIRED_DAYS,
    MIN_PAIRED_DAYS,
    MIN_SHADOW_SHARPE,
    PAIRED_ALPHA,
    PAIRED_LOOKS,
    PairedRead,
    PerfStats,
    evaluate_promotion,
    paired_day_test,
    session_days,
)

START = date(2026, 3, 2)                       # a Monday
BT = PerfStats(n_trades=600, sharpe_ann=1.5, hit_rate=0.55, max_dd=0.10, trades_per_week=10.0)
SHADOW = PerfStats(n_trades=120, sharpe_ann=1.4, hit_rate=0.55, max_dd=0.08, trades_per_week=10.0, weeks=12.0,
                   n_clusters=60)


def as_if(*, breaches: list | None = None, n_sized_days: int = 60) -> AsIfReport:
    return AsIfReport(equity_start=1250.0, equity_end=1300.0, n_trades=120, n_sized=110, n_sized_days=n_sized_days,
                      below_min_lot=10, excluded={"below_min_lot": 10}, breaches=breaches or [],
                      worst_day_loss=0.01, max_drawdown=0.03, worst_trade_loss=0.01, overshoots=0, daily={},
                      trades=[])


def days(n: int) -> list[date]:
    return session_days(START, date(2027, 12, 31))[:n]


def series(n: int, mean: float, sd: float, seed: int) -> dict[date, float]:
    rng = np.random.default_rng(seed)
    return dict(zip(days(n), (mean + sd * rng.standard_normal(n)).tolist(), strict=True))


def paired(n: int, edge: float, *, seed: int = 1, live: bool = True) -> PairedRead:
    ch = series(n, edge, 0.004, seed)
    lv = series(n, 0.0, 0.004, seed + 100) if live else None
    return paired_day_test(ch, lv, start=START, end=days(n)[-1], seed_key=f"t{seed}")


def test_the_adr_0106_thresholds():
    assert MIN_SHADOW_SHARPE == 1.0                # goal of record: "Live Sharpe ratio above 1.0" (was 0.8)
    assert (MIN_PAIRED_DAYS, MAX_PAIRED_DAYS) == (30, 60)
    assert PAIRED_ALPHA == 0.05 and PAIRED_LOOKS == 7


def test_session_days_are_the_weekdays_of_the_window():
    d = session_days(START, date(2026, 3, 15))
    assert len(d) == 10 and all(x.weekday() < 5 for x in d)


def test_paired_days_count_every_session_day_with_zero_for_a_book_that_did_not_trade():
    ch = {days(40)[0]: 0.01, days(40)[39]: 0.01}
    p = paired_day_test(ch, {}, start=START, end=days(40)[-1], seed_key="z")
    assert p.n_days == 40 and p.mean_diff == pytest.approx(0.02 / 40)


def test_a_window_longer_than_sixty_paired_days_is_read_at_sixty():
    p = paired(80, 0.002)
    assert p.n_days == MAX_PAIRED_DAYS and p.capped and p.window_end == days(MAX_PAIRED_DAYS)[-1]


def test_the_bound_is_one_sided_at_alpha_over_the_looks_and_reproducible():
    a, b = paired(45, 0.002, seed=3), paired(45, 0.002, seed=3)
    assert a.alpha_look == pytest.approx(PAIRED_ALPHA / PAIRED_LOOKS)
    assert a.lower_bound == b.lower_bound and a.lower_bound is not None and a.lower_bound < a.mean_diff


def test_fewer_than_thirty_paired_days_is_not_ready_whatever_the_edge():
    d = evaluate_promotion(BT, SHADOW, BT, paired=paired(29, 0.01), as_if=as_if())
    assert not d.ready and not d.promote and "paired_window" in d.failed


def test_a_challenger_that_beats_live_after_costs_with_lb_above_zero_is_promoted():
    p = paired(40, 0.004)
    assert p.lower_bound is not None and p.lower_bound > 0
    d = evaluate_promotion(BT, SHADOW, BT, paired=p, as_if=as_if())
    assert d.promote and d.ready, d.checks


def test_a_challenger_no_better_than_live_waits_before_sixty_days_and_is_retired_at_sixty():
    early = paired(45, 0.0, seed=5)
    assert early.lower_bound is not None and early.lower_bound <= 0
    d = evaluate_promotion(BT, SHADOW, BT, paired=early, as_if=as_if())
    assert not d.promote and not d.ready and "paired_vs_live" in d.failed
    full = paired(60, 0.0, seed=5)
    d = evaluate_promotion(BT, SHADOW, BT, paired=full, as_if=as_if())
    assert not d.promote and d.ready and "paired_vs_live" in d.failed


def test_without_a_paired_read_the_challenger_is_not_ready_fail_closed():
    d = evaluate_promotion(BT, SHADOW, BT)
    assert not d.promote and not d.ready and {"paired_window", "as_if_real_money"} <= set(d.failed)


def test_a_challenger_whose_as_if_sizing_breaches_a_limit_is_not_promoted_whatever_its_edge():
    d = evaluate_promotion(BT, SHADOW, BT, paired=paired(40, 0.01), as_if=as_if(breaches=["daily_stop"]))
    assert not d.promote and d.ready and "as_if_real_money" in d.failed


def test_trades_below_the_minimum_lot_do_not_count_toward_the_forty_clusters():
    d = evaluate_promotion(BT, SHADOW, BT, paired=paired(40, 0.004), as_if=as_if(n_sized_days=39))
    assert not d.ready and "shadow_length" in d.failed
    assert "39 as-if sized" in next(c.detail for c in d.checks if c.name == "shadow_length")


def test_the_sharpe_floor_is_one_point_zero():
    low = SHADOW.model_copy(update={"sharpe_ann": 1.0})
    d = evaluate_promotion(BT.model_copy(update={"sharpe_ann": 1.2}), low, BT, paired=paired(40, 0.004), as_if=as_if())
    assert not d.promote and "sharpe_floor" in d.failed


def test_without_a_live_model_the_challenger_must_beat_a_flat_book():
    p = paired(40, 0.004, live=False)
    assert not p.live_present and p.lower_bound is not None and p.lower_bound > 0
    assert evaluate_promotion(BT, SHADOW, None, paired=p, as_if=as_if()).promote


def test_a_paired_read_refuses_a_non_finite_day():
    with pytest.raises(ValueError, match="finite"):
        paired_day_test({START: float("nan")}, None, start=START, end=START, seed_key="x")


def test_a_paired_read_is_serialisable_for_the_challenger_log():
    p = paired(35, 0.002)
    assert PairedRead.model_validate_json(p.model_dump_json()) == p
    assert pd.Timestamp(p.window_start).date() == START


# ------------------------------------------------------------------------------------------------ confirm reads
def test_a_confirm_read_reports_buy_and_hold_beside_the_random_twin_and_neither_decides(tmp_path):
    """ADR 0106 decision 4, report only: the frozen cards, G3 and the confirm's own thresholds do not move."""
    from goldbot.research import promotion_confirm as PC
    from goldbot.research.benchmarks import BenchmarkLine, BuyHoldRead
    from goldbot.research.registry import TrialRegistry, config_hash
    from tests.research.test_promotion_confirm import item, run, units

    bh = BuyHoldRead(symbol="XAUUSD", start_utc=pd.Timestamp("2008-01-02", tz="UTC"),
                     end_utc=pd.Timestamp("2009-08-24", tz="UTC"), days=400, nights=560, entry_price=850.0,
                     exit_price=950.0, gross_ret=0.1176, cost_frac=0.0005, financing_frac=-0.03, net_ret=0.0871)
    a, b = item("a"), item("b")
    line = BenchmarkLine(random_twin_mean_r=0.02, buy_hold=bh)
    with_line = run(TrialRegistry(tmp_path / "1.jsonl"), [(a, units(0.3)), (b, units(0.0, seed=1))],
                    benchmarks={config_hash(a.config): line})
    plain = run(TrialRegistry(tmp_path / "2.jsonl"), [(a, units(0.3)), (b, units(0.0, seed=1))])
    pcs = [r["results"]["promotion_confirm"] for r in with_line]
    assert pcs[0]["benchmarks"]["buy_hold"]["net_ret"] == pytest.approx(0.0871)
    assert pcs[0]["benchmarks"]["random_twin_mean_r"] == 0.02 and "random twin" in pcs[0]["benchmarks_text"]
    assert pcs[1]["benchmarks"] == BenchmarkLine().model_dump(mode="json")      # not supplied: shown as such
    decided = ("passed", "p", "holm_rejected", "why")
    for r1, r2 in zip(pcs, (r["results"]["promotion_confirm"] for r in plain), strict=True):
        assert {k: r1[k] for k in decided} == {k: r2[k] for k in decided}
    assert PC.Q_PROMOTION == 0.05 and PC.MIN_FRESH_N_EFF == 200.0


def test_the_paired_resamples_match_the_cards_minimum():
    from goldbot.research.eval2.confirm import RESAMPLES_MIN
    from goldbot.research.promotion import PAIRED_RESAMPLES
    assert PAIRED_RESAMPLES == RESAMPLES_MIN
