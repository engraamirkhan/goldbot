"""API contract (Pydantic models) shared by the backend and the generated TypeScript client.
Phase 1 fills the routers; the shapes are fixed here so the frontend can start in parallel."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from goldbot.execution.not_confirmed import NotConfirmed
from goldbot.execution.not_placed import NotPlaced
from goldbot.ops.account_view import CantCheck, LossBudget, Milestones
from goldbot.telegram.approvals import PROPOSAL_ID_PATTERN


class ApiRequest(BaseModel):
    """Base of every request body: unknown fields are rejected (422), never silently dropped, so a misspelt or
    attacker-supplied field on a security-relevant request (sign-in, invite, reset, recovery, TOTP, mode, re-arm,
    halt, approvals) cannot pass unnoticed. Response models stay plain BaseModel."""
    model_config = ConfigDict(extra="forbid")


class EntryBlock(BaseModel):
    """One reason this account cannot open a new trade now, in the owner's words (goldbot/api/entry_status.py)."""
    code: str                               # the RiskGate / supervisor reason (goldbot/risk/gate.py), or a newer one
    scope: Literal["account", "agent"] = "account"   # agent: only that strategy is held back (the throttle)
    agent_id: str | None = None
    text: str                               # what is happening, plain words, no times: "Market closed"
    fix: str                                # the next step: "Nothing to do: it reopens by itself."
    kind: Literal["act", "wait"]            # act: needs the owner or an operator; wait: clears by itself
    at: datetime | None = None              # the time that goes with `at_label`, UTC (shown in the owner's zone)
    at_label: Literal["opens", "until", "resets", "since"] | None = None
    runbook: str | None = None              # the RUNBOOK section that explains it
    runbook_url: str | None = None


class EntryStatus(BaseModel):
    """Can this account take a new trade right now? open: yes; partial: yes, except the strategies named; waiting:
    no, and every reason clears by itself; blocked: no, and something needs the owner. Exits never depend on it."""
    state: Literal["open", "partial", "waiting", "blocked"]
    headline: str                           # one line: "No new trades for now: Market closed"
    blocks: list[EntryBlock]                # what needs the owner first
    notes: list[str] = []                   # worth knowing, never blocking ("price error 4 min ago, carried on")
    exits: str                              # what still protects open trades
    as_of: datetime | None = None           # the engine's last report
    clears_at: datetime | None = None       # waiting only: when the last reason clears by itself, if every one says


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
    # the live account view (ADR 0004), from the engine's state file and the supervisor's; null from an engine that
    # does not publish them yet. Money is in `currency` (the broker's account currency).
    balance: float | None = None
    currency: str | None = None
    margin_level_pct: float | None = None   # equity / margin x 100; null with no margin in use
    day_pnl: float | None = None            # equity - day-start equity (00:00 UTC), floating P&L included
    loss_budget: LossBudget | None = None   # today's effective daily loss limit and what is used of it
    milestones: Milestones | None = None    # timeframes the minimum lot fits now, and the equity the next one needs
    entry_status: EntryStatus | None = None  # can it take a new trade now, why not and what fixes it (null: not built)


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
    created: float                          # the proposal's stamp: sent back with a decision on this card (BACKLOG 63)
    tradingview_url: str | None = None
    risk_usd: float | None = None           # USD lost at the stop (lots x stop distance x 100 oz); older engines
    # the card's money (docs/ux/approval-card.md), in `currency`, the broker account's; null from older engines
    currency: str | None = None
    risk_amount: float | None = None        # lost if the stop is hit (the RiskGate's sizing)
    risk_fraction: float | None = None      # risk_amount / equity, as the gate sized it
    min_lot_exception: bool = False         # the smallest trade size, above the normal risk, within the cap
    risk_normal: float | None = None        # the normal risk per trade, fraction of equity
    risk_cap: float | None = None           # the minimum-lot cap that applied, fraction of equity
    reward_amount: float | None = None      # won at the target (risk x target / stop distance, before costs);
    #                                         in `currency`, or USD with only risk_usd
    loss_left: float | None = None          # the account's loss budget left if this trade loses, now (pending only)
    loss_left_period: Literal["today", "this week"] | None = None
    # the budget can't be checked (account_view LossBudget.unknown; loss_left is then None): "paused" when new entries
    # are paused on the supervisor's report (red), "not_summed" when the supervisor hasn't added up the accounts (amber);
    # "mixed_currencies" (amber) / "mixed_currencies_paused" (red) when the accounts report different currencies
    # (BACKLOG 61: pounds are never added to dollars)
    loss_cant_check: CantCheck | None = None


