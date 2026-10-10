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
    risk_usd: float | None = None           # account currency lost at the stop (lots x stop distance x 100 oz)


class DecidedProposal(Proposal):
    """A proposal decided in the last few minutes, so its card can show what happened to it.

    submitted: decision recorded, the engine applies it (and re-checks the RiskGate) on its next tick;
    approved: the order was sent; refused: approved, but the RiskGate re-check refused the order (see `refusal`);
    rejected: with its reason code; expired: nobody decided within the window."""
    status: Literal["submitted", "approved", "rejected", "expired", "refused"]
    reason_code: Literal["news", "cost", "discretion", "duplicate", "other"] | None = None
    refusal: list[str] = []
    decided_by: str | None = None


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
    outcome: str                    # SUBMITTED: the engine applies it (and re-checks the RiskGate) on its next tick


class HaltRequest(BaseModel):
    reason: str | None = None


class RearmRequest(BaseModel):
    totp: str                       # a fresh authenticator code: re-arming needs a second factor (design)


class Status(BaseModel):
    mode: str
    halted: bool
    halted_by: str | None = None
    halt_reason: str | None = None
    pending: int
    supervisor: dict[str, Any]
    supervisor_halt: bool = False           # combined drawdown / heartbeat halt from state/supervisor.json
    supervisor_reasons: list[str] = []
    drift_halt: bool = False                # system halt from the drift watch (state/drift.json); unreadable halts
    drift_reasons: list[str] = []
    blackout: ActiveBlackout | None = None  # entry blackout an engine is enforcing now


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


# ----------------------------------------------------------------------------- calendar and news
class CalendarEvent(BaseModel):
    """One archived economic-calendar event. Tier 1 (US CPI, NFP, FOMC, PCE) carries its entry-blackout window."""
    event_id: str
    ts_utc: datetime
    country: str
    title: str
    impact: str
    tier: int
    forecast: str
    previous: str
    blackout_start: datetime | None        # tier 1 only
    blackout_end: datetime | None


class ActiveBlackout(BaseModel):
    """An entry blackout an engine is enforcing now (from state/engine_<account>.json)."""
    kind: Literal["calendar", "news_shock"]
    title: str
    ts_utc: datetime | None          # scheduled time of a calendar event
    received_utc: datetime | None    # when a news shock was received
    accounts: list[str]


class CalendarResponse(BaseModel):
    now: datetime
    events: list[CalendarEvent]
    active_blackout: ActiveBlackout | None
    note: str | None                        # why the list is empty, when it is


class Headline(BaseModel):
    """One collected headline; unscored items (prefiltered out or over the daily cap) have relevance and tags null."""
    item_id: str
    ts_utc: datetime
    received_utc: datetime
    source: str
    title: str
    link: str | None
    relevance: float | None
    rates: Literal["hawkish", "dovish", "neutral"] | None
    risk: Literal["risk_on", "risk_off", "neutral"] | None
    dollar: Literal["positive", "negative", "neutral"] | None
    surprise: Literal["beat", "miss", "inline", "none"] | None
    shock: bool


# ============================================================================= Health and Research screens
# Read-only views for any logged-in role (goldbot/api/explain.py builds them from the state files).
CheckStatus = Literal["ok", "warn", "fail"]


class HealthCheckRow(BaseModel):
    """One deterministic health check (goldbot/ops/health.py), evaluated when the view is requested."""
    name: str
    status: CheckStatus
    reason: str


class AgentHealthRow(BaseModel):
    """One champion's drift and health (state/drift.json, research/drift.py AgentHealth) with its halt state."""
    agent_id: str
    version: str
    size_factor: float                      # 1.0 full size, 0.5 sized down (PSI or calibration drift)
    halted: bool                            # CUSUM alarm (sticky until the owner's review or a new champion)
    halted_since: datetime | None
    halt_reasons: list[str]
    notes: list[str]                        # every finding of the last check, in plain words
    psi_warn: list[str]
    psi_size_down: list[str]
    n_live_rows: int
    ece: float | None
    brier: float | None
    n_calib: int
    cusum: float
    cusum_alarm: bool
    dd_30d: float                           # 30-day shadow drawdown, fraction
    backtest_dd: float | None               # the version's backtest max drawdown, fraction
    capital_weight: float | None            # allocator share from state/agents.json, when ranked


