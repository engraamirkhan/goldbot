// The activity feed's words and tones (screens/Activity.tsx; GET /api/activity). Presentation only.
import type { ActivityEvent, ActivityView } from "./api";
import type { Tone } from "../ui";
import { lots, money, signedR } from "../ui/format";

export const ACTIVITY_POLL_MS = 15_000;
export const ACTIVITY_LIMIT = 50;

type Kind = ActivityEvent["kind"];
const KIND_WORDS: Record<Kind, string> = {
  shadow_opened: "Shadow opened", shadow_closed: "Shadow closed", probe_opened: "Probe opened",
  probe_closed: "Probe closed", position_opened: "opened", position_closed: "closed", refused: "Refused",
};
export const SOURCE_WORDS: Record<ActivityView["unreadable"][number], string> = {
  shadow: "shadow trades", positions: "account trades", probes: "cost probes", journal: "refusals",
};

/** "Demo opened", "Live closed" for account positions; the kind's own words otherwise. */
export function label(e: ActivityEvent): string {
  if (e.kind !== "position_opened" && e.kind !== "position_closed") return KIND_WORDS[e.kind];
  const mode = e.mode === "live" ? "Live" : e.mode === "demo" ? "Demo" : "Trade";
  return `${mode} ${KIND_WORDS[e.kind]}`;
}

export function tone(e: ActivityEvent): Tone {
  if (e.kind === "refused") return "idle";
  if (!e.kind.endsWith("_closed")) return "info";
  const result = e.r ?? e.pnl ?? e.ret;
  return result == null ? "idle" : result >= 0 ? "ok" : "warn";
}

const price = (x: number) => x.toFixed(2);

/** One line of facts, in reading order: who, on what, which way, prices, why, result, where. */
export function eventLine(e: ActivityEvent): string {
  const parts: string[] = [];
  if (e.agent_id) parts.push(e.agent_id);
  if (e.timeframe) parts.push(e.timeframe);
  if (e.side) parts.push(e.lots != null ? `${e.side} ${lots(e.lots)}` : e.side);
  if (e.entry != null) parts.push(e.exit != null ? `${price(e.entry)} → ${price(e.exit)}` : `at ${price(e.entry)}`);
  if (e.stop != null) parts.push(`SL ${price(e.stop)}`);
  if (e.target != null) parts.push(`TP ${price(e.target)}`);
  if (e.reason) parts.push(e.reason);
  if (e.r != null) parts.push(signedR(e.r));
  if (e.pnl != null) parts.push(money(e.pnl, e.currency, true));
  if (e.account) parts.push(e.account);
  return parts.join(" · ");
}
