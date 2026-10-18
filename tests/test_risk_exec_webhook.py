import json
from typing import Any

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from goldbot.execution.broker import OrderIntent, Tick
from goldbot.execution.classifier import Classification, PersistentClassifier, classify
from goldbot.execution.paper import PaperBroker
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.risk.gate import Stage
from goldbot.webhook.app import create_app
from goldbot.webhook.inbox import TvInbox


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _stage(st: AccountState) -> Stage:
    # read through a call: rearm() mutates st.stage, which mypy's narrowing cannot see
    return st.stage


def _intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="session_open-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


def test_gate_sizes_by_volatility_and_risk():
    g = RiskGate()
    d = g.check(_intent(), _state())
    assert d.allowed
    # risk 0.5% of 10k = $50; stop = 1.0 * $4 = $4/oz; 100 oz/lot -> 0.125 lots -> 0.12 after step rounding
    assert d.lots == 0.12
    assert d.risk_fraction <= 0.005 * 1.0 + 1e-9


def test_gate_blocks_on_caps_and_blackout():
    g = RiskGate()
    assert "daily_cap" in g.check(_intent(), _state(equity=9_790)).reasons
    assert "weekly_cap" in g.check(_intent(), _state(equity=9_490, day_start_equity=9_490)).reasons
    assert "news_blackout" in g.check(_intent(), _state(in_blackout=True)).reasons
    assert "spread_too_wide" in g.check(_intent(), _state(spread_points=60)).reasons
    assert "negative_ev" in g.check(_intent(p=0.40), _state()).reasons
    assert "supervisor_halt" in g.check(_intent(), _state(supervisor_halt=True)).reasons


def test_gate_drawdown_stages():
    g = RiskGate()
    st = _state(equity=9_150, day_start_equity=9_150, week_start_equity=9_150)   # 8.5% drawdown
    d = g.check(_intent(), st)
    assert st.stage == Stage.SIZE_DOWN and d.allowed and d.lots <= 0.06
    st2 = _state(equity=8_700, day_start_equity=8_700, week_start_equity=8_700)  # 13% drawdown
    d2 = g.check(_intent(), st2)
    assert st2.stage == Stage.HALTED and not d2.allowed
    # recovery does not clear a halt without /rearm
    st2.equity = 9_900
    assert g.update_stage(st2) == Stage.HALTED
    assert g.rearm(st2, "REARM") and _stage(st2) == Stage.NORMAL


def test_paper_broker_fills_stops_and_is_idempotent():
    pb = PaperBroker(equity=10_000)
    t0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    for i in range(10):
        pb.on_tick(Tick(ts_utc=t0 + pd.Timedelta(seconds=i), bid=2400.0, ask=2400.25))
    oi = OrderIntent(client_order_id="icm-1-abc", symbol="XAUUSD", side=1, lots=0.10, sl=2396.0, tp=2406.0, magic=260101, comment="icm-1-abc")
    r = pb.place_order(oi)
    assert r.ok and r.filled_lots == 0.10 and r.price is not None and r.price >= 2400.25
    assert not pb.place_order(oi).ok  # duplicate client order id
    pb.on_tick(Tick(ts_utc=t0 + pd.Timedelta(seconds=20), bid=2395.9, ask=2396.15))  # bid through stop
    assert pb.positions() == []
    deals = pb.deals_since(t0)
    stop = deals[deals["type"] == "stop"].iloc[0]
    assert stop["price"] < 2396.0  # adverse slippage applied
    assert pb.account().balance < 10_000


