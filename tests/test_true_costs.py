"""True costs (finance audit 2026-11-20 N10, P5 / P6): the realised round trip from fills, the versioned engine cost
of record built from it, the engine reading that version (the legacy table only where it has no cell), and the exit
quote recorded on every exploration exit so exit slippage is measured, not imputed.

Hand-worked trade (`_row`): long 0.01 lot (1 oz) at a GBP account, 0.75 GBP per USD, so 1 USD per oz = £0.75.
  spread at send 0.10; requested 4000.00, filled 4000.05: entry slip +0.05
  stop 5.00 below the request: level 3995.00, filled 3994.90: exit slip +0.10 (`level`)
  commission £0.06 = 0.08 USD per oz; swap -£0.03 = 0.04 paid (reported, never in the round trip)
  round trip = 0.10 + 0.05 + 0.10 + 0.08 = 0.33 USD per oz; the engine charged £0.35 = 0.4667 USD per oz
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig, cost_record, exploration
from goldbot.execution.broker import Tick
from goldbot.execution.costs import CostTable, SpreadStat
from goldbot.execution.paper import PaperBroker
from goldbot.research import realised_costs as rc
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_exploration_engine import CLOSE, _arm, _at, _engine

BROKERS = {"icm-demo": "icm", "icm-probe": "icm", "vantage-demo": "vantage"}
MON = pd.Timestamp("2025-03-03 08:00", tz="UTC")       # London (as tests/test_projection_costs.py)


def _row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        client_order_id="o1", account_id="icm-demo", arm_id="X15", tf="15m", side=1, lots=0.01,
        sent_utc="2026-11-10T09:00:00+00:00", requested_price=4000.0, fill_price=4000.05, spread_usd=0.10,
        stop_usd=5.0, target_usd=10.0, exit_utc="2026-11-10T09:30:00+00:00", exit_price=3994.90, exit_reason="stop",
        commission=-0.06, swap=-0.03, fx_acct_per_usd=0.75, cost=0.35, symbol="XAUUSD", retcode=10009)
    base.update(kw)
    return base


def _costs(rows: list[dict[str, Any]], probe: dict[tuple[str, str], float] | None = None,
           **kw: Any) -> pd.DataFrame:
    return rc.trade_costs(rows, BROKERS, exit_prior_usd=0.15, probe_slip=probe or {}, **kw)


# ----------------------------------------------------------------------------------------------- per trade
def test_the_hand_worked_stop_exit_is_measured_against_its_level():
    [t] = _costs([_row()]).to_dict("records")
    assert t["exit_source"] == "level" and t["session"] == "london" and t["tf"] == "15m"
    assert t["entry_slip"] == pytest.approx(0.05) and t["exit_slip"] == pytest.approx(0.10)
    assert t["commission"] == pytest.approx(0.08) and t["swap_paid"] == pytest.approx(0.04)
    assert t["round_trip"] == pytest.approx(0.33)                       # swap is the gate's, never in the round trip
    assert t["engine_cost"] == pytest.approx(0.35 / 0.75)


def test_a_recorded_exit_quote_wins_and_its_spread_pays_half_of_the_round_trip():
    [t] = _costs([_row(exit_bid=3994.95, exit_ask=3995.15)]).to_dict("records")
    assert t["exit_source"] == "quote"
    assert t["exit_slip"] == pytest.approx(0.05)                        # long: the bid it was closed against
    assert t["spread"] == pytest.approx((0.10 + 0.20) / 2)
    [s] = _costs([_row(side=-1, fill_price=3999.95, exit_price=4005.2, exit_bid=4004.9, exit_ask=4005.1)]) \
        .to_dict("records")
    assert s["exit_slip"] == pytest.approx(0.10) and s["entry_slip"] == pytest.approx(0.05)   # short: the ask


def test_target_price_improvement_is_never_credited_but_entry_slip_stays_signed():
    [t] = _costs([_row(exit_reason="target", exit_price=4010.20, fill_price=3999.97)]).to_dict("records")
    assert t["exit_source"] == "level" and t["exit_slip"] == 0.0         # filled 0.20 better than its limit
    assert t["entry_slip"] == pytest.approx(-0.03)                      # noise around the cost: floored on the mean
    [u] = _costs([_row(exit_reason="target", exit_price=4009.9)]).to_dict("records")
    assert u["exit_slip"] == pytest.approx(0.10)


@pytest.mark.parametrize("kw", [
    dict(exit_reason="exploration_hold", exit_price=3999.0),            # a market close with no quote recorded
    dict(exit_price=3999.0),                                            # a stop 4 better than its level: moved
    dict(arm_id="BXR15"),                                               # a trailing arm's stop is not its level
    dict(account_id="vantage-demo"),                                    # demo exits at the level: no evidence
])
def test_an_exit_with_no_measurement_of_its_own_takes_its_brokers_probe_else_the_prior(kw):
    broker = BROKERS[kw.get("account_id", "icm-demo")]
    [p] = _costs([_row(**kw)], {(broker, "london"): 0.03}).to_dict("records")
    assert p["exit_source"] == "probe" and p["exit_slip"] == pytest.approx(0.03)
    [q] = _costs([_row(**kw)], {(broker, "newyork"): 0.03}).to_dict("records")
    assert q["exit_source"] == "prior" and q["exit_slip"] == pytest.approx(0.15)


def test_rows_that_cannot_be_measured_or_are_not_evidence_are_dropped_never_imputed():
    rows = [_row(client_order_id="add", group_id="o0"),                 # a pyramid add
            _row(client_order_id="void"),                               # void for the cost cells
            _row(client_order_id="eur", symbol="EURUSD"),               # gold only
            _row(client_order_id="nofx", fx_acct_per_usd=None),
            _row(client_order_id="nofill", fill_price=None),
            _row(client_order_id="other", account_id="icm-live"),       # not a measured account
            _row(client_order_id="late", sent_utc="2026-11-20T09:00:00+00:00"),
            _row(client_order_id="first", group_id="first")]            # a group's first unit is its own trade
    df = _costs(rows, void={"void"}, lo=pd.Timestamp("2026-11-01", tz="UTC"),
                hi=pd.Timestamp("2026-11-20", tz="UTC"))
    assert len(df) == 1


def test_probe_exit_slip_is_the_brokers_p75_against_the_quote_floored_at_zero():
    probes = [dict(client_order_id=f"p{i}", purpose="cost_measurement", account_id="icm-probe", side=1,
                   sent_utc="2026-11-10T09:00:00+00:00", exit_price=4000.0 - s, exit_bid=4000.0, exit_ask=4000.1)
              for i, s in enumerate([0.0, 0.01, 0.02, 0.04])]
    probes.append(dict(probes[0], client_order_id="shadow", purpose="cost_probe", exit_price=3990.0))
    probes.append(dict(probes[0], client_order_id="vneg", account_id="vantage-demo", exit_price=4000.5))
    got = rc.probe_exit_slip(probes, BROKERS)
    assert got[("icm", "london")] == pytest.approx(np.quantile([0.0, 0.01, 0.02, 0.04], 0.75))
    assert got[("vantage", "london")] == 0.0                            # favourable: floored, never credited


# ----------------------------------------------------------------------------------------------- the cell
def test_the_day_clustered_interval_is_cr1_with_g_minus_1_degrees_of_freedom():
    lo, hi = rc.clustered_ci(np.array([1.0, 2.0, 3.0, 4.0]), np.array(["a", "a", "b", "b"]))
    half = 12.706204736174703 * 1.0                                     # t(1) 97.5 %, se = sqrt(2/1 x 8 / 16)
    assert (lo, hi) == pytest.approx((2.5 - half, 2.5 + half))
    assert rc.clustered_ci(np.array([1.0, 3.0]), np.array(["a", "a"])) == (2.0, 2.0)


def _trades(n: int, tf: str, days: int, cost: float, spread: float = 0.1, slip: float = 0.0) -> list[dict]:
    out = []
    for i in range(n):
        day = pd.Timestamp("2026-11-02 09:00", tz="UTC") + pd.Timedelta(days=i % days)
        x = cost + (0.02 if i % 2 else -0.02)
        out.append(dict(account="icm-demo", broker="icm", session="london", tf=tf, day=day.strftime("%Y-%m-%d"),
                        exit_reason="stop", exit_source="level", spread=spread, entry_slip=slip,
                        exit_slip=x - spread - slip - 0.08, commission=0.08, swap_paid=0.0, engine_cost=0.46,
                        round_trip=x))
    return out


def _record(rule: rc.ChargeRule = "ub95") -> rc.EngineCostRecord:
    df = pd.DataFrame(_trades(60, "5m", 6, 0.25) + _trades(4, "1h", 2, 0.60))
    return rc.build_record(df, version="ecr-test.1", window=(MON, MON + pd.Timedelta(days=7)), exit_prior_usd=0.15,
                           probe_slip={("icm", "london"): 0.03}, built_utc=MON, charge_rule=rule)


def test_a_full_cell_charges_its_upper_bound_and_a_sparse_one_never_undercuts_its_pooled_cell():
    rec = _record()
    five, hour, pooled = (rec.cells["icm-demo"]["london"][k] for k in ("5m", "1h", rc.ALL))
    assert not five.sparse and five.n == 60 and five.n_days == 6
    assert five.charged_usd == pytest.approx(max(five.point_usd, five.ci95[1]), abs=1e-4)
    assert five.engine_cost_usd == pytest.approx(0.46)
    assert hour.sparse and hour.charged_usd == pytest.approx(max(hour.point_usd, pooled.charged_usd), abs=1e-4)
    assert hour.charged_usd == pytest.approx(0.60, abs=1e-4)            # its own dearer point stands
    assert rec.cell("icm-demo", "london", "4h") == pooled               # no 4h fill: the pooled cell
    assert rec.cell("icm-demo", "asia", "5m") is None                   # no fill in the session: the legacy table
    assert rec.cell("vantage-demo", "london", "5m") is None
    assert rec.charged_usd("icm-demo", "london", "5m") == five.charged_usd


def test_favourable_mean_slippage_is_floored_in_the_point_cost():
    df = pd.DataFrame(_trades(40, "5m", 5, 0.10, slip=-0.10))
    c = rc.build_record(df, version="v", window=(MON, MON), exit_prior_usd=0.15, probe_slip={}) \
        .cells["icm-demo"]["london"]["5m"]
    assert c.entry_slip_usd == pytest.approx(-0.10)
    assert c.point_usd == pytest.approx(c.spread_usd + max(0.0, c.exit_slip_usd) + c.commission_usd, abs=1e-4)
    assert c.charged_usd >= c.point_usd


def test_the_p75_rule_is_never_below_the_ub95_rule():
    a, b = _record("ub95").cells["icm-demo"]["london"]["5m"], _record("p75").cells["icm-demo"]["london"]["5m"]
    assert b.charged_usd == pytest.approx(max(a.charged_usd, b.p75_usd), abs=1e-4)


def test_a_version_is_immutable_and_loads_back_exactly(tmp_path):
    rec, path = _record(), tmp_path / "ecr-test.1.yaml"
    rec.save(path)
    assert rc.EngineCostRecord.load(path) == rec
    with pytest.raises(FileExistsError):
        rec.save(path)


# ----------------------------------------------------------------------------------------------- the engine
def _table() -> CostTable:
    """The legacy table: London 0.25 spread + 2 x 0.05 prior + 2 x 3 / 100 commission = 0.41 per oz."""
    return CostTable(account_id="icm-demo", built_utc=MON, spread={"london": SpreadStat(median=0.25, p90=0.4, n=100)},
                     slippage={}, commission_per_lot_side_usd=3.0, slippage_prior_usd=0.05)


def _eng(tmp_path: Path, **cfg: Any) -> Engine:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=MON, bid=2400.0, ask=2400.2))
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                               server_tz="Europe/Athens", **cfg), pb, [], {}, ApprovalCenter({111}))


def _saved(tmp_path: Path) -> Path:
    path = tmp_path / "engine_costs" / "ecr-test.1.yaml"
    _record().save(path)
    return path


def test_the_engine_charges_the_versioned_cell_of_its_account_session_and_timeframe(tmp_path):
    rec = _record()
    eng = _eng(tmp_path, cost_record=str(_saved(tmp_path)))
    _table().save(tmp_path / "costs_icm-demo.json")
    five, pooled = rec.cells["icm-demo"]["london"]["5m"], rec.cells["icm-demo"]["london"][rc.ALL]
    assert eng._cost_atr(10.0, MON, tf="5m") == pytest.approx(five.charged_usd / 10.0)
    assert eng._cost_atr(10.0, MON) == pytest.approx(pooled.charged_usd / 10.0)        # no timeframe: pooled
    ex = max(five.charged_usd - five.spread_usd, five.commission_usd)
    assert eng._cost_atr(10.0, MON, ex_spread=True, tf="5m") == pytest.approx(ex / 10.0)
    assert cost_record.version(eng, MON, "5m") == "ecr-test.1"


def test_a_cell_the_record_lacks_and_an_unreadable_record_keep_the_legacy_table(tmp_path):
    _table().save(tmp_path / "costs_icm-demo.json")
    asia = pd.Timestamp("2025-03-03 02:00", tz="UTC")
    eng = _eng(tmp_path, cost_record=str(_saved(tmp_path)))
    assert cost_record.version(eng, asia, "5m").startswith("table:")
    assert cost_record.version(_eng(tmp_path), MON, "5m").startswith("table:")      # no record configured
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: [")
    worse = _eng(tmp_path, cost_record=str(bad))
    assert worse._cost_atr(10.0, MON, tf="5m") == pytest.approx(0.041)              # the legacy table, 0.41 / 10
    assert cost_record.version(worse, MON, "5m").startswith("table:")
    assert _eng(tmp_path / "none", cost_record=str(tmp_path / "missing.yaml"))._cost_atr(10.0, MON) == \
        pytest.approx(EngineConfig.model_fields["cost_atr"].default)                # nothing at all: the fallback


def test_the_live_spread_and_commission_floor_still_applies_over_the_record(tmp_path):
    eng = _eng(tmp_path, cost_record=str(_saved(tmp_path)))
    eng.state.spread_points = 900.0                                                 # $9 at a 0.01 point
    assert eng._cost_atr(10.0, MON, tf="5m") >= 9.0 * eng.cfg.point / 0.01 / 10.0 - 1e-9


# ----------------------------------------------------------------------------------------------- exploration rows
def test_an_exploration_row_names_the_cost_version_it_was_charged_on(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [rec] = exploration.trades(str(tmp_path))
    assert rec.cost_version == "fallback"                                           # no table, no record


def test_a_broker_stop_exit_records_the_quote_that_went_through_its_level(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a", max_open=1)])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    sl = eng.open[pid].sl
    assert sl is not None
    eng._last_reconcile = CLOSE
    hit = Tick(ts_utc=CLOSE + pd.Timedelta(seconds=40), bid=sl - 1.0, ask=sl - 0.8)
    eng.on_tick(hit)
    [rec] = exploration.trades(str(tmp_path))
    assert rec.exit_reason == "stop"
    assert (rec.exit_bid, rec.exit_ask, rec.exit_quote_utc) == (hit.bid, hit.ask, hit.ts_utc)


def test_an_engine_close_records_the_quote_it_was_sent_on(tmp_path):
    eng, pb = _engine(tmp_path, [_arm("a")])
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    [pid] = list(eng.open)
    eng._try_close(pid, "time_exit", CLOSE)
    eng._flush_closed()
    [rec] = exploration.trades(str(tmp_path))
    assert (rec.exit_bid, rec.exit_ask, rec.exit_quote_utc) == (tick.bid, tick.ask, tick.ts_utc)


def test_rows_written_before_the_exit_quote_load_without_it():
    old = exploration.ExplorationRecord.model_validate(dict(
        client_order_id="x", account_id="icm-demo", arm_id="a", family="f", tf="15m", side=1, lots=0.01,
        signal_utc=MON, sent_utc=MON, atr_usd=5.0, stop_usd=5.0, target_usd=10.0, hold_s=3600))
    assert old.exit_bid is None and old.exit_ask is None and old.cost_version is None



# ----------------------------------------------------------------------------------------------- the shipped version
def test_the_shipped_settings_charge_a_version_that_loads_and_is_named_by_its_file():
    from goldbot.config import load_settings
    path = load_settings().costs.engine_record
    assert path is not None
    for f in Path("config/engine_costs").glob("*.yaml"):
        rec = rc.EngineCostRecord.load(f)
        assert rec.version == f.stem                                     # one version, one file, never edited
        assert all(c.charged_usd >= c.point_usd - 1e-9 for by_s in rec.cells.values() for by_tf in by_s.values()
                   for c in by_tf.values())
    assert rc.EngineCostRecord.load(path).version == Path(path).stem