class DecidedProposal(Proposal):
    """A proposal decided in the last few minutes, so its card can show what happened to it.

    submitted: decision recorded, the engine applies it (and re-checks the RiskGate) on its next tick;
    approved: the order was sent; refused: approved, but not placed: the RiskGate re-check, the MT5 box or the broker
    refused the order (see `refusal`; `not_placed` words the MT5 box's and the broker's refusals);
    rejected: with its reason code; expired: nobody decided within the window. `decided_channel` ("telegram" |
    "dashboard") and `decided_ts` say how and when it was decided, never who (BACKLOG 52: identity is kept in the
    auth audit log only)."""
    status: Literal["submitted", "approved", "rejected", "expired", "refused"]
    reason_code: Literal["news", "cost", "discretion", "duplicate", "other"] | None = None
    refusal: list[str] = []
    # docs/ux/order-not-placed.md: who refused an approved order and why, in the owner's words (the same table as the
    # Telegram card and notice); null for a RiskGate refusal and for every other status
    not_placed: NotPlaced | None = None
    # docs/ux/order-not-confirmed.md (BACKLOG 72): approved and sent, but the broker neither filled nor refused it; what
    # the engine's look-up found, worded (the same table as the Telegram card and notices). Status "approved" ("refused"
    # once no trade was found); null on every other card. Unresolved ones stay listed past the 10-minute window
    not_confirmed: NotConfirmed | None = None
    # the RiskGate's refusal codes in the owner's words (`refusal`, same order), for "Refused at approval (…)"
    refusal_text: list[str] = []
    decided_channel: str | None = None
    decided_ts: float | None = None         # epoch s, the decision's own clock; None for expired and older files


class Decision(ApiRequest):
    # a file name under state/approvals/: the allow-list here (422), and ".." refused by the bus (400)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    action: Literal["approve", "reject"]
    reason_code: Literal["news", "cost", "discretion", "duplicate", "other"] | None = None
    # BACKLOG 63: the `created` of the card decided on, as /api/proposals sent it. Required: a request without it (an
    # old client) is refused, and one for another card with the same id is refused (400 card_mismatch)
    created: float = Field(allow_inf_nan=False)


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


class TvIndicatorStatus(BaseModel):
    """One TradingView indicator at one timeframe: arrival of its bar alerts (counts only, never plotted values)."""
    indicator: str
    version: str
    symbol: str
    tf: str
    last_bar_time: str
    alerts_24h: int
    plots_seen: list[str]
    gaps_24h: int


class TvStatusView(BaseModel):
    now: str
    indicators: list[TvIndicatorStatus]


# ----------------------------------------------------------------------------- auth and users
Role = Literal["owner", "approver", "viewer"]
GrantableRole = Literal["approver", "viewer"]     # the owner role belongs to auth.owner_email and is never granted
# how a session signed in (aux.db sessions.method; ADR 0028): an `email_code` session needs the authenticator or a
# recovery code again for the owner's admin actions (invite, reset link, role, disable, enable, revoke sessions)
SessionMethod = Literal["password_totp", "email_link", "email_code", "recovery"]


class AuthState(BaseModel):
    needs_setup: bool
    users: int
    email_link: bool = False      # the owner's "email me a sign-in link" is offered (auth.mail configured)
    # the owner's "send me a code" is offered: the code channel is ready (Telegram chat set up, or mail), the owner
    # account exists and the code key is in the keyring
    code_sign_in: bool = False
    code_channel: Literal["telegram", "mail"] = "telegram"      # where the code goes (auth.code_channel)


class SetupRequest(ApiRequest):
    setup_code: str = ""
    email: str
    password: str


class TotpEnrolment(BaseModel):
    totp_uri: str


class SetupResponse(TotpEnrolment):
    """The owner's authenticator and 10 one-time recovery codes. Shown once: only their hashes are stored."""
    recovery_codes: list[str]


class LoginRequest(ApiRequest):
    email: str = ""
    password: str = ""
    totp: str = ""


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    role: Role
    email: str
    signed_in_with: SessionMethod = "password_totp"