def test_classifier_rules_and_persistence(tmp_path):
    ts = pd.date_range("2025-03-05 09:00", periods=3000, freq="1s", tz="UTC")  # London session
    rng = np.random.default_rng(0)
    raw_ticks = pd.DataFrame({"ts_utc": ts, "bid": 2400.0, "ask": 2400.0 + 0.15 + rng.random(3000) * 0.05})
    std_ticks = pd.DataFrame({"ts_utc": ts, "bid": 2400.0, "ask": 2400.0 + 0.35 + rng.random(3000) * 0.05})
    no_deals = pd.DataFrame()
    assert classify(raw_ticks, no_deals).account_class == "raw"
    assert classify(std_ticks, no_deals).account_class == "standard"
    assert classify(std_ticks, pd.DataFrame({"commission": [-3.5]})).account_class == "raw"
    asia = raw_ticks.assign(ts_utc=pd.date_range("2025-03-05 02:00", periods=3000, freq="1s", tz="UTC"))
    assert classify(asia, no_deals).account_class == "unknown"
    pc = PersistentClassifier(tmp_path / "acct.json")
    assert pc.update(Classification(account_class="raw", median_spread=0.17, p90_spread=0.2, commission_seen=False, n_ticks=3000, reason="")) == "raw"
    assert pc.update(Classification(account_class="unknown", median_spread=None, p90_spread=None, commission_seen=False, n_ticks=10, reason="")) == "raw"     # one disagreement: keep
    assert pc.update(Classification(account_class="unknown", median_spread=None, p90_spread=None, commission_seen=False, n_ticks=10, reason="")) == "unknown"  # two in a row: change



def _cls(account_class: str) -> Classification:
    n = 10 if account_class == "unknown" else 3000
    return Classification(account_class=account_class, median_spread=None, p90_spread=None, commission_seen=False, n_ticks=n, reason="")


