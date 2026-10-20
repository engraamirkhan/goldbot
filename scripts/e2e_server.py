"""Backend for the dashboard end-to-end tests: the real FastAPI app over a throwaway state directory, serving the
built web/dist, with two pending proposals seeded so the approval flow can be exercised, and a throwaway store holding a
few calendar events and headlines for the News tab, plus drift, shadow and research state for the Health and
Research tabs. The first-run setup
code is written to --info so the browser test can create the owner account the way a person would.

  python scripts/e2e_server.py --port 8790 --info web/e2e/.server.json

--fake-mailer (the auth tests, web/e2e/auth.spec.ts) turns on the owner's email sign-in link and sign-in code with the
e2e-only CaptureMailer and CaptureCodeDelivery (goldbot/api/e2e_mailer.py): the link, and the message the Telegram bot
would send, land in a JSONL outbox in the throwaway state dir (path in --info). It
is refused unless GOLDBOT_E2E_FAKE_MAILER=1 and --host is 127.0.0.1 or ::1; the same host is what uvicorn binds.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOTP_CLOCK_FILE = "totp_clock_steps"           # read by the server below, written by web/e2e/totp.ts
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import uvicorn  # noqa: E402

import goldbot  # noqa: E402
from goldbot.api import auth as auth_module  # noqa: E402
from goldbot.api.app import create_app  # noqa: E402
from goldbot.api.e2e_mailer import STATE_PREFIX, CaptureCodeDelivery, CaptureMailer  # noqa: E402
from goldbot.config import MailSettings  # noqa: E402
from goldbot.data.econ_calendar import COLUMNS as CALENDAR_COLUMNS  # noqa: E402
from goldbot.data.news import COLUMNS as NEWS_COLUMNS  # noqa: E402
from goldbot.data.store import Store  # noqa: E402
from goldbot.engine.shadow import ShadowBook  # noqa: E402
from goldbot.telegram.approvals import AskReason, Proposal  # noqa: E402
from goldbot.telegram.bus import ApprovalBus  # noqa: E402


def check_tree(goldbot_file: str | None = None) -> None:
    """Refuse to serve another checkout's code: an editable install in a different tree's venv would import goldbot
    from there, and the run would pass or fail on code this tree did not write (TD-3)."""
    where = Path(goldbot_file or goldbot.__file__).resolve()
    if not where.is_relative_to(ROOT):
        sys.exit(f"e2e_server: goldbot is imported from {where.parent}, outside this tree ({ROOT}).\n"
                 f"Run with this checkout's interpreter, e.g. PYTHON={ROOT}/.venv/bin/python "
                 f"(created by `uv venv .venv && uv pip install -e '.[dev]'` in this tree).")


def install_totp_clock(state: str) -> None:
    """Let the browser tests step the TOTP clock instead of sleeping for the next 30-s step (TD-4).

    The server accepts each authenticator code once and only within +-1 step of its clock, so a story that signs the
    same user in more than a few times can only wait for real time to pass: 30-60 s per test, and a timeout when
    the machine is slow. The clock is a seam of its own (`auth._totp_now`, "so tests can step it"); here it is the real
    time plus 30 s times the whole number in `<state>/totp_clock_steps`, which only ever grows. Verification, the
    replay guard and the +-1 window are untouched; this is an e2e script and production never imports it."""
    path = Path(state) / TOTP_CLOCK_FILE
    steps = 0

    def now() -> float:
        nonlocal steps
        try:
            steps = max(steps, int(path.read_text()))              # a half-written or missing file keeps the last value
        except (OSError, ValueError):
            pass
        return time.time() + 30.0 * steps

    auth_module._totp_now = now


def install_email_code_key() -> None:
    """A throwaway HMAC key for the emailed sign-in codes (ADR 0028), held only in this process. The API creates the
    key in the OS keyring on first use (`auth.email_code_key`); an e2e run must neither write the developer's keyring
    nor, on a host without one, leave a key in the repo's state/.secrets.json fallback. The key is seeded into the
    module's in-process cache, which `email_code_key` reads first, so the keyring is never consulted."""
    auth_module._KEY_CACHE[auth_module.EMAIL_CODE_KEY] = secrets.token_bytes(32)


