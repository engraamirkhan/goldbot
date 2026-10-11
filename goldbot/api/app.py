"""FastAPI backend: typed REST for the React dashboard + WebSocket for live updates. Single owner auth
(bearer session token issued after passkey/TOTP login; TOTP path implemented, passkey later).

Routers read from the store and engine state files; writes are limited to approval decisions and the owner halt,
which go through the approval bus in the state directory (the engines are separate processes). The React app is served from web/dist when present.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from goldbot.api import entry_status, explain
from goldbot.api.auth import (
    RESET_TTL_H,
    SESSION_TTL_H,
    AuthStore,
    User,
    env_setup_code_hint,
    has_role,
)
from goldbot.api.schema import (
    AcceptRequest,
    AcceptResponse,
    AccountSummary,
    ActiveBlackout,
    AgentRow,
    AgentRunRow,
    AuthState,
    AutoModeView,
    CalendarEvent,
    CalendarResponse,
    ChangePasswordRequest,
    DecidedProposal,
    Decision,
    DecisionResult,
    FeedHealth,
    ForgotPasswordRequest,
    HaltRequest,
    Headline,
    HealthView,
    InviteRequest,
    InviteResponse,
    JobRow,
    LoginRequest,
    LoginResponse,
    Me,
    ModeChangeRequest,
    ModeChangeResult,
    Ok,
    Proposal,
    RearmRequest,
    RecoveryCodesRequest,
    RecoveryCodesResponse,
    RecoveryLoginRequest,
    RecoveryLoginResponse,
    ResearchView,
    ResetLinkResponse,
    ResetRequest,
    RevokeResponse,
    Role,
    RoleChange,
    SetupRequest,
    SetupResponse,
    Status,
    UserRef,
    UserRow,
)
from goldbot.ops.account_view import LossBudget, loss_budget, margin_level_pct, milestones, proposal_budget
from goldbot.telegram.approvals import Outcome
from goldbot.telegram.approvals import Proposal as BusProposal
from goldbot.telegram.bus import ApprovalBus

log = logging.getLogger("goldbot.api")

if TYPE_CHECKING:
    from goldbot.agents.tools import ReadOnlyTools
    from goldbot.config import Settings

bearer = HTTPBearer(auto_error=False)


class State:
    def __init__(self, state_dir: str | Path, data_root: str | Path | None = None, owner_email: str | None = None):
        self.dir = Path(state_dir)
        self.bus = ApprovalBus(self.dir)
        self.auth = AuthStore(self.dir, owner_email=owner_email)
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
            try:                               # torn (ValueError) or unreadable (OSError): skipped, never a 500
                out.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                log.warning("engine state %s unreadable: skipped", f.name)
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


def _settings_owner_email() -> str | None:
    """auth.owner_email from settings (settings.local.yaml on the server); unreadable settings mean unset."""
    try:
        from goldbot.config import load_settings
        return load_settings().auth.owner_email
    except Exception as exc:                    # noqa: BLE001 - the API still serves; bootstrap stays refused
        print(f"goldbot api: settings unreadable, auth.owner_email unset: {exc}", flush=True)
        return None


LOOPBACK = {"127.0.0.1", "::1"}
CODES_LOCKED = "too many attempts: re-arm and auto mode are locked for 15 minutes; /halt still works"


def client_ip(request: Request) -> str | None:
    """The caller's address for the per-IP lockout. The API listens on 127.0.0.1 behind the Cloudflare tunnel, so a
    loopback peer is cloudflared and the visitor's address is its CF-Connecting-IP header; any other peer is used as
    is (a header from a non-local peer is never trusted)."""
    host = request.client.host if request.client else None
    if host in LOOPBACK:
        fwd = request.headers.get("cf-connecting-ip", "").strip()
        if fwd:
            return fwd[:64]
    return host


def create_app(state_dir: str | Path = "state", web_dist: str | Path = "web/dist",
               data_root: str | Path | None = None, docs_dir: str | Path | None = None,
               owner_email: str | None = None) -> FastAPI:
    """owner_email: the dashboard owner; None reads `auth.owner_email` from settings."""
    st = State(state_dir, data_root, owner_email if owner_email is not None else _settings_owner_email())
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
        """By rank; "owner" also needs the account to be auth.owner_email (AuthStore.is_owner, fails closed)."""
        def dep(u: User = Depends(auth)) -> User:
            if not has_role(u, role) or (role == "owner" and not st.auth.is_owner(u)):
                raise HTTPException(403, f"{role} role required")
            return u
        return dep

    def shown(u: User, text: str) -> str:
        """Fault text from the services (job errors): the owner reads it whole; other roles get the exception type
        and no paths (explain.redact)."""
        return text if st.auth.is_owner(u) else explain.redact(text)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """422 with each error's type, location and message only: FastAPI's default also echoes `input` (the rejected
        value, e.g. a password under a misspelt key, or the whole body on a `missing` error) and `ctx`."""
        return JSONResponse(status_code=422, content={
            "detail": [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]})

    @app.middleware("http")
    async def no_referrer(request: Request, call_next: Callable[[Request], Any]) -> Response:
        # invite and reset links carry their token in the URL fragment; this also keeps any URL out of Referer
        response: Response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            # session tokens, recovery codes, TOTP enrolment URIs and invite/reset tokens must never sit in a cache
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------- auth endpoints
    @app.get("/api/auth/state")
    def auth_state() -> AuthState:
        return AuthState(needs_setup=st.auth.setup_code is not None, users=st.auth.user_count())

    @app.post("/api/auth/setup")
    def setup(body: SetupRequest) -> SetupResponse:
        try:
            e = st.auth.bootstrap_owner(body.setup_code, body.email, body.password)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return SetupResponse(totp_uri=e.totp_uri, recovery_codes=e.recovery_codes)

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request) -> LoginResponse:
        try:
            tok = st.auth.login(body.email, body.password, body.totp, ip=client_ip(request))
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

    @app.post("/api/auth/password/change")
    def change_password(body: ChangePasswordRequest, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ok:
        if creds is None or st.auth.session_user(creds.credentials) is None:
            raise HTTPException(401, "login required")
        try:
            st.auth.change_password(creds.credentials, body.current_password, body.totp, body.new_password)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/auth/password/forgot")
    def forgot_password(body: ForgotPasswordRequest, request: Request) -> Ok:
        try:
            st.auth.forgot_password(body.email, body.totp, body.new_password, recovery_code=body.recovery_code or None,
                                    ip=client_ip(request))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/auth/reset-link")
    def reset_link(body: UserRef, u: User = Depends(need("owner"))) -> ResetLinkResponse:
        try:
            tok = st.auth.create_reset_link(u.email, body.email)
        except (KeyError, PermissionError) as exc:
            raise HTTPException(400, f"no such user: {exc}" if isinstance(exc, KeyError) else str(exc))
        return ResetLinkResponse(reset_token=tok, expires_h=RESET_TTL_H)

    @app.post("/api/auth/reset")
    def reset(body: ResetRequest) -> AcceptResponse:
        try:
            e = st.auth.use_reset_link(body.token, body.new_password)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))
        return AcceptResponse(email=e.email, totp_uri=e.totp_uri)

    @app.post("/api/auth/recovery/login")
    def recovery_login(body: RecoveryLoginRequest, request: Request) -> RecoveryLoginResponse:
        try:
            r = st.auth.recovery_login(body.email, body.password, body.recovery_code, ip=client_ip(request))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        return RecoveryLoginResponse(token=r.token, expires_in=SESSION_TTL_H * 3600, role=r.role, email=r.email,
                                     totp_uri=r.totp_uri, recovery_codes_left=r.recovery_codes_left)

    @app.post("/api/auth/recovery/codes")
    def recovery_codes(body: RecoveryCodesRequest, u: User = Depends(need("owner")),
                       creds: HTTPAuthorizationCredentials = Depends(bearer)) -> RecoveryCodesResponse:
        try:
            codes = st.auth.regenerate_recovery_codes(creds.credentials, body.password, body.totp)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        return RecoveryCodesResponse(recovery_codes=codes)

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

    @app.post("/api/users/enable")
    def enable(body: UserRef, u: User = Depends(need("owner"))) -> Ok:
        try:
            st.auth.enable(u.email, body.email)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/users/revoke-sessions")
    def revoke_sessions(body: UserRef, u: User = Depends(need("owner"))) -> RevokeResponse:
        try:
            n = st.auth.revoke_sessions(u.email, body.email)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return RevokeResponse(sessions=n)

    @app.get("/api/me")
    def me(u: User = Depends(auth)) -> Me:
        return Me(email=u.email, role=u.role)

    @app.get("/api/accounts", response_model=list[AccountSummary])
    def accounts(u: User = Depends(auth)) -> list[AccountSummary]:
        rows = []
        engines = st.engines()
        try:                                   # a torn or missing supervisor.json only drops the combined caps
            sup = st.supervisor()
        except (OSError, ValueError):
            sup = {}
        sup = sup if isinstance(sup, dict) else {}

        def view(fn: Callable[..., Any], *args: Any) -> Any:
            """The account view is reporting only: a bad state file shows as null, never as a failed request."""
            try:
                return fn(*args)
            except Exception:                  # noqa: BLE001
                log.exception("account view %s failed", getattr(fn, "__name__", fn))
                return None

        status_inputs = view(entry_inputs, st.auth.is_owner(u))
        for e in engines:
            def num(k: str, e: dict = e) -> float:
                v = e.get(k)
                return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            eq, d0, w0, hwm = num("equity"), num("day_start_equity") or 1, num("week_start_equity") or 1, num("balance_closed_hwm") or 1
            bal, ccy = e.get("balance"), e.get("currency")
            budget = view(loss_budget, e, sup, engines)
            ladder = view(milestones, e)
            rows.append(AccountSummary(account_id=e["account"], broker=e.get("broker", ""), mode=e.get("mode", "paper"), equity=eq,
                                       day_pnl_pct=eq / d0 - 1, week_pnl_pct=eq / w0 - 1, drawdown_pct=1 - eq / hwm,
                                       stage=e.get("stage", "normal"), open_positions=e.get("open_positions", 0),
                                       account_class=e.get("account_class", "unknown"),
                                       balance=bal if isinstance(bal, (int, float)) else None,
                                       currency=ccy if isinstance(ccy, str) else None,
                                       margin_level_pct=view(margin_level_pct, e),
                                       day_pnl=eq - num("day_start_equity") if num("day_start_equity") else None,
                                       loss_budget=budget, milestones=ladder,
                                       entry_status=(None if status_inputs is None else
                                                     view(account_entry_status, e, budget, ladder, status_inputs))
                                       or entry_status.unknown_status()))
        return rows

    def entry_inputs(is_owner: bool) -> dict[str, Any]:
        """What every account's entry status shares: the owner's halt, the supervisor's halt as the engines read it,
        the drift watch's system halt, the RiskGate's and the supervisor's limits from settings (the defaults when
        unreadable) and whether this viewer may re-arm."""
        from goldbot.risk.gate import RiskLimits
        from goldbot.risk.supervisor import Supervisor, SupervisorLimits
        c = st.bus.control()
        halt, why = Supervisor.engine_should_halt(st.dir)
        s = view_settings()
        try:
            limits = RiskLimits.from_settings(s.risk, tiny_live=False) if s is not None else RiskLimits()
            sup_limits = SupervisorLimits.from_settings(s.risk) if s is not None else SupervisorLimits()
        except Exception:                      # noqa: BLE001 - a bad settings edit: the defaults
            limits, sup_limits = RiskLimits(), SupervisorLimits()
        return dict(owner_halt=(c.by or "unknown") if c.halted else None,
                    supervisor_reasons=[r for r in why.split(",") if r] if halt else None,
                    drift_reasons=st.drift_halt(), limits=limits, combined_dd=sup_limits.dd_stage2, is_owner=is_owner)

    def account_entry_status(e: dict, budget: LossBudget | None, ladder: Any, inputs: dict[str, Any]) -> Any:
        return entry_status.account_entry_status(e, now=pd.Timestamp.now("UTC"), budget=budget, milestones=ladder,
                                                 **inputs)

    def card(p: BusProposal, budget: LossBudget | None = None) -> dict[str, Any]:
        return dict(proposal_id=p.proposal_id, account_id=p.account_id, agent_id=p.agent_id,
                    side="long" if p.side > 0 else "short", lots=p.lots, entry=p.entry, stop=p.stop, target=p.target,
                    p=p.p, ev_r=p.ev_r, spread_points=p.spread_points, top_features=p.top_features, risk_usd=p.risk_usd,
                    currency=p.currency, risk_amount=p.risk_amount, risk_fraction=p.risk_fraction,
                    min_lot_exception=p.min_lot_exception, risk_normal=p.risk_normal, risk_cap=p.risk_cap,
                    reward_amount=p.reward_amount, loss_left=budget.remaining if budget else None,
                    loss_left_period=budget.period if budget else None,
                    expires_at=datetime.fromtimestamp(p.created + p.window_s, tz=timezone.utc))

    @app.get("/api/proposals", response_model=list[Proposal])
    def proposals(_: User = Depends(auth)) -> list[Proposal]:
        pending = st.bus.pending()
        engines: list[dict] = []
        sup: dict = {}
        if pending:
            try:                               # reporting only: unreadable state leaves the budget unknown
                engines = st.engines()
            except (OSError, ValueError):
                log.exception("engine states unreadable: proposal budgets unknown")
            try:                               # a torn supervisor.json only drops the combined caps
                s = st.supervisor()
                sup = s if isinstance(s, dict) else {}
            except (OSError, ValueError):
                sup = {}

        def budget(p: BusProposal) -> LossBudget | None:
            """The loss budget left if this trade loses, in the account currency only (never a USD fallback)."""
            try:
                return proposal_budget(engines, sup, p.account_id, p.risk_amount)
            except Exception:                  # noqa: BLE001 - reporting only: a bad state file shows as unknown
                log.exception("proposal budget failed")
                return None
        return [Proposal(**card(p, budget(p))) for p in pending]

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
    async def rearm(body: RearmRequest, request: Request, u: User = Depends(need("owner"))) -> Status:
        # each code once (replay refused); wrong codes count towards the code lockout (`code:<email>`, shared with
        # /mode; rearm_failed / rearm_locked). The SQLite work runs off the event loop.
        try:
            ok = await asyncio.to_thread(st.auth.verify_code_guarded, body.totp, "rearm", email=u.email,
                                         ip=client_ip(request))
        except PermissionError:
            raise HTTPException(403, CODES_LOCKED)
        if not ok:
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
    def jobs(u: User = Depends(auth)) -> list[JobRow]:
        sch = st.scheduler()
        if not sch:
            return []
        age = max(0.0, time.time() - datetime.fromisoformat(sch["ts"]).timestamp())   # clamp clock skew
        return [JobRow(name=n, last_slot=j.get("last_slot"), last_finished=j.get("last_finished"), last_ok=j.get("last_ok"),
                       last_error=shown(u, (j.get("last_error") or "").splitlines()[0]) if j.get("last_error") else None,
                       next_slot=j.get("next_slot"), runs=j.get("runs", 0), failures=j.get("failures", 0), heartbeat_age_s=age)
                for n, j in sorted(sch.get("jobs", {}).items())]

    @app.get("/api/agent-runs", response_model=list[AgentRunRow])
    def agent_runs(u: User = Depends(auth)) -> list[AgentRunRow]:
        owner = st.auth.is_owner(u)                 # account-security notices are the owner's alone
        return [AgentRunRow(role=r["role"], started_utc=r["started_utc"], status=r["status"], turns=r.get("turns", 0),
                            cost_usd=r.get("cost_usd", 0.0), detail=r.get("detail"), report=r.get("report"))
                for r in st.agent_runs() if owner or r.get("role") != "account_security"]

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

    # ============================================================== Health and Research screens (read-only, any role)
    # Built by goldbot/api/explain.py from drift.json, the shadow book, the health checks, the trial registry, the
    # research plan and docs/research/hypotheses.md. Nothing here writes.
    docs = Path(docs_dir) if docs_dir is not None else Path(__file__).resolve().parents[2] / "docs"

    def view_settings() -> Settings | None:
        from goldbot.config import load_settings
        try:
            s = load_settings()
        except Exception:                     # a bad settings edit: the views fall back to the design defaults
            return None
        return s.model_copy(update={"data_root": str(data_root)}) if data_root is not None else s

    @app.get("/api/health", response_model=HealthView)
    def health_view(u: User = Depends(auth)) -> HealthView:
        return explain.health_view(st.dir, view_settings(), is_owner=st.auth.is_owner(u))

    @app.get("/api/research", response_model=ResearchView)
    def research_view(_: User = Depends(auth)) -> ResearchView:
        return explain.research_view(st.dir, view_settings(), docs)
    # ============================================================== end Health and Research screens

    # ============================================================== auto mode (A10): card for any role, switch owner-only
    # The switch runs telegram/automode.py `mode_command`, the code behind Telegram's /mode, with the same checks:
    # the owner pinned to auth.owner_email (need("owner") + verify_owner_totp), each authenticator code spent once
    # (the replay guard shared with sign-in, re-arm and /mode) inside the code lockout (verify_code_guarded, key
    # `code:<email>`, shared with re-arm and Telegram /mode; mode_failed / mode_locked; it never locks sign-in, and
    # failed sign-ins lock it too), then the evidence check. Switching to propose needs no code and is never locked. Every outcome is audited by
    # mode_command. The engines still force propose under the 30-day re-arm lock and the 12% kill switch.
    @app.get("/api/automode", response_model=AutoModeView)
    def automode_get(u: User = Depends(auth)) -> AutoModeView:
        return explain.automode_view(st.dir, view_settings(), st.auth.is_owner(u))

    @app.post("/api/automode", response_model=ModeChangeResult)
    async def automode_set(body: ModeChangeRequest, request: Request,
                           u: User = Depends(need("owner"))) -> ModeChangeResult:
        from goldbot.config import TelegramSettings
        from goldbot.telegram import automode
        s = view_settings()
        tg = s.telegram if s is not None else TelegramSettings()
        seen: dict[str, bool] = {}
        ip = client_ip(request)

        def totp_ok(code: str) -> bool:
            try:        # the owner's lockout counter, shared with sign-in, re-arm and Telegram /mode
                seen["totp"] = st.auth.verify_code_guarded(code, "mode", ip=ip)
            except PermissionError:
                seen["locked"] = True
                raise
            return seen["totp"]

        def eligibility() -> automode.Eligibility:
            e = automode.auto_mode_eligibility_from_state(st.dir, tg)
            seen["eligible"] = e.eligible
            return e

        code = body.totp.strip()
        arg = "propose" if body.mode == "propose" else ("auto " + code if code.isdigit() else "auto")
        msg = await asyncio.to_thread(automode.mode_command, st.bus, arg, f"dashboard:{u.email}", totp_ok=totp_ok,
                                      eligibility=eligibility, audit=st.auth.audit)
        if seen.get("locked"):
            raise HTTPException(403, CODES_LOCKED)
        if body.mode == "auto" and not seen.get("totp"):
            raise HTTPException(403, "authenticator code required to enable auto mode")
        if body.mode == "auto" and not seen.get("eligible"):
            raise HTTPException(409, msg)
        await st.broadcast({"type": "mode", "mode": body.mode})
        return ModeChangeResult(message=msg, view=explain.automode_view(st.dir, s, True))
    # ============================================================== end auto mode

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
