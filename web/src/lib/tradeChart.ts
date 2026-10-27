// Pure helpers of the trade chart (screens/trades/TradeChart.tsx): levels in words, the bar a fill fell in, and
// label spacing so two close levels never print on top of each other.
import type { Bar, TradeRow } from "./trades";

export function chartLevels(t: TradeRow): { key: string; value: number; word: string; dash?: string; strong?: boolean }[] {
  const out: { key: string; value: number; word: string; dash?: string; strong?: boolean }[] = [];
  if (t.target != null) out.push({ key: "target", value: t.target, word: "target", dash: "6 4" });
  if (t.entry != null) out.push({ key: "entry", value: t.entry, word: "entry", strong: true });
  if (t.stop != null) out.push({ key: "stop", value: t.stop, word: t.stop_now != null && t.stop_now !== t.stop ? "first stop" : "stop", dash: "6 4" });
  if (t.stop_now != null && t.stop_now !== t.stop) out.push({ key: "stop_now", value: t.stop_now, word: t.status === "open" ? "stop now" : "last stop", dash: "2 3" });
  if (t.exit != null) out.push({ key: "exit", value: t.exit, word: "exit" });
  return out;
}

/** Index of the last bar that starts at or before `iso` (the bar a fill happened in); -1 when none. */
export function barIndexAt(bars: Bar[], iso: string | null | undefined): number {
  if (!iso) return -1;
  const t = new Date(iso).getTime();
  let i = -1;
  for (let k = 0; k < bars.length; k++) if (new Date(bars[k].t).getTime() <= t) i = k;
  return i;
}

/** Spread labels at least `gap` apart vertically, keeping their order (no two labels on top of each other). */
export function spreadLabels(ys: number[], gap: number, min: number, max: number): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  const out = new Array<number>(ys.length);
  let last = -Infinity;
  for (const o of order) { const y = Math.max(o.y, last + gap, min); out[o.i] = y; last = y; }
  const over = last - max;
  if (over > 0) for (let i = 0; i < out.length; i++) out[i] -= over;
  return out;
}