def seed_proposals(state: str) -> None:
    """Two proposals as an engine would publish them to the approval bus (backlog 103): the long on a real-money
    account with the engine's own 90 s window (risk in the account currency, sized by the minimum-lot exception), the
    short on a demo account forced to ask by the re-arm lock, with a 60-minute window and USD risk only (as an older
    engine's money). Seeded last, just before the server starts, so the 90 s card is still open when the approval
    story reaches it."""
    bus = ApprovalBus(state)
    now = time.time()
    cards: dict[str, dict[str, Any]] = {
        "e2e-long": dict(account_id="icm-live", account_mode="live", window_s=90, currency="GBP", risk_amount=8.2,
                         risk_fraction=0.0082, min_lot_exception=True, risk_normal=0.005, risk_cap=0.01),
        "e2e-short": dict(account_id="icm-demo", account_mode="demo", window_s=3600,
                          ask_reason=AskReason(kind="rearm_lock", until=now + 30 * 86400))}
    for pid, side, entry in (("e2e-long", 1, 2400.25), ("e2e-short", -1, 2410.50)):
        bus.publish(Proposal(
            proposal_id=pid, agent_id="session_open-g0-e2e", side=side, lots=0.05, entry=entry,
            stop=entry - side * 4.0, target=entry + side * 6.0, p=0.62, ev_r=0.31, spread_points=22.0,
            top_features=[("atr_14", 0.4), ("adx_14", -0.2)], risk_usd=20.0, created=now, **cards[pid]))


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
    ap.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1"])   # loopback only
    ap.add_argument("--info", default=str(ROOT / "web" / "e2e" / ".server.json"))
    ap.add_argument("--fake-mailer", action="store_true", help="capture mail to an outbox (e2e only, see docstring)")
    args = ap.parse_args()
    check_tree()
    state = tempfile.mkdtemp(prefix=STATE_PREFIX)                 # 0700, this user only
    install_totp_clock(state)
    seed_store(str(Path(state) / "data"))
    seed_health_research(state)
    seed_automode(state)
    seed_proposals(state)                                         # last: the 90 s window starts as late as it can
    mail, mailer, codes, outbox = None, None, None, None
    if args.fake_mailer:
        # refused (E2EMailerRefused) unless the flag is set and args.host, the address uvicorn binds below, is loopback
        mailer = CaptureMailer(Path(state) / "e2e_outbox.jsonl", bind_host=args.host)
        codes = CaptureCodeDelivery(Path(state) / "e2e_outbox.jsonl", bind_host=args.host)    # same outbox, same guards
        install_email_code_key()                                  # the code channel is ready, so codes are offered
        outbox = str(mailer.outbox)
        url_host = f"[{args.host}]" if ":" in args.host else args.host
        # host is required once enabled; the fakes never connect anywhere
        mail = MailSettings(enabled=True, host="127.0.0.1", from_address="goldbot-e2e@example.com",
                            dashboard_origin=f"http://{url_host}:{args.port}")
    app = create_app(state, web_dist=ROOT / "web" / "dist", data_root=Path(state) / "data", owner_email="owner@example.com",
                     mail=mail, mailer=mailer, code_delivery=codes)
    Path(args.info).parent.mkdir(parents=True, exist_ok=True)
    Path(args.info).write_text(json.dumps({"setup_code": app.state.st.auth.setup_code, "state_dir": state,
                                           "outbox": outbox}))
    # Playwright stops the server with SIGTERM (gracefulShutdown). uvicorn shuts down, then re-raises the signal to
    # the handler it found: make that a clean exit, so the finally below removes the throwaway state dir.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning", timeout_graceful_shutdown=2)
    finally:
        shutil.rmtree(state, ignore_errors=True)


if __name__ == "__main__":
    main()
