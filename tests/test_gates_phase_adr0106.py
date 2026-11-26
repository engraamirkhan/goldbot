"""ADR 0106 goals of record as paper -> tiny-live gate items (goldbot/ops/gates_phase.py): beats buy-and-hold after
costs and financing over the paper window, as-if Sharpe above 1.0, costs under a third of gross, and no breach of the
real-money limits when re-sized. The existing items, the 182-day paper rule and the frozen thresholds do not move."""
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import GateSettings
from goldbot.instruments import GOLD
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade, evaluate_gates
from tests.test_phase_gates import (
    ADV,
    BUY_HOLD,
    CFG,
    NOW,
    PAPER_START,
    SETTINGS,
    full_state,
    item_of,
    report,
    trades_over,
    write_trades,
)

NEW = ("as_if_real_money", "paper_sharpe", "beats_buy_and_hold", "paper_cost_share")
BEFORE = ["previous_gate", "paper_trades", "paper_days", "paper_expectancy", "fills", "chaos_drill", "no_incident",
          "paper_gross_share"]


def paper_gate(rep: gates_phase.GateReport) -> gates_phase.GateStatus:
    return next(g for g in rep.gates if g.gate == "paper_to_tiny_live")


def with_paper(state, paper: list[ClosedTrade]) -> None:
    live = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "live"]
    write_trades(state, paper + live)


def test_the_paper_gate_keeps_its_items_and_adds_the_four_adr_0106_items_as_required(tmp_path):
    state, trials = full_state(tmp_path)
    g = paper_gate(report(state, trials))
    names = [i.name for i in g.items]
    assert names[:len(BEFORE)] == BEFORE and set(NEW) <= set(names)
    assert all(i.required and i.met for i in g.items if i.name in NEW), [i for i in g.items if i.name in NEW]
    assert g.met


def test_the_settings_can_only_be_stricter_than_the_adr_0106_goals():
    assert CFG.paper_min_days == 182 and CFG.paper_min_sharpe == 1.0
    assert CFG.paper_max_cost_share == pytest.approx(1 / 3)
    for bad in ({"paper_min_sharpe": 0.99}, {"paper_max_cost_share": 0.34}, {"paper_min_days": 181}):
        with pytest.raises(ValidationError):
            GateSettings(**bad)
    assert GateSettings(paper_min_sharpe=1.5, paper_max_cost_share=0.25, paper_min_days=200).paper_min_sharpe == 1.5
    assert "as_if_equity_usd" not in GateSettings.PROPOSED      # a simulation parameter, not an owner threshold


# ------------------------------------------------------------------------------------------------ buy-and-hold
def test_the_paper_book_must_beat_buy_and_hold_after_costs_and_financing(tmp_path):
    state, trials = full_state(tmp_path)
    it = item_of(report(state, trials), "paper_to_tiny_live", "beats_buy_and_hold")
    assert it.met and "buy-and-hold XAUUSD" in it.evidence and "+24.02%" in it.evidence
    strong = {GOLD: BUY_HOLD[GOLD].model_copy(update={"net_ret": 0.2403})}
    rep = evaluate_gates(state, SETTINGS, NOW, trials_path=trials, buy_hold=strong, adv_usd=ADV)
    assert not item_of(rep, "paper_to_tiny_live", "beats_buy_and_hold").met
    assert not paper_gate(rep).met


def test_without_a_buy_and_hold_read_of_every_traded_instrument_the_item_is_not_met(tmp_path):
    state, trials = full_state(tmp_path)
    rep = evaluate_gates(state, SETTINGS, NOW, trials_path=trials, adv_usd=ADV)
    it = item_of(rep, "paper_to_tiny_live", "beats_buy_and_hold")
    assert not it.met and "no buy-and-hold read for XAUUSD" in it.evidence


def test_buy_and_hold_is_judged_only_after_the_182_day_paper_minimum(tmp_path):
    state, trials = full_state(tmp_path)
    early = PAPER_START + pd.Timedelta(days=181)
    rep = evaluate_gates(state, SETTINGS, early, trials_path=trials, buy_hold=BUY_HOLD, adv_usd=ADV)
    it = item_of(rep, "paper_to_tiny_live", "beats_buy_and_hold")
    assert not it.met and "182" in it.need