def test_a_stored_unknown_is_replaced_by_the_first_known_class_on_its_first_run(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    assert pc.update(_cls("unknown")) == "unknown"   # thin first Friday
    assert PersistentClassifier(tmp_path / "acct.json").update(_cls("raw")) == "raw"   # adopted at once, after a reload
    assert PersistentClassifier(tmp_path / "acct.json").state["pending"] is None


def test_a_stored_unknown_adopts_standard_on_its_first_run(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    assert pc.update(_cls("unknown")) == "unknown"
    assert pc.update(_cls("standard")) == "standard"
    assert pc.state["pending"] is None


def test_after_falling_from_standard_to_unknown_a_spread_only_raw_run_needs_a_second_agreeing_run(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    assert pc.update(_cls("standard")) == "standard"
    assert pc.update(_cls("unknown")) == "standard"
    assert pc.update(_cls("unknown")) == "unknown"
    assert pc.update(_cls("raw")) == "unknown"   # last known class was standard: no relaxing to raw on one run
    assert pc.state["pending"] == "raw"
    assert pc.update(_cls("raw")) == "raw"


def test_raw_runs_that_were_only_pending_never_count_as_the_last_known_class(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    for c in ("standard", "unknown", "unknown", "raw", "unknown"):
        pc.update(_cls(c))
    assert pc.update(_cls("raw")) == "unknown"   # the two raw runs were not consecutive
    assert pc.state["pending"] == "raw"
    assert pc.state["last_known"] == "standard"


def test_a_last_known_standard_does_not_expire_after_more_than_twenty_unknown_runs(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    pc.update(_cls("standard"))
    for _ in range(25):
        pc.update(_cls("unknown"))
    assert all(r["account_class"] == "unknown" for r in pc.state["runs"])   # standard has left the kept runs
    assert PersistentClassifier(tmp_path / "acct.json").update(_cls("raw")) == "unknown"


def _legacy(tmp_path, cls: str | None, runs: list[str]) -> PersistentClassifier:
    path = tmp_path / f"legacy_{cls}_{'_'.join(runs)}.json"
    path.write_text(json.dumps({"class": cls, "pending": None, "runs": [_cls(r).model_dump() for r in runs]}))
    return PersistentClassifier(path)


def test_a_legacy_state_file_without_last_known_is_migrated_from_its_class_and_runs(tmp_path):
    assert _legacy(tmp_path, "unknown", ["standard", "unknown", "unknown"]).state["last_known"] == "standard"
    assert _legacy(tmp_path, "unknown", ["raw", "unknown", "unknown"]).state["last_known"] is None
    assert _legacy(tmp_path, "unknown", ["unknown"]).state["last_known"] is None
    assert _legacy(tmp_path, "raw", ["standard", "raw"]).state["last_known"] == "raw"
    assert _legacy(tmp_path, "standard", ["standard"]).state["last_known"] == "standard"
    assert _legacy(tmp_path, None, []).state["last_known"] is None
    migrated = _legacy(tmp_path, "unknown", ["standard", "unknown", "unknown", "raw", "unknown"])
    assert migrated.update(_cls("raw")) == "unknown"   # migrated standard keeps the two-run rule
    assert migrated.state["pending"] == "raw"


def test_after_falling_from_standard_to_unknown_a_commission_raw_run_is_adopted_at_once(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    for c in ("standard", "unknown", "unknown"):
        pc.update(_cls(c))
    commission_raw = _cls("raw").model_copy(update={"commission_seen": True})
    assert pc.update(commission_raw) == "raw"


def test_a_known_class_needs_two_agreeing_runs_to_change_to_another_known_class(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    assert pc.update(_cls("raw")) == "raw"
    assert pc.update(_cls("standard")) == "raw"
    assert pc.state["pending"] == "standard"
    assert pc.update(_cls("standard")) == "standard"


def test_a_known_class_does_not_fall_back_to_unknown_on_one_thin_week(tmp_path):
    pc = PersistentClassifier(tmp_path / "acct.json")
    assert pc.update(_cls("raw")) == "raw"
    assert pc.update(_cls("unknown")) == "raw"
    assert pc.state["pending"] == "unknown"
    assert pc.update(_cls("raw")) == "raw"   # a known run in between resets the pending fall-back
    assert pc.state["pending"] is None


def test_webhook_auth_hash_and_intrabar(tmp_path):
    box = TvInbox(tmp_path / "tv.db")
    app = create_app(secret="s3cret", inbox=box, enforce_ip=False)
    c = TestClient(app)
    body = {"indicator": "K-Indi", "version": "2.02", "symbol": "XAUUSD", "tf": "15",
            "bar_time": "2025-03-05T10:00:00Z", "fired_at": "2025-03-05T10:15:01Z", "signal": "long",
            "strength": 1.0, "secret": "s3cret"}
    assert c.post("/tv", json=body).json() == {"ok": True, "dup": False, "intrabar": False}
    assert c.post("/tv", json=body).json()["dup"] is True
    assert c.post("/tv", json={**body, "secret": "nope"}).status_code == 403
    early = {**body, "bar_time": "2025-03-05T10:15:00Z", "fired_at": "2025-03-05T10:20:00Z"}
    assert c.post("/tv", json=early).json()["intrabar"] is True
    assert box.count() == 2


def test_webhook_ip_allow_list_uses_the_peer_address_not_a_forwarded_header(tmp_path):
    box = TvInbox(tmp_path / "tv.db")
    app = create_app(secret="s3cret", inbox=box)
    body = {"indicator": "K-Indi", "symbol": "XAUUSD", "tf": "15", "bar_time": "2025-03-05T10:00:00Z",
            "fired_at": "2025-03-05T10:15:01Z", "signal": "long", "secret": "s3cret"}
    outsider = TestClient(app, client=("203.0.113.9", 40000))
    assert outsider.post("/tv", json=body, headers={"X-Forwarded-For": "52.89.214.238"}).status_code == 403
    assert box.count() == 0
    tradingview = TestClient(app, client=("52.89.214.238", 40000))
    assert tradingview.post("/tv", json={**body, "secret": "s3crët"}).status_code == 403   # non-ASCII: refused, not a 500
    assert tradingview.post("/tv", json=body).json()["ok"] is True and box.count() == 1
