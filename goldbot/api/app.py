"""FastAPI backend: typed REST for the React dashboard + WebSocket for live updates. Single owner auth
(bearer session token issued after passkey/TOTP login; TOTP path implemented, passkey later).

Routers read from the store and engine state files; writes are limited to approval decisions and the owner halt,
which go through the approval bus in the state directory (the engines are separate processes). The React app is served from web/dist when present.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from goldbot.api import (
    activity,
    chart_layouts,
    entry_status,
    explain,
    home_cards,
    limits,
    mt5_crop,
    progress,
    spread_normal,
    trades_overview,
)
from goldbot.api.auth import (
    EMAIL_CODE_TTL_S,
    EMAIL_LINK_TTL_S,
    RESET_TTL_H,
    SESSION_TTL_H,
    AuthStore,
    EmailCodeIssue,
    User,
    coarse_ip,
    env_setup_code_hint,
    has_role,
    need_stepup,
)
from goldbot.api.code_delivery import (
    CodeDelivery,
    CodeMessage,
    MailCodeDelivery,
    TelegramCodeDelivery,
    quiet_http_logs,
)
from goldbot.api.mailer import (
    Mailer,
    email_link_message,
    mailer_from_settings,
    sign_in_notice_message,
)
from goldbot.api.schema import (
    AcceptRequest,
    AcceptResponse,
    AccountSummary,
    ActiveBlackout,
    ActivityView,
    AgentRow,
    AgentRunRow,
    AttributionView,
    AuthState,
    AutoModeView,
    CalendarEvent,
    CalendarResponse,
    ChangePasswordRequest,
    ChartLayout,
    ChartLayoutsRequest,
    ChartLayoutsView,
    CodeSignIn,
    CodeSignIns,
    DecidedProposal,
    Decision,
    DecisionResult,
    EmailCodeChallenge,
    EmailCodeRequest,
    EmailCodeResendRequest,
    EmailCodeVerifyRequest,
    EmailLinkRedeemRequest,
    EmailLinkRequest,
    EmailLinkResponse,
    FeedHealth,
    ForgotPasswordRequest,
    GatesView,
    HaltRequest,
    Headline,
    HealthView,
    InviteRequest,
    InviteResponse,
    JobRow,
    LimitChange,
    LimitsView,
    LoginRequest,
    LoginResponse,
    Me,
    ModeChangeRequest,
    ModeChangeResult,
    Ok,
    OpenPositionsView,
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
    StrategyWatchView,
    TodayView,
    TradesOverview,
    TradesSummary,
    TvStatusView,
    UserRef,
    UserRow,
)
from goldbot.api.tv_status import tv_status
from goldbot.config import MailSettings
from goldbot.data.store import Store
from goldbot.data.timeutil import heartbeat_problem
from goldbot.execution.not_confirmed import note as not_confirmed_note
from goldbot.execution.not_placed import first_not_placed
from goldbot.ops.account_view import LossBudget, loss_budget, margin_level_pct, milestones, proposal_budget
from goldbot.ops.accounts import engine_state_files
from goldbot.telegram.approvals import Outcome, decision_channel
from goldbot.telegram.approvals import Proposal as BusProposal
from goldbot.telegram.bus import ApprovalBus

log = logging.getLogger("goldbot.api")


# The dashboard runs only its own scripts; the one third party is TradingView's chart, a cross-origin iframe that cannot
# reach our storage (the session token lives in sessionStorage), so script-src stays 'self'.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; "
       "frame-src https://www.tradingview-widget.com https://www.tradingview.com; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


MT5_CHART_MAX_AGE_S = 300.0


def supervisor_heartbeat_age_s(sup: dict[str, Any], now: float) -> float:
    """The Feeds page's supervisor heartbeat age: 1e9 (not reporting, the page marks it bad) when missing, not a
    finite number or stamped more than a few seconds in the future, which would otherwise show as fresh for ever
    (found 2026-10-16); a small skew reads as 0 s."""
    ts: Any = sup.get("ts")
    if heartbeat_problem(ts, now, math.inf) is not None:
        return 1e9
    return max(0.0, now - float(ts))

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
            root = self._data_root if self._data_root is not None else load_settings().data_root
            self._tools = ReadOnlyTools(self.dir, Store(root))
        return self._tools

    def store(self) -> Store:
        """The Parquet store (same root as `tools()`)."""
        from goldbot.config import load_settings
        root = self._data_root if self._data_root is not None else load_settings().data_root
        return Store(root)

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
        """The ENABLED accounts' engine state (accounts.engine_state_files: a disabled account's leftover file is
        ignored)."""
        out = []
        for f in engine_state_files(self.dir):
            try:                               # torn (ValueError) or unreadable (OSError): skipped, never a 500
                out.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                log.warning("engine state %s unreadable: skipped", f.name)
        return out

    def drift_halt(self) -> list[str] | None:
        """Reasons for the drift watch's system halt, None when there is none (entry_status.read_drift_halt)."""
        return entry_status.read_drift_halt(self.dir)

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


