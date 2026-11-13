"""BACKLOG 90: the 20-day measured cost/model series that ADR 0031's frozen cost rule reads ("20-day measured
cost/model > 1.25 -> floors, 1.10-1.25 -> one step down"). Measured: each demo fill's spread at the fill, slippage
against the requested price (entry and every exit leg), commission and swap, USD per oz. Model: what the research labels
charge for the same trade (the 15m bar spread at entry, the cost table's extra cost, the model swap). None below the
pre-registered minimum of fills; the nightly job passes it, and it only ever tightens.

Quant review of 9903fb2d: the model is the cost the strategies were qualified under (the settings priors), never the
live cost table, which already holds the measured slippage; commission and swap are the record's own when present; live
fills count too; more than 20% unmeasured fills give None and a dq alert; the model bars are dukascopy's."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.execution.costs import settings_extra_cost_usd, settings_swap
from goldbot.ops import accounts as acc_mod
from goldbot.ops.cost_imputation import CostImputer
from goldbot.ops.cost_ratio import MIN_FILLS, WINDOW_RISK_DAYS, cost_ratio_20d, fill_costs, window_start
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.limits_job import limits_job
from goldbot.risk.adaptive import AdaptiveState, AdaptiveStore, Limits, day_start_ns
from goldbot.risk.limits_profile import RiskCeilings, RiskOverrides

S = load_settings()
DAY = date(2026, 11, 25)                                  # the decision day (a Wednesday)
SLOT = pd.Timestamp("2026-11-24 23:05", tz="UTC")         # the nightly slot that decides it
BAR_SPREAD = 0.30                                         # $/oz, the 15m bars' ask - bid
IMPUTER = CostImputer.from_settings(S)
COMM = 2 * S.costs.commission_per_lot_side_usd["icm"] / 100.0     # $/oz round trip, the icm prior (no cost table)
_seq = iter(range(1, 10**9))


def _fill(entry: pd.Timestamp, *, spread: float = 0.30, entry_slip: float = 0.05, exit_slip: float = 0.05,
          side: int = 1, account: str = "icm-demo", mode: str = "demo", symbol: str | None = None,
          hold: pd.Timedelta = pd.Timedelta(minutes=45), r: float = 1.0, **kw: Any) -> tuple[ClosedTrade, dict]:
    """One closed demo fill and its exit leg (adverse slippage positive)."""
    pid = next(_seq)
    req, out_req = 2400.0, 2400.0 + side * 5.0
    t = ClosedTrade(account_id=account, broker="icm", mode=mode, exit_utc=entry + hold, ret=r * 0.001, pnl=r * 5.0,  # type: ignore[arg-type]
                    equity_before=1_000.0, position_id=pid, side=side, lots=0.01, entry_utc=entry,
                    entry_price=req + side * entry_slip, exit_price=out_req - side * exit_slip, r=r, commission=COMM,
                    swap=0.0, decision_spread_points=round(spread / 0.01, 6), requested_price=req,
                    initial_sl=req - side * 5.0, model_version="v1", pnl_basis="net", ret_basis="pnl", symbol=symbol)
    t = t.model_copy(update=kw)
    leg = {"ts_utc": entry + hold, "position_id": pid, "side": side, "lots": 0.01, "kind": "close",
           "requested": out_req, "filled": out_req - side * exit_slip}
    return t, leg


def _bars(start: pd.Timestamp, end: pd.Timestamp, spread: float = BAR_SPREAD) -> pd.DataFrame:
    ts = pd.date_range(start, end, freq="15min", inclusive="left")
    return pd.DataFrame({"ts_utc": ts, "bid_close": 2400.0, "ask_close": 2400.0 + spread, "high": 2401.0,
                         "low": 2399.0, "close": 2400.0})


BARS = _bars(pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp(DAY, tz="UTC"))


def _ratio(fills: list[tuple[ClosedTrade, dict]], *, extra: float = 0.20, bars: pd.DataFrame = BARS,
           legs: pd.DataFrame | None = None) -> Any:
    trades = [t for t, _ in fills]
    frame = pd.DataFrame([leg for _, leg in fills]) if legs is None else legs
    return cost_ratio_20d(trades, "icm-demo", DAY, imputer=IMPUTER, legs=frame, bars=bars, model_extra_usd=extra,
                          model_swap=settings_swap(S), acct_per_usd=1.0)


def _weekday_entries(n: int, first: pd.Timestamp = pd.Timestamp("2026-10-28 10:00", tz="UTC")) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    d = first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += pd.Timedelta(days=1)
    return out


# ---------------------------------------------------------------------------------------------- the definition
def test_a_fills_measured_cost_is_spread_slippage_commission_and_swap_against_the_bar_spread_plus_extra_cost():
    t, leg = _fill(pd.Timestamp("2026-11-20 10:00", tz="UTC"), spread=0.40, entry_slip=0.07, exit_slip=0.03)
    start = day_start_ns(window_start(DAY))
    (fc,), unmeasured = fill_costs([t], "icm-demo", imputer=IMPUTER, legs=pd.DataFrame([leg]), bars=BARS,
                                   model_extra_usd=0.20, model_swap=settings_swap(S), start_ns=start,
                                   before_ns=day_start_ns(DAY), acct_per_usd=1.0)
    assert unmeasured == []
    assert fc.spread == pytest.approx(0.40) and fc.slippage == pytest.approx(0.10)
    assert fc.commission == pytest.approx(COMM) and fc.swap == 0.0
    assert fc.measured == pytest.approx(0.40 + 0.10 + COMM)
    assert fc.model == pytest.approx(BAR_SPREAD + 0.20)


def test_a_record_without_its_swap_pays_the_accounts_measured_rate_and_the_model_its_own_swap():
    t, leg = _fill(pd.Timestamp("2026-11-16 10:00", tz="UTC"), hold=pd.Timedelta(days=2), swap=None)   # Mon -> Wed
    swap = settings_swap(S)
    (fc,), _ = fill_costs([t], "icm-demo", imputer=IMPUTER, legs=pd.DataFrame([leg]), bars=BARS, model_extra_usd=0.20,
                          model_swap=swap, start_ns=0, before_ns=day_start_ns(DAY), acct_per_usd=1.0)
    from goldbot.ops.cost_imputation import paid_swap_per_oz
    paid = paid_swap_per_oz(swap, 1, t.entry_utc, t.exit_utc)  # type: ignore[arg-type]
    assert paid > 0 and fc.swap == pytest.approx(paid) and fc.model == pytest.approx(BAR_SPREAD + 0.20 + paid)


def test_commission_and_swap_are_the_records_own_in_usd_and_a_swap_credit_never_counts():
    t, leg = _fill(pd.Timestamp("2026-11-20 10:00", tz="UTC"), hold=pd.Timedelta(days=1), commission=0.24, swap=-0.40,
                   lots=0.02)                                      # account currency, 2 oz, 0.8 GBP per USD
    kw = {"imputer": IMPUTER, "legs": pd.DataFrame([leg]), "bars": BARS, "model_extra_usd": 0.20,
          "model_swap": settings_swap(S), "start_ns": 0, "before_ns": day_start_ns(DAY)}
    (fc,), _ = fill_costs([t], "icm-demo", acct_per_usd=0.8, **kw)  # type: ignore[arg-type]
    assert fc.commission == pytest.approx(0.24 / 0.8 / 2) and fc.swap == pytest.approx(0.40 / 0.8 / 2)
    (fc2,), _ = fill_costs([t.model_copy(update={"swap": 0.40})], "icm-demo", acct_per_usd=0.8, **kw)  # type: ignore[arg-type]
    assert fc2.swap == 0.0
    # no conversion known (a non-USD account without the engine's factor): the account's measured terms instead
    (fc3,), _ = fill_costs([t], "icm-demo", acct_per_usd=None, **kw)  # type: ignore[arg-type]
    assert fc3.commission == pytest.approx(COMM)


def test_the_window_is_the_last_20_risk_days_before_the_decision_day():
    start = window_start(DAY)
    days = [start + timedelta(days=i) for i in range((DAY - start).days)]
    assert WINDOW_RISK_DAYS == 20 and sum(d.weekday() < 5 for d in days) == 20 and start.weekday() < 5


def test_only_the_accounts_gold_fills_demo_and_live_closed_in_the_window_count():
    entries = _weekday_entries(MIN_FILLS)
    fills = [_fill(e, mode="live" if i == 1 else "demo") for i, e in enumerate(entries)]
    others = [_fill(entries[0], account="vantage-demo"), _fill(entries[2], symbol="BTCUSD"),
              _fill(pd.Timestamp(window_start(DAY), tz="UTC") - pd.Timedelta(hours=2)),     # closed before the window
              _fill(pd.Timestamp(DAY, tz="UTC") + pd.Timedelta(hours=1))]                   # on the decision day
    got = _ratio([*fills, *others])
    assert got.fills == MIN_FILLS and got.unmeasured == 0 and got.ratio is not None


def test_the_ratio_is_none_below_the_pre_registered_minimum_of_fills():
    fills = [_fill(e) for e in _weekday_entries(MIN_FILLS - 1)]
    got = _ratio(fills)
    assert got.fills == MIN_FILLS - 1 and got.ratio is None
    assert _ratio([]).ratio is None


def test_the_ratio_pools_every_fill_measured_over_model():
    fills = [_fill(e, spread=0.42, entry_slip=0.05, exit_slip=0.05) for e in _weekday_entries(MIN_FILLS)]
    got = _ratio(fills, extra=0.20)
    assert got.ratio == pytest.approx((0.42 + 0.10 + COMM) / (BAR_SPREAD + 0.20))


def test_price_improvement_offsets_slippage_but_pooled_slippage_never_reads_below_zero():
    entries = _weekday_entries(MIN_FILLS)
    mixed = [_fill(e, entry_slip=0.10 if i % 2 else -0.06, exit_slip=0.0) for i, e in enumerate(entries)]
    got = _ratio(mixed)
    assert got.ratio == pytest.approx((0.30 + 0.02 + COMM) / (BAR_SPREAD + 0.20))
    better = [_fill(e, entry_slip=-0.10, exit_slip=-0.10) for e in entries]
    assert _ratio(better).ratio == pytest.approx((0.30 + COMM) / (BAR_SPREAD + 0.20))


@pytest.mark.parametrize("gap", ["requested_price", "decision_spread_points", "exit_leg", "bar", "side",
                                 "leg_requested_nan"])
def test_a_fill_missing_any_part_of_its_measure_is_counted_unmeasured_never_guessed(gap):
    entries = _weekday_entries(MIN_FILLS)
    fills = [_fill(e) for e in entries]
    t, leg = fills[0]
    bars = BARS
    legs = pd.DataFrame([lg for _, lg in fills])
    if gap in ("requested_price", "decision_spread_points", "side"):
        fills[0] = (t.model_copy(update={gap: None}), leg)
    elif gap == "exit_leg":
        legs = legs[legs["position_id"] != t.position_id]
    elif gap == "leg_requested_nan":
        legs.loc[legs["position_id"] == t.position_id, "requested"] = np.nan
    else:                                                    # no 15m bar closed within the hour before the entry
        ts = pd.DatetimeIndex(BARS["ts_utc"])
        bars = BARS[(ts < t.entry_utc - pd.Timedelta(hours=3)) | (ts > t.entry_utc)]  # type: ignore[operator]
    got = _ratio(fills, legs=legs, bars=bars)
    assert got.unmeasured == 1 and got.fills == MIN_FILLS - 1 and got.ratio is None


def test_more_than_a_fifth_of_fills_unmeasured_gives_none_and_says_why():
    entries = [*_weekday_entries(MIN_FILLS), *(e + pd.Timedelta(hours=4) for e in _weekday_entries(6))]
    fills = [_fill(e) for e in entries]
    legs = pd.DataFrame([lg for _, lg in fills[6:]])              # 6 of 26 (23%) without their exit leg (stop gaps)
    got = _ratio(fills, legs=legs)
    assert got.fills == MIN_FILLS and got.unmeasured == 6 and got.ratio is None and got.reason == "unmeasured_share"
    legs5 = pd.DataFrame([lg for _, lg in fills[5:]])             # 5 of 26 (19%): measured
    assert _ratio(fills, legs=legs5).ratio is not None


@pytest.mark.parametrize("bad", ["spread_nan", "price_nan", "leg_inf"])
def test_a_non_finite_part_makes_the_fill_unmeasured_never_a_nan_ratio(bad):
    fills = [_fill(e) for e in [*_weekday_entries(MIN_FILLS), _weekday_entries(1)[0] + pd.Timedelta(hours=4)]]
    t, leg = fills[0]
    if bad == "spread_nan":
        fills[0] = (t.model_copy(update={"decision_spread_points": float("nan")}), leg)
    elif bad == "price_nan":
        fills[0] = (t.model_copy(update={"entry_price": float("nan")}), leg)
    else:
        fills[0] = (t, {**leg, "filled": float("inf")})
    got = _ratio(fills)
    assert got.unmeasured == 1 and got.fills == MIN_FILLS and got.ratio is not None and np.isfinite(got.ratio)


def test_a_zero_model_cost_gives_none():
    fills = [_fill(e) for e in _weekday_entries(MIN_FILLS)]
    got = _ratio(fills, extra=0.0, bars=_bars(pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp(DAY, tz="UTC"), 0.0))
    assert got.fills == MIN_FILLS and got.ratio is None and got.reason == "model_cost_zero"


def test_the_model_bar_is_the_last_one_closed_at_the_entry_never_a_later_one():
    entry = pd.Timestamp("2026-11-20 10:05", tz="UTC")
    bars = BARS.copy()
    ts = pd.DatetimeIndex(bars["ts_utc"])
    bars.loc[ts == pd.Timestamp("2026-11-20 09:45", tz="UTC"), "ask_close"] = 2400.50   # closed 10:00: the one
    bars.loc[ts == pd.Timestamp("2026-11-20 10:00", tz="UTC"), "ask_close"] = 2409.00   # still open at 10:05
    t, leg = _fill(entry)
    (fc,), _ = fill_costs([t], "icm-demo", imputer=IMPUTER, legs=pd.DataFrame([leg]), bars=bars, model_extra_usd=0.0,
                          model_swap=settings_swap(S), start_ns=0, before_ns=day_start_ns(DAY), acct_per_usd=1.0)
    assert fc.model == pytest.approx(0.50)


# ---------------------------------------------------------------------------------------------- through the job
OVERRIDES = RiskOverrides(profile="demo-2026-10-18", ceilings=RiskCeilings(daily=0.05, weekly=0.10,
                                                                          min_lot_risk_cap=0.01))
FREEZE = pd.Timestamp("2024-06-01", tz="UTC")


@pytest.fixture
def phase_file(tmp_path, monkeypatch):
    f = tmp_path / "phase_state.json"
    monkeypatch.setattr(acc_mod, "PHASE_FILE", f)
    return f


def _acc() -> acc_mod.Account:
    return acc_mod.Account(account_id="icm-demo", broker="icm", mode="demo", server="s", login=1, terminal_path="t",
                           server_tz="UTC", symbol="XAUUSD", magic_base=260100, enabled=True,
                           daily_loss_limit_abs=50.0, risk_overrides=OVERRIDES)


def _good_history(ratio: float) -> list[tuple[ClosedTrade, dict]]:
    """A clear edge since the evidence start (2 fills a day, daily sums 1.0 and 0.5 R), each fill's spread set so the
    pooled measured cost is `ratio` times what the labels charge (the settings extra cost: no cost table)."""
    model = BAR_SPREAD + settings_extra_cost_usd(S)
    spread = ratio * model - 0.10 - COMM
    out: list[tuple[ClosedTrade, dict]] = []
    d = pd.Timestamp("2026-10-20 10:00", tz="UTC")
    i = 0
    while d.date() < DAY:
        for k, r in enumerate([1.5, -0.5] if i % 2 == 0 else [1.0, -0.5]):
            out.append(_fill(d + pd.Timedelta(hours=k), spread=spread, r=r))
        d += pd.Timedelta(days=1)
        i += 1
    return out


READS: list[tuple[str, dict[str, Any]]] = []


def _ctx(tmp_path: Path, legs: pd.DataFrame, accounts: list[acc_mod.Account] | None = None) -> Any:
    def read(table: str, **kw: Any) -> pd.DataFrame:
        READS.append((table, kw))
        return legs if table == "exit_fills" else BARS

    models = SimpleNamespace(entries=[SimpleNamespace(version="v1", promoted_utc=FREEZE)])
    return SimpleNamespace(settings=S, accounts=accounts or [_acc()], state_dir=tmp_path, models=models, clock=None,
                           store=SimpleNamespace(read=read, append=lambda *a, **k: 0))


def _engine_file(tmp_path: Path) -> None:
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(
        {"account": "icm-demo", "mode": "demo", "currency": "USD", "ts": time.time(), "equity": 1_000.0,
         "balance_closed_hwm": 1_000.0, "stage": "normal"}))


def _run_job(tmp_path: Path, ratio: float) -> dict[str, Any]:
    fills = _good_history(ratio)
    for t, _ in fills:
        append_closed_trade(tmp_path, t)
    _engine_file(tmp_path)
    top = Limits(daily_cap=0.05, weekly_cap=0.10, supervisor_daily_cap=0.0375, supervisor_weekly_cap=0.075,
                 min_lot_risk_cap=0.01)
    AdaptiveStore(tmp_path).save(AdaptiveState(account_id="icm-demo", day=DAY - timedelta(days=1), limits=top,
                                               decided_ns=1), [])
    legs = pd.DataFrame([leg for _, leg in fills])
    return limits_job(_ctx(tmp_path, legs), SLOT)["icm-demo"]


@pytest.mark.parametrize("ratio, daily, weekly, reason", [(1.0, 0.05, 0.10, None), (1.2, 0.045, 0.09, "cost_step"),
                                                           (1.3, 0.02, 0.05, "cost_floor")])
def test_the_nightly_job_passes_the_series_so_1_3_gives_the_floors_and_1_2_one_step_down(tmp_path, phase_file,
                                                                                         ratio, daily, weekly, reason):
    out = _run_job(tmp_path, ratio)
    assert out["problems"] == [] and out["cost_ratio_20d"] == pytest.approx(ratio)
    assert out["cost_fills"] >= MIN_FILLS and out["cost_unmeasured"] == 0
    assert out["limits"]["daily_cap"] == pytest.approx(daily) and out["limits"]["weekly_cap"] == pytest.approx(weekly)
    reasons = {r for c in AdaptiveStore(tmp_path).changes("icm-demo") for r in c.reasons}
    assert (reason in reasons) if reason else not ({"cost_step", "cost_floor"} & reasons)


def test_without_exit_legs_the_series_is_unknown_and_the_cost_rule_stays_inert(tmp_path, phase_file):
    from goldbot.ops.limits_job import decide_account
    for t, _ in _good_history(1.3):
        append_closed_trade(tmp_path, t)
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(
        {"account": "icm-demo", "ts": time.time(), "equity": 1_000.0, "balance_closed_hwm": 1_000.0,
         "stage": "normal"}))
    a = _acc()
    models = SimpleNamespace(entries=[SimpleNamespace(version="v1", promoted_utc=FREEZE)])
    out = decide_account(S, a, [a], state_dir=tmp_path, models=models, decision_day=DAY,  # type: ignore[arg-type]
                         now_ns=time.time_ns(), dq=lambda c, d: None, bars_15m=lambda s, e: BARS)
    assert out["cost_ratio_20d"] is None and out["cost_fills"] == 0


def _decide(tmp_path: Path, fills: list[tuple[ClosedTrade, dict]], legs: pd.DataFrame, **kw: Any) -> dict[str, Any]:
    from goldbot.ops.limits_job import decide_account
    for t, _ in fills:
        append_closed_trade(tmp_path, t)
    _engine_file(tmp_path)
    a = _acc()
    models = SimpleNamespace(entries=[SimpleNamespace(version="v1", promoted_utc=FREEZE)])
    return decide_account(S, a, [a], state_dir=tmp_path, models=models, decision_day=DAY,  # type: ignore[arg-type]
                          now_ns=time.time_ns(), bars_15m=lambda s, e: BARS, cost_bars_15m=lambda s, e: BARS,
                          exit_legs=lambda s, e: legs, **kw)


def test_the_model_is_the_qualified_cost_never_the_live_table_that_holds_the_measured_slippage(tmp_path, phase_file):
    fills = _good_history(1.2)
    out = _decide(tmp_path, fills, pd.DataFrame([leg for _, leg in fills]), dq=lambda c, d: None,
                  extra_cost_usd=5.0)                                 # a live table's extra cost: not the model
    assert out["cost_ratio_20d"] == pytest.approx(1.2) and out["cost_model"] == "settings priors"


def test_the_job_reads_the_model_bars_from_dukascopy_the_labels_source(tmp_path, phase_file):
    READS.clear()
    _run_job(tmp_path, 1.0)
    assert any(t == "bars_15m" and kw.get("source") == "dukascopy" for t, kw in READS)


def test_too_many_unmeasured_fills_raise_a_dq_alert_and_leave_the_rule_inert(tmp_path, phase_file):
    fills = _good_history(1.3)
    events: list[tuple[str, str]] = []
    out = _decide(tmp_path, fills, pd.DataFrame([leg for _, leg in fills[-20:]]),   # most exit legs missing
                  dq=lambda c, d: events.append((c, d)))
    assert out["cost_ratio_20d"] is None and out["cost_unmeasured"] > 0 and out["problems"] == []
    assert "cost_ratio_unmeasured" in {c for c, _ in events}


def test_a_failing_series_gives_none_and_the_decision_still_stands(tmp_path, phase_file, monkeypatch):
    import goldbot.ops.cost_ratio as cr

    def boom(*a: Any, **k: Any) -> Any:
        raise ValueError("bad frame")

    monkeypatch.setattr(cr, "cost_ratio_20d", boom)
    out = _run_job(tmp_path, 1.3)
    assert out["cost_ratio_20d"] is None and out["limits"]["daily_cap"] == pytest.approx(0.05)


def test_an_account_without_adaptive_limits_is_never_measured(tmp_path, phase_file):
    READS.clear()
    plain = _acc().model_copy(update={"risk_overrides": None})
    assert limits_job(_ctx(tmp_path, pd.DataFrame(), [plain]), SLOT) == {}
    assert READS == []


def test_fills_after_the_last_label_bar_wait_for_the_weekly_refresh_and_are_not_unmeasured():
    entries = [*_weekday_entries(MIN_FILLS), *(e + pd.Timedelta(hours=4) for e in _weekday_entries(MIN_FILLS))]
    fills = [_fill(e) for e in entries]
    cut = pd.Timestamp("2026-11-19", tz="UTC")                      # the dukascopy bars end 6 days before the decision
    got = _ratio(fills, bars=BARS[pd.DatetimeIndex(BARS["ts_utc"]) < cut])
    assert got.pending > 0 and got.unmeasured == 0 and got.fills + got.pending == 2 * MIN_FILLS
    assert got.ratio is not None


def _two_a_day() -> list[tuple[ClosedTrade, dict]]:
    return [_fill(e) for e in [*_weekday_entries(MIN_FILLS),
                               *(e + pd.Timedelta(hours=4) for e in _weekday_entries(MIN_FILLS))]]


def test_label_bars_older_than_9_days_count_later_fills_unmeasured_and_say_so():
    from goldbot.ops.cost_ratio import MAX_BAR_LAG_DAYS
    from goldbot.ops.limits_job import ATR_MAX_LAG_DAYS
    assert MAX_BAR_LAG_DAYS == ATR_MAX_LAG_DAYS == 9
    stale = BARS[pd.DatetimeIndex(BARS["ts_utc"]) < pd.Timestamp("2026-11-15", tz="UTC")]   # 10 days before DAY
    got = _ratio(_two_a_day(), bars=stale)
    assert got.bars_stale and got.pending == 0 and got.unmeasured > 0 and got.ratio is None
    assert not _ratio(_two_a_day()).bars_stale


def test_the_job_alerts_on_stale_label_bars_and_on_a_failing_series(tmp_path, phase_file, monkeypatch):
    import goldbot.ops.cost_ratio as cr
    from goldbot.ops.limits_job import decide_account
    fills = _good_history(1.3)
    for t, _ in fills:
        append_closed_trade(tmp_path, t)
    _engine_file(tmp_path)
    a = _acc()
    events: list[tuple[str, str]] = []
    legs = pd.DataFrame([leg for _, leg in fills])
    models = SimpleNamespace(entries=[SimpleNamespace(version="v1", promoted_utc=FREEZE)])
    kw: dict[str, Any] = {"state_dir": tmp_path, "models": models, "decision_day": DAY, "now_ns": time.time_ns(),
                          "dq": lambda c, d: events.append((c, d)), "exit_legs": lambda s, e: legs}
    stale = BARS[pd.DatetimeIndex(BARS["ts_utc"]) < pd.Timestamp("2026-11-12", tz="UTC")]
    out = decide_account(S, a, [a], cost_bars_15m=lambda s, e: stale, **kw)
    assert out["cost_ratio_20d"] is None and "cost_bars_stale" in {c for c, _ in events}

    def boom(*x: Any, **k: Any) -> Any:
        raise ValueError("bad frame")

    monkeypatch.setattr(cr, "cost_ratio_20d", boom)
    events.clear()
    out = decide_account(S, a, [a], cost_bars_15m=lambda s, e: BARS, **kw)
    assert out["cost_ratio_20d"] is None and "cost_ratio_error" in {c for c, _ in events}