class EmailCodeRequest(ApiRequest):
    """Email + password: the owner gets a 6-digit code on the code channel (Telegram by default; the route names are
    from the first, email-only design); every input gets the same answer (ADR 0028)."""
    email: str = ""
    password: str = ""


class EmailCodeChallenge(BaseModel):
    """The same shape for every request: an opaque challenge id held only by this browser (a decoy that can never
    verify when nothing was sent), how long a code works and when the screen may offer Resend."""
    challenge: str
    expires_in_s: int
    resend_after_s: int


class EmailCodeVerifyRequest(ApiRequest):
    challenge: str = ""
    code: str = Field("", max_length=16)      # 6 digits; a pasted "482 913" keeps its space, the server strips it


class EmailCodeResendRequest(ApiRequest):
    challenge: str = ""


class InviteRequest(ApiRequest):
    email: str
    role: GrantableRole = "viewer"
    totp: str = ""          # the step-up after an email-code sign-in: authenticator or recovery code (ADR 0028)


class InviteResponse(BaseModel):
    invite_token: str
    expires_h: int


class AcceptRequest(ApiRequest):
    token: str = ""
    password: str = ""


class AcceptResponse(BaseModel):
    email: str
    totp_uri: str


class Me(BaseModel):
    email: str
    role: Role
    signed_in_with: SessionMethod = "password_totp"     # the screens ask for the step-up code when "email_code"


class UserRow(BaseModel):
    email: str
    role: Role
    enabled: bool
    last_login: float | None


class RoleChange(ApiRequest):
    email: str
    role: GrantableRole
    totp: str = ""          # the step-up after an email-code sign-in (ADR 0028)


class UserRef(ApiRequest):
    """A user for reset-link, disable, enable and revoke-sessions."""
    email: str
    totp: str = ""          # the step-up after an email-code sign-in: authenticator or recovery code (ADR 0028)


class Ok(BaseModel):
    ok: bool = True


class ChangePasswordRequest(ApiRequest):
    """Logged in. Revokes the user's other sessions."""
    current_password: str = ""
    totp: str = ""
    new_password: str = ""


class ForgotPasswordRequest(ApiRequest):
    """Self-service reset with a current authenticator code; the owner account also needs one of its recovery codes
    (spent). The same 403 for an unknown email, a wrong code or a missing recovery code."""
    email: str = ""
    totp: str = ""
    new_password: str = ""
    recovery_code: str = ""


class ResetLinkResponse(BaseModel):
    reset_token: str
    expires_h: int


class ResetRequest(ApiRequest):
    """Owner-issued one-time link: sets a new password and enrols a new authenticator."""
    token: str = ""
    new_password: str = ""


class RecoveryLoginRequest(ApiRequest):
    """Owner only: a one-time recovery code in place of the authenticator code."""
    email: str = ""
    password: str = ""
    recovery_code: str = ""


class RecoveryLoginResponse(LoginResponse):
    totp_uri: str                 # the new authenticator: enrol it now, the old one no longer works
    recovery_codes_left: int


class EmailLinkRequest(ApiRequest):
    """Owner only, but the answer is the same generic 200 for every address (nothing can be enumerated)."""
    email: str = Field("", max_length=320)


class EmailLinkResponse(BaseModel):
    ok: bool = True
    detail: str = "If this address can use email sign-in, a link is on its way"


class EmailLinkRedeemRequest(ApiRequest):
    """The token from the link's fragment AND a current authenticator code: the link replaces the password only."""
    token: str = Field("", max_length=256)
    totp: str = Field("", max_length=16)


class RecoveryCodesRequest(ApiRequest):
    password: str = ""
    totp: str = ""


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class CodeSignIn(BaseModel):
    at_utc: str
    network: str                  # coarse (auth.coarse_ip), never the full address


class CodeSignIns(BaseModel):
    """The owner's Telegram-code sign-ins of the last 7 days, newest first (security condition 6): the banner shown
    after every sign-in."""
    sign_ins: list[CodeSignIn]
    channel: Literal["telegram", "mail"]


class RevokeResponse(BaseModel):
    ok: bool = True
    sessions: int


class DecisionResult(BaseModel):
    outcome: str                    # SUBMITTED: the engine applies it (and re-checks the RiskGate) on its next tick


