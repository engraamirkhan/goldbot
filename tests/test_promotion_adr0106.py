"""ADR 0106 decision 6 (challenger beats the live model 30-60 days after costs) and decision 2 (as-if real-money
sizing) in the champion/challenger promotion gates (goldbot/research/promotion.py), as ruled by the quant on #490
(P1-P4: O'Brien-Fleming looks, block length, two as-if accounts, the challenger as a procedure)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest
from scipy import optimize, stats

from goldbot.research.as_if_sizing import AsIfReport, AsIfTrade
from goldbot.research.promotion import (
    LOOK_DAYS,
    MAX_PAIRED_DAYS,
    MIN_PAIRED_DAYS,
    MIN_SHADOW_SHARPE,
    OBF_ALPHA,
    OBF_C,
    PAIRED_ALPHA,
    PAIRED_READ,
    PairedRead,
    PerfStats,
    book_first_look,
    evaluate_promotion,
    mean_episode_days,
    paired_day_test,
    session_days,
)

START = date(2026, 3, 2)                       # a Monday
START_UTC = pd.Timestamp("2026-03-01 22:00", tz="UTC")     # the opening of session day 2026-03-02
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


def book(daily: dict[date, float], *, sha: str = "c0ffee1", hold: int = 1, every: int = 2,
         equity: float = 1250.0, start: pd.Timestamp = START_UTC) -> AsIfReport:
    """An as-if account opened at `start` with this daily P&L and one sized trade held `hold` session days on every
    `every`-th day (its holding episodes), each tagged with the scoring sha."""
    ds = sorted(daily)
    trades = [AsIfTrade(index=i, status="sized", entry_day=d, day=ds[min(k + hold - 1, len(ds) - 1)], lots=0.01,
                        notional_usd=200_000.0, scoring_sha=sha)
              for i, (k, d) in enumerate((k, d) for k, d in enumerate(ds) if k % every == 0)]
    return AsIfReport(equity_start=equity, start_utc=start, equity_end=equity, n_trades=len(trades),
                      n_sized=len(trades), n_sized_days=len(trades), below_min_lot=0, excluded={}, breaches=[],
                      worst_day_loss=0.0, max_drawdown=0.0, worst_trade_loss=0.0, overshoots=0, daily=daily,
                      trades=trades, shas_by_day={t.entry_day: [sha] for t in trades})


def series(n: int, mean: float, sd: float, seed: int) -> dict[date, float]:
    rng = np.random.default_rng(seed)
    return dict(zip(days(n), (mean + sd * rng.standard_normal(n)).tolist(), strict=True))


def paired(n: int, edge: float, *, seed: int = 1, live: bool = True) -> PairedRead:
    ch = book(series(n, edge, 0.004, seed))
    lv = book(series(n, 0.0, 0.004, seed + 100), sha="11fe000") if live else None
    return paired_day_test(ch, lv, procedure=f"proc-{seed}", end=days(n)[-1])


def test_the_adr_0106_thresholds():
    assert MIN_SHADOW_SHARPE == 1.0                # goal of record: "Live Sharpe ratio above 1.0" (was 0.8)
    assert (MIN_PAIRED_DAYS, MAX_PAIRED_DAYS) == (30, 60) and LOOK_DAYS == (30, 35, 40, 45, 50, 55, 60)
    assert PAIRED_ALPHA == 0.05


def test_the_obrien_fleming_table_is_pinned_and_holds_the_overall_level():
    """P1: alpha_k = 1 - Phi(c / sqrt(n_k / 60)), c = 1.8117. The exact level of the pinned c (7-variate normal,
    Brownian increments) is at most 0.05; a fixed-seed simulation of 2,000,000 Brownian paths reproduces the exact c
    to 1e-3, and the pinned c sits within 3e-3 above it (conservative: the quant's 400,000-path estimate)."""
    t = np.array(LOOK_DAYS) / 60
    for n, a in OBF_ALPHA.items():
        assert a == pytest.approx(1 - stats.norm.cdf(OBF_C / np.sqrt(n / 60)), abs=6e-6)
    cov = np.sqrt(np.minimum.outer(t, t) / np.maximum.outer(t, t))
    mvn = stats.multivariate_normal(mean=np.zeros(len(t)), cov=cov)

    def level(c: float) -> float:
        return 1 - float(mvn.cdf(c / np.sqrt(t)))

    exact = optimize.brentq(lambda c: level(c) - 0.05, 1.5, 2.5, xtol=1e-7)
    assert 0.0495 <= level(OBF_C) <= 0.05
    rng = np.random.default_rng(0)
    s = np.cumsum(rng.standard_normal((2_000_000, len(t))) * np.sqrt(np.diff(np.concatenate([[0.0], t]))), axis=1)
    sim = float(np.quantile(s.max(axis=1), 0.95))     # reject at look k when S(t_k) > c, i.e. Z_k > c / sqrt(t_k)
    assert sim == pytest.approx(exact, abs=1e-3)
    assert 0 <= OBF_C - exact < 3e-3


def test_session_days_are_the_weekdays_of_the_window():
    d = session_days(START, date(2026, 3, 15))
    assert len(d) == 10 and all(x.weekday() < 5 for x in d)


def test_paired_days_count_every_session_day_with_zero_for_a_book_that_did_not_trade():
    ch = book({days(40)[0]: 0.01, days(40)[39]: 0.01})
    p = paired_day_test(ch, book({}), procedure="z", end=days(40)[-1])
    assert p.n_days == 40 and p.mean_diff == pytest.approx(0.02 / 40)


def test_a_read_off_a_look_day_is_waiting_with_no_bound():
    p = paired(33, 0.01)
    assert p.status == "waiting" and p.lower_bound is None and p.alpha_look is None
    d = evaluate_promotion(BT, SHADOW, BT, paired=p, as_if=as_if())
    assert not d.promote and not d.ready and "paired_vs_live" in d.failed
    look = paired(35, 0.01)
    assert look.status == "look" and look.alpha_look == OBF_ALPHA[35] and look.lower_bound is not None


def test_a_window_longer_than_sixty_paired_days_is_read_at_sixty():
    p = paired(80, 0.002)
    assert p.n_days == MAX_PAIRED_DAYS and p.capped and p.window_end == days(MAX_PAIRED_DAYS)[-1]
    assert p.status == "look" and p.alpha_look == OBF_ALPHA[60]


def test_the_bound_is_reproducible_and_below_the_mean():
    a, b = paired(45, 0.002, seed=3), paired(45, 0.002, seed=3)
    assert a.lower_bound == b.lower_bound and a.lower_bound is not None and a.lower_bound < a.mean_diff


def test_the_mean_block_is_one_and_a_half_times_the_longer_mean_holding_episode_at_least_five():
    assert paired(40, 0.002).mean_block == 5.0                  # one-day episodes
    ch = book(series(40, 0.002, 0.004, 1), hold=4, every=8)    # four-day episodes: ceil(1.5 x 4) = 6
    assert mean_episode_days(ch, days(40)) == pytest.approx(4.0)
    p = paired_day_test(ch, book(series(40, 0.0, 0.004, 2)), procedure="b", end=days(40)[-1])
    assert p.mean_episode_days == pytest.approx(4.0) and p.mean_block == 6.0


def test_challenger_and_live_are_two_accounts_with_the_same_start_and_equity():
    ch = book(series(40, 0.002, 0.004, 1))
    with pytest.raises(ValueError, match="same start"):
        paired_day_test(ch, book(series(40, 0.0, 0.004, 2), equity=5000.0), procedure="x", end=days(40)[-1])
    with pytest.raises(ValueError, match="same start"):
        paired_day_test(ch, book(series(40, 0.0, 0.004, 2), start=START_UTC + pd.Timedelta(days=1)),
                        procedure="x", end=days(40)[-1])


def test_every_challenger_row_carries_its_scoring_sha_and_the_live_shas_are_logged_per_day():
    unsigned = book(series(40, 0.002, 0.004, 1), sha="")
    with pytest.raises(ValueError, match="scoring sha"):
        paired_day_test(unsigned, None, procedure="x", end=days(40)[-1])
    p = paired(40, 0.002)
    assert p.challenger_shas == ["c0ffee1"] and p.live_shas_by_day[days(40)[0]] == ["11fe000"]


def test_the_paired_read_is_one_registry_trial_booked_once_at_the_first_look(tmp_path):
    from goldbot.research.registry import TrialRegistry
    reg = TrialRegistry(tmp_path / "r.jsonl")
    assert book_first_look(reg, paired(33, 0.002), agent_id="pd", feature_version="f") is None     # waiting
    k0 = reg.n_trials
    row = book_first_look(reg, paired(35, 0.002), agent_id="pd", feature_version="f")
    assert row is not None and row["status"] == PAIRED_READ and reg.n_trials == k0 + 1
    assert book_first_look(reg, paired(40, 0.002), agent_id="pd", feature_version="f") is None     # K once
    assert reg.n_trials == k0 + 1


def test_fewer_than_thirty_paired_days_is_not_ready_whatever_the_edge():
    p = paired(29, 0.01)
    assert p.status == "not_ready"
    d = evaluate_promotion(BT, SHADOW, BT, paired=p, as_if=as_if())
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
        paired_day_test(book({START: float("nan")}), None, procedure="x", end=START)


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
