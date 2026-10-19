// "Why this limit today" for dynamic per-strategy limits (docs/design/dynamic-limits-2026-10-19.md, ADR 0039), read
// from /api/limits `risk_today`. Pure formatting: the Home Today line and the Trades > Summary "Risk per trade
// today" card. Money per trade is the applied rate times the day-start equity (what the daily cap is a percentage
// of), shown with "≈" because the gate sizes on equity now, then applies the allocator's multiplier and lot rounding.
// Red only for what stops new entries (a spent cap); every state is in words as well as colour.
import type { components } from "./schema";
import { pctText, type LimitAccount, type Tone } from "./limits";

export type RiskTodayView = components["schemas"]["RiskTodayView"];

const FACTORS = [
  ["k_vol", "high volatility"],
  ["k_dd", "drawdown"],
  ["k_day", "half the daily allowance lost"],
] as const;

/** 0.5 -> "halved", 0.7 -> "cut to 70%"; 1 or unknown -> null (no cut). */
export function cutWords(k: number | null | undefined): string | null {
  if (k == null || k >= 1) return null;
  if (Math.abs(k - 0.5) < 1e-9) return "halved";
  if (k <= 0) return "stopped";
  return `cut to ${Math.round(k * 100)}%`;
}

/** Each same-day cut in plain words: ["high volatility: halved", "drawdown: cut to 75%"]; [] when none applies. */
export function factorWords(r: RiskTodayView): string[] {
  return FACTORS.flatMap(([k, label]) => {
    const w = cutWords(r[k]);
    return w ? [`${label}: ${w}`] : [];
  });
}

/** The short reason after a figure: "high volatility: halved" or "no cut today". */
export function whyShort(r: RiskTodayView): string {
  const f = factorWords(r);
  return f.length ? f.join(", ") : "no cut today";
}

/** What a trade risks at its stop: day-start equity, else the balance. */
export const equityBase = (a: LimitAccount) => a.day_start_equity ?? a.balance ?? null;

export type StrategyRiskRow = { version: string; rate: number | null; money: number | null; reason: string | null; base: boolean };

/** One row per strategy: the applied rate (or base x scale when the engine gives none) and its money. */
export function strategyRows(a: LimitAccount): StrategyRiskRow[] {
  const r = a.risk_today;
  if (!r) return [];
  const eq = equityBase(a);
  const fallback = r.base != null ? r.base * (r.scale ?? 1) : null;
  return r.strategies.map((s) => {
    const rate = s.applied ?? fallback;
    return { version: s.version, rate, money: rate != null && eq != null ? rate * eq : null, reason: s.reason ?? null,
             base: s.decided == null };
  });
}

/** The day's money range per trade [low, high] across strategies (base x scale when none listed); null if unknown. */
export function moneyRange(a: LimitAccount): [number, number] | null {
  const r = a.risk_today;
  const eq = equityBase(a);
  if (!r || eq == null) return null;
  const rows = strategyRows(a).map((x) => x.money).filter((x): x is number => x != null);
  if (rows.length === 0) {
    if (r.base == null) return null;
    const m = r.base * (r.scale ?? 1) * eq;
    return [m, m];
  }
  return [Math.min(...rows), Math.max(...rows)];
}

/** "£2.50–£5.00" or "£5.00" when low and high agree to the penny. */
export function rangeText(range: [number, number], fmt: (x: number) => string): string {
  const [lo, hi] = range.map(fmt);
  return lo === hi ? lo : `${lo}–${hi}`;
}

/** Home's one line: "Risk per trade today: ≈£2.50–£5.00 (why: high volatility: halved)"; null when dynamic limits are off. */
export function riskTodayLine(a: LimitAccount, fmt: (x: number) => string): string | null {
  if (!a.risk_today) return null;
  const range = moneyRange(a);
  const fig = range ? `≈${rangeText(range, fmt)}` : "not known";
  return `Risk per trade today: ${fig} (why: ${whyShort(a.risk_today)})`;
}

/** Money left today before the binding daily limit (never below zero); null when the engine does not say. */
export function leftToday(a: LimitAccount): number | null {
  if (a.daily_limit == null || a.daily_used == null) return null;
  return Math.max(0, a.daily_limit - a.daily_used);
}

/** /api/accounts `loss_budget`: the cap with the least left of every cap (account, all accounts, absolute). */
export type Budget = { limit: number; remaining: number; binding_label: string; period: string; unknown?: string | null };

/** The state of new entries today, in words: bad only when a cap is spent or nothing is left. With the account's
 *  loss budget the money left is the binding cap's (it may be the all-accounts cap), else the gate's daily limit. */
export function leftState(a: LimitAccount, fmt: (x: number) => string, budget?: Budget | null): { tone: Tone; text: string } {
  if (a.cap_hit === "weekly") return { tone: "bad", text: "Weekly loss limit spent: no new entries this week (exits still work)" };
  if (budget && !budget.unknown) {
    if (budget.remaining <= 0) return { tone: "bad", text: `No room left: ${budget.binding_label} is spent, no new entries (exits still work)` };
    const warn = budget.limit > 0 && budget.remaining / budget.limit < 0.25;
    return { tone: warn ? "warn" : "ok", text: `${fmt(budget.remaining)} left ${budget.period} before the binding limit: ${budget.binding_label}` };
  }
  const left = leftToday(a);
  if (a.cap_hit === "daily" || left === 0) return { tone: "bad", text: "Daily loss limit spent: no new entries today (exits still work)" };
  if (left == null) return { tone: "idle", text: "Left today: not reported by this engine" };
  const warn = a.daily_limit != null && a.daily_limit > 0 && left / a.daily_limit <= 0.25;
  return { tone: warn ? "warn" : "ok", text: `${fmt(left)} left today before the daily loss limit` };
}

export const rateText = (x: number | null) => (x == null ? "—" : pctText(x));