# ------------------------------------------------------------------------------------------------ Sharpe
def test_the_paper_sharpe_is_on_as_if_daily_returns_and_must_exceed_one(tmp_path):
    state, trials = full_state(tmp_path)
    it = item_of(report(state, trials), "paper_to_tiny_live", "paper_sharpe")
    assert it.met and it.threshold == "paper_min_sharpe"
    # winners and losers of equal size in turn: an as-if Sharpe near 0
    with_paper(state, trades_over(150, [0.002 if i % 2 else -0.002 for i in range(150)], 790, 410))
    assert not item_of(report(state, trials), "paper_to_tiny_live", "paper_sharpe").met


# ------------------------------------------------------------------------------------------------ cost share
def test_costs_must_be_under_a_third_of_gross_profit(tmp_path):
    state, trials = full_state(tmp_path)
    it = item_of(report(state, trials), "paper_to_tiny_live", "paper_cost_share")
    # each trade: net 0.1001%, 1 bp commission and swap, 1 bp spread: 2 / 12.01 = 16.7% of gross
    assert it.met and "16.7%" in it.evidence and it.threshold == "paper_max_cost_share"
    costly = [t.model_copy(update={"exit_price": 2_000.0 * (1 + t.ret + 0.0006)}) for t in trades_over(150, 0.001001, 790, 410)]
    with_paper(state, costly)
    it = item_of(report(state, trials), "paper_to_tiny_live", "paper_cost_share")
    assert not it.met and "41.2%" in it.evidence             # 7 bp of 17.01 bp gross


def test_more_than_one_in_twenty_trades_without_a_measurable_cost_fails_the_item_closed(tmp_path):
    state, trials = full_state(tmp_path)
    paper = trades_over(150, 0.001001, 790, 410)
    blind = [t.model_copy(update={"decision_spread_points": None}) if i < 8 else t for i, t in enumerate(paper)]
    with_paper(state, blind)
    it = item_of(report(state, trials), "paper_to_tiny_live", "paper_cost_share")
    assert not it.met and "8 of 150" in it.evidence
    with_paper(state, blind[1:] + paper[:1])                   # 7 of 150: measured on the rest
    assert item_of(report(state, trials), "paper_to_tiny_live", "paper_cost_share").met


# ------------------------------------------------------------------------------------------------ as-if sizing
def test_as_if_sizing_must_size_the_minimum_paper_trades_without_a_breach(tmp_path):
    state, trials = full_state(tmp_path)
    it = item_of(report(state, trials), "paper_to_tiny_live", "as_if_real_money")
    assert it.met and it.threshold == "as_if_equity_usd" and it.progress is not None and it.progress.value == 150


def test_trades_below_the_minimum_lot_are_reported_and_not_counted(tmp_path):
    state, trials = full_state(tmp_path)
    small = SETTINGS.model_copy(update={"gates": CFG.model_copy(update={"as_if_equity_usd": 500.0})})
    rep = evaluate_gates(state, small, NOW, trials_path=trials, buy_hold=BUY_HOLD, adv_usd=ADV)
    it = item_of(rep, "paper_to_tiny_live", "as_if_real_money")
    assert not it.met and "below_min_lot 150" in it.evidence and it.progress is not None and it.progress.value == 0
    # never counted where the paper record's own count is: that item is unchanged
    assert item_of(rep, "paper_to_tiny_live", "paper_trades").met


def test_a_day_past_the_two_percent_stop_when_re_sized_fails_the_gate(tmp_path):
    state, trials = full_state(tmp_path)
    paper = trades_over(150, 0.001001, 790, 410)
    crash = [paper[-1].model_copy(update={"exit_utc": paper[-1].exit_utc + pd.Timedelta(hours=k), "r": -5.0})
             for k in (1, 2)]
    with_paper(state, paper + [c.model_copy(update={"entry_utc": c.exit_utc - pd.Timedelta(hours=1)}) for c in crash])
    it = item_of(report(state, trials), "paper_to_tiny_live", "as_if_real_money")
    assert not it.met and "daily_stop" in it.evidence


