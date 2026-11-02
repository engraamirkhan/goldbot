// Words and tones for the read-only Limits card (docs/ux/limits-and-tradingview.md A). Red only for what stops trading
// (a spent daily or weekly cap). Every state is said in words as well as colour.
import type { components } from "./schema";

type Schemas = components["schemas"];
export type LimitsView = Schemas["LimitsView"];
export type LimitAccount = Schemas["LimitAccount"];
export type LimitChange = Schemas["LimitChange"];
export type Tone = "ok" | "warn" | "bad" | "idle" | "info";

/** 0.05 -> "5.0%", 0.0375 -> "3.75%": one decimal at least, a configured step is never rounded away. */
export function pctText(x: number): string {
  const s = (x * 100).toFixed(2).replace(/0$/, "");
  return `${s}%`;
}

export const NEAR_CAP = 0.75;     // "Near cap (>=75% used)": amber chip and bar

/** A cap's state from what is used of it: bad = spent (entries stop), warn = 75% or more used. */
export function capTone(used: number | null | undefined, limit: number | null | undefined): Tone {
  if (used == null || limit == null || limit <= 0) return "idle";
  if (used >= limit) return "bad";
  return used / limit >= NEAR_CAP ? "warn" : "ok";
}

export const FIELD_WORDS: Record<LimitChange["field"], string> = {
  daily_cap: "Daily loss limit", weekly_cap: "Weekly loss limit", min_lot_risk_cap: "Minimum-lot exception",
  supervisor_daily_cap: "All accounts, daily", supervisor_weekly_cap: "All accounts, weekly",
};

export const SOURCE_WORDS: Record<LimitChange["source"], string> = {
  static: "your approved demo ceilings", adaptive: "the adaptive rule", revert: "the safety revert",
  owner: "your re-arm", default: "the defaults", fault: "a controller fault (defaults, fails safe)",
};

export const ageSeconds = (iso: string | null | undefined, now: number) =>
  iso ? Math.max(0, (now - new Date(iso).getTime()) / 1000) : null;

/** "5 min ago", "8 h ago", "2 days ago". */
export function ago(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

const ZONED_TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short" });
const ZONED_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short" });
const UTC_TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
/** "14:05 BST": the viewer's local time with its zone name (UTC goes beside it or in the hover title). */
export const zoned = (iso: string) => ZONED_TIME.format(new Date(iso)).replace(",", "");
/** "17 Oct 09:12 BST". */
export const zonedDay = (iso: string) => ZONED_DAY.format(new Date(iso)).replace(",", "");
/** "13:05 UTC". */
export const utcClock = (iso: string) => `${UTC_TIME.format(new Date(iso))} UTC`;
