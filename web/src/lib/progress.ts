// Words and order for the Progress tab (docs/ux/progress-screens.md). "Verdicts, staleness, proposed flags and the
// stop-rule status all come from the API": nothing here judges a gate or a result, it only names what the API sent
// and keeps the rows in a fixed order ("never sorted by result, so the screen does not invite picking the best row").
import type { GateView, ResultCell } from "./api";
import type { Tone } from "./account";
import { signed } from "./explain";

export type BadgeTone = Tone | "info";

/** The four roadmap gates in order (goldbot/ops/accounts.py GATES), in the owner's words. */
export const GATE_TITLE: Record<string, string> = {
  foundation_to_backtest: "Leakage audit",
  backtest_to_paper: "Backtest → paper (demo)",
  paper_to_tiny_live: "Paper → tiny live",
  tiny_live_to_full_size: "Tiny live → full size",
};

/** Passing gate i puts the system in phase i + 1 (accounts.py). */
export const PHASE_WORDS = ["foundation", "backtest", "paper (demo)", "tiny live", "full size"];

/** Gate items (goldbot/ops/gates_phase.py) in plain words; the raw name goes in the hover title. */
export const ITEM_LABEL: Record<string, string> = {
  shuffle_auc: "Shuffle test at chance",
  asof_violations: "As-of audit clean",
  feed_mismatch_share: "Broker and Dukascopy bars agree",
  previous_gate: "Previous gate recorded first",
  research_gates: "Research gates passed",
  dsr: "Deflated Sharpe",
  positive_years: "Positive years",
  backtest_trades: "Backtest trades",
  live_tick_spread_model: "Spread measured from own ticks",
  paper_trades: "Demo trades",
  paper_days: "Time on paper",
  paper_expectancy: "Results vs backtest",
  fills: "Fills vs model",
  chaos_drill: "Chaos drill",
  no_incident: "No single big loss (stop rule)",
  paper_gross_share: "Trades with estimated costs",
  live_gross_share: "Trades with estimated costs",
  shadow_record: "Shadow trades",
  live_trades: "Live trades",
  live_days: "Time live",
  live_expectancy: "Results vs paper",
  live_drawdown: "Live drawdown vs backtest",
  brokers_agree: "Two brokers agree",
};

export const itemLabel = (name: string) => ITEM_LABEL[name] ?? name.replace(/_/g, " ");

export type GateKind = "recorded" | "met" | "met_proposed" | "not_met" | "waits";
export type GateState = { kind: GateKind; badge: string; tone: BadgeTone; line: string };

/** Each gate's badge, in order. A gate met on a threshold the owner has not signed off (`met_on_proposed`, from the
 *  API) never reads as a plain MET; a gate whose previous gate is not recorded waits for it. */
export function gateStates(gates: GateView[]): GateState[] {
  return gates.map((g, i) => {
    const req = g.items.filter((it) => it.required);
    const k = req.filter((it) => it.met).length;
    if (g.recorded) return { kind: "recorded", badge: "Recorded", tone: "ok", line: "" };
    if (g.met && g.met_on_proposed) return { kind: "met_proposed", badge: "Met on a proposed threshold", tone: "info", line: "" };
    if (g.met) return { kind: "met", badge: "Met", tone: "info", line: "" };
    if (i > 0 && !gates[i - 1].recorded) return { kind: "waits", badge: "Waits", tone: "idle", line: `Waits for gate ${i}` };
    return { kind: "not_met", badge: "Not met", tone: "warn", line: `${k} of ${req.length} met` };
  });
}

/** The first gate not recorded (it starts open), or null when all are. */
export function nextGate(gates: GateView[]): number | null {
  const i = gates.findIndex((g) => !g.recorded);
  return i < 0 ? null : i;
}

/** `ci95_basis` from the API in words: the gates' interval treats trades as independent draws, unlike the
 *  day-clustered Results rows, so the screen says so. */
export const intervalLabel = (basis: string) => (basis === "per-trade iid, return" ? "per-trade interval, not day-clustered" : basis);

// ------------------------------------------------------------------------------------------------------- results
export type Verdict = ResultCell["verdict"];

