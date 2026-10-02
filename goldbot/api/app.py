"""FastAPI backend: typed REST for the React dashboard + WebSocket for live updates. Single owner auth
(bearer session token issued after passkey/TOTP login; TOTP path implemented, passkey later).

Routers read from the store and engine state files; writes are limited to approvals, mode and halt,
which are forwarded to the ApprovalCenter. The React app is served from web/dist when present.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from goldbot.api.auth import AuthStore, User, env_setup_code_hint, has_role
from goldbot.api.schema import (
    AcceptRequest,
    AcceptResponse,
    AccountSummary,
    AgentRow,
    AuthState,
    Decision,
    DecisionResult,
    FeedHealth,
    InviteRequest,
    InviteResponse,
    JobRow,
    LoginRequest,
    LoginResponse,
    Me,
    Ok,
    Proposal,
    Role,
    RoleChange,
    SetupRequest,
    Status,
    TotpEnrolment,
    UserRef,
    UserRow,
)
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.approvals import Proposal as CoreProposal

bearer = HTTPBearer(auto_error=False)


class State:
    def __init__(self, state_dir: str | Path, center: ApprovalCenter):
        self.dir = Path(state_dir)
        self.center = center
        self.auth = AuthStore(self.dir)
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

    def scheduler(self) -> dict:
        f = self.dir / "scheduler.json"
        try:
            return json.loads(f.read_text()) if f.exists() else {}
        except json.JSONDecodeError:
            return {}

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
               totp_verify: Callable[[str], bool] | None = None, web_dist: str | Path = "web/dist") -> FastAPI:
    center = center or ApprovalCenter(set(), totp_verify=totp_verify)
    st = State(state_dir, center)
    app = FastAPI(title="goldbot api", version="0.2")
    app.state.st = st
    hint = env_setup_code_hint(st.auth)
    if hint:
        print(hint, flush=True)

    def auth(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
        u = st.auth.session_user(creds.credentials if creds else None)
        if u is None:
            raise HTTPException(401, "login required")
        return u

    def need(role: Role) -> Callable[[User], User]:
        def dep(u: User = Depends(auth)) -> User:
            if not has_role(u, role):
                raise HTTPException(403, f"{role} role required")
            return u
        return dep

    # ------------------------------------------------------------- auth endpoints
    @app.get("/api/auth/state")
    def auth_state() -> AuthState:
        return AuthState(needs_setup=st.auth.setup_code is not None, users=len(st.auth.users))

    @app.post("/api/auth/setup")
    def setup(body: SetupRequest) -> TotpEnrolment:
        try:
            uri = st.auth.bootstrap_owner(body.setup_code, body.email, body.password)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        return TotpEnrolment(totp_uri=uri)

    @app.post("/api/auth/login")
    def login(body: LoginRequest) -> LoginResponse:
        try:
            tok = st.auth.login(body.email, body.password, body.totp)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        u = st.auth.users[body.email.lower()]
        return LoginResponse(token=tok, expires_in=12 * 3600, role=u.role, email=u.email)

    @app.post("/api/auth/logout")
    def logout(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ok:
        if creds:
            st.auth.logout(creds.credentials)
        return Ok()

    @app.post("/api/auth/invite")
    def invite(body: InviteRequest, u: User = Depends(need("owner"))) -> InviteResponse:
        try:
            tok = st.auth.create_invite(u.email, body.email, body.role)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))
        return InviteResponse(invite_token=tok, expires_h=72)

    @app.post("/api/auth/accept")
    def accept(body: AcceptRequest) -> AcceptResponse:
        try:
            email, uri = st.auth.accept_invite(body.token, body.password)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))
        return AcceptResponse(email=email, totp_uri=uri)

    @app.get("/api/users")
    def users(u: User = Depends(need("owner"))) -> list[UserRow]:
        return [UserRow.model_validate(r) for r in st.auth.list_users()]

    @app.post("/api/users/role")
    def set_role(body: RoleChange, u: User = Depends(need("owner"))) -> Ok:
        try:
            st.auth.set_role(u.email, body.email, body.role)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/users/disable")
    def disable(body: UserRef, u: User = Depends(need("owner"))) -> Ok:
        try:
            st.auth.disable(u.email, body.email)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.get("/api/me")
    def me(u: User = Depends(auth)) -> Me:
        return Me(email=u.email, role=u.role)

    @app.get("/api/accounts", response_model=list[AccountSummary])
    def accounts(_: User = Depends(auth)) -> list[AccountSummary]:
        rows = []
        for e in st.engines():
            eq, d0, w0, hwm = e.get("equity", 0), e.get("day_start_equity", 0) or 1, e.get("week_start_equity", 0) or 1, e.get("balance_closed_hwm", 0) or 1
            rows.append(AccountSummary(account_id=e["account"], broker=e.get("broker", ""), mode=e.get("mode", "paper"), equity=eq,
                                       day_pnl_pct=eq / d0 - 1, week_pnl_pct=eq / w0 - 1, drawdown_pct=1 - eq / hwm,
                                       stage=e.get("stage", "normal"), open_positions=e.get("open_positions", 0),
                                       account_class=e.get("account_class", "unknown")))
        return rows

    @app.get("/api/proposals", response_model=list[Proposal])
    def proposals(_: User = Depends(auth)) -> list[Proposal]:
        out = []
        for p in center.pending.values():
            out.append(Proposal(proposal_id=p.proposal_id, account_id=p.account_id, agent_id=p.agent_id,
                                side="long" if p.side > 0 else "short", lots=p.lots, entry=p.entry, stop=p.stop, target=p.target,
                                p=p.p, ev_r=p.ev_r, spread_points=p.spread_points, top_features=p.top_features,
                                expires_at=datetime.fromtimestamp(p.created + p.window_s, tz=timezone.utc)))
        return out

    @app.post("/api/decisions")
    async def decide(d: Decision, u: User = Depends(need("approver"))) -> DecisionResult:
        actor = next(iter(center.allowed), 0)   # the center's owner slot; the dashboard user is recorded in audit
        try:
            p = center.decide(d.proposal_id, actor, d.action == "approve", d.reason_code)
        except (KeyError, ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))
        outcome = p.outcome.value if p.outcome is not None else "PENDING"
        st.auth.audit("decision", by=u.email, proposal=p.proposal_id, outcome=outcome, reason=p.reason_code)
        await st.broadcast({"type": "decision", "proposal_id": p.proposal_id, "outcome": outcome})
        return DecisionResult(outcome=outcome)

    @app.get("/api/agents", response_model=list[AgentRow])
    def agents(_: User = Depends(auth)) -> list[AgentRow]:
        return [AgentRow(**a) for a in st.agents()]

    @app.get("/api/feeds", response_model=list[FeedHealth])
    def feeds(_: User = Depends(auth)) -> list[FeedHealth]:
        sup = st.supervisor()
        age = time.time() - sup.get("ts", 0) if sup else 1e9
        return [FeedHealth(account_id=e["account"], last_tick_age_s=e.get("last_tick_age_s", 1e9), spread_points=e.get("spread_points", 0),
                           terminal_connected=e.get("terminal_connected", False), webhook_p99_latency_s=e.get("webhook_p99_latency_s"),
                           supervisor_heartbeat_age_s=age) for e in st.engines()]

    @app.get("/api/jobs", response_model=list[JobRow])
    def jobs(_: User = Depends(auth)) -> list[JobRow]:
        sch = st.scheduler()
        if not sch:
            return []
        age = max(0.0, time.time() - datetime.fromisoformat(sch["ts"]).timestamp())   # clamp clock skew
        return [JobRow(name=n, last_slot=j.get("last_slot"), last_finished=j.get("last_finished"), last_ok=j.get("last_ok"),
                       last_error=(j.get("last_error") or "").splitlines()[0] if j.get("last_error") else None,
                       next_slot=j.get("next_slot"), runs=j.get("runs", 0), failures=j.get("failures", 0), heartbeat_age_s=age)
                for n, j in sorted(sch.get("jobs", {}).items())]

    @app.get("/api/status")
    def status() -> Status:
        return Status(mode=center.mode, halted=center.halted, pending=len(center.pending), supervisor=st.supervisor())

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
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
        def spa(path: str) -> FileResponse:
            f = dist / path
            return FileResponse(f if f.is_file() else dist / "index.html")

    return app


def make_core_proposal(**kw: Any) -> CoreProposal:  # helper for engines/tests
    return CoreProposal(**kw)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run(create_app(os.environ.get("GOLDBOT_STATE", "state")), host="127.0.0.1", port=8787)
