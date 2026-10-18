// Typed client over the FastAPI backend. Every shape comes from schema.d.ts, which is generated from the
// backend's OpenAPI schema: `python scripts/export_openapi.py && npm run gen:api`. CI regenerates both and
// fails on a diff, so a contract change in goldbot/api/schema.py cannot reach main without the client.
import type { components, operations } from "./schema";

type Schemas = components["schemas"];
export type AccountSummary = Schemas["AccountSummary"];
export type Stage = AccountSummary["stage"];
export type EntryStatus = Schemas["EntryStatus"];
export type EntryBlock = Schemas["EntryBlock"];
export type Proposal = Schemas["Proposal"];
export type DecidedProposal = Schemas["DecidedProposal"];
export type Decision = Schemas["Decision"];
export type ReasonCode = NonNullable<Decision["reason_code"]>;
export type AgentRow = Schemas["AgentRow"];
export type FeedHealth = Schemas["FeedHealth"];
export type Status = Schemas["Status"];
export type Role = Schemas["Me"]["role"];
export type GrantableRole = Schemas["RoleChange"]["role"];   // the owner role is never granted
export type Me = Schemas["Me"];
export type UserRow = Schemas["UserRow"];
export type JobRow = Schemas["JobRow"];
export type AgentRunRow = Schemas["AgentRunRow"];
export type CalendarResponse = Schemas["CalendarResponse"];
export type CalendarEvent = Schemas["CalendarEvent"];
export type ActiveBlackout = Schemas["ActiveBlackout"];
export type Headline = Schemas["Headline"];
export type AutoModeView = Schemas["AutoModeView"];
export type ModeChangeResult = Schemas["ModeChangeResult"];
export type ApprovalMode = Schemas["ModeChangeRequest"]["mode"];
export type ChartLayoutsView = Schemas["ChartLayoutsView"];
export type ChartLayoutIn = Schemas["ChartLayoutIn"];
export type GatesView = Schemas["GatesView"];
export type GateView = Schemas["GateView"];
export type GateItemView = Schemas["GateItemView"];
export type StopRuleView = Schemas["StopRuleView"];
export type AttributionView = Schemas["AttributionView"];
export type AttributionReportView = Schemas["AttributionReportView"];
export type ResultCell = Schemas["ResultCell"];
export type TradesSummary = Schemas["TradesSummary"];
export type LimitsView = Schemas["LimitsView"];
export type LimitChange = Schemas["LimitChange"];
export type TvStatusView = Schemas["TvStatusView"];
export type LimitChangesQuery = NonNullable<operations["limits_changes_api_limits_changes_get"]["parameters"]["query"]>;
export type CalendarQuery = NonNullable<operations["calendar_api_calendar_get"]["parameters"]["query"]>;
export type NewsQuery = NonNullable<operations["news_api_news_get"]["parameters"]["query"]>;

/** `?a=1&b=2` from the defined, non-null values of a typed query object (the server clamps out-of-range values). */
function qs(q: Record<string, number | string | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v != null) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

let token: string | null = sessionStorage.getItem("goldbot_token");
let me: Me | null = JSON.parse(sessionStorage.getItem("goldbot_me") ?? "null");

const sessionEnded = new Set<() => void>();
/** Called when the server says the session is gone (any 401), so the app can return to the sign-in screen. */
export function onSessionEnded(fn: () => void): () => void {
  sessionEnded.add(fn);
  return () => { sessionEnded.delete(fn); };
}

export function setToken(t: string | null) {
  token = t;
  if (t) sessionStorage.setItem("goldbot_token", t); else { sessionStorage.removeItem("goldbot_token"); sessionStorage.removeItem("goldbot_me"); me = null; }
}
export function setUser(u: Me | null) { me = u; if (u) sessionStorage.setItem("goldbot_me", JSON.stringify(u)); }
export const hasToken = () => !!token;
export const currentUser = () => me;
export const hasRole = (r: Role) => { const rank = { viewer: 0, approver: 1, owner: 2 }; return !!me && rank[me.role] >= rank[r]; };

/** The server refused the token a request or socket was SENT with. Act only if that is still the current session: a
 * late 401 for an old token (a poll in flight when someone signed in again) must not wipe the new session, and a failed
 * sign-in (no token sent) is not a lost session. */
function endSession(sent: string | null) {
  if (sent === null || token !== sent) return;
  setToken(null);
  sessionEnded.forEach((fn) => fn());
}

