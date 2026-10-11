// Presentation helpers for the Entry status panel (docs/ux/entry-status.md). Every reason, its words and its next
// step come from the API (goldbot/api/entry_status.py): nothing here decides whether an entry is allowed.
import type { AccountSummary, EntryBlock, EntryStatus } from "./api";
import type { Tone } from "./account";

export const BADGE: Record<EntryStatus["state"], string> = { open: "Open", partial: "Partly open", waiting: "Paused", blocked: "Blocked" };
export const TONE: Record<EntryStatus["state"], Tone> = { open: "ok", partial: "warn", waiting: "warn", blocked: "bad" };

const MIN = 60_000;

function span(ms: number): string {
  const m = Math.max(1, Math.round(Math.abs(ms) / MIN));
  const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mm = m % 60;
  if (d > 0) return h > 0 ? `${d} d ${h} h` : `${d} d`;
  if (h > 0) return mm > 0 ? `${h} h ${mm} min` : `${h} h`;
  return `${m} min`;
}

/** "opens Sun 23:02 your time (in 1 d 12 h)": the owner's zone, the weekday when not today, how long until (or ago);
 *  `utc` for the hover title. */
export function whenText(iso: string, label: NonNullable<EntryBlock["at_label"]>, now: number): { text: string; utc: string } {
  const d = new Date(iso);
  const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  const today = new Date(now).toDateString() === d.toDateString();
  const day = today ? "" : `${d.toLocaleDateString("en-GB", { weekday: "short" })} `;
  const delta = d.getTime() - now;
  const rel = delta >= 0 ? `in ${span(delta)}` : `${span(delta)} ago`;
  return { text: `${label} ${day}${t} your time (${rel})`, utc: utcText(iso) };
}

/** "2026-10-14 13:59 UTC". */
export function utcText(iso: string): string {
  return new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/** Why a new entry would be refused now, as the API words it: on one account (and for one strategy's proposal), or
 *  across every account (naming the account when more than one reports). A strategy's own block counts only for that
 *  strategy, or when every strategy is held back. Null when the server does not send the status. */
export function entryBlockers(accounts: AccountSummary[] | undefined, accountId?: string, agentId?: string): string[] | null {
  const rel = (accounts ?? []).filter((a) => !accountId || a.account_id === accountId);
  if (rel.length === 0 || rel.some((a) => !a.entry_status)) return null;
  const out: string[] = [];
  for (const a of rel) {
    const s = a.entry_status!;
    const who = !accountId && rel.length > 1 ? ` (${a.account_id})` : "";
    for (const b of s.blocks) {
      const counts = b.scope !== "agent" || (agentId ? b.agent_id === agentId : s.state === "waiting" || s.state === "blocked");
      if (counts) out.push(`${b.text}${who}`);
    }
  }
  return out;
}
