"""Backend for the dashboard end-to-end tests: the real FastAPI app over a throwaway state directory, serving the
built web/dist, with two pending proposals seeded so the approval flow can be exercised, and a throwaway store holding a
few calendar events and headlines for the News tab, plus drift, shadow and research state for the Health and
Research tabs. The first-run setup
code is written to --info so the browser test can create the owner account the way a person would.

  python scripts/e2e_server.py --port 8790 --info web/e2e/.server.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import uvicorn  # noqa: E402

from goldbot.api.app import create_app  # noqa: E402
from goldbot.data.econ_calendar import COLUMNS as CALENDAR_COLUMNS  # noqa: E402
from goldbot.data.news import COLUMNS as NEWS_COLUMNS  # noqa: E402
from goldbot.data.store import Store  # noqa: E402
from goldbot.engine.shadow import ShadowBook  # noqa: E402
from goldbot.telegram.approvals import Proposal  # noqa: E402
from goldbot.telegram.bus import ApprovalBus  # noqa: E402


def seed_proposals(state: str) -> None:
    """Two proposals as an engine would publish them to the approval bus."""
    bus = ApprovalBus(state)
    for pid, side, entry in (("e2e-long", 1, 2400.25), ("e2e-short", -1, 2410.50)):
        bus.publish(Proposal(
            proposal_id=pid, account_id="icm-demo", agent_id="session_open-g0-e2e", side=side, lots=0.05,
            entry=entry, stop=entry - side * 4.0, target=entry + side * 6.0, p=0.62, ev_r=0.31, spread_points=22.0,
            top_features=[("atr_14", 0.4), ("adx_14", -0.2)], risk_usd=20.0, created=time.time(), window_s=3600))


def seed_store(data_root: str) -> None:
    """Upcoming events (one tier-1 with a blackout window) and recent headlines (one scored shock, one unscored)."""
    now = pd.Timestamp.now("UTC").floor("min")
    store = Store(data_root)
    events = [("e2e-cpi", now + pd.Timedelta(hours=3), "USD", "CPI m/m", "High", 1, "0.3%", "0.2%"),
              ("e2e-ism", now + pd.Timedelta(hours=1), "USD", "ISM Manufacturing PMI", "High", 2, "49.5", "48.7")]
    store.append("calendar_events", pd.DataFrame(
        [{"event_id": i, "ts_utc": t, "country": c, "title": ti, "impact": im, "tier": tier, "forecast": f,
          "previous": p, "received_utc": now} for i, t, c, ti, im, tier, f, p in events], columns=CALENDAR_COLUMNS),
        source="forexfactory")
    items = [("e2e-n1", 5, "Fed's Powell signals a pause in hikes", True, 0.8, "dovish", "risk_on", "negative", False),
             ("e2e-n2", 20, "Missile strike reported near Gulf shipping lane", True, 0.95, "neutral", "risk_off", "positive", True),
             ("e2e-n3", 40, "Weekend football results", False, float("nan"), "", "", "", False)]
    store.append("news", pd.DataFrame(
        [{"item_id": i, "ts_utc": now - pd.Timedelta(minutes=m), "received_utc": now - pd.Timedelta(minutes=m),
          "source": "forexlive", "title": t, "summary": "", "link": "", "scored": sc, "relevance": rel, "rates": ra,
          "risk": ri, "dollar": d, "surprise": "none" if sc else "", "shock": sh}
         for i, m, t, sc, rel, ra, ri, d, sh in items], columns=NEWS_COLUMNS), source="rss")


def seed_health_research(state: str) -> None:
    """A drift report (one agent sized down, one halted; no system halt, so the approval story is unaffected), a few
    closed shadow trades for the reliability curve and CUSUM trace, two registry trials and a research plan."""
    now = pd.Timestamp.now("UTC").floor("h")
    book = ShadowBook(state)
    book.track("tsmom-v1", now - pd.Timedelta(days=3))
    for i in range(8):
        ts = now - pd.Timedelta(hours=40 - 2 * i)
        hit = i % 3 != 0
        book.open_trade(version="tsmom-v1", agent_id="tsmom-g0", side=1, bar_ts=ts, entry=2400.0, atr_usd=4.0, target_atr=1.5,
                        stop_atr=1.0, max_bars=8, p=0.65 if hit else 0.45, timeframe="1h", threshold=0.4)
        book.on_bar(pd.Series({"ts_utc": ts + pd.Timedelta(hours=1), "bid_low": 2399.0 if hit else 2390.0,
                               "bid_high": 2410.0 if hit else 2401.0, "ask_high": 2401.0, "ask_low": 2399.0,
                               "bid_close": 2400.0, "ask_close": 2400.2}), timeframe="1h")
    book.save(now)
    base = {"psi_warn": [], "psi_size_down": [], "n_live_rows": 80, "ece": 0.04, "brier": 0.21, "n_calib": 40, "cusum": 0.8,
            "cusum_alarm": False, "dd_30d": 0.03, "backtest_dd": 0.05, "size_factor": 1.0, "halted": False, "notes": []}
    agents = {
        "tsmom-g0": {**base, "agent_id": "tsmom-g0", "version": "tsmom-v1", "psi": {"atr_14": 0.31, "adx_14": 0.12, "rsi_14": 0.03},
                     "psi_warn": ["adx_14"], "psi_size_down": ["atr_14"], "size_factor": 0.5,
                     "notes": ["PSI > 0.25 on atr_14: sized down"]},
        "trend-g1": {**base, "agent_id": "trend-g1", "version": "trend-v2", "psi": {"atr_14": 0.04, "rsi_14": 0.02},
                     "cusum": 4.6, "cusum_alarm": True, "halted": True, "dd_30d": 0.06,
                     "notes": ["CUSUM alarm on trade residuals: agent halted"]},
    }
    (Path(state) / "drift.json").write_text(json.dumps({
        "ts": now.isoformat(), "agents": agents, "size_factor": {"tsmom-g0": 0.5}, "system_halt": None, "errors": {},
        "halted": {"trend-g1": {"version": "trend-v2", "since": now.isoformat(), "reasons": agents["trend-g1"]["notes"]}}}))
    gates = {"passed": False, "checks": [{"name": "dsr", "passed": False, "detail": "deflated Sharpe n/a on 900 trades"},
                                         {"name": "events", "passed": True, "detail": "900 events"}]}
    trials = [{"trial": i, "ts": (now - pd.Timedelta(days=3 - i)).isoformat(), "agent_id": f"tsmom-e2e{i}", "family": "tsmom",
               "config": {"max_bars": 24 * i}, "status": "evaluated", "rationale": f"e2e trial {i}", "results": {
                   "rule_only": {"gross": {"n": 900, "mean_r": 0.061, "t_stat": 2.63}, "net": {"n": 900, "mean_r": -0.079, "t_stat": -2.1}},
                   "gates": gates}} for i in (1, 2)]
    (Path(state) / "research_registry.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trials))
    (Path(state) / "research_plan.json").write_text(json.dumps({
        "created_utc": now.isoformat(), "quarter": "2026Q4", "quarter_budget": 20, "quarter_used": 2, "total_budget": 18, "floor": 1,
        "cap": 20, "budget": {"tsmom": 12, "trend": 6}, "grid_budget": {"tsmom": 0, "trend": 0}, "unallocated": 0,
        "holdout_from": "2025-10-01", "holdout_to": "2026-10-01", "holdout_trials_ignored": 0,
        "focus": [{"rank": 1, "family": "tsmom", "budget": 12, "evidence": 1.1, "reasons": ["gross t 2.63 but net negative"]}],
        "evidence": [{"family": "mean_reversion", "trials": 3, "evidence": 0.0, "blocked": False, "flags": [],
                      "median_auc": None, "best_dsr": None, "shadow_trades": 0, "retired_id": "R-01",
                      "retired_status": "net negative after costs", "retired_since": "2026-07-01", "retired": True}],
        "rule": "e2e", "quarter_reserved": 4, "reservation": {"setting": 6, "run": 2, "pending": 3, "reserved": 4},
        "retired_floor": {"mean_reversion": 1}, "reinstate_t": 2.61, "hypotheses_sha256": "0" * 64,
        "moves": [{"family": "tsmom", "source": "attribution", "detail": "net-R t 2.1 over 60 trades", "budget_before": 11,
                   "budget_after": 12, "share_before": 0.5, "share_after": 0.56, "shift_pct": 12.0}]}))


def seed_automode(state: str) -> None:
    """Auto-mode evidence that holds (row A10): 100 decided proposals, archived long ago (so the Approvals screen's
    decided list ignores them), each with its closed shadow trade, approved and rejected outcomes drawn from one
    distribution. Kept in a shadow version no champion uses, so the Health charts are unaffected."""
    import os

    import numpy as np

    from goldbot.engine.shadow import ShadowTrade, VersionBook
    from goldbot.telegram.approvals import Outcome
    t0 = 1_740_000_000
    rs = list(np.random.default_rng(1).normal(0.1, 1.0, 100))
    bus = ApprovalBus(state)
    trades = []
    for i, rr in enumerate(rs):
        close, approve = t0 + i * 3600, i % 2 == 0
        p = Proposal(proposal_id=f"icm-demo-{close}-auto{i}", account_id="icm-demo", agent_id="trend-g0-auto", side=1,
                     lots=0.1, entry=2400.0, stop=2390.0, target=2420.0, p=0.6, ev_r=0.2, spread_points=20,
                     top_features=[], created=float(close), outcome=Outcome.APPROVED if approve else Outcome.REJECTED,
                     reason_code=None if approve else "discretion")
        bus.archive(p)
        os.utime(bus.done_dir / f"{p.proposal_id}.json", (close, close))
        trades.append(ShadowTrade(version="auto-v1", agent_id="trend-g0-auto", side=1,
                                  entry_ts=pd.Timestamp(close - 900, unit="s", tz="UTC"), entry=2400.0, stop=2390.0,
                                  target=2420.0, max_bars=4, p=0.6, threshold=0.55, taken=approve,
                                  exit_ts=pd.Timestamp(close + 3600, unit="s", tz="UTC"), exit=2400.0 + 10 * rr,
                                  barrier="time", ret=10 * rr / 2400.0))
    book = ShadowBook(state)
    book.books["auto-v1"] = VersionBook(version="auto-v1", started_utc=pd.Timestamp(t0 - 9000, unit="s", tz="UTC"),
                                        closed=trades)
    book.save(pd.Timestamp.now("UTC"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--info", default=str(ROOT / "web" / "e2e" / ".server.json"))
    args = ap.parse_args()
    state = tempfile.mkdtemp(prefix="goldbot-e2e-")
    seed_proposals(state)
    seed_store(str(Path(state) / "data"))
    seed_health_research(state)
    seed_automode(state)
    app =create_app(state, web_dist=ROOT / "web" / "dist", data_root=Path(state) / "data", owner_email="owner@example.com")
    Path(args.info).parent.mkdir(parents=True, exist_ok=True)
    Path(args.info).write_text(json.dumps({"setup_code": app.state.st.auth.setup_code, "state_dir": state}))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
