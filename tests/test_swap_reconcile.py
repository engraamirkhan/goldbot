"""BACKLOG 175: the broker's swap on a weekend hold of a seven-night (crypto) book is reconciled automatically.

Rule (BACKLOG 153 / R130): a crypto book is charged swap "every server-midnight rollover, weekends included, with no
triple day", from the item's reading of the broker, not a measured charge; Vantage reports triple day 5 (Friday) for
BTCUSD, so "reconcile whether Friday is also charged three nights on top of the weekend ones, or not every night is
charged". The daily `swap_reconcile` job compares each closed weekend hold's broker swap (the exploration ledger,
from the deal history) with the seven-night projection (the instruments.db rate x lots x nights, in the account
currency), stores the result in aux.db `swap_reconciliations` once, and health `swap_reconcile` warns (told to the
owner) past `costs.swap_reconcile_tolerance`. No weekend hold: nothing to do. Read-only: never a broker call or an
order."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import exploration
from goldbot.engine.instruments_db import InstrumentsDb, InstrumentTerms
from goldbot.ops import accounts as acc_mod
from goldbot.ops import health, jobs, swap_reconcile

FRI = pd.Timestamp("2026-11-06 14:00", tz="UTC")       # Friday 16:00 on the NY+7 server clock (UTC+2 in November)
MON = pd.Timestamp("2026-11-09 10:00", tz="UTC")       # Monday 12:00 server: the Saturday, Sunday, Monday rollovers
RATE = -20.0                                           # USD per lot (1 BTC) per night, broker sign
LOTS, FX = 0.01, 0.8                                   # 0.01 lot; GBP 0.80 per USD -> GBP -0.16 a night


@pytest.fixture
def vantage() -> acc_mod.Account:
    return acc_mod.load_accounts(logins=False)["vantage-demo"]


def _ctx(state_dir, now: pd.Timestamp) -> Any:
    """The two fields the check reads (a HealthContext otherwise needs the whole host)."""
    return SimpleNamespace(state_dir=state_dir, now=now)


def _terms(state_dir, rate: float = RATE) -> None:
    InstrumentsDb(state_dir).save(InstrumentTerms(
        account_id="vantage-demo", symbol="BTCUSD", store_symbol="BTCUSD", contract_size=1.0, digits=2, point=0.01,
        volume_min=0.01, volume_step=0.01, volume_max=100.0, currency_profit="USD", swap_long_per_lot=rate,
        swap_short_per_lot=rate, swap_triple_weekday=5, updated_utc=FRI - pd.Timedelta(days=1)))


def _trade(state_dir, cid: str, swap: float | None, *, entry=FRI, exit=MON, symbol: str = "BTCUSD") -> None:
    with exploration.ledger_db(str(state_dir)).write() as conn:
        exploration.LEDGER.upsert(conn, exploration.ExplorationRecord(
            client_order_id=cid, account_id="vantage-demo", arm_id="I0-BTCUSD", family="random_bracket", tf="15m",
            side=1, lots=LOTS, signal_utc=entry, sent_utc=entry, atr_usd=100.0, stop_usd=100.0, target_usd=150.0,
            hold_s=3600, position_id=abs(hash(cid)) % 10_000, fill_price=90_000.0, exit_utc=exit,
            exit_price=90_100.0, exit_reason="time_exit", swap=swap, commission=-0.1, pnl=0.5,
            fx_acct_per_usd=FX, symbol=symbol))


def test_the_three_candidate_schedules_count_a_friday_to_monday_hold():
    n = swap_reconcile.nights(FRI, MON, "NY+7", triple_weekday=4)
    assert n == {"seven_night": 3, "five_plus_triple": 3, "seven_plus_triple": 5}
    assert swap_reconcile.weekend_rollovers(FRI, MON, "NY+7") == 3
    tue, wed = pd.Timestamp("2026-11-10 10:00", tz="UTC"), pd.Timestamp("2026-11-11 10:00", tz="UTC")
    assert swap_reconcile.weekend_rollovers(tue, wed, "NY+7") == 0


def test_a_weekend_hold_charged_seven_nights_is_stored_within_tolerance_once(tmp_path, vantage):
    _terms(tmp_path)
    _trade(tmp_path, "ex1", swap=3 * RATE * LOTS * FX)
    out = swap_reconcile.run(tmp_path, [vantage], tolerance=0.25)
    assert out["reconciled"] == 1 and out["outside"] == []
    [row] = swap_reconcile.results(tmp_path)
    assert row.ok and row.nights["seven_night"] == 3 and row.implied_nights == pytest.approx(3.0)
    assert row.projected == pytest.approx(-0.48) and "seven_night" in row.matches
    assert swap_reconcile.run(tmp_path, [vantage], tolerance=0.25)["reconciled"] == 0     # idempotent
    assert len(swap_reconcile.results(tmp_path)) == 1


def test_a_friday_triple_on_top_of_the_weekend_is_outside_tolerance_and_health_warns_the_owner(tmp_path, vantage):
    _terms(tmp_path)
    _trade(tmp_path, "ex1", swap=5 * RATE * LOTS * FX)
    out = swap_reconcile.run(tmp_path, [vantage], tolerance=0.25)
    assert out["outside"] == ["vantage-demo:ex1"]
    [row] = swap_reconcile.results(tmp_path)
    assert not row.ok and row.matches == ["seven_plus_triple"] and row.implied_nights == pytest.approx(5.0)
    check = health.check_swap_reconcile(_ctx(tmp_path, MON + pd.Timedelta(days=1)))
    assert check.status == "warn" and "ex1" in check.reason and "seven_plus_triple" in check.reason
    assert health._alerting(check.name, check.status)


def test_no_weekend_hold_is_a_no_op_and_health_is_ok(tmp_path, vantage):
    _terms(tmp_path)
    tue, wed = pd.Timestamp("2026-11-10 10:00", tz="UTC"), pd.Timestamp("2026-11-11 10:00", tz="UTC")
    _trade(tmp_path, "weekday", swap=RATE * LOTS * FX, entry=tue, exit=wed)
    _trade(tmp_path, "gold", swap=-1.0, symbol="XAUUSD")                   # five-night book: not this check's
    assert swap_reconcile.run(tmp_path, [vantage], tolerance=0.25) == {"reconciled": 0, "outside": []}
    check = health.check_swap_reconcile(_ctx(tmp_path, MON))
    assert check.status == "ok" and check.name == "swap_reconcile"


def test_a_hold_with_no_known_rate_is_stored_unpriced_and_warns(tmp_path, vantage):
    _trade(tmp_path, "ex1", swap=-0.48)                                   # no instruments.db row
    out = swap_reconcile.run(tmp_path, [vantage], tolerance=0.25)
    assert out["outside"] == ["vantage-demo:ex1"]
    [row] = swap_reconcile.results(tmp_path)
    assert not row.ok and row.projected is None and "rate" in row.reason


def test_an_open_or_swapless_hold_waits_for_its_broker_figures(tmp_path, vantage):
    _terms(tmp_path)
    _trade(tmp_path, "ex1", swap=None)
    assert swap_reconcile.run(tmp_path, [vantage], tolerance=0.25)["reconciled"] == 0


def test_the_job_is_scheduled_daily_and_its_tolerance_is_a_setting():
    s = load_settings()
    assert jobs.JOBS["swap_reconcile"] is jobs.swap_reconcile
    assert s.scheduler.swap_reconcile.kind == "daily"
    assert s.costs.swap_reconcile_tolerance == 0.25
