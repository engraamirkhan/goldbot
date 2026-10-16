"""Live account view (owner request, ADR 0004): balance, currency, margin level, today's loss budget and the milestone
ladder, all from the engine's published state (engine_<account>.json) and the supervisor's, never from config."""
import math

import pytest

from goldbot.ops.account_view import (
    PENDING_BROKER_TERMS,
    loss_budget,
    margin_level_pct,
    milestones,
    usd_to_account,
)
from goldbot.risk.sizing import MIN_LOT_TOLERANCE

LIMITS = {"risk_per_trade": 0.005, "max_risk_per_trade": 0.01, "multiplier_bounds": [0.25, 1.5], "daily_cap": 0.02,
          "weekly_cap": 0.05}
LOT = {"contract_oz": 100.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50.0, "stops_level_points": 0.0}
ATR = {"15m": 10.90 / 1.5, "1h": 21.90 / 1.5, "4h": 43.50 / 1.5, "1d": 105.0 / 1.5}   # ADR 0004 section 2 stops


NOW = 1_800_000_000.0


def _engine(**over):
    e = {"account": "icm-demo", "equity": 990.0, "balance": 1000.0, "currency": "GBP", "margin": 0.0,
         "day_start_equity": 1000.0, "week_start_equity": 1000.0, "stage": "normal", "limits": dict(LIMITS),
         "price": 4150.0, "spread_points": 0.0, "ladder_atr": dict(ATR), "lot_terms": dict(LOT)}
    e.update(over)
    return e


def _sup(**over):
    s = {"daily_cap": 0.015, "combined_day_start_equity": 1000.0, "weekly_cap": 0.04,
         "combined_week_start_equity": 1000.0, "combined_equity": 990.0, "ts": NOW - 5}
    s.update(over)
    return s


def _budget(e, s, engines=None):
    return loss_budget(e, s, [e] if engines is None else engines, now=NOW)


# ---------------------------------------------------------------------------------------------- margin and currency
def test_margin_level_is_equity_over_margin_in_percent_and_none_without_margin_in_use():
    assert margin_level_pct(_engine(equity=1000.0, margin=156.0)) == pytest.approx(641.03, abs=0.01)
    assert margin_level_pct(_engine(margin=0.0)) is None
    assert margin_level_pct({"equity": 1000.0}) is None          # an engine from before balance was published


def test_the_usd_factor_is_one_on_a_usd_account_and_only_the_engines_own_factor_otherwise():
    assert usd_to_account(_engine(currency="USD")) == 1.0
    assert usd_to_account(_engine(currency="GBP")) is None                      # never guessed from a rate table
    assert usd_to_account(_engine(currency="GBP", usd_to_account=0.75)) == 0.75
    for bad in (0, -1, float("nan"), "x", None):
        assert usd_to_account(_engine(currency="GBP", usd_to_account=bad)) is None
    assert usd_to_account(_engine(currency=None)) is None


# ---------------------------------------------------------------------------------------------- loss budget
def test_at_1000_the_combined_one_and_a_half_percent_cap_binds_before_the_account_cap():
    b = _budget(_engine(), _sup())
    assert b is not None
    caps = {c.name: c for c in b.caps}
    assert caps["account 2.0% daily cap"].limit == pytest.approx(20.0)
    assert caps["combined 1.5% daily cap"].limit == pytest.approx(15.0)
    assert caps["account 5.0% weekly cap"].limit == pytest.approx(50.0)
    assert caps["combined 4.0% weekly cap"].limit == pytest.approx(40.0)
    assert (b.limit, b.used, b.remaining) == pytest.approx((15.0, 10.0, 5.0))
    assert b.binding == "combined 1.5% daily cap"
    assert b.binding_label == "all accounts together (1.5% of today's start)"
    assert b.period == "today"
    assert any("BACKLOG 29" in n for n in b.notes)                # the absolute limit is not published yet


