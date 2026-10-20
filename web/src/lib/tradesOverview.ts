// The Home trades summary's words and numbers (screens/TradesOverview.tsx; GET /api/trades/overview). Presentation
// only: every count comes from the API. Green for a positive result, amber for a negative one; red stays for what
// stops trading (docs/ux/design-system.md).
import type { TradeStats, TradesOverview, TradeTypeSummary } from "./api";
import type { Tone } from "../ui";
import { money, signedR } from "../ui/format";

export { signedR };

export const TRADES_POLL_MS = 30_000;

export type Period = TradesOverview["periods"][number]["period"];
export type TradeType = TradeTypeSummary["type"];
export type Breakdown = "family" | "timeframe";

export const PERIODS: { id: Period; label: string }[] = [
  { id: "today", label: "Today" }, { id: "7d", label: "7 days" }, { id: "all", label: "All" },
];
export const BREAKDOWNS: { id: Breakdown; label: string }[] = [
  { id: "family", label: "By strategy" }, { id: "timeframe", label: "By timeframe" },
];
export const TYPE_WORDS: Record<TradeType, string> = {
  shadow_taken: "Shadow (taken)", shadow_all: "Shadow (all signals)", demo: "Demo", live: "Live", probe: "Cost probes",
};

const DASH = "—";

/** "60%"; a dash with nothing closed. */
export function winRate(s: TradeStats): string {
  return s.win_rate == null ? DASH : `${Math.round(s.win_rate * 100)}%`;
}

/** Net P&L per currency, "+£12.40 · −$3.00"; never added across currencies. A dash with no net money. */
export function netPnl(s: TradeStats): string {
  const parts = Object.entries(s.net_pnl).map(([ccy, x]) => money(x, ccy === "unknown" ? null : ccy, true));
  return parts.length ? parts.join(" · ") : DASH;
}

/** The headline figure: net money when the type has it (accounts, probes), else total R (shadow). */
export function headline(s: TradeStats): string {
  return Object.keys(s.net_pnl).length ? netPnl(s) : signedR(s.total_r);
}

/** "2 open · 5 closed · 60% won". */
export function counts(s: TradeStats): string {
  const parts = [`${s.open} open`, `${s.closed} closed`];
  if (s.win_rate != null) parts.push(`${winRate(s)} won`);
  return parts.join(" · ");
}

export function resultTone(s: TradeStats): Tone {
  const cash = Object.values(s.net_pnl);
  const x = cash.length === 1 ? cash[0] : s.total_r;     // several currencies: judged by R
  return x == null || x === 0 ? "idle" : x > 0 ? "ok" : "warn";
}

export interface Row { type: TradeType; key: string; stats: TradeStats }

/** Table rows for one period: every type with any trade, then its strategies (or timeframes), in the API's order. */
export function rows(v: TradesOverview, period: Period, by: Breakdown): Row[] {
  const p = v.periods.find((x) => x.period === period);
  if (!p) return [];
  return p.types.flatMap((t) => Object.entries(by === "family" ? t.by_family : t.by_timeframe)
    .map(([key, stats]) => ({ type: t.type, key, stats })));
}

/** Exit reasons, "3 target · 2 stop · 1 time"; only the non-zero ones. */
export function exits(s: TradeStats): string {
  const e = s.exits;
  const parts = (["target", "stop", "time", "other"] as const).filter((k) => e[k] > 0).map((k) => `${e[k]} ${k}`);
  return parts.length ? parts.join(" · ") : DASH;
}

/** "best +2.00 R · worst −1.00 R" (by R when the type has it, else by money); empty with nothing closed. */
export function bestWorst(s: TradeStats): string {
  const one = (t: TradeStats["best"]) =>
    !t ? DASH : t.r != null ? signedR(t.r) : t.pnl != null ? money(t.pnl, t.currency, true) : DASH;
  return s.best || s.worst ? `best ${one(s.best)} · worst ${one(s.worst)}` : "";
}
