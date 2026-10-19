// Logic of the Home status card stack (docs/ux/home-redesign-2026-10-19.md, "Decision 2026-10-19"). Pure functions;
// the cards in screens/HomeCards.tsx only render what these return.
import type { AccountSummary, LossMeter, Proposal, TradesOverview } from "./api";
import { signedR } from "./tradesOverview";

/** "Money or status older than 60 s marked stale, never allowed." */
export const HOME_STALE_S = 60;
export const HOME_POLL_MS = 5_000;
export const WATCH_POLL_MS = 15_000;

/** REAL for a live account, DEMO otherwise: every £ figure carries it; real and demo are never summed. */
export function modeBadge(mode: string | null | undefined): "REAL" | "DEMO" {
  return mode === "live" ? "REAL" : "DEMO";
}

/** Waiting proposals, real money first, then by expiry: a waiting real-money proposal is always card 1. */
export function orderWaiting(open: Proposal[], accounts: AccountSummary[] | undefined): Proposal[] {
  const live = new Set((accounts ?? []).filter((a) => a.mode === "live").map((a) => a.account_id));
  return [...open].sort((a, b) => Number(live.has(b.account_id)) - Number(live.has(a.account_id))
    || a.expires_at.localeCompare(b.expires_at));
}

/** The loss meter's words: which limit binds first and what is left of it. Red only when it is used up (a cap hit). */
export function meterWords(m: LossMeter, money: (x: number) => string): { text: string; tone: "ok" | "warn" | "bad"; limit: number } {
  const b = m.limits.find((c) => c.key === m.binding) ?? m.limits[0];
  const name = b.key === "probe_stop" ? "probe stop" : "daily loss limit";
  const tone = b.remaining <= 0 ? "bad" : b.remaining < b.limit * 0.25 ? "warn" : "ok";
  const text = b.remaining <= 0
    ? `${name} reached: ${b.key === "probe_stop" ? "no new cost probes today" : "no new entries today"}`
    : `${money(b.remaining)} left before the ${name} (${money(b.limit)})`;
  return { text, tone, limit: b.limit };
}

/** "Box break close 1h" from a family and a timeframe. */
export function strategyName(family: string, timeframe: string | null | undefined): string {
  const words = family.replace(/_/g, " ");
  const name = words.charAt(0).toUpperCase() + words.slice(1);
  return timeframe ? `${name} ${timeframe}` : name;
}

/** A change in R per trade smaller than this (shown to 2 decimals) is not a digest line. */
export const MIN_CHANGE_R = 0.01;

export interface DigestLine { family: string; text: string; change: number }

/** Up to 3 "since you last looked" lines: shadow trades that cleared their threshold, the 7-day R per trade against
 *  all time, by family, ranked by the largest change. Shadow numbers are in R only, with n (shadow, not money). */
export function digest(v: TradesOverview, max = 3): DigestLine[] {
  const pick = (period: string) => v.periods.find((p) => p.period === period)?.types.find((t) => t.type === "shadow_taken");
  const week = pick("7d"), all = pick("all");
  if (!week || !all) return [];
  const out: DigestLine[] = [];
  for (const [family, s] of Object.entries(week.by_family)) {
    const base = all.by_family[family];
    if (s.avg_r == null || s.n_r === 0 || !base || base.avg_r == null) continue;
    const change = s.avg_r - base.avg_r;
    if (Math.abs(change) < MIN_CHANGE_R) continue;              // nothing learned: the week is the whole history
    out.push({ family, change, text: `${strategyName(family, null)}: ${signedR(s.avg_r)} per trade over 7 days `
      + `(n ${s.n_r}), all time ${signedR(base.avg_r)} (n ${base.n_r})` });
  }
  return out.sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, max);
}

/** The two shadow tiles: every candidate vs those taken, 7 days, R per trade with n. */
export function shadowTiles(v: TradesOverview): { label: string; avgR: number | null; n: number }[] {
  const week = v.periods.find((p) => p.period === "7d");
  return (["shadow_all", "shadow_taken"] as const).map((type) => {
    const s = week?.types.find((t) => t.type === type)?.stats;
    return { label: type === "shadow_all" ? "All candidates, 7 days" : "Taken, 7 days", avgR: s?.avg_r ?? null, n: s?.n_r ?? 0 };
  });
}