def test_a_bad_week_makes_the_weekly_cap_bind_even_on_a_quiet_day():
    e = _engine(equity=962.0, day_start_equity=965.0, week_start_equity=1000.0)   # £3 today, £38 this week
    b = _budget(e, _sup(combined_day_start_equity=965.0, combined_equity=962.0))
    assert b is not None
    assert b.binding == "combined 4.0% weekly cap" and b.period == "this week"
    assert b.remaining == pytest.approx(2.0) and b.used == pytest.approx(38.0)
    assert b.binding_label == "all accounts together this week (4.0% of the week's start)"


def test_the_absolute_limit_reserves_the_next_trades_stop_once_the_engine_publishes_both():
    lim = {**LIMITS, "daily_loss_limit_abs": 50.0, "min_lot_risk_account": 8.2}
    e = _engine(equity=4960.0, balance=5000.0, day_start_equity=5000.0, week_start_equity=5000.0, limits=lim)
    b = _budget(e, _sup(combined_day_start_equity=5000.0, combined_week_start_equity=5000.0, combined_equity=4960.0))
    assert b is not None
    assert b.binding == "absolute daily limit"
    assert (b.limit, b.used, b.remaining) == pytest.approx((50.0, 40.0, 1.8))   # 50 - 40 - 8.20 for the next stop
    assert "includes the smallest trade's stop" in b.binding_label
    assert not any("BACKLOG 29" in n for n in b.notes)
    spent = _engine(equity=4955.0, balance=5000.0, day_start_equity=5000.0, week_start_equity=5000.0, limits=lim)
    b = _budget(spent, _sup(combined_day_start_equity=5000.0, combined_week_start_equity=5000.0, combined_equity=4955.0))
    assert b is not None and b.remaining == 0.0                   # £5 left < £8.20 for the smallest trade


def test_the_absolute_limit_without_the_next_trades_risk_counts_the_loss_so_far_only():
    lim = {**LIMITS, "daily_loss_limit_abs": 50.0}
    e = _engine(equity=4960.0, balance=5000.0, day_start_equity=5000.0, week_start_equity=5000.0, limits=lim)
    b = _budget(e, _sup(combined_day_start_equity=5000.0, combined_week_start_equity=5000.0, combined_equity=4960.0))
    assert b is not None and b.binding == "absolute daily limit" and b.remaining == pytest.approx(10.0)
    assert "smallest trade" not in b.binding_label


def test_todays_gains_add_room_before_the_cap_and_an_exhausted_budget_has_none_left():
    up = _engine(equity=1012.0)                                   # the cap is on the loss from today's start
    b = _budget(up, _sup(combined_equity=1012.0))
    assert b is not None and b.used == 0.0 and b.remaining == pytest.approx(27.0) and b.limit == pytest.approx(15.0)
    down = _engine(equity=975.0)
    b = _budget(down, _sup(combined_equity=975.0))
    assert b is not None and b.remaining == 0.0 and b.used == pytest.approx(25.0)


MIXED = "The accounts report different currencies (GBP, USD), so the combined loss cap can't be checked. "
AMBER_TAIL = "Each account's own loss limit still applies; exits always run."  # ui-ux-designer 2026-10-16


def test_mixed_currencies_mean_the_budget_cant_be_checked_in_amber_never_the_roomier_account_cap():
    """BACKLOG 61 (UX review 2026-10-16): with a GBP and a USD account the combined caps are left out (pounds are never
    added to dollars), so the account's own 2% cap alone would be the roomier figure. The budget can't be checked:
    amber while the supervisor does not halt on it; the account caps stay listed for the entry status's blocks."""
    other = _engine(account="vantage-demo", currency="USD", equity=10_000.0, day_start_equity=10_000.0)
    b = _budget(_engine(), _sup(combined_day_start_equity=11_000.0, combined_equity=10_990.0), [_engine(), other])
    assert b is not None
    assert (b.unknown, b.unknown_paused, b.unknown_mixed) == (MIXED + AMBER_TAIL, False, True)
    assert b.cant_check_reason == "the accounts report different currencies"
    assert [c.name for c in b.caps] == ["account 2.0% daily cap", "account 5.0% weekly cap"]
    assert (b.binding, b.remaining) == ("account 2.0% daily cap", pytest.approx(10.0))   # entry status: unchanged