def _settings_mail() -> MailSettings:
    """auth.mail from settings; unreadable settings mean mail off (the email sign-in link is then not offered)."""
    try:
        from goldbot.config import load_settings
        return load_settings().auth.mail
    except Exception as exc:                    # noqa: BLE001 - the API still serves; email sign-in stays off
        print(f"goldbot api: settings unreadable, auth.mail off: {type(exc).__name__}", flush=True)
        return MailSettings()


LOOPBACK = {"127.0.0.1", "::1"}
CODES_LOCKED = "too many attempts: re-arm and auto mode are locked for 15 minutes; /halt still works"
# STEPUP_REQUIRED and STEPUP_LOCKED are matched word for word by the Users screen (web/src/lib/api.ts SERVER_SAYS,
# pinned by tests/test_web_auth_wording.py)
STEPUP_REQUIRED = "authenticator code required"
STEPUP_LOCKED = "too many attempts: user management, re-arm and auto mode are locked for 15 minutes"


def _settings_owner_tz() -> str:
    """The owner's time zone for the emailed code's times (telegram.owner_tz); UTC when settings are unreadable."""
    try:
        from goldbot.config import load_settings
        return load_settings().telegram.owner_tz
    except Exception:                           # noqa: BLE001 - the mail still goes, with UTC times
        return "UTC"