class HaltRequest(ApiRequest):
    reason: str | None = None


class RearmRequest(ApiRequest):
    totp: str                       # a fresh authenticator code: re-arming needs a second factor (design)


class Status(BaseModel):
    mode: str
    halted: bool
    halted_by: str | None = None           # the channel that halted ("dashboard" | "telegram"), never who (A15)
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
    # where h comes from (row M25, research/cusum.py): "calibrated" = tuned to a 5% quarterly false-alarm rate at the
    # agent's backtest trade rate and mean taken p; "fixed" = drift.cusum_h, with `h_note` saying why
    h_source: Literal["calibrated", "fixed"] = "fixed"
    h_note: str = ""
    trades_per_week: float | None = None    # the backtest's trade rate the calibration used
    p_mean: float | None = None             # mean model p of the trades in the trace (the residuals' win probability)
    false_alarm: float | None = None        # target false-alarm rate per quarter of trading


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
    gates_passed: bool | None               # null when the trial recorded no gate verdict; a pass counts only
                                            # with its cost treatment recorded (registry.gate_pass_counts)
    pre_swap: bool = False                  # recorded a pass but no swap/cost source: shown, not counted
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
    # the director's governance fields (absent from plans written before them: defaults then)
    quarter_reserved: int = 0               # trials held for the quarter's pre-registered queue
    reservation: PlanReservation | None = None
    retired: list[RetiredFamilyView] = []
    reinstate_t: float | None = None        # Bonferroni-corrected bar on the shrunk attribution t to reinstate
    moves: list[PlanMove] = []              # which input moved which family's budget / share vs the evidence-only plan
    attribution_note: str | None = None
    hypotheses_note: str | None = None
    hypotheses_sha256: str | None = None    # hypotheses.md as the director read it
    hypotheses_sha256_now: str | None = None  # hypotheses.md as the API reads it now
    hypotheses_changed: bool = False        # the doc changed since the plan (both hashes known and different)
    hypotheses_drift: list[str] = []        # where the doc disagrees with research.retired_families


class PlanReservation(BaseModel):
    """Trials of the quarter held for the pre-registered queue (research/director.py Reservation)."""
    setting: int                            # research.reserved_trials_quarter for the plan's quarter (keyed by quarter)
    run: int                                # queued rows of the quarter a trial ran, and slots a trial used
    pending: int                            # queued rows not yet run, and slots still held or live
    reserved: int                           # max(setting - run - withdrawn - expired slots, pending, 0)


class RetiredFamilyView(BaseModel):
    family: str
    hypothesis_id: str | None               # its row in hypotheses.md section B
    since: str | None                       # retirement date (ISO)
    reason: str | None
    floor: int                              # exploration trials it keeps this quarter (never from the evidence pool)
    reinstated: bool                        # attribution trades after the retirement date cleared the bar
    new_evidence: list[str]                 # what reinstated it


class PlanMove(BaseModel):
    family: str
    source: str                             # "retired_families" | "attribution"
    detail: str
    budget_before: int                      # trials in the evidence-only plan
    budget_after: int
    share_before: float | None = None       # share of the evidence pool before / after the tilt
    share_after: float | None = None
    shift_pct: float | None = None          # (after / before - 1) x 100, at most +-25


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


# ============================================================================= auto mode (A10)
class VetoTestView(BaseModel):
    """Welch's two-sample t-test of mean R, approved minus rejected (telegram/automode.py VetoTest)."""
    n_approved: int
    n_rejected: int
    mean_r_approved: float | None
    mean_r_rejected: float | None
    diff: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    alpha: float
    indistinguishable: bool                 # p >= alpha AND the (1 - alpha) interval spans 0


class ForcedPropose(BaseModel):
    """An engine override that keeps propose-and-approve whatever the owner's mode says."""
    account_id: str
    kind: Literal["rearm_lock", "kill_switch", "risk_unreadable"]
    detail: str
    until: datetime | None = None           # rearm_lock: propose-only until then