class PsiHeatmap(BaseModel):
    """PSI of the top features (rows) per agent (columns); null where the agent does not use the feature."""
    features: list[str]
    agents: list[str]
    values: list[list[float | None]]
    warn: float                             # 0.1: warns
    size_down: float                        # 0.25: sizes the agent down


class ReliabilityBin(BaseModel):
    lo: float
    hi: float
    n: int
    mean_p: float
    hit_rate: float


class ReliabilityCurve(BaseModel):
    """Predicted p (10 equal bins) against the realised target-hit rate of closed, taken shadow trades."""
    agent_id: str                           # "all" for the pooled curve
    version: str | None
    n: int
    ece: float | None
    brier: float | None
    bins: list[ReliabilityBin]


class CusumPoint(BaseModel):
    ts: datetime                            # trade exit time
    z: float                                # standardised residual (realised R minus the R p implied)
    s: float                                # downward CUSUM statistic after this trade


class CusumTrace(BaseModel):
    agent_id: str
    version: str
    k: float
    h: float                                # alarm threshold: the agent halts when s exceeds it
    alarm: bool
    points: list[CusumPoint]


class SystemHaltView(BaseModel):
    since: datetime | None
    reasons: list[str]
    review_command: str
    clear_command: str


class HealthView(BaseModel):
    generated_utc: datetime
    drift_ts: datetime | None               # when drift_watch last ran; null before its first run
    drift_error: str | None                 # drift.json unreadable: the engines halt entries (fail closed)
    system_halt: SystemHaltView | None
    agents: list[AgentHealthRow]
    psi: PsiHeatmap
    reliability: list[ReliabilityCurve]
    cusum: list[CusumTrace]
    dd_mult: float                          # system halt when dd_30d > dd_mult x backtest_dd
    checks: list[HealthCheckRow]            # deploy, data quality, drift
    errors: dict[str, str]                  # agents whose model could not be checked


class TrialBudget(BaseModel):
    quarter: str
    budget: int
    used: int
    left: int


class GateCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class TrialRow(BaseModel):
    """One research-registry trial (state/research_registry.jsonl). R figures are the rule's own expectancy over
    every candidate: gross on mid prices, net of every cost."""
    trial: int
    ts: datetime | None
    family: str
    timeframe: str | None
    status: str
    agent_id: str
    gross_r: float | None
    gross_t: float | None
    net_r: float | None
    net_t: float | None
    n: int | None
    gates_passed: bool | None               # null when the trial recorded no gate verdict
    gates: list[GateCheck]
    rationale: str


class PlanFocus(BaseModel):
    rank: int
    family: str
    budget: int
    evidence: float
    reasons: list[str]


class PlanFamily(BaseModel):
    family: str
    trials: int
    evidence: float
    blocked: bool
    flags: list[str]
    median_auc: float | None
    best_dsr: float | None
    shadow_trades: int


class ResearchPlanView(BaseModel):
    """The research director's latest plan (state/research_plan.json)."""
    created_utc: datetime
    stale: bool                             # older than 21 days: the monthly loop uses the flat budget instead
    quarter: str
    quarter_budget: int
    quarter_used: int
    total_budget: int
    budget: dict[str, int]
    grid_budget: dict[str, int]
    unallocated: int
    holdout_from: str
    holdout_to: str
    focus: list[PlanFocus]
    evidence: list[PlanFamily]


class HypothesisTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str]]


class HypothesisDoc(BaseModel):
    """The hypothesis portfolio (docs/research/hypotheses.md), its tables parsed, read-only."""
    path: str
    updated_utc: datetime | None
    tables: list[HypothesisTable]
    note: str | None


class ResearchView(BaseModel):
    generated_utc: datetime
    budget: TrialBudget
    trials: list[TrialRow]                  # newest first, at most 200
    trials_total: int
    plan: ResearchPlanView | None
    plan_error: str | None
    hypotheses: HypothesisDoc
# ============================================================================= end Health and Research screens


Status.model_rebuild()          # `blackout` refers to ActiveBlackout, defined after Status
