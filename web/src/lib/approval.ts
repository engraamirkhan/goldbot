// Pure helpers behind the approval card and the safety strip (kept out of the component files so they are unit
// tested directly). Spec: docs/ux/approval-card.md.
import type { AccountSummary, DecidedProposal, Proposal, ReasonCode, Status } from "./api";
import { secondsLeft } from "./useNow";

export const WINDOW_S = 90;      // the engine's approval window (goldbot.telegram.approvals.Proposal.window_s)
export const EXPIRING_S = 15;    // last seconds: the card turns urgent

/** The backend's reason codes (goldbot.telegram.approvals.REASON_CODES), in the order the buttons show them. */
export const REASONS: { code: ReasonCode; label: string; hint: string }[] = [
  { code: "news", label: "News", hint: "event or headline risk" },
  { code: "cost", label: "Cost", hint: "spread or costs too high" },
  { code: "discretion", label: "Discretion", hint: "my judgement" },
  { code: "duplicate", label: "Duplicate", hint: "already in this trade" },
  { code: "other", label: "Other", hint: "anything else" },
];

export type AnyProposal = Proposal | DecidedProposal;
export type CardState = "pending" | "expiring" | "expired" | "submitted" | "approved" | "rejected" | "refused";

export const isDecided = (p: AnyProposal): p is DecidedProposal => "status" in p;

export function cardState(p: AnyProposal, now: number): CardState {
  if (isDecided(p)) return p.status;
  const left = secondsLeft(p.expires_at, now);
  if (left <= 0) return "expired";
  return left <= EXPIRING_S ? "expiring" : "pending";
}

/** 90 -> "1:30", 9 -> "0:09". */
export function countdown(s: number): string {
  const v = Math.max(0, Math.floor(s));
  return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")}`;
}

export const reasonLabel = (code: string | null | undefined) => REASONS.find((r) => r.code === code)?.label ?? code ?? "";
export const plain = (code: string) => code.replace(/_/g, " ");

export function money(x: number): string {
  return `$${x.toLocaleString("en-US", { maximumFractionDigits: x < 100 ? 2 : 0 })}`;
}

export function signed(x: number, digits = 2): string {
  return `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;
}

/** Local wall time for display, UTC for the hover title. */
export function localTime(iso: string): { text: string; utc: string } {
  const d = new Date(iso);
  return { text: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
           utc: d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") };
}

/** "dashboard:a@x.io" -> "dashboard (a@x.io)", "telegram:111" -> "Telegram". */
export function via(by: string | null | undefined): string {
  if (!by) return "";
  const [ch, who] = by.split(/:(.*)/s);
  return ch === "telegram" ? "Telegram" : ch === "dashboard" && who ? `dashboard (${who})` : by;
}

/** Why the RiskGate would refuse a new entry on this account right now, in plain words (empty: entries allowed). */
export function blockers(status: Status | undefined, accounts: AccountSummary[] | undefined, accountId?: string): string[] {
  const out: string[] = [];
  if (status?.halted) out.push("owner halt");
  if (status?.supervisor_halt) out.push("supervisor halt");
  if (status?.drift_halt) out.push("drift halt");
  const b = status?.blackout;
  if (b && (!accountId || b.accounts.includes(accountId))) out.push(b.kind === "news_shock" ? "news shock" : "news blackout");
  for (const a of accounts ?? []) {
    if (accountId && a.account_id !== accountId) continue;
    const who = accountId ? "" : ` (${a.account_id})`;
    if (a.stage === "halted") out.push(`drawdown halt${who}`);
    // paper engines size as a raw account whatever the classifier says (engine/runner.py), so only demo/live block
    if (a.account_class === "unknown" && a.mode !== "paper") out.push(`account class unknown${who}`);
  }
  return out;
}
