"""FastAPI backend: typed REST for the React dashboard + WebSocket for live updates. Single owner auth
(bearer session token issued after passkey/TOTP login; TOTP path implemented, passkey later).

Routers read from the store and engine state files; writes are limited to approval decisions and the owner halt,
which go through the approval bus in the state directory (the engines are separate processes). The React app is served from web/dist when present.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from goldbot.api.auth import AuthStore, User, env_setup_code_hint, has_role, totp_verify
from goldbot.api.schema import (
    AcceptRequest,
    AcceptResponse,
    AccountSummary,
    ActiveBlackout,
    AgentRow,
    AgentRunRow,
    AuthState,
    CalendarEvent,
    CalendarResponse,
    DecidedProposal,
    Decision,
    DecisionResult,
    FeedHealth,
    HaltRequest,
    Headline,
    InviteRequest,
    InviteResponse,
    JobRow,
    LoginRequest,
    LoginResponse,
    Me,
    Ok,
    Proposal,
    RearmRequest,
    Role,
    RoleChange,
    SetupRequest,
    Status,
    TotpEnrolment,
    UserRef,
    UserRow,
)
from goldbot.telegram.approvals import Outcome
from goldbot.telegram.approvals import Proposal as BusProposal
from goldbot.telegram.bus import ApprovalBus

if TYPE_CHECKING:
    from goldbot.agents.tools import ReadOnlyTools

bearer = HTTPBearer(auto_error=False)


class State:
    def __init__(self, state_dir: str | Path, data_root: str | Path | None = None):
        self.dir = Path(state_dir)
        self.bus = ApprovalBus(self.dir)
        self.auth = AuthStore(self.dir)
        self.ws_clients: set[WebSocket] = set()
        self._data_root = data_root
        self._tools: ReadOnlyTools | None = None

    def tools(self) -> ReadOnlyTools:
        """The agents' read-only store tools (calendar, headlines), opened on first use: the store root comes from
        settings.data_root unless create_app was given one, so apps that never read the store never touch it."""
        if self._tools is None:
            from goldbot.agents.tools import ReadOnlyTools
            from goldbot.config import load_settings
            from goldbot.data.store import Store
            root = self._data_root if self._data_root is not None else load_settings().data_root
            self._tools = ReadOnlyTools(self.dir, Store(root))
        return self._tools

    def active_blackout(self) -> ActiveBlackout | None:
        """The entry blackout the engines report (calendar event or news shock), with the accounts enforcing it."""
        found: dict[tuple[str, str], tuple[dict[str, Any], list[str]]] = {}
        for e in self.engines():
            b = e.get("blackout")
            if not isinstance(b, dict) or not b.get("title"):
                continue
            kind = "news_shock" if b.get("kind") == "news_shock" else "calendar"
            key = (kind, str(b["title"]))
            found.setdefault(key, (b, []))[1].append(str(e.get("account", "?")))
        if not found:
            return None
        (kind, title), (b, accounts) = next(iter(found.items()))
        return ActiveBlackout(kind="news_shock" if kind == "news_shock" else "calendar", title=title,
                              ts_utc=b.get("ts_utc"), received_utc=b.get("received_utc"), accounts=sorted(accounts))

    def engines(self) -> list[dict]:
        out = []
        for f in self.dir.glob("engine_*.json"):
            try:
                out.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                pass
        return out

    def drift_halt(self) -> list[str] | None:
        """Reasons for the drift watch's system halt (state/drift.json), None when there is none. Read the way the
        engines read it: missing restricts nothing, unreadable or invalid halts entries (fail closed)."""
        f = self.dir / "drift.json"
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text())
        except (ValueError, OSError):
            return ["drift.json unreadable"]
        if not isinstance(d, dict):
            return ["drift.json invalid"]
        sh = d.get("system_halt")
        if not sh:
            return None
        reasons = sh.get("reasons") if isinstance(sh, dict) else None
        return [str(r) for r in reasons] if isinstance(reasons, list) and reasons else ["drift system halt"]

    def supervisor(self) -> dict:
        f = self.dir / "supervisor.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def scheduler(self) -> dict:
        f = self.dir / "scheduler.json"
        try:
            return json.loads(f.read_text()) if f.exists() else {}
        except json.JSONDecodeError:
            return {}

    def agent_runs(self, limit: int = 30) -> list[dict]:
        f = self.dir / "agent_runs.jsonl"
        if not f.exists():
            return []
        rows = []
        for line in f.read_text().splitlines()[-limit:]:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = r.get("report_path")
            rp = Path(path) if path else None
            # only reports inside this state dir are served
            ok = rp is not None and rp.exists() and self.dir.resolve() in rp.resolve().parents
            r["report"] = rp.read_text() if ok and rp is not None else None
            rows.append(r)
        return rows[::-1]

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


def create_app(state_dir: str | Path = "state", web_dist: str | Path = "web/dist",
               data_root: str | Path | None = None) -> FastAPI:
    st = State(state_dir, data_root)
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
        return AuthState(needs_setup=st.auth.setup_code is not None, users=st.auth.user_count())

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
        u = st.auth.session_user(tok)
        if u is None:                                   # disabled between the check and now
            raise HTTPException(403, "invalid credentials")
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

    def card(p: BusProposal) -> dict[str, Any]:
        return dict(proposal_id=p.proposal_id, account_id=p.account_id, agent_id=p.agent_id,
                    side="long" if p.side > 0 else "short", lots=p.lots, entry=p.entry, stop=p.stop, target=p.target,
                    p=p.p, ev_r=p.ev_r, spread_points=p.spread_points, top_features=p.top_features, risk_usd=p.risk_usd,
                    expires_at=datetime.fromtimestamp(p.created + p.window_s, tz=timezone.utc))

    @app.get("/api/proposals", response_model=list[Proposal])
    def proposals(_: User = Depends(auth)) -> list[Proposal]:
        return [Proposal(**card(p)) for p in st.bus.pending()]

    @app.get("/api/proposals/recent", response_model=list[DecidedProposal])
    def recent_proposals(_: User = Depends(auth)) -> list[DecidedProposal]:
        """Proposals decided in the last 10 minutes (newest first), so a decided card says what happened to it."""
        out = []
        for p, d in st.bus.recent():
            if d is not None:                       # decided, the engine has not applied it yet
                out.append(DecidedProposal.model_validate({**card(p), "status": "submitted" if d.approve else "rejected",
                                                           "reason_code": None if d.approve else d.reason_code,
                                                           "decided_by": d.by}))
                continue
            if p.outcome == Outcome.APPROVED:
                status = "refused" if p.gate_refusal else "approved"
            elif p.outcome == Outcome.REJECTED:
                status = "rejected"
            else:
                status = "expired"
            by = p.decided_via or (f"telegram:{p.decided_by}" if p.decided_by is not None else None)
            out.append(DecidedProposal.model_validate({**card(p), "status": status, "decided_by": by,
                                                       "reason_code": p.reason_code if status == "rejected" else None,
                                                       "refusal": list(p.gate_refusal or [])}))
        return out

    @app.post("/api/decisions")
    async def decide(d: Decision, u: User = Depends(need("approver"))) -> DecisionResult:
        try:
            st.bus.submit(d.proposal_id, d.action == "approve", d.reason_code, by=f"dashboard:{u.email}")
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc).strip("'\""))
        st.auth.audit("decision", by=u.email, proposal=d.proposal_id, action=d.action, reason=d.reason_code)
        await st.broadcast({"type": "decision", "proposal_id": d.proposal_id, "action": d.action})
        return DecisionResult(outcome="SUBMITTED")

    @app.post("/api/halt")
    async def halt(body: HaltRequest, u: User = Depends(need("approver"))) -> Status:
        # stopping new entries needs no second factor (design); exits and open positions are untouched
        st.bus.set_halt(True, by=f"dashboard:{u.email}", reason=body.reason)
        st.auth.audit("halt", by=u.email, reason=body.reason)
        await st.broadcast({"type": "halt", "halted": True})
        return current_status()

    @app.post("/api/rearm")
    async def rearm(body: RearmRequest, u: User = Depends(need("owner"))) -> Status:
        if not totp_verify(u.totp_secret, body.totp):
            st.auth.audit("rearm_failed", by=u.email)
            raise HTTPException(403, "authenticator code required to re-arm")
        c = st.bus.owner_rearm(by=f"dashboard:{u.email}")      # also clears the engines' drawdown halts
        st.auth.audit("rearm", by=u.email, rearm_id=c.rearm_id)
        await st.broadcast({"type": "halt", "halted": False})
        return current_status()

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

    @app.get("/api/agent-runs", response_model=list[AgentRunRow])
    def agent_runs(_: User = Depends(auth)) -> list[AgentRunRow]:
        return [AgentRunRow(role=r["role"], started_utc=r["started_utc"], status=r["status"], turns=r.get("turns", 0),
                            cost_usd=r.get("cost_usd", 0.0), detail=r.get("detail"), report=r.get("report"))
                for r in st.agent_runs()]

    @app.get("/api/calendar", response_model=CalendarResponse)
    def calendar(days: int = 7, max_tier: int = 2, _: User = Depends(auth)) -> CalendarResponse:
        out = st.tools().read_calendar(min(max(days, 1), 14), min(max(max_tier, 1), 3))
        events = []
        for e in out["events"]:
            start, end = e.get("blackout_utc") or (None, None)
            events.append(CalendarEvent.model_validate({**e, "impact": e["impact"] or "", "forecast": e["forecast"] or "",
                                                        "previous": e["previous"] or "", "blackout_start": start,
                                                        "blackout_end": end}))
        return CalendarResponse(now=out["now"], events=events, active_blackout=st.active_blackout(), note=out.get("note"))

    @app.get("/api/news", response_model=list[Headline])
    def news(hours: int = 24, min_relevance: float = 0.0, _: User = Depends(auth)) -> list[Headline]:
        mr = min(max(min_relevance, 0.0), 1.0) if min_relevance == min_relevance else 0.0   # NaN -> 0
        rows = st.tools().read_headlines(min(max(hours, 1), 72), mr, extra=("item_id", "ts_utc", "link"))
        tags = ("rates", "risk", "dollar", "surprise")      # unscored rows store "" for the direction tags
        return [Headline.model_validate({**r, "link": r.get("link") or None, "shock": bool(r["shock"]),
                                         **{k: r[k] or None for k in tags}}) for r in rows]

    def current_status() -> Status:
        modes = sorted({str(e.get("approval_mode", "propose")) for e in st.engines()})
        c = st.bus.control()
        sup = st.supervisor()
        drift = st.drift_halt()
        return Status(mode=",".join(modes) or "propose", halted=c.halted, halted_by=c.by if c.halted else None,
                      halt_reason=c.reason if c.halted else None, pending=len(st.bus.pending()), supervisor=sup,
                      supervisor_halt=bool(sup.get("halt")), supervisor_reasons=[str(r) for r in sup.get("reasons") or []],
                      drift_halt=drift is not None, drift_reasons=drift or [], blackout=st.active_blackout())

    @app.get("/api/status")
    def status(_: User = Depends(auth)) -> Status:
        return current_status()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        # browsers cannot set headers on a WebSocket, so the session token is the first message: {"token": "..."}
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            token = str(first.get("token", "")) if isinstance(first, dict) else ""
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            token = ""
        if st.auth.session_user(token) is None:
            await websocket.close(code=4401)
            return
        await websocket.send_json({"type": "hello"})
        st.ws_clients.add(websocket)
        try:
            while True:
                await asyncio.sleep(30)
                if st.auth.session_user(token) is None:      # logged out, expired or disabled: stop the feed
                    await websocket.close(code=4401)
                    break
                await websocket.send_json({"type": "ping", "t": time.time()})
        except WebSocketDisconnect:
            pass
        finally:
            st.ws_clients.discard(websocket)

    dist = Path(web_dist).resolve()
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            # the path arrives percent-decoded ("..%2f" -> "../"): serve only files that resolve inside web/dist
            f = (dist / path).resolve()
            return FileResponse(f if f.is_file() and dist in f.parents else dist / "index.html")

    return app


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run(create_app(os.environ.get("GOLDBOT_STATE", "state"), data_root=os.environ.get("GOLDBOT_DATA")),
                host="127.0.0.1", port=8787)