def test_mixed_currencies_read_red_when_the_supervisor_halts_entries_on_them():
    """`mixed_account_currencies` halts every account's entries (supervisor.py): the same "can't check", in red."""
    other = _engine(account="vantage-demo", currency="USD")
    halt = _sup(halt=True, reasons=["mixed_account_currencies"])
    b = _budget(_engine(), halt, [_engine(), other])
    assert b is not None
    assert (b.unknown, b.unknown_paused, b.unknown_mixed) == (MIXED + "New entries are paused; exits always run.",
                                                              True, True)
    assert b.cant_check_reason == "the accounts report different currencies; new entries paused"
    assert [c.name for c in b.caps] == ["account 2.0% daily cap", "account 5.0% weekly cap"]


def test_a_supervisor_halting_on_mixed_currencies_is_believed_even_when_this_view_sees_one_currency():
    """Its combined figures then add pounds to dollars: never shown, whatever engine files the dashboard can read."""
    halt = _sup(halt=True, reasons=["mixed_account_currencies"], currencies={"icm-demo": "GBP", "vantage-demo": "USD"})
    b = _budget(_engine(), halt)
    assert b is not None and b.unknown == MIXED + "New entries are paused; exits always run." and b.unknown_paused
    assert not any(c.name.startswith("combined") for c in b.caps)


def test_an_account_that_does_not_report_its_currency_next_to_others_reads_as_mixed():
    b = _budget(_engine(), _sup(), [_engine(), _engine(account="vantage-demo", currency=None)])
    assert b is not None and b.unknown_mixed and b.unknown is not None
    assert b.unknown.startswith("The accounts report different currencies (GBP, one not reported)")


def test_a_stale_engine_file_in_another_currency_no_longer_holds_the_budget_at_cant_check():
    """BACKLOG 61 L1 (BACKLOG 65 a): the currency check counts only running engines, the supervisor's own rule
    (supervisor.py `evaluate`: "a file left by an engine that no longer runs must not halt the book"), so a removed USD
    engine's leftover file does not keep a single-currency account amber. A running one still does."""
    gone = _engine(account="vantage-demo", currency="USD", ts=NOW - 600)
    b = _budget(_engine(), _sup(), [_engine(), gone])
    assert b is not None and (b.unknown, b.unknown_mixed) == (None, False)
    assert any(c.name == "combined 1.5% daily cap" for c in b.caps)
    running = _engine(account="vantage-demo", currency="USD", ts=NOW - 5)
    b = _budget(_engine(), _sup(), [_engine(), running])
    assert b is not None and b.unknown_mixed and not b.unknown_paused
    no_ts = _engine(account="vantage-demo", currency="USD")          # an unstamped file is never assumed gone
    b = _budget(_engine(), _sup(), [_engine(), no_ts])
    assert b is not None and b.unknown_mixed


def test_mixed_currencies_during_a_supervisor_halt_for_another_reason_read_red_not_amber():
    """BACKLOG 61 L2 (BACKLOG 65 a): any supervisor halt pauses every account's new entries
    (`Supervisor.engine_should_halt`), so mixed currencies seen during a combined-cap halt are "paused" (red)."""
    other = _engine(account="vantage-demo", currency="USD", ts=NOW - 5)
    halt = _sup(halt=True, reasons=["combined_daily_cap"])
    b = _budget(_engine(), halt, [_engine(), other])
    assert b is not None
    assert (b.unknown, b.unknown_paused, b.unknown_mixed) == (MIXED + "New entries are paused; exits always run.",
                                                              True, True)
    assert b.cant_check == "mixed_currencies_paused"