def test_unknown_reference_volume_refuses_every_trade_so_the_item_fails_closed(tmp_path):
    state, trials = full_state(tmp_path)
    rep = evaluate_gates(state, SETTINGS, NOW, trials_path=trials, buy_hold=BUY_HOLD)
    it = item_of(rep, "paper_to_tiny_live", "as_if_real_money")
    assert not it.met and "adv_unknown 150" in it.evidence


def test_buy_and_hold_is_not_met_when_nothing_could_be_sized_even_against_a_falling_market(tmp_path):
    state, trials = full_state(tmp_path)
    falling = {GOLD: BUY_HOLD[GOLD].model_copy(update={"net_ret": -0.2})}
    rep = evaluate_gates(state, SETTINGS, NOW, trials_path=trials, buy_hold=falling)     # no ADV: nothing sized
    assert not item_of(rep, "paper_to_tiny_live", "beats_buy_and_hold").met


def test_the_paper_sharpe_is_the_lower_of_naive_and_lo_2002_so_autocorrelation_cannot_inflate_it():
    import math
    from datetime import date

    import numpy as np

    from goldbot.research.promotion import session_days
    grid = session_days(date(2026, 1, 5), date(2026, 12, 31))
    rng = np.random.default_rng(4)
    e = rng.standard_normal(len(grid))
    x = np.convolve(e, np.ones(4) / 4, mode="same") * 0.002 + 0.0004    # positively autocorrelated (MA(3))
    daily = dict(zip(grid, x.tolist(), strict=True))
    naive = float(np.mean(x) / np.std(x, ddof=1) * math.sqrt(260))
    got = gates_phase.daily_sharpe(daily, grid[0], grid[-1])
    assert got is not None and got < naive - 0.5
    iid = dict(zip(grid, (rng.standard_normal(len(grid)) * 0.002 + 0.0004).tolist(), strict=True))
    y = np.array(list(iid.values()))
    got = gates_phase.daily_sharpe(iid, grid[0], grid[-1])
    assert got is not None and got <= float(np.mean(y) / np.std(y, ddof=1) * math.sqrt(260)) + 1e-12
    # a series the Lo formula cannot annualise (alternating signs: its denominator is not positive) fails closed
    alt = {d: (0.001 if i % 2 else -0.0005) for i, d in enumerate(grid)}
    assert gates_phase.daily_sharpe(alt, grid[0], grid[-1]) is None


# ------------------------------------------------------------------------------------------------ the rate (safety ruling)
def test_the_as_if_item_sizes_at_risk_per_trade_never_above_it(tmp_path):
    """Trading-safety ruling on #490: the required as-if read sizes at the stricter of ADR 0106's 1 % and
    `risk.risk_per_trade`, never above it."""
    state, trials = full_state(tmp_path)
    rep = report(state, trials)
    assert item_of(rep, "paper_to_tiny_live", "as_if_real_money").met
    assert f"sized at <= {min(0.01, SETTINGS.risk.risk_per_trade):.2%}" in \
        item_of(rep, "paper_to_tiny_live", "as_if_real_money").evidence
    paper = [t for t in gates_phase.load_closed_trades(state)[0] if t.mode == "demo"]
    inputs, _, _ = gates_phase.as_if_inputs(paper, gates_phase.CostImputer.from_settings(SETTINGS))
    from goldbot.research.as_if_sizing import AsIfLimits, as_if_real_money
    replay = as_if_real_money(inputs, equity=CFG.as_if_equity_usd, adv_usd=ADV,
                              limits=AsIfLimits.stricter(SETTINGS.risk))
    assert all(t.risk_frac <= SETTINGS.risk.risk_per_trade + 1e-12 for t in replay.trades if t.status == "sized")


def test_the_tiny_live_read_is_reported_and_never_decides_the_gate(tmp_path):
    state, trials = full_state(tmp_path)
    rep = report(state, trials)
    it = item_of(rep, "paper_to_tiny_live", "as_if_tiny_live")
    assert it.met and not it.required and "report only" in it.need
    assert f"at {SETTINGS.risk.risk_per_trade_tiny_live:.2%} risk" in it.evidence
    assert "0 of 150 trades could be sized" in it.evidence          # $1.25 at 0.1 % sizes no 0.01-lot $5 stop
    assert paper_gate(rep).met                                       # the gate is decided without it