def _local_and_utc(ts: float, tz_name: str) -> tuple[str, str]:
    """('14:42 London time', '13:42 UTC') for the email copy; an unknown zone shows UTC only."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    utc = datetime.fromtimestamp(ts, timezone.utc)
    try:
        local = utc.astimezone(ZoneInfo(tz_name))
        place = tz_name.rsplit("/", 1)[-1].replace("_", " ")
    except (ZoneInfoNotFoundError, ValueError):
        local, place = utc, "UTC"
    return (f"{local:%H:%M} UTC" if place == "UTC" else f"{local:%H:%M} {place} time", f"{utc:%H:%M} UTC")


def _default_code_delivery(mailer: Mailer | None, mail: MailSettings, mailer_injected: bool,
                           owner_email: Callable[[], str | None]) -> CodeDelivery:
    """`auth.code_channel` decides (telegram unless settings say mail); unreadable settings mean Telegram with no
    allowed user, which is never ready (fails closed)."""
    channel: str = "mail" if mailer_injected else "telegram"
    allowed: list[int] = []
    if not mailer_injected:
        try:
            from goldbot.config import load_settings
            cfg = load_settings()
            channel, allowed = cfg.auth.code_channel, list(cfg.telegram.allowed_user_ids)
        except Exception as exc:                # noqa: BLE001 - the API still serves; codes stay off
            print(f"goldbot api: settings unreadable, sign-in codes off: {type(exc).__name__}", flush=True)
    if channel == "mail":
        return MailCodeDelivery(mailer, owner_email, mail.enabled)
    return TelegramCodeDelivery(allowed)


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
               owner_email: str | None = None, mail: MailSettings | None = None,
               mailer: Mailer | None = None, code_delivery: CodeDelivery | None = None) -> FastAPI:
    """owner_email: the dashboard owner; None reads `auth.owner_email` from settings. mail: None reads `auth.mail`;
    mailer: None sends through SMTP when mail is enabled (tests pass a fake). code_delivery: how the sign-in code
    reaches the owner; None follows `auth.code_channel` (Telegram by default), and an injected `mailer` with no
    `code_delivery` means the mail channel (the tests of the SMTP path)."""
    quiet_http_logs()                           # the bot token is in the Telegram URL (code_delivery.py)
    st = State(state_dir, data_root, owner_email if owner_email is not None else _settings_owner_email())
    mail = mail if mail is not None else _settings_mail()
    mailer_injected = mailer is not None
    mailer = mailer if mailer is not None else mailer_from_settings(mail)
    if code_delivery is None:
        code_delivery = _default_code_delivery(mailer, mail, mailer_injected, lambda: st.auth.owner_email)
    st.auth.code_channel = code_delivery.channel

    def email_link_ready() -> bool:
        """The owner's email sign-in link is offered: auth.mail enabled and complete, and the SMTP password stored."""
        return mail.enabled and mailer is not None and mailer.ready()

    def mail_owner(subject: str, text: str, what: str, failed_event: str) -> None:
        """Runs after the answer has gone, in the thread pool (SMTP has its own timeout). A failure is logged and
        audited by exception type only: smtplib's errors can carry the address and the message."""
        to = st.auth.owner_email
        if mailer is None or to is None:
            return
        try:
            mailer.send(to, subject, text)
        except Exception as exc:                # noqa: BLE001 - the caller already has its answer
            log.warning("%s not sent: %s", what, type(exc).__name__)
            st.auth.audit(failed_event, error=type(exc).__name__)

    def send_email_link(token: str) -> None:
        if mail.dashboard_origin is not None:
            subject, text = email_link_message(mail.dashboard_origin, token, EMAIL_LINK_TTL_S // 60)
            mail_owner(subject, text, "email sign-in link", "email_link_send_failed")

    def send_sign_in_notice(ip: str | None, method: str = "email_link") -> None:
        """ADR 0013 and 0028: every sign-in with an emailed link or code is also mailed to the owner (the Telegram
        notice is queued by AuthStore). No token or code, only a coarse network hint."""
        subject, text = sign_in_notice_message(time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), coarse_ip(ip),
                                               method=method)
        failed = "email_code_notice_failed" if method == "email_code" else "email_link_notice_failed"
        mail_owner(subject, text, "sign-in notice", failed)

    def delete_message(message_id: str | None) -> None:
        """Best effort: a delivered code that is used, burned, replaced or expired should not stay in the chat."""
        if message_id is None:
            return
        try:
            code_delivery.delete(message_id)
        except Exception as exc:                # noqa: BLE001 - the token is in the URL: the type only
            log.warning("sign-in code message not deleted: %s", type(exc).__name__)

    def sweep_expired_code_messages() -> None:
        """At API start: delete the chat messages of codes that expired while the process was down (their expiry timers
        died with it). Best effort, off the start-up path."""
        try:
            for mid in st.auth.take_expired_code_messages():
                delete_message(mid)
        except Exception as exc:                # noqa: BLE001 - never stops the API starting
            log.warning("expired sign-in code messages not swept: %s", type(exc).__name__)

    if code_delivery.channel == "telegram":
        threading.Thread(target=sweep_expired_code_messages, daemon=True).start()

    def send_email_code(code: str, challenge: str, ip: str | None, stale: str | None) -> None:
        """After the answer has gone (condition 6: a real send takes as long as a decoy). The code is never logged; a
        failure anywhere, formatting included, is logged and audited by exception type only (`code_channel_unavailable`
        on Telegram, where nothing is sent when the chat is not the owner's private chat). The message id is kept with
        the challenge so the message can be deleted on use, burn, resend and expiry."""
        try:
            now = time.time()
            tz = _settings_owner_tz()
            expires_local, expires_utc = _local_and_utc(now + EMAIL_CODE_TTL_S, tz)
            requested_local, _ = _local_and_utc(now, tz)
            mid = code_delivery.send(CodeMessage(code=code, expires_local=expires_local, expires_utc=expires_utc,
                                                 requested_local=requested_local, network=coarse_ip(ip)))
        except Exception as exc:                # noqa: BLE001 - the caller already has its answer
            log.warning("sign-in code not sent: %s", type(exc).__name__)
            st.auth.audit("code_channel_unavailable" if code_delivery.channel == "telegram" else "email_code_send_failed",
                          error=type(exc).__name__)
            return
        finally:
            delete_message(stale)
        if mid is not None:
            st.auth.set_code_message(challenge, mid)
            timer = threading.Timer(EMAIL_CODE_TTL_S + 5, delete_message, args=(mid,))
            timer.daemon = True
            timer.start()

    def challenge_answer(issue: EmailCodeIssue, ip: str | None, background: BackgroundTasks,
                         stale: str | None = None) -> EmailCodeChallenge:
        """The same JSON for a real challenge and a decoy; the code (if any) goes in the background, after it."""
        if issue.code is not None:
            background.add_task(send_email_code, issue.code, issue.challenge, ip, stale)
        return EmailCodeChallenge(challenge=issue.challenge, expires_in_s=EMAIL_CODE_TTL_S,
                                  resend_after_s=issue.resend_after_s)
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
        response.headers["Content-Security-Policy"] = CSP
        if request.url.path.startswith("/api/"):
            # session tokens, recovery codes, TOTP enrolment URIs and invite/reset tokens must never sit in a cache
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------- auth endpoints
    @app.get("/api/auth/state")
    def auth_state() -> AuthState:
        return AuthState(needs_setup=st.auth.setup_code is not None, users=st.auth.user_count(),
                         email_link=email_link_ready(),
                         code_sign_in=code_delivery.ready() and st.auth.email_code_available(),
                         code_channel="mail" if code_delivery.channel == "mail" else "telegram")

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

    @app.post("/api/auth/email-link")
    def email_link(body: EmailLinkRequest, request: Request, background: BackgroundTasks) -> EmailLinkResponse:
        """Owner only, the same generic 200 for every address, configured or not (AuthStore.request_email_link)."""
        token = st.auth.request_email_link(body.email, ip=client_ip(request), enabled=email_link_ready())
        if token is not None:
            background.add_task(send_email_link, token)
        return EmailLinkResponse()

    @app.post("/api/auth/email-link/redeem")
    def email_link_redeem(body: EmailLinkRedeemRequest, request: Request, background: BackgroundTasks) -> LoginResponse:
        """The link's token AND a current authenticator code (the link replaces the password, not the second factor).
        A success also emails the owner a sign-in notice (ADR 0013)."""
        ip = client_ip(request)
        try:
            tok = st.auth.redeem_email_link(body.token, body.totp, ip=ip)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        u = st.auth.session_user(tok)
        if u is None:
            raise HTTPException(403, "invalid credentials")
        background.add_task(send_sign_in_notice, ip)
        return LoginResponse(token=tok, expires_in=SESSION_TTL_H * 3600, role=u.role, email=u.email,
                             signed_in_with="email_link")

    # ADR 0028: password + a 6-digit code by email signs the owner in without the authenticator, only while mail is
    # ready; the same answer for every input; the session then needs a step-up for the owner's admin actions
    @app.post("/api/auth/email-code")
    def email_code(body: EmailCodeRequest, request: Request, background: BackgroundTasks) -> EmailCodeChallenge:
        """Owner only. A challenge for every input (a decoy unless the password is right, the address is the owner's
        and mail is ready); 403 only while the sign-in lockout holds, as for sign-in."""
        ip = client_ip(request)
        try:
            issue = st.auth.request_email_code(body.email, body.password, ip=ip, enabled=code_delivery.ready())
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        return challenge_answer(issue, ip, background)

    @app.post("/api/auth/email-code/resend")
    def email_code_resend(body: EmailCodeResendRequest, request: Request,
                          background: BackgroundTasks) -> EmailCodeChallenge:
        """A new code for this challenge (only the newest works): not before resend_after_s; the same shape always."""
        ip = client_ip(request)
        stale, _ = st.auth.code_message(body.challenge)
        issue = st.auth.resend_email_code(body.challenge, ip=ip, enabled=code_delivery.ready())
        return challenge_answer(issue, ip, background, stale)

    @app.post("/api/auth/email-code/verify", response_model=LoginResponse)
    def email_code_verify(body: EmailCodeVerifyRequest, request: Request,
                          background: BackgroundTasks) -> LoginResponse | JSONResponse:
        """The challenge and its code: a 12 h owner session (method email_code). A Telegram-code sign-in is audited
        `tg_code_login` and listed in the owner's banner (condition 6); the mail channel also mails a notice. The
        delivered message is deleted once the code is used or burned."""
        ip = client_ip(request)
        before, _ = st.auth.code_message(body.challenge)
        try:
            tok = st.auth.verify_email_code(body.challenge, body.code, ip=ip)
        except PermissionError as exc:
            if before is not None and not st.auth.code_message(body.challenge)[1]:       # burned
                return JSONResponse({"detail": str(exc)}, status_code=403,
                                    background=BackgroundTask(delete_message, before))
            raise HTTPException(403, str(exc))
        u = st.auth.session_user(tok)
        if u is None:
            raise HTTPException(403, "invalid credentials")
        background.add_task(delete_message, before)
        if code_delivery.channel == "mail":
            background.add_task(send_sign_in_notice, ip, "email_code")
        return LoginResponse(token=tok, expires_in=SESSION_TTL_H * 3600, role=u.role, email=u.email,
                             signed_in_with="email_code")

    @app.get("/api/auth/code-sign-ins")
    def code_sign_ins(u: User = Depends(need("owner"))) -> CodeSignIns:
        """The owner's Telegram-code sign-ins of the last 7 days (time, coarse network), for the banner shown after
        every sign-in with its one-tap "sign out everywhere"."""
        return CodeSignIns(sign_ins=[CodeSignIn(**r) for r in st.auth.code_sign_ins()],
                           channel="mail" if code_delivery.channel == "mail" else "telegram")

    @app.post("/api/auth/logout")
    def logout(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ok:
        if creds:
            st.auth.logout(creds.credentials)
        return Ok()

    def stepup(u: User, creds: HTTPAuthorizationCredentials | None, code: str, request: Request) -> None:
        """ADR 0028 ruling 2: a session opened with an emailed code (its stored `method`, nothing the client says)
        needs the authenticator or a recovery code for invite, reset link, role, disable, enable and revoke-sessions.
        403 STEPUP_REQUIRED for a missing or wrong code (counted under code:<email>, like re-arm and auto mode)."""
        if not need_stepup(st.auth.session_method(creds.credentials if creds else None)):
            return
        try:
            ok = st.auth.verify_stepup(u.email, code, ip=client_ip(request))
        except PermissionError:
            raise HTTPException(403, STEPUP_LOCKED)
        if not ok:
            raise HTTPException(403, STEPUP_REQUIRED)

    @app.post("/api/auth/invite")
    def invite(body: InviteRequest, request: Request, u: User = Depends(need("owner")),
               creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> InviteResponse:
        stepup(u, creds, body.totp, request)
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
    def reset_link(body: UserRef, request: Request, u: User = Depends(need("owner")),
                   creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> ResetLinkResponse:
        stepup(u, creds, body.totp, request)
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
    def set_role(body: RoleChange, request: Request, u: User = Depends(need("owner")),
                 creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ok:
        stepup(u, creds, body.totp, request)
        try:
            st.auth.set_role(u.email, body.email, body.role)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/users/disable")
    def disable(body: UserRef, request: Request, u: User = Depends(need("owner")),
                creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ok:
        stepup(u, creds, body.totp, request)
        try:
            st.auth.disable(u.email, body.email)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/users/enable")
    def enable(body: UserRef, request: Request, u: User = Depends(need("owner")),
               creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ok:
        stepup(u, creds, body.totp, request)
        try:
            st.auth.enable(u.email, body.email)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return Ok()

    @app.post("/api/users/revoke-sessions")
    def revoke_sessions(body: UserRef, request: Request, u: User = Depends(need("owner")),
                        creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> RevokeResponse:
        stepup(u, creds, body.totp, request)
        try:
            n = st.auth.revoke_sessions(u.email, body.email)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return RevokeResponse(sessions=n)

    @app.get("/api/me")
    def me(u: User = Depends(auth), creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Me:
        method = st.auth.session_method(creds.credentials if creds else None)
        return Me(email=u.email, role=u.role,
                  signed_in_with=method or "password_totp")

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
                                                     view(account_entry_status, e, budget, status_inputs))
                                       or entry_status.unknown_status()))
        return rows

    def entry_inputs(is_owner: bool) -> dict[str, Any]:
        """What every account's entry status shares (entry_status.entry_inputs; Telegram /status reads the same)."""
        return entry_status.entry_inputs(st.dir, view_settings(), is_owner=is_owner)

    def account_entry_status(e: dict, budget: LossBudget | None, inputs: dict[str, Any]) -> Any:
        return entry_status.account_entry_status(e, now=pd.Timestamp.now("UTC"), budget=budget, **inputs)

    def by_account(engines: list[dict]) -> dict[str, dict]:
        return {str(e.get("account")): e for e in engines if isinstance(e, dict)}

    def card(p: BusProposal, budget: LossBudget | None = None, eng: dict | None = None,
             pending: bool = False) -> dict[str, Any]:
        """One card. Backlog 103: the money class (the proposal's, else its engine file's) and, on demo only, why it
        asks; backlog 93, `pending` only: the usual spread for its hour and what else is open on the account."""
        e = eng or {}
        mode = p.account_mode or (e["mode"] if e.get("mode") in ("demo", "live", "paper") else None)
        extras: dict[str, Any] = {}
        if pending:
            n, risk = e.get("open_positions"), e.get("open_risk")
            extras = dict(open_positions=n if isinstance(n, int) and not isinstance(n, bool) else None,
                          open_risk_amount=float(risk) if isinstance(risk, (int, float))
                          and not isinstance(risk, bool) and math.isfinite(risk) else None,
                          spread_normal_points=usual_spread(p))
        return dict(account_mode=mode, window_s=p.window_s,
                    ask_reason=explain.ask_reason_view(p.ask_reason if mode == "demo" else None), **extras,
                    proposal_id=p.proposal_id, account_id=p.account_id, agent_id=p.agent_id,
                    side="long" if p.side > 0 else "short", lots=p.lots, entry=p.entry, stop=p.stop, target=p.target,
                    p=p.p, ev_r=p.ev_r, spread_points=p.spread_points, top_features=p.top_features, risk_usd=p.risk_usd,
                    currency=p.currency, risk_amount=p.risk_amount, risk_fraction=p.risk_fraction,
                    min_lot_exception=p.min_lot_exception, risk_normal=p.risk_normal, risk_cap=p.risk_cap,
                    reward_amount=p.reward_amount,
                    loss_left=budget.remaining if budget and budget.unknown is None else None,
                    loss_left_period=budget.period if budget and budget.unknown is None else None,
                    loss_cant_check=budget.cant_check if budget else None,
                    expires_at=datetime.fromtimestamp(p.created + p.window_s, tz=timezone.utc), created=p.created)

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
        accounts = by_account(engines)
        return [Proposal(**card(p, budget(p), accounts.get(p.account_id), pending=True)) for p in pending]

    def usual_spread(p: BusProposal) -> float | None:
        """Reporting only: an unreadable tick record leaves the usual spread unknown, never fails the list."""
        try:
            return spread_normal.normal_spread_points(st.dir, p.account_id, pd.Timestamp(p.created, unit="s", tz="UTC"))
        except Exception:                      # noqa: BLE001 - the card shows "usual: unknown"
            log.exception("usual spread failed")
            return None

    @app.get("/api/proposals/recent", response_model=list[DecidedProposal])
    def recent_proposals(_: User = Depends(auth)) -> list[DecidedProposal]:
        """Proposals decided in the last 10 minutes (newest first), so a decided card says what happened to it: by
        channel and time only, never who (BACKLOG 52). An approval whose order is not confirmed yet stays listed until
        it resolves (BACKLOG 72: the dashboard never drops an unanswered order)."""
        out = []
        recent = st.bus.recent()
        try:                                        # reporting only: the badge falls back to the engine file's mode
            accounts = by_account(st.engines()) if recent else {}
        except (OSError, ValueError):
            accounts = {}
        for p, d in recent:
            if d is not None:                       # decided, the engine has not applied it yet
                out.append(DecidedProposal.model_validate({**card(p, eng=accounts.get(p.account_id)), "status": "submitted" if d.approve else "rejected",
                                                           "reason_code": None if d.approve else d.reason_code,
                                                           "decided_channel": decision_channel(d.by),
                                                           "decided_ts": d.ts}))
                continue
            nc = not_confirmed_note(p.not_confirmed) if p.not_confirmed is not None else None
            if p.outcome == Outcome.APPROVED and p.placing:         # BACKLOG 72 review: the order is in flight
                status = "submitted"
            elif p.outcome == Outcome.APPROVED and nc is not None:    # BACKLOG 72: never "refused" while unknown
                status = "refused" if nc.outcome == "no_trade" else "approved"
            elif p.outcome == Outcome.APPROVED:
                status = "refused" if p.gate_refusal else "approved"
            elif p.outcome == Outcome.REJECTED:
                status = "rejected"
            else:
                status = "expired"
            out.append(DecidedProposal.model_validate({**card(p, eng=accounts.get(p.account_id)), "status": status,
                                                       "decided_channel": p.decided_channel, "decided_ts": p.decided_ts,
                                                       "reason_code": p.reason_code if status == "rejected" else None,
                                                       "refusal": list(p.gate_refusal or []),
                                                       "not_placed": first_not_placed(p.gate_refusal)
                                                       if status == "refused" else None,
                                                       "not_confirmed": nc,
                                                       "refusal_text": [entry_status.refusal_words(c)
                                                                    for c in p.gate_refusal or []]}))
        return out

    @app.post("/api/decisions")
    async def decide(d: Decision, u: User = Depends(need("approver"))) -> DecisionResult:
        try:
            # the channel only: who decided is the audit row below, never the decision file (A15)
            st.bus.submit(d.proposal_id, d.action == "approve", d.reason_code, by="dashboard",
                          card_created=d.created)       # bound to the card the owner saw (BACKLOG 63)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc).strip("'\""))
        try:
            st.auth.audit("decision", by=u.email, proposal=d.proposal_id, action=d.action, reason=d.reason_code)
        except Exception as exc:            # noqa: BLE001 - the decision already stands (first click wins on the bus)
            # as the Telegram service does (bot.OffLoopAudit): logged by channel and proposal, never the email; the
            # owner still reads SUBMITTED, not a 500 that hides a decision the engine will apply
            log.warning("audit of decision via dashboard (%s) failed: %s", d.proposal_id, type(exc).__name__)
        await st.broadcast({"type": "decision", "proposal_id": d.proposal_id, "action": d.action})
        return DecisionResult(outcome="SUBMITTED")

    @app.post("/api/halt")
    async def halt(body: HaltRequest, u: User = Depends(need("approver"))) -> Status:
        # stopping new entries needs no second factor (design); exits and open positions are untouched
        st.bus.set_halt(True, by="dashboard", reason=body.reason)       # who: the audit row below (A15)
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
        c = st.bus.owner_rearm(by="dashboard")      # also clears the engines' drawdown halts; who: the audit row
        st.auth.audit("rearm", by=u.email, rearm_id=c.rearm_id)
        await st.broadcast({"type": "halt", "halted": False})
        return current_status()

    @app.get("/api/agents", response_model=list[AgentRow])
    def agents(_: User = Depends(auth)) -> list[AgentRow]:
        return [AgentRow(**a) for a in st.agents()]

    @app.get("/api/feeds", response_model=list[FeedHealth])
    def feeds(_: User = Depends(auth)) -> list[FeedHealth]:
        sup = st.supervisor()
        age = supervisor_heartbeat_age_s(sup or {}, time.time())
        return [FeedHealth(account_id=e["account"], last_tick_age_s=e.get("last_tick_age_s", 1e9), spread_points=e.get("spread_points", 0),
                           terminal_connected=e.get("terminal_connected", False), webhook_p99_latency_s=e.get("webhook_p99_latency_s"),
                           supervisor_heartbeat_age_s=age) for e in st.engines()]

    @app.get("/api/tradingview", response_model=TvStatusView)
    def tradingview_status(_: User = Depends(auth)) -> TvStatusView:
        s = view_settings()
        keys = (s.tv_pull.sessionid_key, s.tv_pull.sessionid_sign_key) if s is not None else None
        return tv_status(st.store(), pd.Timestamp.now(tz="UTC"), state_dir=st.dir, cookie_keys=keys,
                         aliases=s.tv_pull.aliases if s is not None else None)

    # ============================================================== Limits card (read-only, any signed-in role)
    # goldbot/api/limits.py: what the engines publish, the supervisor's combined caps and core.db's change logs
    # (docs/ux/limits-and-tradingview.md A). Nothing here changes a limit; the revert is cleared only by the re-arm.
    @app.get("/api/limits", response_model=LimitsView)
    def limits_get(_: User = Depends(auth)) -> LimitsView:
        try:                                   # a torn supervisor.json only drops the combined caps
            sup = st.supervisor()
        except (OSError, ValueError):
            sup = {}
        return limits.limits_view(st.dir, st.engines(), sup if isinstance(sup, dict) else {}, view_settings())

    @app.get("/api/limits/changes", response_model=list[LimitChange])
    def limits_changes(account: str | None = None, limit: int = 20, _: User = Depends(auth)) -> list[LimitChange]:
        return limits.changes_view(st.dir, account, limit)

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

    # ============================================================== Progress tab (W2-3; read-only, viewer and above)
    # goldbot/api/progress.py: the gates evaluated on request with nothing written (cached 60 s), the daily results
    # file without its trade rows, and each account's last 30 opportunity days. Nothing here records a gate, unlocks
    # live, halts or changes a setting; accounts are named with their digits cut.
    gates_cache = progress.GatesCache()

    def engines_or_none() -> list[dict]:
        try:
            return st.engines()
        except OSError:
            log.warning("engine states unreadable: progress views without them")
            return []

    def build_gates() -> GatesView:
        now, s = pd.Timestamp.now("UTC"), view_settings()
        if s is None:
            return progress.gates_unavailable(now, None)
        try:
            return progress.gates_view(st.dir, s, now, engines_or_none())
        except Exception:                      # noqa: BLE001 - reporting only: the owner reads "can't check"
            log.exception("gates view failed")
            return progress.gates_unavailable(now, s)

    @app.get("/api/gates", response_model=GatesView)
    def gates(_: User = Depends(need("viewer"))) -> GatesView:
        return gates_cache.get(build_gates)

    @app.get("/api/attribution", response_model=AttributionView)
    def attribution(_: User = Depends(need("viewer"))) -> AttributionView:
        return progress.attribution_view(st.dir, view_settings(), pd.Timestamp.now("UTC"))

    @app.get("/api/trades/summary", response_model=list[TradesSummary])
    def trades_summary(_: User = Depends(need("viewer"))) -> list[TradesSummary]:
        return progress.trades_summary(st.dir, pd.Timestamp.now("UTC"), engines_or_none())
    # ============================================================== end Progress tab

    # ============================================================== Home activity feed (read-only, viewer and above)
    # goldbot/api/activity.py: shadow trades, positions, cost probes and refusals, newest first. One build serves every
    # poll for ACTIVITY_TTL_S; `limit` only slices it. Nothing here writes.
    activity_cache = progress.GatesCache(ttl_s=activity.ACTIVITY_TTL_S)

    def build_activity() -> ActivityView:
        try:
            store: Store | None = st.store()
        except Exception as exc:               # noqa: BLE001 - no store: the feed shows without refusals
            log.warning("activity feed: store unavailable (%s)", type(exc).__name__)
            store = None
        v = activity.activity_view(st.dir, store, pd.Timestamp.now("UTC"), engines_or_none())
        return v if store is not None else v.model_copy(update={"unreadable": [*v.unreadable, "journal"]})

    @app.get("/api/activity", response_model=ActivityView)
    def activity_feed(limit: int = Query(50, ge=1, le=activity.MAX_EVENTS),
                      _: User = Depends(need("viewer"))) -> ActivityView:
        v = activity_cache.get(build_activity)
        return v.model_copy(update={"events": v.events[:limit]})
    # ============================================================== end Home activity feed

    # ============================================================== Home trades summary (read-only, viewer and above)
    # goldbot/api/trades_overview.py: every trade type (shadow taken and all, demo, live, cost probes) for today, 7 days
    # and all time, from the activity feed's readers. One build serves every poll for OVERVIEW_TTL_S. Nothing writes.
    overview_cache = progress.GatesCache(ttl_s=trades_overview.OVERVIEW_TTL_S)

    @app.get("/api/trades/overview", response_model=TradesOverview)
    def trades_overview_view(_: User = Depends(need("viewer"))) -> TradesOverview:
        return overview_cache.get(lambda: trades_overview.trades_overview(st.dir, pd.Timestamp.now("UTC"),
                                                                          engines_or_none()))
    # ============================================================== end Home trades summary

    # ============================================================== Home status card stack (read-only, viewer and above)
    # goldbot/api/home_cards.py (docs/ux/home-redesign-2026-10-19.md): open positions (N1), the strategy agents' states
    # (N2) and today's money. Each its own route and cache (one failing source never blanks Home). Nothing writes.
    positions_cache = progress.GatesCache(ttl_s=home_cards.HOME_TTL_S)
    watch_cache = progress.GatesCache(ttl_s=home_cards.HOME_TTL_S)
    today_cache = progress.GatesCache(ttl_s=home_cards.HOME_TTL_S)

    @app.get("/api/positions/open", response_model=OpenPositionsView)
    def positions_open(_: User = Depends(need("viewer"))) -> OpenPositionsView:
        return positions_cache.get(lambda: home_cards.open_positions(st.dir, pd.Timestamp.now("UTC"),
                                                                     engines_or_none()))

    @app.get("/api/strategies/watch", response_model=StrategyWatchView)
    def strategies_watch(_: User = Depends(need("viewer"))) -> StrategyWatchView:
        return watch_cache.get(lambda: home_cards.strategy_watch(st.dir, pd.Timestamp.now("UTC"), engines_or_none()))

    @app.get("/api/today", response_model=TodayView)
    def today(_: User = Depends(need("viewer"))) -> TodayView:
        return today_cache.get(lambda: home_cards.today_money(st.dir, pd.Timestamp.now("UTC"),
                                                              home_cards.probe_terms_from_config(), engines_or_none()))
    # ============================================================== end Home status card stack

    # ============================================================== auto mode (A10): card for any role, switch owner-only
    # The switch runs telegram/automode.py `mode_command`, the code behind Telegram's /mode, with the same checks:
    # the owner pinned to auth.owner_email (need("owner") + verify_owner_totp), each authenticator code spent once
    # (the replay guard shared with sign-in, re-arm and /mode) inside the code lockout (verify_code_guarded, key
    # `code:<email>`, shared with re-arm and Telegram /mode; mode_failed / mode_locked; it never locks sign-in, and
    # failed sign-ins lock it too), then the evidence check. Switching to propose, or back to each account's own setting
    # (`account`, ADR 0041), needs no code and is never locked. Every outcome is audited by
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
        arg = body.mode if body.mode != "auto" else ("auto " + code if code.isdigit() else "auto")
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

    # ============================================================== Chart tab: saved TradingView layouts
    # The URL identifies the owner's TradingView layout: only the owner reads it back, and the audit row never holds it.
    # The MT5 terminal's own chart (IC Markets, Wine/Xvfb), screenshotted every 60 s on the MT5 box and pulled by the
    # brain (docs/RUNBOOK.md "MT5 terminal chart on the dashboard"). Read-only; the file's mtime is the capture time.
    @app.get("/api/chart/mt5", responses={200: {"content": {"image/png": {}}}, 404: {"description": "no fresh capture"}})
    def chart_mt5(_: User = Depends(auth)) -> Response:
        png = Path(st.dir) / "chart" / "mt5.png"
        try:
            age = time.time() - png.stat().st_mtime
        except OSError:
            raise HTTPException(404, "no MT5 chart capture yet") from None
        if age > MT5_CHART_MAX_AGE_S:
            raise HTTPException(404, f"the MT5 chart capture is stale ({int(age)} s old)")
        try:
            res, captured = mt5_crop.cached_crop(Path(st.dir))
        except OSError:
            raise HTTPException(404, "no MT5 chart capture yet") from None
        iso = datetime.fromtimestamp(captured, tz=timezone.utc).isoformat()
        # only the chart pane is served (goldbot/api/mt5_crop.py); the headers say which chart it is
        return Response(res.png, media_type="image/png", headers={
            "X-Captured-At": iso, "Cache-Control": "no-store", "X-Chart-Cropped": str(res.cropped).lower(),
            "X-Chart-Symbol": res.symbol, "X-Chart-Timeframe": res.timeframe,
            "X-Chart-Gold": "unknown" if res.gold is None else str(res.gold).lower()})

    @app.get("/api/chart/layouts", response_model=ChartLayoutsView)
    def chart_layouts_get(u: User = Depends(auth)) -> ChartLayoutsView:
        owner = st.auth.is_owner(u)
        return ChartLayoutsView(layouts=[ChartLayout(name=n, url=url if owner else None)
                                         for n, url in chart_layouts.read_layouts(st.auth.db)], can_edit=owner)

    @app.post("/api/chart/layouts", response_model=ChartLayoutsView)
    def chart_layouts_put(body: ChartLayoutsRequest, request: Request, u: User = Depends(need("owner")),
                          creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> ChartLayoutsView:
        stepup(u, creds, body.totp, request)
        for item in body.layouts:
            if not chart_layouts.valid_layout_url(item.url):
                raise HTTPException(422, "a layout link must look like https://www.tradingview.com/chart/<id>/")
        chart_layouts.write_layouts(st.auth.db, [(i.name.strip(), i.url.strip()) for i in body.layouts])
        st.auth.audit("chart_layouts_set", actor=u.email, count=len(body.layouts))
        return chart_layouts_get(u)
    # ============================================================== end Chart tab

    def current_status() -> Status:
        modes = sorted({str(e.get("approval_mode", "propose")) for e in st.engines()})
        c = st.bus.control()
        sup = st.supervisor()
        drift = st.drift_halt()
        # who halted is the channel for every role (A15); an older control.json's "<channel>:<id>" is cut to it
        halted_by = decision_channel(c.by) if c.halted and c.by else None
        return Status(mode=",".join(modes) or "propose", halted=c.halted, halted_by=halted_by,
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