PAUSED = "New entries are paused; exits always run."
CANT_CHECK = "so the combined loss cap can't be checked. " + PAUSED
STALE = "The safety supervisor hasn't reported for over a minute, " + CANT_CHECK
MISSING = "The safety supervisor's report is missing or unreadable, " + CANT_CHECK
FUTURE = "The safety supervisor's report is dated ahead of this server's clock (check the server time), " + CANT_CHECK


def test_a_rejected_or_capless_supervisor_report_means_the_budget_cant_be_checked_with_the_reason():
    """The account's 2% (20) alone is roomier than the combined 1.5% (15) the supervisor enforces (release manager,
    2026-10-16): the budget can't be checked, red when entries are paused on the same report, amber when not; the
    account caps stay listed for the entry status's spent-cap blocks. The reason matches `engine_should_halt`: no
    `ts` is stale, a `ts` that is not a number is unreadable."""
    no_ts = {k: v for k, v in _sup().items() if k != "ts"}
    for sup, why in [(_sup(ts=NOW - 600), STALE), (no_ts, STALE), ({}, MISSING), (_sup(ts=None), MISSING),
                     (_sup(ts="junk"), MISSING), (_sup(ts=NOW + 600), FUTURE)]:
        b = _budget(_engine(), sup)
        assert b is not None and (b.unknown, b.unknown_paused) == (why, True), sup
        assert b.cant_check_reason == "supervisor not reporting; new entries paused"
        assert [c.name for c in b.caps] == ["account 2.0% daily cap", "account 5.0% weekly cap"]
    halted = {"ts": NOW - 5, "halt": True, "reasons": ["supervisor_error"]}         # Supervisor.error_halt
    b = _budget(_engine(), halted)
    assert b is not None and b.unknown_paused
    assert b.unknown == "The safety supervisor has halted and isn't reporting the combined loss cap. " + PAUSED
    b = _budget(_engine(), _sup(combined_day_start_equity=0.0, halt=False))           # no engine summed yet
    assert b is not None and not b.unknown_paused
    assert b.unknown == ("The safety supervisor hasn't added up the accounts yet, so the combined loss cap isn't "
                         "shown. Exits always run.")
    assert b.cant_check_reason == "the supervisor hasn't added up the accounts yet"
    b = _budget(_engine(), _sup())
    assert b is not None and (b.unknown, b.unknown_paused, b.cant_check_reason) == (None, False, None)


def test_an_engine_file_without_limits_or_day_start_gives_no_budget_rather_than_a_guess():
    assert loss_budget({"account": "a", "equity": 1.0}, _sup(), [], now=NOW) is None
    assert loss_budget(_engine(day_start_equity=0), _sup(), [], now=NOW) is None


# ---------------------------------------------------------------------------------------------- milestone ladder
def test_a_usd_account_ladder_is_the_min_lot_risk_over_the_gates_tolerance_at_each_timeframe():
    e = _engine(currency="USD", equity=1330.0, balance=1330.0)
    m = milestones(e)
    assert m is not None and m.pending is None
    need = {r.timeframe: r.equity_needed for r in m.rungs}
    target = 0.005                                         # risk per trade x multiplier 1
    for tf, atr in ATR.items():
        stop = 1.5 * atr
        assert need[tf] == pytest.approx(0.01 * stop * 100 / (MIN_LOT_TOLERANCE * target))
    assert m.tradeable == []                               # $1,330 < $1,817 needed for 15m at today's gate rule
    n15 = need["15m"]
    assert n15 is not None and m.next_timeframe == "15m" and m.next_equity == pytest.approx(n15)
    assert m.progress == pytest.approx(1330.0 / n15)
    assert "at most 0.60% of equity at normal conviction" in m.rule      # the effective figure, not 1.2 x 0.50%
    assert " x 0.50%" not in m.rule


