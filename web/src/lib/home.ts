// Logic of the Home status card stack (docs/ux/home-redesign-2026-10-19.md, "Decision 2026-10-19"). Pure functions;
// the cards in screens/HomeCards.tsx only render what these return.
import type { AccountSummary, FeedHealth, LossMeter, Proposal, Status, TradesOverview } from "./api";
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

// ------------------------------------------------------------------------------------------------ system status line
/** One part of Home's one-line system status. Never "bad": red belongs to a halt, a cap hit or a refused order, which
 *  the Needs-you card and the header pill carry; here amber means "look", grey means "expected, nothing to do". */
export interface StatusPart { key: "engine" | "prices" | "market"; label: string; word: string; tone: "ok" | "warn" | "idle" }

const ENGINE_DOWN = new Set(["engine_not_reporting", "status_unknown", "engine_state_unreadable", "engine_startup_refused"]);
const PRICE_CODES = new Set(["stale_data", "tick_error", "data_quality_error", "data_recovering", "tick_from_future"]);
const SHUT_CODES = new Set(["market_closed", "weekend", "holiday_thin", "reopen_window", "rollover"]);
/** The tick age the API reports when an engine does not publish one (goldbot/api/app.py feeds: 1e9). */
const NO_TICK_AGE = 1e8;

const ago = (s: number) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);
const dayHm = (iso: string, now: number) => {
  const d = new Date(iso);
  const day = new Date(now).toDateString() === d.toDateString() ? "" : `${d.toLocaleDateString("en-GB", { weekday: "short" })} `;
  return `${day}${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
};

export interface StatusInputs {
  accounts?: AccountSummary[]; accountsError?: boolean;
  feeds?: FeedHealth[]; feedsError?: boolean;
  status?: Status; now: number;
}

/** Engine, prices and market in words, from the entry status the API words (goldbot/api/entry_status.py), the feed
 *  health and the active blackout. Nothing here decides whether an entry is allowed. */
export function systemLine({ accounts, accountsError, feeds, feedsError, status, now }: StatusInputs): StatusPart[] {
  const blocks = (accounts ?? []).flatMap((a) => a.entry_status?.blocks ?? []);
  const has = (set: Set<string>) => blocks.filter((b) => set.has(b.code));

  let engine: StatusPart;
  if (!accounts) engine = { key: "engine", label: "Engine", word: accountsError ? "can't reach the server" : "checking…", tone: accountsError ? "warn" : "idle" };
  else if (accounts.length === 0 || has(ENGINE_DOWN).length) engine = { key: "engine", label: "Engine", word: "not reporting", tone: "warn" };
  else {
    const ages = accounts.map((a) => (a.entry_status?.as_of ? (now - Date.parse(a.entry_status.as_of)) / 1000 : null));
    const known = ages.filter((x): x is number => x != null);
    const oldest = known.length ? Math.max(...known) : null;
    engine = oldest != null && oldest > HOME_STALE_S
      ? { key: "engine", label: "Engine", word: `last report ${ago(oldest)} ago`, tone: "warn" }
      : { key: "engine", label: "Engine", word: `running · ${accounts.length} account${accounts.length === 1 ? "" : "s"}`, tone: "ok" };
  }

  const shut = has(SHUT_CODES);
  let market: StatusPart;
  if (!accounts || accounts.length === 0) market = { key: "market", label: "Market", word: "not known", tone: "idle" };
  else if (shut.length) {
    const opens = shut.find((b) => b.at && b.at_label === "opens")?.at;
    market = { key: "market", label: "Market", word: opens ? `closed · opens ${dayHm(opens, now)}` : shut[0].text, tone: "idle" };
  } else if (status?.blackout || blocks.some((b) => b.code === "news_blackout")) {
    market = { key: "market", label: "Market", word: `news blackout${status?.blackout ? `: ${status.blackout.title}` : ""}`, tone: "warn" };
  } else market = { key: "market", label: "Market", word: "open", tone: "ok" };

  let prices: StatusPart;
  const priceBlock = has(PRICE_CODES)[0];
  const ticks = (feeds ?? []).map((f) => f.last_tick_age_s).filter((x) => x < NO_TICK_AGE);
  if (priceBlock) prices = { key: "prices", label: "Prices", word: priceBlock.text, tone: "warn" };
  else if (shut.length) prices = { key: "prices", label: "Prices", word: "paused while closed", tone: "idle" };
  else if (ticks.length === 0) prices = { key: "prices", label: "Prices", word: feedsError ? "feed not readable" : "not reported", tone: "idle" };
  else {
    const worst = Math.max(...ticks);
    prices = worst > HOME_STALE_S
      ? { key: "prices", label: "Prices", word: `last tick ${ago(worst)} ago`, tone: "warn" }
      : { key: "prices", label: "Prices", word: `fresh · ${ago(worst)} old`, tone: "ok" };
  }
  return [engine, prices, market];
}

/** "3 open trades" for Home's link to Trades. */
export const openTradesWords = (n: number) => (n === 0 ? "No open trades" : `${n} open trade${n === 1 ? "" : "s"}`);

/** Accounts that cannot take every new trade now (anything but "open"): what Home's Needs-you card lists. */
export const notOpen = (accounts: AccountSummary[]) => accounts.filter((a) => a.entry_status?.state !== "open");
