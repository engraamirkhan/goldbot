"""API contract (Pydantic models) shared by the backend and the generated TypeScript client.
Phase 1 fills the routers; the shapes are fixed here so the frontend can start in parallel."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class AccountSummary(BaseModel):
    account_id: str
    broker: str
    mode: Literal["demo", "live", "paper"]
    equity: float
    day_pnl_pct: float
    week_pnl_pct: float
    drawdown_pct: float
    stage: Literal["normal", "size_down", "halted"]
    open_positions: int
    account_class: Literal["raw", "standard", "unknown"]


class Proposal(BaseModel):
    proposal_id: str
    account_id: str
    agent_id: str
    side: Literal["long", "short"]
    lots: float
    entry: float
    stop: float
    target: float
    p: float
    ev_r: float
    spread_points: float
    top_features: list[tuple[str, float]]
    expires_at: datetime
    tradingview_url: str | None = None


class Decision(BaseModel):
    proposal_id: str
    action: Literal["approve", "reject"]
    reason_code: Literal["news", "cost", "discretion", "duplicate", "other"] | None = None


class AgentRow(BaseModel):
    agent_id: str
    family: str
    generation: int
    parent_id: str | None
    status: Literal["shadow", "live", "retired"]
    n_trades: int
    expectancy_r: float
    hit_rate: float
    calibration_ece: float
    fitness: float
    capital_weight: float


class FeedHealth(BaseModel):
    account_id: str
    last_tick_age_s: float
    spread_points: float
    terminal_connected: bool
    webhook_p99_latency_s: float | None
    supervisor_heartbeat_age_s: float


# ----------------------------------------------------------------------------- auth and users
Role = Literal["owner", "approver", "viewer"]


class AuthState(BaseModel):
    needs_setup: bool
    users: int


class SetupRequest(BaseModel):
    setup_code: str = ""
    email: str
    password: str


class TotpEnrolment(BaseModel):
    totp_uri: str


class LoginRequest(BaseModel):
    email: str = ""
    password: str = ""
    totp: str = ""


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    role: Role
    email: str


class InviteRequest(BaseModel):
    email: str
    role: Role = "viewer"


class InviteResponse(BaseModel):
    invite_token: str
    expires_h: int


class AcceptRequest(BaseModel):
    token: str = ""
    password: str = ""


class AcceptResponse(BaseModel):
    email: str
    totp_uri: str


class Me(BaseModel):
    email: str
    role: Role


class UserRow(BaseModel):
    email: str
    role: Role
    enabled: bool
    last_login: float | None


class RoleChange(BaseModel):
    email: str
    role: Role


class UserRef(BaseModel):
    email: str


class Ok(BaseModel):
    ok: bool = True


class DecisionResult(BaseModel):
    outcome: str


class Status(BaseModel):
    mode: str
    halted: bool
    pending: int
    supervisor: dict[str, Any]


class JobRow(BaseModel):
    """One scheduler job as the dashboard shows it (from state/scheduler.json)."""
    name: str
    last_slot: datetime | None
    last_finished: datetime | None
    last_ok: bool | None
    last_error: str | None
    next_slot: datetime | None
    runs: int
    failures: int
    heartbeat_age_s: float


class AgentRunRow(BaseModel):
    """One staff-agent run (data steward, risk officer, journal coach, improvement agent) and its report."""
    role: str
    started_utc: datetime
    status: str
    turns: int
    cost_usd: float
    detail: str | None
    report: str | None
