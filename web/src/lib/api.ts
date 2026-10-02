// Typed client over the FastAPI backend. Every shape comes from schema.d.ts, which is generated from the
// backend's OpenAPI schema: `python scripts/export_openapi.py && npm run gen:api`. CI regenerates both and
// fails on a diff, so a contract change in goldbot/api/schema.py cannot reach main without the client.
import type { components } from "./schema";

type Schemas = components["schemas"];
export type AccountSummary = Schemas["AccountSummary"];
export type Stage = AccountSummary["stage"];
export type Proposal = Schemas["Proposal"];
export type Decision = Schemas["Decision"];
export type ReasonCode = NonNullable<Decision["reason_code"]>;
export type AgentRow = Schemas["AgentRow"];
export type FeedHealth = Schemas["FeedHealth"];
export type Status = Schemas["Status"];
export type Role = Schemas["Me"]["role"];
export type Me = Schemas["Me"];
export type UserRow = Schemas["UserRow"];
export type JobRow = Schemas["JobRow"];

let token: string | null = sessionStorage.getItem("goldbot_token");
let me: Me | null = JSON.parse(sessionStorage.getItem("goldbot_me") ?? "null");

export function setToken(t: string | null) {
  token = t;
  if (t) sessionStorage.setItem("goldbot_token", t); else { sessionStorage.removeItem("goldbot_token"); sessionStorage.removeItem("goldbot_me"); me = null; }
}
export function setUser(u: Me | null) { me = u; if (u) sessionStorage.setItem("goldbot_me", JSON.stringify(u)); }
export const hasToken = () => !!token;
export const currentUser = () => me;
export const hasRole = (r: Role) => { const rank = { viewer: 0, approver: 1, owner: 2 }; return !!me && rank[me.role] >= rank[r]; };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init?.headers ?? {}) } });
  if (r.status === 401) { setToken(null); throw new Error("login required"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? r.statusText);
  return r.json() as Promise<T>;
}

export const api = {
  authState: () => req<Schemas["AuthState"]>("/api/auth/state"),
  setup: (setup_code: string, email: string, password: string) => req<Schemas["TotpEnrolment"]>("/api/auth/setup", { method: "POST", body: JSON.stringify({ setup_code, email, password }) }),
  login: (email: string, password: string, totp: string) => req<Schemas["LoginResponse"]>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password, totp }) }),
  logout: () => req<Schemas["Ok"]>("/api/auth/logout", { method: "POST" }),
  accept: (token: string, password: string) => req<Schemas["AcceptResponse"]>("/api/auth/accept", { method: "POST", body: JSON.stringify({ token, password }) }),
  invite: (email: string, role: Role) => req<Schemas["InviteResponse"]>("/api/auth/invite", { method: "POST", body: JSON.stringify({ email, role }) }),
  users: () => req<UserRow[]>("/api/users"),
  setRole: (email: string, role: Role) => req<Schemas["Ok"]>("/api/users/role", { method: "POST", body: JSON.stringify({ email, role }) }),
  disable: (email: string) => req<Schemas["Ok"]>("/api/users/disable", { method: "POST", body: JSON.stringify({ email }) }),
  status: () => req<Status>("/api/status"),
  accounts: () => req<AccountSummary[]>("/api/accounts"),
  proposals: () => req<Proposal[]>("/api/proposals"),
  decide: (proposal_id: string, action: "approve" | "reject", reason_code?: ReasonCode) =>
    req<Schemas["DecisionResult"]>("/api/decisions", { method: "POST", body: JSON.stringify({ proposal_id, action, reason_code }) }),
  agents: () => req<AgentRow[]>("/api/agents"),
  feeds: () => req<FeedHealth[]>("/api/feeds"),
  jobs: () => req<JobRow[]>("/api/jobs"),
};

export function liveSocket(onEvent: (e: { type: string } & Record<string, unknown>) => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (m) => onEvent(JSON.parse(m.data));
  return () => ws.close();
}
