"""Backend for the dashboard end-to-end tests: the real FastAPI app over a throwaway state directory, serving the
built web/dist, with two pending proposals seeded so the approval flow can be exercised, and a throwaway store holding a
few calendar events and headlines for the News tab. The first-run setup
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--info", default=str(ROOT / "web" / "e2e" / ".server.json"))
    args = ap.parse_args()
    state = tempfile.mkdtemp(prefix="goldbot-e2e-")
    seed_proposals(state)
    seed_store(str(Path(state) / "data"))
    app = create_app(state, web_dist=ROOT / "web" / "dist", data_root=Path(state) / "data")
    Path(args.info).parent.mkdir(parents=True, exist_ok=True)
    Path(args.info).write_text(json.dumps({"setup_code": app.state.st.auth.setup_code, "state_dir": state}))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