/** A failed request: the server's `detail` (or the status text) and the HTTP status; 0 when the server was not reached,
 * with NETWORK_TEXT instead of the browser's own words ("Failed to fetch", "Load failed"). */
export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message); this.name = "ApiError"; }
}
export const NETWORK_TEXT = "Could not reach goldbot. Check your connection and try again.";

/** Server refusals the screens recognise by their exact words (goldbot/api/auth.py SIGN_IN_LOCKED, goldbot/api/app.py
 * STEPUP_REQUIRED and STEPUP_LOCKED; tests/test_web_auth_wording.py fails when either side changes alone). */
export const SERVER_SAYS = {
  signInLocked: "too many attempts; wait 15 minutes",
  stepupRequired: "authenticator code required",
  stepupLocked: "too many attempts: user management, re-arm and auto mode are locked for 15 minutes",
} as const;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const sent = token;
  let r: Response;
  try {
    r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(sent ? { authorization: `Bearer ${sent}` } : {}), ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(0, NETWORK_TEXT);
  }
  if (r.status === 401) {
    endSession(sent);
    throw new ApiError(401, "login required");
  }
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({})))?.detail ?? r.statusText);
  return r.json() as Promise<T>;
}

export const api = {
  authState: () => req<Schemas["AuthState"]>("/api/auth/state"),
  setup: (setup_code: string, email: string, password: string) => req<Schemas["SetupResponse"]>("/api/auth/setup", { method: "POST", body: JSON.stringify({ setup_code, email, password }) }),
  login: (email: string, password: string, totp: string) => req<Schemas["LoginResponse"]>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password, totp }) }),
  logout: () => req<Schemas["Ok"]>("/api/auth/logout", { method: "POST" }),
  accept: (token: string, password: string) => req<Schemas["AcceptResponse"]>("/api/auth/accept", { method: "POST", body: JSON.stringify({ token, password }) }),
  // `totp` on the six user-management calls: the step-up after an email-code sign-in (ADR 0028), an authenticator or
  // recovery code; "" after any other sign-in (the server decides from the session's stored method)
  invite: (email: string, role: GrantableRole, totp = "") => req<Schemas["InviteResponse"]>("/api/auth/invite", { method: "POST", body: JSON.stringify({ email, role, totp }) }),
  users: () => req<UserRow[]>("/api/users"),
  setRole: (email: string, role: GrantableRole, totp = "") => req<Schemas["Ok"]>("/api/users/role", { method: "POST", body: JSON.stringify({ email, role, totp }) }),
  disable: (email: string, totp = "") => req<Schemas["Ok"]>("/api/users/disable", { method: "POST", body: JSON.stringify({ email, totp }) }),
  enable: (email: string, totp = "") => req<Schemas["Ok"]>("/api/users/enable", { method: "POST", body: JSON.stringify({ email, totp }) }),
  revokeSessions: (email: string, totp = "") => req<Schemas["RevokeResponse"]>("/api/users/revoke-sessions", { method: "POST", body: JSON.stringify({ email, totp }) }),
  resetLink: (email: string, totp = "") => req<Schemas["ResetLinkResponse"]>("/api/auth/reset-link", { method: "POST", body: JSON.stringify({ email, totp }) }),
  reset: (token: string, new_password: string) => req<Schemas["AcceptResponse"]>("/api/auth/reset", { method: "POST", body: JSON.stringify({ token, new_password }) }),
  changePassword: (current_password: string, totp: string, new_password: string) =>
    req<Schemas["Ok"]>("/api/auth/password/change", { method: "POST", body: JSON.stringify({ current_password, totp, new_password }) }),
  forgotPassword: (email: string, totp: string, new_password: string, recovery_code = "") =>
    req<Schemas["Ok"]>("/api/auth/password/forgot", { method: "POST", body: JSON.stringify({ email, totp, new_password, recovery_code }) }),
  recoveryLogin: (email: string, password: string, recovery_code: string) =>
    req<Schemas["RecoveryLoginResponse"]>("/api/auth/recovery/login", { method: "POST", body: JSON.stringify({ email, password, recovery_code }) }),
  emailLink: (email: string) => req<Schemas["EmailLinkResponse"]>("/api/auth/email-link", { method: "POST", body: JSON.stringify({ email }) }),
  redeemEmailLink: (token: string, totp: string) =>
    req<Schemas["LoginResponse"]>("/api/auth/email-link/redeem", { method: "POST", body: JSON.stringify({ token, totp }) }),
  // ADR 0028: password + a 6-digit code emailed to the owner. The same answer for every input (a decoy challenge when
  // nothing was sent); the challenge and the code live only in the Login screen's memory, never in storage.
  emailCode: (email: string, password: string) =>
    req<Schemas["EmailCodeChallenge"]>("/api/auth/email-code", { method: "POST", body: JSON.stringify({ email, password }) }),
  verifyEmailCode: (challenge: string, code: string) =>
    req<Schemas["LoginResponse"]>("/api/auth/email-code/verify", { method: "POST", body: JSON.stringify({ challenge, code }) }),
  resendEmailCode: (challenge: string) =>
    req<Schemas["EmailCodeChallenge"]>("/api/auth/email-code/resend", { method: "POST", body: JSON.stringify({ challenge }) }),
  // Telegram-code sign-ins of the last 7 days (owner only), for the banner shown after every sign-in
  codeSignIns: () => req<Schemas["CodeSignIns"]>("/api/auth/code-sign-ins"),
  recoveryCodes: (password: string, totp: string) =>
    req<Schemas["RecoveryCodesResponse"]>("/api/auth/recovery/codes", { method: "POST", body: JSON.stringify({ password, totp }) }),
  status: () => req<Status>("/api/status"),
  automode: () => req<AutoModeView>("/api/automode"),
  setMode: (mode: ApprovalMode, totp = "") => req<ModeChangeResult>("/api/automode", { method: "POST", body: JSON.stringify({ mode, totp }) }),
  chartLayouts: () => req<ChartLayoutsView>("/api/chart/layouts"),
  setChartLayouts: (layouts: ChartLayoutIn[], totp = "") => req<ChartLayoutsView>("/api/chart/layouts", { method: "POST", body: JSON.stringify({ layouts, totp }) }),
  accounts: () => req<AccountSummary[]>("/api/accounts"),
  proposals: () => req<Proposal[]>("/api/proposals"),
  recentProposals: () => req<DecidedProposal[]>("/api/proposals/recent"),
  // `created` is the card's own stamp from /api/proposals: the decision binds to the card shown (BACKLOG 63)
  decide: (proposal_id: string, created: number, action: "approve" | "reject", reason_code?: ReasonCode) =>
    req<Schemas["DecisionResult"]>("/api/decisions", { method: "POST", body: JSON.stringify({ proposal_id, created, action, reason_code }) }),
  halt: (reason?: string) => req<Status>("/api/halt", { method: "POST", body: JSON.stringify({ reason: reason || null }) }),
  rearm: (totp: string) => req<Status>("/api/rearm", { method: "POST", body: JSON.stringify({ totp }) }),
  agents: () => req<AgentRow[]>("/api/agents"),
  feeds: () => req<FeedHealth[]>("/api/feeds"),
  jobs: () => req<JobRow[]>("/api/jobs"),
  agentRuns: () => req<AgentRunRow[]>("/api/agent-runs"),
  calendar: (q: CalendarQuery = {}) => req<CalendarResponse>(`/api/calendar${qs(q)}`),
  news: (q: NewsQuery = {}) => req<Headline[]>(`/api/news${qs(q)}`),
  health: () => req<Schemas["HealthView"]>("/api/health"),
  research: () => req<Schemas["ResearchView"]>("/api/research"),
  // Progress tab (docs/ux/progress-screens.md): read-only, any signed-in role
  gates: () => req<GatesView>("/api/gates"),
  attribution: () => req<AttributionView>("/api/attribution"),
  tradesSummary: () => req<TradesSummary[]>("/api/trades/summary"),
  // read-only cards (docs/ux/limits-and-tradingview.md): nothing here changes a limit or the TradingView link
  limits: () => req<LimitsView>("/api/limits"),
  limitChanges: (q: LimitChangesQuery = {}) => req<LimitChange[]>(`/api/limits/changes${qs(q)}`),
  tradingview: () => req<TvStatusView>("/api/tradingview"),
};

export function liveSocket(onEvent: (e: { type: string } & Record<string, unknown>) => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  // browsers cannot set headers on a WebSocket: the session token is the first message, or the server closes it
  const sent = token;
  ws.onopen = () => ws.send(JSON.stringify({ token: sent }));
  ws.onclose = (e) => { if (e.code === 4401) endSession(sent); };   // the server dropped this session (app.py: logged out, expired, disabled)
  ws.onmessage = (m) => onEvent(JSON.parse(m.data));
  return () => ws.close();
}