export const VERDICT_BADGE: Record<Verdict, string> = {
  positive: "Ahead", negative: "Behind", "indistinguishable from zero": "Can't tell from luck", noise: "Too few trades",
  "by construction": "By construction",
};
// "A breach is amber, not red": nothing on these screens stops trading, so BEHIND is amber and nothing is red
export const VERDICT_TONE: Record<Verdict, BadgeTone> = {
  positive: "ok", negative: "warn", "indistinguishable from zero": "idle", noise: "idle", "by construction": "idle",
};

/** "Below 100 trades the overall headline shows no AHEAD or BEHIND badge, only CAN'T TELL FROM LUCK or TOO FEW TRADES." */
export const MIN_HEADLINE_TRADES = 100;

export function headlineVerdict(c: ResultCell): Verdict {
  if (c.n < MIN_HEADLINE_TRADES && (c.verdict === "positive" || c.verdict === "negative")) return "indistinguishable from zero";
  return c.verdict;
}

/** The breakdowns offered, in the select's order; "by agent" last (agent ids are long). */
export const DIMENSIONS: [string, string][] = [
  ["timeframe", "Timeframe"], ["family", "Family"], ["session", "Session"], ["side", "Side"], ["regime", "Volatility"],
  ["decision", "Taken vs not"], ["exit", "Exit"], ["agent", "Agent"],
];

const ORDER: Record<string, string[]> = {
  timeframe: ["15m", "1h", "4h", "1d"],
  session: ["asia", "london", "newyork"],
  side: ["long", "short"],
  regime: ["low", "mid", "high", "unknown"],
  decision: ["taken", "not_taken"],
  exit: ["target", "stop", "time", "policy"],
};

const LABELS: Record<string, Record<string, string>> = {
  session: { asia: "Asia", london: "London", newyork: "New York" },
  side: { long: "Long", short: "Short" },
  regime: { low: "Calm", mid: "Normal", high: "Busy", unknown: "Unknown" },
  decision: { taken: "Taken", not_taken: "Not taken" },
  exit: { target: "Target", stop: "Stop", time: "Time exit", policy: "Exit rule" },
};

const sentence = (k: string) => { const s = k.replace(/_/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); };

export function rowLabel(dim: string, key: string): string {
  if (dim === "timeframe" || dim === "agent") return key;
  return LABELS[dim]?.[key] ?? sentence(key);
}

/** A breakdown's rows in the dimension's natural order (families and unknown keys alphabetically after it). */
export function orderedRows<T>(dim: string, rows: Record<string, T>): [string, T][] {
  const order = ORDER[dim] ?? [];
  const rank = (k: string) => { const i = order.indexOf(k); return i < 0 ? order.length : i; };
  return Object.entries(rows).sort(([a], [b]) => rank(a) - rank(b) || rowLabel(dim, a).localeCompare(rowLabel(dim, b)));
}

// ------------------------------------------------------------------------------------------------------- formats
/** "+0.04 R", "−0.10 R". */
export const signedR = (x: number | null | undefined, digits = 2) => (x == null ? "—" : `${signed(x, digits)} R`);
/** A per-trade return as a signed percent of price: "−0.02%". */
export const signedPct = (frac: number | null | undefined, digits = 2) => (frac == null ? "—" : `${signed(frac * 100, digits)}%`);
/** A range "−0.05 to +0.13". */
export const range = ([lo, hi]: [number, number], digits = 2, k = 1) => `${signed(lo * k, digits)} to ${signed(hi * k, digits)}`;

/** "Thu 1 Apr 2027" (a calendar date, in the owner's zone). */
export function dayText(iso: string, year = true): string {
  const d = new Date(iso);
  const part = (o: Intl.DateTimeFormatOptions) => d.toLocaleDateString("en-GB", o);
  return `${part({ weekday: "short" })} ${d.getDate()} ${part({ month: "short" })}${year ? ` ${d.getFullYear()}` : ""}`;
}

/** "Tue 13 Oct" for an opportunity day "2026-10-13" (a date label, not an instant). */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

/** "14:02" today, else "Tue 13 Oct 16:20", in the owner's zone. */
export function whenShort(iso: string, now = Date.now()): string {
  const d = new Date(iso);
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  return new Date(now).toDateString() === d.toDateString() ? hm : `${dayLabel(`${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`)} ${hm}`;
}