class AutoModeView(BaseModel):
    owner_mode: Literal["propose", "auto"] | None    # control.json; None: never set (engines use their configured mode)
    mode_by: str | None
    mode_since: datetime | None             # last mode change: the evidence counts from here
    mode_reason: str | None
    engine_modes: dict[str, str]            # account -> the mode the engine is running now
    eligible: bool                          # the exact check /mode auto runs (auto_mode_eligibility_from_state)
    reasons: list[str]                      # why not, when not eligible
    decided: int
    approved: int
    rejected: int
    min_proposals: int
    min_outcomes_per_side: int
    breaches: list[str]
    test: VetoTestView
    forced_propose: list[ForcedPropose]
    can_enable: bool                        # this user is the owner, the evidence holds and the mode is not auto
    error: str | None = None                # the evidence could not be read (fails closed: not eligible)


class ModeChangeRequest(ApiRequest):
    mode: Literal["auto", "propose"]
    totp: str = Field("", max_length=16)    # auto only: the owner's one-time authenticator code


class ModeChangeResult(BaseModel):
    message: str
    view: AutoModeView
# ============================================================================= end auto mode


# ============================================================================= Chart tab (saved TradingView layouts)
class ChartLayout(BaseModel):
    name: str
    url: str | None         # the owner's layout URL; None for every other role (it identifies the owner's account)


class ChartLayoutsView(BaseModel):
    layouts: list[ChartLayout]
    can_edit: bool


class ChartLayoutIn(ApiRequest):
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(max_length=200)


class ChartLayoutsRequest(ApiRequest):
    layouts: list[ChartLayoutIn] = Field(max_length=3)
    totp: str = Field("", max_length=16)    # the step-up after an email-code sign-in (ADR 0028)
# ============================================================================= end Chart tab


# ============================================================================= Progress tab (W2-3, read-only)
# docs/ux/progress-screens.md "API gaps", built by goldbot/api/progress.py. Accounts appear by config name with any run
# of 5+ digits cut to its last two; no operator-typed text, no email, login or host; every time is UTC.
class GateProgressView(BaseModel):
    value: float
    target: float
    unit: Literal["trades", "days"]


class GateItemView(BaseModel):
    name: str
    met: bool
    required: bool                          # False: context only, decides nothing
    need: str
    evidence: str                           # the measured value; never the owner's `gate-evidence --detail` note
    proposed: bool                          # its threshold is PROPOSED and not in gates.signed_off
    progress: GateProgressView | None = None
    earliest_utc: datetime | None = None    # a day item: the calendar date the count is reached (never a forecast)
    ci95: tuple[float, float] | None = None # an expectancy item: 95% interval of the phase's mean return per trade
    # how `ci95` is computed: on per-trade returns as independent draws (the gates' convention), not clustered by day
    # like the Results rows (ADR 0022), so the screen can say so
    ci95_basis: str = "per-trade iid, return"


class GateView(BaseModel):
    gate: str
    met: bool                               # met on the evidence; recording stays the owner's command
    recorded: bool
    recorded_utc: datetime | None = None
    items: list[GateItemView]
    met_on_proposed: bool = False           # met, but a required item's threshold is PROPOSED and not signed off


class StopIncidentView(BaseModel):
    account: str
    mode: Literal["demo", "live"]
    closed_utc: datetime
    loss_frac: float                        # of the equity before the trade


class ImputedView(BaseModel):
    n: int                                  # pooled rows whose return is not net, charged an imputed cost
    charged_usd: float
    unpriced: int                           # gross rows that could not be priced


class StopRuleView(BaseModel):
    """P6 in fields a screen words itself. status: breached > cant_check > not_started > ok (a breach stays a breach
    while part of the record is unreadable: a missing row can only hide more)."""
    status: Literal["ok", "not_started", "breached", "cant_check"]
    cant_check: str | None = None           # owner wording; set whenever part of the record is unreadable or lost
    months: float | None = None
    after_months: float | None = None
    pooled_trades: int | None = None
    min_pooled_trades: int | None = None
    confidence: float | None = None
    lower_bound: float | None = None        # per-trade return, fraction (one-sided bound at `confidence`)
    min_trades_for_estimate: int            # below it there is no bound: "too few trades to estimate yet"
    worst_loss_frac: float | None = None
    weekly_cap: float | None = None
    clock_start_utc: datetime | None = None
    clock_source: Literal["paper_gate", "first_trade"] | None = None
    breach: list[Literal["long_run", "single_loss"]] = []
    incidents: list[StopIncidentView] = []  # worst first, at most 5
    incident_count: int = 0
    imputed: ImputedView | None = None


