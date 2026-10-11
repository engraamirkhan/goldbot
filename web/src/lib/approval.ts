// Pure helpers behind the approval card and the safety strip (kept out of the component files so they are unit
// tested directly). Spec: docs/ux/approval-card.md.
import type { AccountSummary, DecidedProposal, Proposal, ReasonCode, Status } from "./api";
import { type Tone, cash } from "./account";
import { entryBlockers } from "./entry";
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

/** 0.0082 -> "0.82%", 0.005 -> "0.5%", 0.01 -> "1%" (goldbot.telegram.approvals.pct). */
export function pct(fraction: number): string {
  return `${(100 * fraction).toFixed(2).replace(/\.?0+$/, "")}%`;
}

/** Up to the cent: a risk is never shown below the RiskGate's figure (goldbot.telegram.approvals.ceil_cent). */
export const ceilCent = (x: number) => Math.ceil(Number((x * 100).toFixed(6))) / 100;
/** Down to the cent: a budget left is never shown larger than it is. */
export const floorCent = (x: number) => Math.floor(Number((x * 100).toFixed(6))) / 100;
/** The budget does not reserve open positions' stop risk; the card says so. */
export const OPEN_ASIDE = "(open trades aside)";

/** The card's stop risk and target gain as text: in the account currency, else an older engine's USD figure
 * (`usd`: the card labels it), else null. */
export function cardMoney(p: AnyProposal): { risk: string; reward: string | null; usd: boolean } | null {
  const usd = p.risk_amount == null;
  const amount = usd ? p.risk_usd : p.risk_amount;
  if (amount == null) return null;
  const ccy = usd ? "USD" : p.currency;
  return { risk: cash(ceilCent(amount), ccy), reward: p.reward_amount == null ? null : cash(p.reward_amount, ccy), usd };
}

/** The account's loss budget left if this trade loses (live from the API; pending cards only). */
export function budgetLine(p: AnyProposal): { text: string; tone: Tone } {
  if (p.loss_left == null || !p.loss_left_period) return { text: "Loss budget left today: unknown", tone: "idle" };
  const left = floorCent(p.loss_left);
  if (left <= 0) {
    return { text: `No loss budget left ${p.loss_left_period} if this loses ${OPEN_ASIDE}: new entries stop`, tone: "warn" };
  }
  return { text: `${cash(left, p.currency)} left ${p.loss_left_period} if this loses ${OPEN_ASIDE}`, tone: "ok" };
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

/** Why the RiskGate would refuse a new entry on this account (and for this strategy) right now, in plain words (empty:
 *  entries allowed). The API's entry status words it (goldbot/api/entry_status.py); the checks below are only for a
 *  server that does not send it yet, and go once every deployed API does. */
export function blockers(status: Status | undefined, accounts: AccountSummary[] | undefined, accountId?: string,
                         agentId?: string): string[] {
  const fromApi = entryBlockers(accounts, accountId, agentId);
  if (fromApi) return fromApi;
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
    // the daily caps the risk check enforces (account and combined): spent means no new entry until 00:00 UTC
    if (a.loss_budget && a.loss_budget.remaining <= 0) {
      out.push(`${a.loss_budget.period === "this week" ? "weekly" : "daily"} loss limit reached${who}`);
    }
    // paper engines size as a raw account whatever the classifier says (engine/runner.py), so only demo/live block
    if (a.account_class === "unknown" && a.mode !== "paper") out.push(`account class unknown${who}`);
  }
  return out;
}