def test_fifty_percent_higher_atr_raises_every_threshold_by_fifty_percent():
    base = milestones(_engine(currency="USD"))
    wide = milestones(_engine(currency="USD", ladder_atr={k: 1.5 * v for k, v in ATR.items()}))
    assert base is not None and wide is not None
    for a, b in zip(base.rungs, wide.rungs):
        assert a.equity_needed is not None and b.equity_needed == pytest.approx(1.5 * a.equity_needed)


def test_a_gbp_account_without_the_engines_currency_factor_shows_pending_broker_terms_and_no_numbers():
    m = milestones(_engine(currency="GBP"))
    assert m is not None
    assert m.pending == PENDING_BROKER_TERMS
    assert all(r.equity_needed is None and r.reached is None for r in m.rungs)
    assert m.next_equity is None and m.progress is None and m.tradeable == []


def test_a_gbp_account_with_the_engines_factor_computes_the_ladder_in_pounds():
    usd = milestones(_engine(currency="USD"))
    gbp = milestones(_engine(currency="GBP", usd_to_account=1 / 1.33, equity=2000.0))
    assert usd is not None and gbp is not None and gbp.pending is None
    for a, b in zip(usd.rungs, gbp.rungs):
        assert a.equity_needed is not None and b.equity_needed == pytest.approx(a.equity_needed / 1.33)
    assert gbp.tradeable == ["15m"]                        # £1,366 needed; 1h needs £2,744
    assert gbp.next_timeframe == "1h"


def test_the_min_lot_exception_cap_replaces_the_tolerance_rule_once_the_engine_publishes_it():
    e = _engine(currency="USD", limits={**LIMITS, "min_lot_risk_cap": 0.01})
    m = milestones(e)
    assert m is not None
    need = {r.timeframe: r.equity_needed for r in m.rungs}
    assert need["15m"] == pytest.approx(0.01 * 10.90 * 100 / 0.01)
    assert "1.00%" in m.rule


def test_the_eight_percent_stage_raises_every_threshold():
    normal = milestones(_engine(currency="USD"))
    down = milestones(_engine(currency="USD", stage="size_down"))
    assert normal is not None and down is not None
    for a, b in zip(normal.rungs, down.rungs):
        assert a.equity_needed is not None and b.equity_needed is not None and b.equity_needed > a.equity_needed
    assert "reduced (8% size-down stage)" in down.rule and "halved" not in down.rule
    capped = milestones(_engine(currency="USD", stage="size_down", limits={**LIMITS, "min_lot_risk_cap": 0.01}))
    assert capped is not None and "halved" in capped.rule and "0.50%" in capped.rule


def test_a_timeframe_without_enough_bars_for_its_atr_is_pending_and_the_others_still_compute():
    m = milestones(_engine(currency="USD", ladder_atr={**ATR, "1d": None}))
    assert m is not None
    rung = {r.timeframe: r for r in m.rungs}
    assert rung["1d"].equity_needed is None and rung["1d"].pending is not None
    assert rung["4h"].equity_needed is not None and math.isfinite(rung["4h"].equity_needed)


def test_no_price_or_lot_terms_from_the_engine_means_pending_never_the_design_defaults():
    assert milestones(_engine(price=None)).pending is not None              # type: ignore[union-attr]
    assert milestones(_engine(currency="USD", lot_terms=None)).pending == PENDING_BROKER_TERMS  # type: ignore[union-attr]
    assert milestones({"account": "a", "equity": 1.0}) is None              # older engine file: nothing to show


def test_reaching_the_top_rung_leaves_no_next_milestone():
    m = milestones(_engine(currency="USD", equity=1_000_000.0))
    assert m is not None
    assert m.tradeable == ["15m", "1h", "4h", "1d"] and m.next_timeframe is None and m.progress is None