class ProposedThreshold(BaseModel):
    name: str                               # the config/settings.yaml `gates:` key
    value: float


class GatesView(BaseModel):
    generated_utc: datetime
    phase: int | None = None
    gates: list[GateView]
    stop_rule: StopRuleView
    proposed_count: int                     # from settings: PROPOSED thresholds not in gates.signed_off
    proposed: list[ProposedThreshold]
    cant_check: str | None = None           # owner wording for any unreadable input; never the raw error


AttributionVerdict = Literal["noise", "positive", "negative", "indistinguishable from zero", "by construction"]


class ResultCell(BaseModel):
    """One attribution cell (research/attribution.py Cell, ADR 0022). Rows are judged on `verdict_adj` at the row's
    own bar `t_bar`; "by construction" marks a cell whose sign is set by its definition (exit target / stop)."""
    n: int
    mean_r_gross: float | None
    mean_r_net: float | None
    std_r_net: float | None
    t_stat: float | None
    ci95_net: tuple[float, float] | None
    hit_rate: float | None
    profit_factor: float | None
    verdict: AttributionVerdict             # raw, |t| >= 2 (the overall headline)
    t_bar: float | None                     # the bar this cell applied
    ci_adj: tuple[float, float] | None      # mean +- t_bar x se; agrees with verdict_adj
    verdict_adj: AttributionVerdict
    se: float | None = None                 # day-clustered standard error, R per trade


class ResultExitCell(ResultCell):
    share: float
    mae_r_median: float | None = None
    mfe_r_median: float | None = None


class ResultCosts(BaseModel):
    source: str
    n: int
    mean_r: dict[str, float | None]         # spread, slippage, commission, swap, total (R per trade)
    share_of_gross: float | None
    killed: int                             # won before costs, lost after them
    by_session: dict[str, dict[str, float | None]]


class ResultLiveFills(BaseModel):
    fills: int
    mean_slippage_usd: float | None
    prior_usd: float | None
    mean_excess_usd: float | None
    t_stat: float | None
    mean_excess_r: float | None
    n_with_risk: int
    verdict: AttributionVerdict
    t_bar: float
    verdict_adj: AttributionVerdict
    orders: dict[str, int]


class AttributionReportView(BaseModel):
    as_of: datetime
    window_from: datetime
    min_trades: int
    t_bar: float                            # nominal row bar over `m` rows (each row carries its own `t_bar`)
    m: int
    n_candidates: int
    n_taken: int
    overall: ResultCell
    challengers: ResultCell
    breakdowns: dict[str, dict[str, ResultCell]]
    exits: dict[str, ResultExitCell]
    costs: ResultCosts
    live: dict[str, ResultLiveFills]        # by account (digits cut)
    notes: list[str]


class AttributionView(BaseModel):
    """state/attribution.json without its trade rows and calibration (calibration stays on Health)."""
    report: AttributionReportView | None    # None: no report yet (or unreadable: see cant_check)
    stale: bool                             # older than the job's last slot plus its max_late_hours
    next_run_utc: datetime | None
    cant_check: str | None = None


class DayNet(BaseModel):
    day: str                                # YYYY-MM-DD opportunity day (D-1 22:00 to D 22:00 UTC)
    net: float                              # account currency, net rows only
    n: int


class TradesSummary(BaseModel):
    """Your account's closed trades over the last `days` opportunity days (ops/report_days.py, the monthly report's
    day and P&L basis: net rows in the account currency only; other rows are counted and flagged, never summed)."""
    account: str
    mode: Literal["demo", "live", "paper"] | None
    currency: str | None
    days: int
    first_day: str
    last_day: str
    net: float
    n: int                                  # every closed trade in the window
    net_n: int                              # the ones in `net`
    days_traded: int
    trading_days: int                       # Monday-Friday opportunity days in the window
    target: float                           # ADR 0004's £50 day, counted, never aimed at
    days_at_target: int | None              # GBP accounts only, as the monthly report
    best_day: DayNet | None
    worst_day: DayNet | None
    by_day: list[DayNet]
    gross_rows: int                         # costs not recorded: not in `net`
    other_currency_rows: int                # P&L not in the account currency: not in `net`
# ============================================================================= end Progress tab


ResearchPlanView.model_rebuild()   # refers to models defined after it
Status.model_rebuild()          # `blackout` refers to ActiveBlackout, defined after Status
