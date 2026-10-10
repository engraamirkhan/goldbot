"""/api/accounts carries the live account view (owner request, ADR 0004): balance, currency, margin level, today's
P&L in account currency, the daily loss budget and the milestone ladder, read from engine_<account>.json and
supervisor.json. An engine file from before these fields existed still renders (they are null)."""
import json

import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import totp_code

ENGINE = {
    "account": "icm-demo", "broker": "icm", "mode": "demo", "equity": 990.0, "balance": 1000.0, "currency": "GBP",
    "margin": 156.0, "day_start_equity": 1000.0, "week_start_equity": 1000.0,
    "balance_closed_hwm": 1000.0, "stage": "normal", "open_positions": 1, "account_class": "raw", "ts": 0,
    "limits": {"risk_per_trade": 0.005, "max_risk_per_trade": 0.01, "multiplier_bounds": [0.25, 1.5],
               "daily_cap": 0.02, "weekly_cap": 0.05},
    "price": 4150.0, "spread_points": 20.0,
    "ladder_atr": {"15m": 7.27, "1h": 14.6, "4h": 29.0, "1d": 70.0},
    "lot_terms": {"contract_oz": 100.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50.0,
                  "stops_level_points": 0.0},
}


def _owner(c: TestClient, app) -> dict[str, str]:
    st = app.state.st
    uri = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io",
                                           "password": "owner password 123"}).json()["totp_uri"]
    secret = uri.split("secret=")[1].split("&")[0]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "owner password 123",
                                          "totp": totp_code(secret)}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _get(tmp_path, engine: dict, supervisor: dict | None = None) -> dict:
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(engine))
    if supervisor is not None:
        (tmp_path / "supervisor.json").write_text(json.dumps(supervisor))
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    [row] = c.get("/api/accounts", headers=_owner(c, app)).json()
    return row


def test_accounts_show_balance_currency_margin_level_and_todays_pnl_in_account_currency(tmp_path):
    row = _get(tmp_path, ENGINE, {"daily_cap": 0.015, "combined_day_start_equity": 1000.0, "combined_equity": 990.0})
    assert (row["balance"], row["equity"], row["currency"]) == (1000.0, 990.0, "GBP")
    assert row["margin_level_pct"] == pytest.approx(990.0 / 156.0 * 100)
    assert row["day_pnl"] == pytest.approx(-10.0)
    b = row["loss_budget"]
    assert (b["limit"], b["used"], b["remaining"], b["binding"]) == (pytest.approx(15.0), pytest.approx(10.0),
                                                                    pytest.approx(5.0), "combined 1.5% cap")


def test_a_gbp_account_ladder_waits_for_the_engines_currency_factor(tmp_path):
    row = _get(tmp_path, ENGINE)
    m = row["milestones"]
    assert m["pending"] == "pending broker terms"
    assert all(r["equity_needed"] is None for r in m["rungs"])
    assert [r["timeframe"] for r in m["rungs"]] == ["15m", "1h", "4h", "1d"]


def test_with_the_engines_factor_the_ladder_and_next_milestone_are_in_pounds(tmp_path):
    row = _get(tmp_path, {**ENGINE, "usd_to_account": 1 / 1.33, "equity": 1500.0})
    m = row["milestones"]
    assert m["pending"] is None and m["tradeable"] == ["15m"] and m["next_timeframe"] == "1h"
    assert m["next_equity"] == pytest.approx(0.01 * 1.5 * 14.6 * 100 / (1.2 * 0.005) / 1.33)
    assert 0 < m["progress"] < 1


def test_an_engine_file_from_before_the_account_view_still_renders_with_nulls(tmp_path):
    old = {k: v for k, v in ENGINE.items()
           if k not in ("balance", "currency", "margin", "limits", "price", "ladder_atr", "lot_terms")}
    row = _get(tmp_path, old)
    assert row["equity"] == 990.0
    assert row["balance"] is None and row["currency"] is None and row["margin_level_pct"] is None
    assert row["loss_budget"] is None and row["milestones"] is None
    assert row["day_pnl"] == pytest.approx(-10.0)
