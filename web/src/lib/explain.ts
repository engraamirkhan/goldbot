// Helpers for the Health and Research screens (docs/ux/health-research.md): bands, formatting and chart scales.
import type { components } from "./schema";

type Schemas = components["schemas"];
export type HealthView = Schemas["HealthView"];
export type AgentHealthRow = Schemas["AgentHealthRow"];
export type PsiHeatmap = Schemas["PsiHeatmap"];
export type ReliabilityCurve = Schemas["ReliabilityCurve"];
export type CusumTrace = Schemas["CusumTrace"];
export type HealthCheckRow = Schemas["HealthCheckRow"];
export type ResearchView = Schemas["ResearchView"];
export type TrialRow = Schemas["TrialRow"];
export type ResearchPlanView = Schemas["ResearchPlanView"];
export type HypothesisTable = Schemas["HypothesisTable"];

export type Band = "ok" | "warn" | "bad";

/** PSI band: above `sizeDown` the agent is sized down (bad), above `warn` it warns, else stable. */
export function psiBand(v: number, warn: number, sizeDown: number): Band {
  if (v > sizeDown) return "bad";
  if (v > warn) return "warn";
  return "ok";
}

export const PSI_WORDS: Record<Band, string> = { ok: "stable", warn: "drifting", bad: "sized down" };

/** The 30-day drawdown limit (dd_mult × backtest) and how close the agent is to it. */
export function ddState(dd: number, backtest: number | null | undefined, mult: number): { limit: number | null; band: Band } {
  if (backtest == null || backtest <= 0) return { limit: null, band: "ok" };
  const limit = mult * backtest;
  return { limit, band: dd > limit ? "bad" : dd > 0.75 * limit ? "warn" : "ok" };
}

export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export const num = (x: number | null | undefined, digits = 2) => (x == null ? "—" : x.toFixed(digits));
export const signed = (x: number | null | undefined, digits = 3) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`);

const LOCAL = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const LOCAL_DAY = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", year: "numeric" });
/** Local time for the text, UTC for the hover title (every time on the dashboard). */
export const localTime = (iso: string) => LOCAL.format(new Date(iso));
export const localDay = (iso: string) => LOCAL_DAY.format(new Date(iso));
export const utcTitle = (iso: string) => `${new Date(iso).toISOString().replace("T", " ").slice(0, 16)} UTC`;

/** Linear scale from [d0, d1] to [r0, r1]. */
export function scale(d0: number, d1: number, r0: number, r1: number): (v: number) => number {
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** "M x,y L x,y …" through the points. */
export const linePath = (pts: [number, number][]) =>
  pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

/** The failing gate names of a trial, for the compact gates cell. */
export const failedGates = (t: TrialRow) => t.gates.filter((g) => !g.passed).map((g) => g.name);
