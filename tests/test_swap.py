"""Overnight financing (swap) in labels and research costs: one charge per broker server-day rollover a position is
held through, three on the broker's triple day, none for holds that cross no rollover."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.technical import atr
from goldbot.labels import BarrierSpec, SwapSpec, rollover_nights, triple_barrier

SWAP = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=20.0, triple_weekday=2, server_tz="Europe/Athens")


def _utc(*ts: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t in ts])


def test_rollover_nights_count_server_midnights_with_a_triple_day():
    # March 2025, Athens = UTC+2 until 30 March: server midnight is 22:00 UTC
    entry = _utc("2025-03-04 12:00", "2025-03-07 12:00", "2025-03-04 12:00", "2025-03-04 12:00", "2025-03-04 21:00",
                 "2025-03-04 22:00")
    exit_ = _utc("2025-03-06 12:00", "2025-03-10 12:00", "2025-03-04 20:00", "2025-03-04 22:00", "2025-03-04 22:00:01",
                 "2025-03-05 12:00")
    n = rollover_nights(entry, exit_, "Europe/Athens", triple_weekday=2)
    # Tue->Thu: Tue night 1 + Wed night 3; Fri->Mon: Fri night 1 (Sat/Sun nights are in Wednesday's triple);
    # same server day 0; closed exactly at the rollover 0; open across it 1; opened exactly at it 0
    assert n.tolist() == [4, 1, 0, 0, 1, 0]
    assert rollover_nights(entry[:1], exit_[:1], "Europe/Athens", triple_weekday=4).tolist() == [2]   # triple on Friday
    # summer: Athens = UTC+3, server midnight is 21:00 UTC
    assert rollover_nights(_utc("2025-07-01 20:30"), _utc("2025-07-01 21:30"), "Europe/Athens", 2).tolist() == [1]
    assert rollover_nights(_utc("2025-07-01 21:30"), _utc("2025-07-01 23:30"), "Europe/Athens", 2).tolist() == [0]


def test_swap_spec_rejects_a_weekend_triple_day():
    with pytest.raises(ValueError):
        SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=0.0, triple_weekday=5)


@pytest.fixture(scope="module")
def bars() -> dict[str, pd.DataFrame]:
    b1 = ticks_to_1m(synthetic_ticks("2025-03-03", "2025-04-12", ticks_per_minute=2, seed=3))
    return {tf: resample_bars(b1, tf).reset_index(drop=True) for tf in ("15m", "1h", "1d")}


def _signals(n: int, step: int) -> pd.DataFrame:
    idx = np.arange(30, n - 12, step)
    return pd.DataFrame({"idx": idx, "side": np.where(idx % 2 == 0, 1, -1)})


@pytest.mark.parametrize("tf", ["15m", "1h"])
def test_intraday_labels_without_rollovers_are_unchanged(bars, tf):
    b = bars[tf]
    a = atr(mid(b), 14)
    spec = BarrierSpec(target_atr=1.5, stop_atr=1.0, max_bars=8)
    plain = triple_barrier(b, _signals(len(b), 7), spec, a)
    charged = triple_barrier(b, _signals(len(b), 7), spec, a, swap=SWAP)
    assert len(plain) == len(charged) > 50
    none = charged["swap_nights"] == 0
    assert none.sum() > 20                                       # most short holds cross no rollover
    pd.testing.assert_frame_equal(charged.loc[none].drop(columns=["swap_nights", "swap_ret"]), plain.loc[none])
    # a hold that does cross one pays for it, in return units of its entry price
    held = charged[~none]
    usd = np.where(held["side"] > 0, SWAP.long_usd_per_lot, SWAP.short_usd_per_lot) / 100.0
    assert held["swap_ret"].to_numpy() == pytest.approx(held["swap_nights"].to_numpy() * usd / held["entry"].to_numpy())
    assert held["ret"].to_numpy() == pytest.approx(plain.loc[~none, "ret"].to_numpy() + held["swap_ret"].to_numpy())
    # exits are untouched: swap is a cost, not a barrier
    assert (charged["t_exit"] == plain["t_exit"]).all() and (charged["label"] == plain["label"]).all()


def test_daily_holds_pay_every_night_on_their_side(bars):
    b = bars["1d"]
    a = pd.Series(np.full(len(b), 1e6))                          # barriers out of reach: every label runs to max_bars
    spec = BarrierSpec(target_atr=3.0, stop_atr=1.5, max_bars=5)
    sig = pd.DataFrame({"idx": [3, 3], "side": [1, -1]})
    out = triple_barrier(b, sig, spec, a, swap=SWAP)
    plain = triple_barrier(b, sig, spec, a)
    vis = pd.DatetimeIndex(pd.to_datetime(b["visible_at"], utc=True))
    nights = rollover_nights(vis[[3]], vis[[int(out["t_exit"].iloc[0])]], "Europe/Athens", 2)[0]
    assert nights >= 7                                           # six daily bars span a week: 7 nights incl. one triple
    assert out["swap_nights"].tolist() == [nights, nights]
    long_, short = out.iloc[0], out.iloc[1]
    assert long_["ret"] == pytest.approx(plain["ret"].iloc[0] - nights * 0.60 / long_["entry"])
    assert short["ret"] == pytest.approx(plain["ret"].iloc[1] + nights * 0.20 / short["entry"])


def test_swap_settings_and_cost_table():
    from goldbot.config import load_settings
    from goldbot.execution.costs import CostTable, settings_swap
    s = load_settings()
    sw = settings_swap(s)
    assert sw.long_usd_per_lot == s.costs.swap_long_usd_per_lot < 0
    assert sw.short_usd_per_lot == s.costs.swap_short_usd_per_lot
    assert sw.triple_weekday == s.costs.swap_triple_weekday == 2 and sw.server_tz == "Europe/Athens"
    table = CostTable(account_id="icm-demo", built_utc=pd.Timestamp("2026-10-05", tz="UTC"), spread={}, slippage={},
                      commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)
    assert table.swap_spec("Europe/Athens") is None             # nothing measured: the caller keeps the settings prior
    broker = table.model_copy(update={"swap_long_usd_per_lot": -45.0, "swap_short_usd_per_lot": 12.0,
                                      "swap_triple_weekday": 4})
    got = broker.swap_spec("Europe/Athens")
    assert got == SwapSpec(long_usd_per_lot=-45.0, short_usd_per_lot=12.0, triple_weekday=4, server_tz="Europe/Athens")
    # the broker's triple day defaults to the settings' when the terminal did not report one
    rates_only = table.model_copy(update={"swap_long_usd_per_lot": -45.0, "swap_short_usd_per_lot": 12.0})
    spec = rates_only.swap_spec("Europe/Athens", default_triple_weekday=3)
    assert spec is not None and spec.triple_weekday == 3


def test_live_swap_prefers_the_brokers_cost_table(tmp_path):
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.execution.costs import CostTable, settings_swap
    from goldbot.ops.accounts import Account
    from goldbot.ops.jobs import JobContext, live_swap
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.research.registry import TrialRegistry
    acc = Account(account_id="icm-demo", broker="icm", mode="demo", server="s", login=None, terminal_path="",
                  server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
    ctx = JobContext(settings=load_settings(), store=Store(tmp_path / "d"), state_dir=tmp_path,
                     models=ModelRegistry(tmp_path / "m"), trials=TrialRegistry(tmp_path / "t.jsonl"), accounts=[acc],
                     population=Population(tmp_path / "p.json"))
    assert live_swap(ctx) == settings_swap(ctx.settings)
    CostTable(account_id="icm-demo", built_utc=pd.Timestamp("2026-10-05", tz="UTC"), spread={}, slippage={},
              commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15, swap_long_usd_per_lot=-50.0,
              swap_short_usd_per_lot=15.0).save(tmp_path / "costs_icm-demo.json")
    assert live_swap(ctx) == SwapSpec(long_usd_per_lot=-50.0, short_usd_per_lot=15.0, triple_weekday=2,
                                      server_tz="Europe/Athens")


def test_research_labels_pay_swap_and_the_gross_screen_does_not():
    from goldbot.features.mtf import TF_LABEL, context_tfs
    from goldbot.research.pipeline import evaluate, prepare
    from goldbot.specialists import SPECIALISTS
    b1 = ticks_to_1m(synthetic_ticks("2024-01-01", "2024-07-01", ticks_per_minute=1, seed=21))
    spec = SPECIALISTS["tsmom"](timeframe="4h", max_bars=12)
    dec = resample_bars(b1, "4h").reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs("4h")}
    plain = prepare(spec, dec, context, extra_cost_usd=0.3)
    charged = prepare(spec, dec, context, extra_cost_usd=0.3, swap=SWAP)
    pd.testing.assert_frame_equal(plain.gross, charged.gross)              # the screen's gross R carries no cost
    assert (charged.labels["swap_nights"] > 0).any()
    diff = charged.labels["ret"].to_numpy() - plain.labels["ret"].to_numpy()
    assert diff == pytest.approx(charged.labels["swap_ret"].to_numpy())
    m = evaluate(charged, extra_cost_usd=0.3).metrics
    assert m["swap"]["long_usd_per_lot"] == -60.0 and m["swap"]["mean_nights"] > 0
    assert evaluate(plain, extra_cost_usd=0.3).metrics["swap"] is None
