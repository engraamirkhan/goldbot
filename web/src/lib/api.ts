// Typed client over the FastAPI backend. Shapes mirror goldbot/api/schema.py; run `npm run gen:api`
// against a running backend to regenerate api.d.ts from the OpenAPI schema when the contract changes.

export type Stage = "normal" | "size_down" | "halted";

export interface AccountSummary {
  account_id: string; broker: string; mode: "demo" | "live" | "paper"; equity: number;
  day_pnl_pct: number; week_pnl_pct: number; drawdown_pct: number; stage: Stage;
  open_positions: number; account_class: "raw" | "standard" | "unknown";
}
export interface Proposal {
  proposal_id: string; account_id: string; agent_id: string; side: "long" | "short"; lots: number;
  entry: number; stop: number; target: number; p: number; ev_r: number; spread_points: number;
  top_features: [string, number][]; expires_at: string; tradingview_url?: string | null;
}
export type ReasonCode = "news" | "cost" | "discretion" | "duplicate" | "other";
export interface AgentRow {
  agent_id: string; family: string; generation: number; parent_id: string | null;
  status: "shadow" | "live" | "retired"; n_trades: number; expectancy_r: number; hit_rate: number;
  calibration_ece: number; fitness: number; capital_weight: number;
}
export interface FeedHealth {
  account_id: string; last_tick_age_s: number; spread_points: number; terminal_connected: boolean;
  webhook_p99_latency_s: number | null; supervisor_heartbeat_age_s: number;
}
export interface Status { mode: string; halted: boolean; pending: number; supervisor: Record<string, unknown>; }

let token: string | null = sessionStorage.getItem("goldbot_token");

export function setToken(t: string | null) {
  token = t;
  if (t) sessionStorage.setItem("goldbot_token", t); else sessionStorage.removeItem("goldbot_token");
}
export const hasToken = () => !!token;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init?.headers ?? {}) } });
  if (r.status === 401) { setToken(null); throw new Error("login required"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? r.statusText);
  return r.json() as Promise<T>;
}

export const api = {
  login: (totp: string) => req<{ token: string }>("/api/login", { method: "POST", body: JSON.stringify({ totp }) }),
  status: () => req<Status>("/api/status"),
  accounts: () => req<AccountSummary[]>("/api/accounts"),
  proposals: () => req<Proposal[]>("/api/proposals"),
  decide: (proposal_id: string, action: "approve" | "reject", reason_code?: ReasonCode) =>
    req<{ outcome: string }>("/api/decisions", { method: "POST", body: JSON.stringify({ proposal_id, action, reason_code }) }),
  agents: () => req<AgentRow[]>("/api/agents"),
  feeds: () => req<FeedHealth[]>("/api/feeds"),
};

export function liveSocket(onEvent: (e: { type: string } & Record<string, unknown>) => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (m) => onEvent(JSON.parse(m.data));
  return () => ws.close();
}
