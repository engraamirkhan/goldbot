"""Backlog 280(a): FX ECR cells from the brokers' demo fills (bp of the fill), the card's mechanical membership (both
brokers, >= 30 fills in a session, fills after 2026-12-11 23:59 UTC not counted), and the not-of-record dry run."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research import decision_cost as DC
from goldbot.research import fx_cost_cells as F
from goldbot.research import srv3_cost_record as CR

BROKERS = {"icm-demo": "icm", "icm-probe": "icm", "vantage-demo": "vantage"}


def _row(i: int, *, acc: str = "icm-probe", sym: str = "EURUSD", sent: str = "2026-11-03 09:00", side: int = 1,
         req: float = 1.1, fill: float = 1.10001, spread: float | None = 0.00002, reason: str = "stop",
         stop: float = 0.001, exit_px: float | None = None, comm: float = 0.06, tf: str = "15m",
         fx_acct_per_usd: float = 0.8, **kw: Any) -> dict[str, Any]:
    t = pd.Timestamp(sent, tz="UTC") + pd.offsets.BDay(i // 6) + pd.Timedelta(minutes=i % 6)
    level = req - side * stop
    return {"account_id": acc, "symbol": sym, "client_order_id": f"c{acc}{sym}{i}", "group_id": None, "side": side,
            "lots": 0.01, "fill_price": fill, "requested_price": req, "spread_usd": spread,
            "exit_price": level - side * 0.00001 if exit_px is None else exit_px, "exit_reason": reason,
            "stop_usd": stop, "target_usd": 0.002, "commission": comm, "fx_acct_per_usd": fx_acct_per_usd, "tf": tf,
            "sent_utc": t.isoformat(), "exit_utc": (t + pd.Timedelta(minutes=30)).isoformat(), **kw}


def test_round_trip_is_spread_plus_slips_plus_commission_in_bp_of_the_fill() -> None:
    df, drops = F.fx_trade_costs([_row(0)], BROKERS)
    r = df.iloc[0]
    assert r["spread"] == pytest.approx(0.00002 / 1.10001 * 1e4)
    assert r["entry_slip"] == pytest.approx(0.00001 / 1.10001 * 1e4)
    assert r["exit_slip"] == pytest.approx(0.00001 / 1.10001 * 1e4)
    # 0.06 acct / 0.8 acct-per-USD (the quote) = 0.075 USD on 1,000 EUR = 1,100.01 USD
    assert r["commission"] == pytest.approx(0.075 / (0.01 * 100_000 * 1.10001) * 1e4)
    assert r["round_trip"] == pytest.approx(r["spread"] + r["entry_slip"] + r["exit_slip"] + r["commission"])
    assert drops == {}


def test_commission_converts_through_the_quote_currency() -> None:
    """`fx_acct_per_usd` is the account currency per QUOTE unit (per JPY on USDJPY and EURJPY): the commission is
    in bp of the quote notional, the same for a USD base and a cross."""
    kw: dict[str, Any] = {"req": 150.0, "fill": 150.0, "stop": 0.1, "spread": 0.002, "fx_acct_per_usd": 0.0047}
    jpy = _row(0, sym="USDJPY", **kw)
    cross = _row(1, acc="vantage-demo", sym="EURJPY", exit_bid=149.9, exit_ask=149.903, exit_px=149.899, **kw)
    df, drops = F.fx_trade_costs([jpy, cross], BROKERS)
    c = df.set_index("instrument")["commission"]
    want = 0.06 / 0.0047 / (1000.0 * 150.0) * 1e4                   # 12.77 JPY on 150,000 JPY = 0.85 bp
    assert c["USDJPY"] == pytest.approx(want) and c["EURJPY"] == pytest.approx(want) and drops == {}
    assert 0.5 < want < 1.5                                         # a USD-notional reading would give ~128 bp


def test_an_exit_without_a_measured_slip_is_not_a_measured_round_trip() -> None:
    """vantage demo stop/target exits fill at the level by construction (DEMO_EXIT_AT_LEVEL); time exits have no
    level; FX has no probes. Those fills are dropped and counted, never charged a gold prior."""
    rows = [_row(0, acc="vantage-demo"), _row(1, reason="time_exit"),
            _row(2, acc="vantage-demo", exit_bid=1.0989, exit_ask=1.09892, exit_px=1.0989)]
    df, drops = F.fx_trade_costs(rows, BROKERS)
    assert list(df["exit_source"]) == ["quote"]
    assert drops == {"exit slip not measurable (no exit quote, no level)": 2}


def test_moved_stop_tolerance_scales_with_the_fx_stop_distance() -> None:
    far = _row(0, exit_px=1.1 - 0.001 + 0.0005)          # exit 50 % of the stop distance better: a moved stop
    df, drops = F.fx_trade_costs([far], BROKERS)
    assert df.empty and sum(drops.values()) == 1


def test_fills_after_the_card_cut_off_are_not_counted() -> None:
    rows = [_row(0, sent="2026-12-11 23:59"), _row(1, sent="2026-12-12 00:00")]
    df, _ = F.fx_trade_costs(rows, BROKERS)
    assert len(df) == 1
    with pytest.raises(ValueError, match="cut-off"):
        F.fx_trade_costs(rows, BROKERS, hi=pd.Timestamp("2026-12-20", tz="UTC"))


def test_gold_and_unknown_accounts_are_ignored() -> None:
    df, _ = F.fx_trade_costs([_row(0, sym="XAUUSD"), _row(1, acc="other")], BROKERS)
    assert df.empty


def _fills(n_icm: int, n_van: int, session_hour: int = 9) -> pd.DataFrame:
    rows = [_row(i, sent=f"2026-11-02 {session_hour:02d}:00") for i in range(n_icm)]
    rows += [_row(i, acc="vantage-demo", sent=f"2026-11-02 {session_hour:02d}:00", exit_bid=1.0989,
                  exit_ask=1.09892, exit_px=1.0989) for i in range(n_van)]
    return F.fx_trade_costs(rows, BROKERS)[0]


def test_membership_needs_both_brokers_with_min_fills_in_a_session() -> None:
    assert F.membership(F.cells(_fills(30, 30)), ("icm", "vantage"))["EURUSD"][0] == "measured"
    basis, why = F.membership(F.cells(_fills(40, 29)), ("icm", "vantage"))["EURUSD"]
    assert basis == "prior" and "vantage" in why
    basis, why = F.membership(F.cells(_fills(60, 0)), ("icm", "vantage"))["EURUSD"]
    assert basis == "prior" and "vantage" in why


def test_cost_rows_take_the_tf_cell_then_session_all_then_the_brokers_worst_session() -> None:
    tr = _fills(36, 36)                                       # london only, 15m only, 6 days
    cells = F.cells(tr)
    rows = F.cost_rows(cells, {"EURUSD": ("measured", "")}, ("icm", "vantage"), tfs=("15m", "1h"))
    r = rows.set_index(["broker", "session", "tf"])
    assert r.loc[("icm", "london", "15m"), "basis"] == "ecr"
    assert r.loc[("icm", "london", "1h"), "basis"] == "ecr-session-all"
    assert r.loc[("icm", "asia", "15m"), "basis"] == "ecr-broker-max:session-below-min-fills"
    assert r.loc[("icm", "asia", "15m"), "rt_bp"] == r.loc[("icm", "london", "15m"), "rt_bp"]
    assert (rows["rt_bp"] >= DC.RT_BP).all() and (rows["slip_x_bp"] >= 0).all()
    assert (rows["session_n"].groupby([rows["broker"], rows["session"]]).first().loc[("icm", "london")]) == 36
    lon = cells[(cells["broker"] == "icm") & (cells["session"] == "london") & (cells["tf"] == "15m")].iloc[0]
    assert lon["charged"] == pytest.approx(max(lon["point"], lon["ub95"]))
    assert r.loc[("icm", "london", "15m"), "slip_x_bp"] == pytest.approx(
        max(0.0, lon["charged"] - lon["spread"] - lon["commission"]), abs=1e-6)       # the record keeps 6 decimals


def test_dry_run_record_is_labelled_not_of_record_and_refused_if_recorded(tmp_path, monkeypatch) -> None:
    (gold,) = CR.recorded().values()
    tr = _fills(36, 36)
    rec = F.build_record(gold, tr, tfs=tuple(sorted({c.tf for c in gold.gold_cells})), dry_run=True,
                         files={}, content_sha256="0" * 64)
    assert rec.dry_run and rec.schema_version == 2 and "NOT OF RECORD" in rec.frozen_by
    assert rec.fill_cutoff_utc == CR.FILL_CUTOFF_UTC
    assert rec.measured_fx() == frozenset({"EURUSD"}) and getattr(rec.class_of("USDJPY"), "basis", None) == "prior"
    assert rec.gold_cells == gold.gold_cells
    import yaml
    rec = rec.model_copy(update={"record_sha256": CR.record_sha256(rec)})
    (tmp_path / f"{rec.version}.yaml").write_text(yaml.safe_dump(rec.model_dump(mode="json"), sort_keys=False))
    monkeypatch.setattr(CR, "RECORD_DIR", tmp_path)
    with pytest.raises(CR.CostRecordRefused, match="dry run"):
        CR.recorded()


def test_fx_spread_table_ub95_and_quote_basis(tmp_path) -> None:
    d = tmp_path / "dukascopy_fx"
    d.mkdir()
    ts = pd.date_range("2015-01-05 08:00", periods=60 * 24 * 20, freq="1min", tz="UTC")
    spr = np.where(np.arange(len(ts)) % 2 == 0, 0.00001, 0.00003)
    pd.DataFrame({"ts_utc": ts, "spread_mean": spr, "dq_flag": "ok"}).to_parquet(d / "eurusd_1m_dukascopy_2015.parquet")
    pd.DataFrame({"ts_utc": ts, "bid_close": 1.1}).to_parquet(d / "usdjpy_1m_dukascopy_2015.parquet")
    t = F.fx_spread_table(tmp_path, ("EURUSD", "USDJPY"), years=range(2015, 2016))
    e = t[t["instrument"] == "EURUSD"]
    assert set(e["quote_basis"]) == {CR.BID_ASK} and (e["ub95_px"] >= e["mean_px"]).all()
    assert e["mean_px"].iloc[0] == pytest.approx(0.00002)
    assert (e["available_utc"] == pd.Timestamp("2015-02-01", tz="UTC")).all()
    j = t[t["instrument"] == "USDJPY"]
    assert set(j["quote_basis"]) == {"bid_only"} and j["ub95_px"].isna().all()
    late = pd.DataFrame({"ts_utc": pd.date_range("2025-10-01", periods=10, freq="1min", tz="UTC"),
                         "spread_mean": 0.00002, "dq_flag": "ok"})
    late.to_parquet(d / "eurusd_1m_dukascopy_2025.parquet")
    t2 = F.fx_spread_table(tmp_path, ("EURUSD",), years=range(2025, 2026))
    assert t2.empty                                        # the holdout and later are never read
