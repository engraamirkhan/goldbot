"""API contract (Pydantic models) shared by the backend and the generated TypeScript client.
Phase 1 fills the routers; the shapes are fixed here so the frontend can start in parallel."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from goldbot.execution.not_confirmed import NotConfirmed
from goldbot.execution.not_placed import NotPlaced
from goldbot.ops.account_view import CantCheck, LossBudget, Milestones
from goldbot.telegram.approvals import PROPOSAL_ID_PATTERN, AskKind


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


class AskReasonView(BaseModel):
    """Why a demo account asks for this entry instead of entering on its own (backlog 103; ADR 0036: demo enters
    automatically). `text` is one plain sentence (telegram/approvals.py `ask_reason_text`, the Telegram card's words);
    the card prefixes "Asking because: ". `until`: the re-arm lock's end. A `terminal_not_demo` card is real money:
    Approve is disabled and the API refuses an approval (sign-off correction 2)."""
    kind: AskKind
    text: str
    until: datetime | None = None


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
    # backlog 103 (docs/design/ui-91-92-approval-card.md section 1): the money class first; null = neither the
    # proposal (an older engine) nor its engine's state file says: the card shows "account type unknown", never demo
    account_mode: Literal["demo", "live", "paper"] | None = None
    # the proposal's own approval window (bar and expired copy; no literal 90); always set by this API, nullable so
    # a client built before it keeps compiling (the card then uses expires_at - created)
    window_s: int | None = None
    ask_reason: AskReasonView | None = None   # demo only; null on live, paper and an unknown type (asking is the rule)
    # backlog 93, pending cards only (null on decided ones and when unknown): the median spread for this UTC
    # hour-of-week over the last 20 recorded trading days (api/spread_normal.py), in the same points as
    # `spread_points`; and what else is open on the account now, its summed stop risk in `currency`
    spread_normal_points: float | None = None
    open_positions: int | None = None
    open_risk_amount: float | None = None


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
    # history imported from the owner's chart export (counts only): bars and plot rows, and whether the repaint check has
    # passed; `last_bar_time` and `alerts_24h` count live alerts only ("" when only an export exists)
    csv_bars: int = 0
    csv_rows: int = 0
    # whether every history row (chart export and pull_history) has passed the repaint check; "none": no history
    repaint_status: Literal["none", "unvalidated", "validated"] = "none"
    # the direct pull (ADR 0030; goldbot/data/tv_pull.py): closed bars received live in the last 24 h (pull_live) and
    # backfilled history (pull_history), counted apart from the webhook alerts above
    pull_live_24h: int = 0
    pull_history_bars: int = 0


class TvStatusView(BaseModel):
    """The TradingView card (docs/ux/limits-and-tradingview.md B). TradingView is an optional second feed and never
    stops trading, so no state here is a failure: the screen shows expired, unknown and stale in amber."""
    now: str
    indicators: list[TvIndicatorStatus]
    # the direct pull's sign-in (state/tv_auth.db, goldbot/ops/tv_pull_health.py): not_set_up = the pull never ran
    # (opt-in); unknown = the state is unreadable, treated as expired
    signin: Literal["ok", "expired", "not_set_up", "unknown"] = "not_set_up"
    last_pull_utc: datetime | None = None   # the last successful pull
    stale: bool = False                     # signed in, but no successful pull for over 36 h (one is expected daily)
    fix_command: str | None = None          # with expired / unknown: the one line to run on the brain (hidden prompts)
    live_bars_24h: int = 0                  # every indicator: webhook alerts + pull_live bars, last 24 h
    history_bars: int = 0                   # every indicator: chart-export + pull_history bars


# ----------------------------------------------------------------------------- Limits card (read-only)
LimitSource = Literal["static", "adaptive", "revert", "owner", "default", "fault"]


class LimitChange(BaseModel):
    """One limit of one account that moved (goldbot/ops/limits_log.py: core.db ADR 0032 `limits_changes`, diffed per
    field, and ADR 0031 `adaptive_changes`). Values are fractions of equity; null = the minimum-lot exception off."""
    account_id: str
    ts: datetime
    field: Literal["daily_cap", "weekly_cap", "min_lot_risk_cap", "supervisor_daily_cap", "supervisor_weekly_cap"]
    old_value: float | None
    new_value: float | None
    unit: Literal["fraction_of_equity"] = "fraction_of_equity"
    # static: the owner's ADR 0032 ceilings; adaptive: the ADR 0031 rule; revert: the drawdown revert; owner: the
    # owner's re-arm after a revert; default: the defaults; fault: a controller failure (the defaults, fail safe)
    source: LimitSource
    reason: str                             # one plain sentence
    adr: str | None


class StrategyRiskView(BaseModel):
    """One strategy's (model version's) risk per trade today under dynamic limits (fractions of equity, before the
    allocator's multiplier). Null = the engine does not say."""
    version: str
    decided: float | None                   # the nightly decision's r_s for today, after the ceiling; null: base rate
    applied: float | None                   # what the gate sizes at now: r_s (or the base) x the same-day scale
    reason: str | None                      # the nightly decision's why, one sentence


class RiskTodayView(BaseModel):
    """Why the risk per trade is what it is today (docs/design/dynamic-limits-2026-10-19.md): the base rate, its
    ceiling and the same-day tighten-only factors (each 1 = no cut). Only on an account with dynamic limits on."""
    base: float | None                      # today's fixed risk per trade (the rate a strategy without a value gets)
    ceiling: float | None                   # the most any strategy may get (1% demo; the base rate on live)
    k_vol: float | None                     # 0.5: the 20-day median ATR15 is at or above its 1-year p90
    k_dd: float | None                      # 1 -> 0.5 as the drawdown from the high-water mark goes 2% -> 4%
    k_day: float | None                     # 0.5: today's loss reached half the daily allowance
    scale: float | None                     # the day's lowest k_vol x k_dd x k_day (cuts hold until the next day)
    strategies: list[StrategyRiskView]
    why: str                                # one plain sentence


class LimitAccount(BaseModel):
    """One account's risk limits now, from what its engine publishes (state/engine_<account>.json) and the revert in
    core.db `limits_state`. Money is in `currency`; percentages are fractions. Null = the engine does not say."""
    account_id: str
    mode: Literal["demo", "live", "paper"]
    profile: str                            # "default" or the ADR 0032 profile ("demo-2026-10-18")
    source: LimitSource                     # what set the current values (ADR 0032 static ceilings now)
    adr: str | None
    updated_utc: datetime | None            # the engine file's time
    stale: bool                             # the engine file is over 60 s old
    currency: str | None
    balance: float | None
    day_start_equity: float | None          # what the daily cap is a percentage of (00:00 UTC)
    week_start_equity: float | None         # what the weekly cap is a percentage of (Sunday 00:00 UTC)
    daily_cap_pct: float | None
    daily_limit: float | None               # the gate's: min(daily cap x day-start equity, absolute daily limit)
    daily_used: float | None                # lost since 00:00 UTC, floating P&L included (gains add room)
    daily_resets_utc: datetime
    absolute_daily_limit: float | None      # ADR 0004 (accounts.yaml daily_loss_limit_abs), null when off
    weekly_cap_pct: float | None
    weekly_limit: float | None              # weekly cap x week-start equity
    weekly_used: float | None
    weekly_resets_utc: datetime             # Sunday 00:00 UTC
    min_lot: float | None                   # the broker's smallest trade (lots)
    min_lot_risk: float | None              # that trade's stop risk now, account currency
    min_lot_risk_cap: float | None          # the minimum-lot exception: most it may risk (fraction); null = off
    revert_active: bool                     # the 8% revert holds: the defaults until the owner re-arms
    revert_since: datetime | None
    revert_reason: str | None
    cap_hit: Literal["none", "daily", "weekly"]   # a spent cap: new entries stopped, exits keep working
    last_change: LimitChange | None
    error: str | None                       # why the limits cannot be shown (the engines fail closed on it)
    risk_today: RiskTodayView | None = None   # dynamic limits: "why this limit today"; null when they are off


class LimitsSupervisorView(BaseModel):
    """The supervisor's combined caps (all accounts together; goldbot/risk/supervisor.py), fractions, and (backlog 98)
    the same in money: cap x the combined day/week-start equity, used = that start equity - combined equity now
    (floating P&L included, a gain uses nothing). Money is null while the supervisor is not reporting, has not summed
    the accounts, or the accounts report different currencies (`mixed_currencies`: pounds are never added to
    dollars, ADR 0004)."""
    daily_cap: float | None                 # now (state/supervisor.json); null when it is not reporting
    weekly_cap: float | None
    default_daily: float                    # settings.yaml risk.supervisor_daily_cap / _weekly_cap
    default_weekly: float
    envelope_daily: float                   # the CFO's demo envelope (ADR 0032 SUPERVISOR_DEMO_ENVELOPE)
    envelope_weekly: float
    currency: str | None = None
    mixed_currencies: bool | None = None    # null: the supervisor is not reporting
    daily_cap_amount: float | None = None
    daily_used_amount: float | None = None
    weekly_cap_amount: float | None = None
    weekly_used_amount: float | None = None
    # always set by this API (nullable only for clients built before it)
    day_resets_utc: datetime | None = None    # the next 00:00 UTC (the risk day)
    week_resets_utc: datetime | None = None   # the next Sunday 00:00 UTC


class LimitsView(BaseModel):
    generated_utc: datetime
    accounts: list[LimitAccount]
    supervisor: LimitsSupervisorView
    error: str | None                       # core.db's change log or revert could not be read


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


class ResearchToday(BaseModel):
    """Trials of the current UTC day by verdict (docs/ux/limits-and-tradingview.md C), counted server-side so the day
    boundary lives in one place. A pass counts only with its cost treatment (registry.gate_pass_counts)."""
    date_utc: date
    run: int                                # trial rows stamped today
    passed: int                             # passed every gate, costs recorded
    not_counted: int                        # recorded a pass without its cost treatment
    failed: int
    no_verdict: int                         # no gate verdict recorded
    latest: TrialRow | None
    quarter_end: date                       # the trial budget (ResearchView.budget) renews the day after


class ResearchCounts(BaseModel):
    """Trials by verdict, counted as the trial table reads them (a pass counts only with its cost treatment)."""
    run: int
    passed: int
    failed: int
    no_verdict: int
    not_counted: int                        # recorded a pass without its cost treatment


class NextTrial(BaseModel):
    """Where the next trials go: the research plan's top-ranked family. `planned_date` stays null until the plan
    carries dates (none is invented)."""
    family: str
    timeframe: str | None
    budget: int                             # trials the plan gives the family this quarter
    planned_date: date | None = None


class ResearchSummary(BaseModel):
    """Backlog 97 (docs/design/ui-97-98-research-limits.md, "Is research getting us to £50 a day?"): the first card
    of the Research screen, from the registry and the plan only. No money projection (needs backlog 33 and
    quant-reviewer sign-off); the research-director signs off the wording before release."""
    passed_total: int                       # counted passes, all time (the registry, not the capped trials list)
    failed_total: int
    no_verdict_total: int
    not_counted_total: int
    since_last_week: ResearchCounts         # trials stamped in the 7 days before now
    best_candidate: TrialRow | None         # the counted pass with the highest net R per trade; null: none passed
    next_trial: NextTrial | None            # null without a readable plan
    north_star_gbp: float                   # settings research.north_star_daily_gbp (ADR 0004)


class ResearchView(BaseModel):
    generated_utc: datetime
    budget: TrialBudget
    trials: list[TrialRow]                  # newest first, at most 200
    trials_total: int
    plan: ResearchPlanView | None
    plan_error: str | None
    hypotheses: HypothesisDoc
    today: ResearchToday
    summary: ResearchSummary | None = None   # backlog 97; always set by this API (nullable for older clients)
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
    """Why a demo account asks for its entries now (ADR 0036: demo normally enters on its own), with the same kinds
    and words as the approval card's `ask_reason`: the re-arm lock and the kill switch (read from the risk file), an
    unreadable risk file, and what the engine publishes (`ask_reason`: the owner's /mode propose, a terminal that is
    not demo, the account's own configuration)."""
    account_id: str
    kind: AskKind
    detail: str
    until: datetime | None = None           # rearm_lock: propose-only until then


class EntryModeAccount(BaseModel):
    """One reporting engine's entry mode (backlog 92): the account's class and whether it enters on its own or asks.
    Real-money accounts always ask (ADR 0036); `ask_reason` is set on demo only."""
    account_id: str
    account_mode: Literal["demo", "live", "paper"] | None    # null: the engine file does not say
    effective_mode: Literal["auto", "propose"]
    ask_reason: AskReasonView | None
    last_seen_utc: datetime | None          # the engine file's time (the card marks an old one as stale)


class AutoModeView(BaseModel):
    owner_mode: Literal["propose", "auto"] | None    # control.json; None: each account's own setting (ADR 0041)
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
    # backlog 92 (always set by this API; nullable for older clients): one row per reporting engine, and the
    # approval window real-money rows quote (settings risk.approval_window_seconds: "asks you (90 s)")
    accounts: list[EntryModeAccount] | None = None
    window_s: int | None = None
    can_enable: bool                        # this user is the owner, the evidence holds and the mode is not auto
    error: str | None = None                # the evidence could not be read (fails closed: not eligible)


class ModeChangeRequest(ApiRequest):
    # account: clear the owner's override, each engine uses its account's own mode (ADR 0041; owner role, no code)
    mode: Literal["auto", "propose", "account"]
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


# ============================================================================= Home activity feed (GET /api/activity)
ActivityKind = Literal["shadow_opened", "shadow_closed", "position_opened", "position_closed", "probe_opened",
                       "probe_closed", "refused"]
ActivitySource = Literal["shadow", "positions", "probes", "journal"]


class ActivityEvent(BaseModel):
    """One thing the system did (goldbot/api/activity.py): a shadow trade, an account position or a cost-probe order
    opened or closed, or an entry the engine refused. Fields a kind does not have are null."""
    ts: datetime                            # when it happened, UTC (a shadow trade: its bar's close)
    kind: ActivityKind
    account: str | None = None              # config name, any run of 5+ digits cut; None for shadow trades (paper)
    mode: Literal["demo", "live", "paper"] | None = None
    agent_id: str | None = None
    version: str | None = None              # the model version (shadow trades, probes, probe refusals)
    timeframe: str | None = None
    side: Literal["long", "short"] | None = None
    lots: float | None = None
    p: float | None = None
    entry: float | None = None              # USD per oz
    exit: float | None = None
    stop: float | None = None               # the stop and the take-profit target the trade was opened with (shadow
    target: float | None = None             # trades, open positions); null where the record does not keep them
    reason: str | None = None               # exit: target | stop | time | ...; refused: the journal's action code
    r: float | None = None                  # result in R of the initial risk
    ret: float | None = None                # return, side x (exit - entry) / entry (shadow: gross of costs)
    pnl: float | None = None                # account currency (`currency`)
    currency: str | None = None


class ActivityView(BaseModel):
    events: list[ActivityEvent]             # newest first
    unreadable: list[ActivitySource]        # sources that could not be read: their events are missing, not zero
# ============================================================================= end Home activity feed


# ============================================================================= Home trades summary (GET /api/trades/overview)
# shadow_taken: shadow trades that cleared the threshold; shadow_all: those plus the counterfactual candidates;
# demo / live: the accounts' positions by mode; probe: cost-probe orders (demo)
TradeType = Literal["shadow_taken", "shadow_all", "demo", "live", "probe"]
TradePeriod = Literal["today", "7d", "all"]


class TradeRef(BaseModel):
    """One closed trade named by a summary (its best or worst)."""
    ts: datetime                            # its close, UTC
    agent_id: str | None
    timeframe: str | None
    r: float | None
    pnl: float | None
    currency: str | None


class ExitCounts(BaseModel):
    target: int = 0
    stop: int = 0
    time: int = 0
    other: int = 0                          # trail, hard flat, engine stop close, manual, not recorded, ...


class TradeStats(BaseModel):
    """Closed trades of the period, and the trades open now (open is the same in every period)."""
    open: int
    closed: int
    wins: int                               # result > 0 (R, else P&L, else return); a zero result is neither
    losses: int
    win_rate: float | None                  # wins / closed; None with nothing closed
    n_r: int                                # closed trades with an R
    total_r: float | None                   # over those n_r; None when there are none
    avg_r: float | None
    net_pnl: dict[str, float]               # by currency, net-of-cost rows only (nothing gross is labelled net)
    best: TradeRef | None                   # by R when the bucket has any, else by net P&L
    worst: TradeRef | None
    exits: ExitCounts


class TradeTypeSummary(BaseModel):
    type: TradeType
    stats: TradeStats
    by_family: dict[str, TradeStats]        # strategy family (the agent id without its generation and hash)
    by_timeframe: dict[str, TradeStats]     # 15m / 1h / 4h / 1d; "unknown" when the record has none (cost probes)


class TradesPeriodView(BaseModel):
    period: TradePeriod
    since: datetime | None                  # the first opportunity day's start (D-1 22:00 UTC); None for all time
    types: list[TradeTypeSummary]           # every type, in TradeType order, empty ones included


class TradesOverview(BaseModel):
    as_of: datetime
    periods: list[TradesPeriodView]         # today, 7d, all
    unreadable: list[ActivitySource]        # sources that could not be read: their trades are missing, not zero
# ============================================================================= end Home trades summary


# ============================================================================= Home status card stack
# goldbot/api/home_cards.py (docs/ux/home-redesign-2026-10-19.md, Decision 2026-10-19); read-only, viewer and above
class OpenPosition(BaseModel):
    """One open position (N1): money in the account currency, prices in USD per oz. `floating_pnl` is at the engine's
    mid price before spread, commission and swap; None when the engine publishes no price or conversion."""
    account: str                            # config name, any run of 5+ digits cut
    mode: Literal["demo", "live", "paper"] | None
    probe: bool                             # a cost-probe order (ADR 0037), not a strategy's trade
    position_id: int
    agent_id: str
    family: str
    timeframe: str | None
    side: Literal["long", "short"]
    lots: float
    entry: float | None
    stop: float | None                      # the stop and target the engine holds now
    target: float | None
    opened_utc: datetime
    price: float | None                     # the engine's last mid price
    floating_pnl: float | None
    currency: str | None
    r_now: float | None                     # the move from entry over the risk at entry
    open_risk: float | None = None          # the loss if the stop held now, in the account currency; 0 once locked in
    age_s: float | None                     # how old the engine's figures are
    stale: bool                             # older than 60 s (or unknown): never shown as current


class OpenPositionsView(BaseModel):
    as_of: datetime
    positions: list[OpenPosition]           # oldest first
    unreadable: list[str]                   # accounts whose orders file could not be read: missing, not zero


StrategyState = Literal["in_trade", "signal", "watching", "idle", "unknown"]


class StrategyWatch(BaseModel):
    """One strategy agent's state in words (N2). Box edges and MFI are not published by the engine yet: None."""
    agent_id: str
    family: str
    timeframe: str | None
    state: StrategyState
    text: str                               # the state in words; the client adds the time of `signal_utc`
    signal_utc: datetime | None = None      # the last signal bar's close
    taken: bool | None = None               # the last signal cleared its threshold
    side: Literal["long", "short"] | None = None
    entry: float | None = None              # in a trade (shadow): USD per oz
    stop: float | None = None
    target: float | None = None
    box_high: float | None = None
    box_low: float | None = None
    mfi: float | None = None


class StrategyWatchView(BaseModel):
    as_of: datetime
    agents: list[StrategyWatch]             # by family, then timeframe
    unreadable: list[Literal["agents", "shadow"]]


class DayCosts(BaseModel):
    """Today's costs of the closed records, account currency: commission paid (positive), swap (broker sign), spread
    where the record keeps it in money (`spread_complete` False: some records keep it only in points)."""
    spread: float
    spread_complete: bool
    commission: float
    swap: float


class LossLimit(BaseModel):
    key: Literal["probe_stop", "daily_limit"]
    label: str
    limit: float                            # account currency
    remaining: float


class LossMeter(BaseModel):
    used: float                             # today's loss counted by the engine's daily limit, account currency
    limits: list[LossLimit]                 # smallest first
    binding: Literal["probe_stop", "daily_limit"]   # the limit with the least left: it stops entries first


class TodayAccount(BaseModel):
    """Today's money for one account (never summed across accounts or modes)."""
    account: str
    mode: Literal["demo", "live", "paper"] | None
    currency: str | None
    day_pnl: float | None                   # equity - the day's starting equity: after every cost the broker charged
    open_pnl: float | None                  # equity - balance: the open positions' floating P&L
    closed_pnl: float | None                # net records closed this opportunity day (positions and cost probes)
    closed_n: int
    closed_complete: bool                   # False: a gross or unreadable record is left out of closed_pnl
    costs: DayCosts | None
    loss_meter: LossMeter | None            # None: the engine publishes no daily limit
    age_s: float | None
    stale: bool                             # older than 60 s (or unknown)


class TodayView(BaseModel):
    as_of: datetime
    accounts: list[TodayAccount]
# ============================================================================= end Home status card stack


ResearchPlanView.model_rebuild()   # refers to models defined after it
Status.model_rebuild()          # `blackout` refers to ActiveBlackout, defined after Status
