"""FastAPI backend: typed REST for the React dashboard + WebSocket for live updates. Single owner auth
(bearer session token issued after passkey/TOTP login; TOTP path implemented, passkey later).

Routers read from the store and engine state files; writes are limited to approvals, mode and halt,
which are forwarded to the ApprovalCenter. The React app is served from web/dist when present.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from goldbot.api.schema import AccountSummary, AgentRow, Decision, FeedHealth, Proposal
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.approvals import Proposal as CoreProposal

bearer = HTTPBearer(auto_error=False)


class State:
    def __init__(self, state_dir: str | Path, center: ApprovalCenter):
        self.dir = Path(state_dir)
        self.center = center
        self.sessions: dict[str, float] = {}
        self.ws_clients: set[WebSocket] = set()

    def engines(self) -> list[dict]:
        out = []
        for f in self.dir.glob("engine_*.json"):
            try:
                out.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                pass
        return out

    def supervisor(self) -> dict:
        f = self.dir / "supervisor.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def agents(self) -> list[dict]:
        f = self.dir / "agents.json"
        return json.loads(f.read_text()) if f.exists() else []

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws in self.ws_clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)


def create_app(state_dir: str | Path = "state", center: ApprovalCenter | None = None,
               totp_verify=None, web_dist: str | Path = "web/dist") -> FastAPI:
    center = center or ApprovalCenter(set(), totp_verify=totp_verify)
    st = State(state_dir, center)
    app = FastAPI(title="goldbot api", version="0.1")
    app.state.st = st

    def auth(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
        if creds is None or creds.credentials not in st.sessions or st.sessions[creds.credentials] < time.time():
            raise HTTPException(401, "login required")
        return creds.credentials

    @app.post("/api/login")
    def login(body: dict):
        code = str(body.get("totp", ""))
        if not (totp_verify and totp_verify(code)):
            raise HTTPException(403, "bad code")
        tok = secrets.token_urlsafe(32)
        st.sessions[tok] = time.time() + 12 * 3600
        return {"token": tok, "expires_in": 12 * 3600}

    @app.get("/api/accounts", response_model=list[AccountSummary])
    def accounts(_=Depends(auth)):
        rows = []
        for e in st.engines():
            eq, d0, w0, hwm = e.get("equity", 0), e.get("day_start_equity", 0) or 1, e.get("week_start_equity", 0) or 1, e.get("balance_closed_hwm", 0) or 1
            rows.append(AccountSummary(account_id=e["account"], broker=e.get("broker", ""), mode=e.get("mode", "paper"), equity=eq,
                                       day_pnl_pct=eq / d0 - 1, week_pnl_pct=eq / w0 - 1, drawdown_pct=1 - eq / hwm,
                                       stage=e.get("stage", "normal"), open_positions=e.get("open_positions", 0),
                                       account_class=e.get("account_class", "unknown")))
        return rows

    @app.get("/api/proposals", response_model=list[Proposal])
    def proposals(_=Depends(auth)):
        out = []
        for p in center.pending.values():
            out.append(Proposal(proposal_id=p.proposal_id, account_id=p.account_id, agent_id=p.agent_id,
                                side="long" if p.side > 0 else "short", lots=p.lots, entry=p.entry, stop=p.stop, target=p.target,
                                p=p.p, ev_r=p.ev_r, spread_points=p.spread_points, top_features=p.top_features,
                                expires_at=datetime.fromtimestamp(p.created + p.window_s, tz=timezone.utc)))
        return out

    @app.post("/api/decisions")
    async def decide(d: Decision, _=Depends(auth)):
        owner = next(iter(center.allowed), 0)
        try:
            p = center.decide(d.proposal_id, owner, d.action == "approve", d.reason_code)
        except (KeyError, ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))
        await st.broadcast({"type": "decision", "proposal_id": p.proposal_id, "outcome": p.outcome.value})
        return {"outcome": p.outcome.value}

    @app.get("/api/agents", response_model=list[AgentRow])
    def agents(_=Depends(auth)):
        return [AgentRow(**a) for a in st.agents()]

    @app.get("/api/feeds", response_model=list[FeedHealth])
    def feeds(_=Depends(auth)):
        sup = st.supervisor()
        age = time.time() - sup.get("ts", 0) if sup else 1e9
        return [FeedHealth(account_id=e["account"], last_tick_age_s=e.get("last_tick_age_s", 1e9), spread_points=e.get("spread_points", 0),
                           terminal_connected=e.get("terminal_connected", False), webhook_p99_latency_s=e.get("webhook_p99_latency_s"),
                           supervisor_heartbeat_age_s=age) for e in st.engines()]

    @app.get("/api/status")
    def status():
        return {"mode": center.mode, "halted": center.halted, "pending": len(center.pending), "supervisor": st.supervisor()}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        st.ws_clients.add(websocket)
        try:
            while True:
                await asyncio.sleep(30)
                await websocket.send_json({"type": "ping", "t": time.time()})
        except WebSocketDisconnect:
            st.ws_clients.discard(websocket)

    dist = Path(web_dist)
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = dist / path
            return FileResponse(f if f.is_file() else dist / "index.html")

    return app


def make_core_proposal(**kw) -> CoreProposal:  # helper for engines/tests
    return CoreProposal(**kw)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run(create_app(os.environ.get("GOLDBOT_STATE", "state")), host="127.0.0.1", port=8787)
