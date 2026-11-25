"""Health on a demo account without limits (ADR 0097).

* `exit_latency:<account>` (trading-safety H1): the engine publishes the p99 of its loop-pass gaps with a position open
  (`order_send.published`). Above the 5 s SLO it FAILs on an unlimited account (exits-first and the duty ceiling are
  meant to hold it there); on any other it is a published baseline, never judged (code review C-M2); too few gaps,
  or an engine that does not publish them, is not judged.
* `demo_floor:<account>` (trading-safety M5, M4): WARN once equity is below half the closed-balance high-water mark,
  well before the floor; FAIL at the floor, where every entry is refused `demo_equity_floor` (learning has stopped:
  an alert, never silent). Only on an unlimited account.
* The loss caps are lifted there, so `check_loss_caps` raises no "LOSS CAP hit" for them."""
from __future__ import annotations

from goldbot.ops import health
from tests.test_health import by_name, engine, make_ctx

EXIT = "exit_latency:icm-demo"
FLOOR = "demo_floor:icm-demo"


def test_an_exit_wait_p99_over_the_slo_fails_on_an_unlimited_account_and_is_a_baseline_elsewhere(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 5, exit_wait_p99_s=7.5, exit_wait_max_s=12.0, exit_wait_n=400, exit_slo_s=5.0,
           demo_unlimited=True)
    c = by_name(health.check_exit_latency(ctx, "icm-demo"))[EXIT]
    assert c.status == "fail" and "7.5" in c.reason
    engine(tmp_path, 5, exit_wait_p99_s=7.5, exit_wait_max_s=12.0, exit_wait_n=400, exit_slo_s=5.0,
           demo_unlimited=False)
    c = by_name(health.check_exit_latency(ctx, "icm-demo"))[EXIT]
    assert c.status == "ok" and "baseline" in c.reason          # code review C-M2: published, never judged
    engine(tmp_path, 5, exit_wait_p99_s=1.2, exit_wait_max_s=3.0, exit_wait_n=400, exit_slo_s=5.0,
           demo_unlimited=True)
    assert by_name(health.check_exit_latency(ctx, "icm-demo"))[EXIT].status == "ok"


def test_too_few_gaps_or_an_engine_that_does_not_publish_them_is_not_judged(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 5, exit_wait_p99_s=9.0, exit_wait_max_s=9.0, exit_wait_n=3, exit_slo_s=5.0, demo_unlimited=True)
    assert health.check_exit_latency(ctx, "icm-demo") == []
    engine(tmp_path, 5)
    assert health.check_exit_latency(ctx, "icm-demo") == []


def test_the_demo_floor_warns_at_half_the_mark_and_fails_at_the_floor(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 5, equity=9_000.0, balance_closed_hwm=10_000.0, demo_unlimited=True)
    assert by_name(health.check_demo_floor(ctx, "icm-demo"))[FLOOR].status == "ok"
    engine(tmp_path, 5, equity=4_500.0, balance_closed_hwm=10_000.0, demo_unlimited=True)
    assert by_name(health.check_demo_floor(ctx, "icm-demo"))[FLOOR].status == "warn"
    engine(tmp_path, 5, equity=1_900.0, balance_closed_hwm=10_000.0, demo_unlimited=True)
    c = by_name(health.check_demo_floor(ctx, "icm-demo"))[FLOOR]
    assert c.status == "fail" and "demo_equity_floor" in c.reason


def test_the_demo_floor_check_is_silent_on_an_account_without_the_flag(tmp_path) -> None:
    engine(tmp_path, 5, equity=1_000.0, balance_closed_hwm=10_000.0)
    assert health.check_demo_floor(make_ctx(tmp_path), "icm-demo") == []


def test_no_loss_cap_alert_on_an_unlimited_account(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 5, equity=4_000.0, day_start_equity=5_000.0, week_start_equity=5_000.0, demo_unlimited=True)
    assert health.check_loss_caps(ctx, "icm-demo") == []
    engine(tmp_path, 5, equity=4_000.0, day_start_equity=5_000.0, week_start_equity=5_000.0)
    assert by_name(health.check_loss_caps(ctx, "icm-demo"))["daily_cap:icm-demo"].status == "warn"
