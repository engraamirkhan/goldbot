// The trades experience (docs/design/trades-ux-2026-10-27.md, ADR 0050, 0053, backlog 128-139): Now / List /
// Strategies / Today. Every shape comes from the API contract (schema.d.ts: GET /api/trades, /api/trades/{trade_id},
// /api/trades/{trade_id}/bars, /api/strategies/board, /api/strategies/ranking, /api/today, /api/positions/open). Pure
// helpers shared by the screens.
//
// ADR 0053 retired the blinding (backlog 139): every list, total and row carries its result. The client never computes
// an aggregate of its own over ledger rows, nor ranks anything: the ranking's order and figures are the server's, and
// every per-strategy figure keeps its n, its CI and "early, not proven" until its read.
import type { components } from "./schema";
import { money, NBSP, signedR, withUnit } from "../ui/format";

type S = components["schemas"];
export type TradeRow = S["TradeRow"];
export type TradesPage = S["TradesPage"];
export type TradeTotals = S["TradeTotals"];
export type CostSplit = S["CostSplit"];
export type KindDay = S["KindDay"];
export type CapUse = S["CapUse"];
export type StripDay = S["StripDay"];
export type PeriodNet = S["PeriodNet"];
export type TodayAccountX = S["TodayAccount"];
export type OpenPositionX = S["OpenPosition"];
export type TradeSource = TradesPage["unreadable"][number];

export const TRADES_TABS = [
  { id: "now", label: "Now" }, { id: "list", label: "List" }, { id: "strategies", label: "Strategies" }, { id: "today", label: "Today" },
] as const;
export type TradesTab = (typeof TRADES_TABS)[number]["id"];

// ------------------------------------------------------------------------------------------------ kinds and words
export type TradeKind = TradeRow["kind"];
export const KINDS: readonly TradeKind[] = ["real", "strategy", "exploration", "control", "cost_probe", "shadow"];
export const KIND_WORDS: Record<TradeKind, string> = {
  real: "Real", strategy: "Strategy", exploration: "Exploration", control: "Control", cost_probe: "Cost probe", shadow: "Shadow",
};

export type ExitReason = NonNullable<TradeRow["exit_reason"]>;
export const EXIT_REASONS: readonly ExitReason[] = ["target", "stop", "trail", "time_limit", "friday_cut", "manual", "kill_switch", "broker_stop_out", "other"];
export const EXIT_WORDS: Record<ExitReason, string> = {
  target: "target", stop: "stop", trail: "trailing stop", time_limit: "time limit", friday_cut: "Friday cut",
  manual: "closed by hand", kill_switch: "kill switch", broker_stop_out: "broker stop-out", other: "other",
};
export const exitWords = (r: string | null | undefined) => (r ? EXIT_WORDS[r as ExitReason] ?? r.replace(/_/g, " ") : "—");

export type Evidence = TradeRow["evidence"];
export const EVIDENCE_WORDS: Record<Evidence, string> = {
  counted: "counted toward the read", excluded: "excluded", censored: "censored (closed by something other than its own exit)",
  pending: "open: counts once it closes", not_evidence: "not research evidence",
};

/** The ledger's sources in words: what is missing (never zero) when one can't be read. */
export const SOURCE_WORDS: Record<TradeSource, string> = {
  shadow: "shadow trades", positions: "closed positions", probes: "cost probes", exploration: "exploration trades", orders: "open orders",
};
export const sourcesWords = (xs: readonly TradeSource[]) => xs.map((x) => SOURCE_WORDS[x] ?? x).join(", ");

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
export type Side = "long" | "short";
export type Mode = "demo" | "live" | "paper";

// ------------------------------------------------------------------------------------------------ chart bars (133)
export type Bar = S["ChartBar"];
export type TradeBars = S["TradeBars"];
export type StopMove = S["StopMove"];

// ------------------------------------------------------------------------------------------------ board (132)
export type StrategyBoard = S["StrategyBoardView"];
export type BoardFamily = S["BoardFamily"];
export type BoardVariant = S["BoardVariant"];
export type ReadFigures = S["ReadFigures"];
export type ReadProgress = S["ReadProgress"];
export type EngineStrategy = S["EngineStrategy"];
export type LabTotal = S["LabTotal"];
export type AnnealState = BoardVariant["anneal_state"];
export type BoardState = BoardFamily["state"];
export type ArmStatus = BoardVariant["status"];
export const ANNEAL_WORDS: Record<AnnealState, string> = { E: "exploring", C1: "candidate", P: "proven", R: "retired" };
/** Before the read only these exist (quant review): "near its stop" would reveal a running loss. */
export const STATUS_WORDS: Record<ArmStatus, string> = {
  warming: "warming up: no counted trade yet", running: "running", read: "read", retired: "retired at its stop",
};
export const statusWords = (s: string | null | undefined) => STATUS_WORDS[s as ArmStatus] ?? "running";

// ------------------------------------------------------------------------------------------------ ranking (136-138)
export type RankingView = S["RankingView"];
export type RankRow = S["RankRow"];
export type RankVariant = S["RankVariant"];
export type RankFigures = S["RankFigures"];
export type RankingWindow = RankingView["window"];
export type RankStatus = RankRow["status"];
export const RANK_STATUS_WORDS: Record<RankStatus, string> = {
  early: "early, not proven", candidate: "candidate, not proven yet", proven: "proven", not_proven: "not proven",
  retired: "retired at its stop",
};
export const WINDOWS: RankingWindow[] = ["all", "30d", "7d"];
export const WINDOW_WORDS: Record<RankingWindow, string> = { all: "All", "30d": "30 days", "7d": "7 days" };
export type BoardOrder = "ranked" | "progress";

/** The Strategies tab's URL state: its order (Ranked by default; "By progress to read" is `order=progress`), the
 *  ranking's window and the filters. */
export type BoardQuery = { order: BoardOrder; window: RankingWindow; acct: string; style: string };
export function parseBoardQuery(query: string): BoardQuery {
  const p = new URLSearchParams(query.replace(/^\?/, ""));
  const w = p.get("window");
  return { order: p.get("order") === "progress" ? "progress" : "ranked",
           window: w === "7d" || w === "30d" ? w : "all", acct: p.get("acct") ?? "", style: p.get("style") ?? "" };
}
export function boardQuery(b: BoardQuery): string {
  const p = new URLSearchParams();
  if (b.order !== "ranked") p.set("order", b.order);
  if (b.window !== "all") p.set("window", b.window);
  if (b.acct) p.set("acct", b.acct);
  if (b.style) p.set("style", b.style);
  return p.toString();
}
/** GET /api/strategies/ranking's query: the window, and the account (config name) and style when chosen. */
export function rankingQuery(b: BoardQuery): string {
  const p = new URLSearchParams({ window: b.window });
  if (b.acct) p.set("account", b.acct);
  if (b.style) p.set("style", b.style);
  return `?${p.toString()}`;
}
/** "+0.03 R/trade at least" for a ranked row; an unranked row says why. */
export const boundWords = (r: RankRow) => (r.figures.lower_adjusted != null
  ? `${signedR(r.figures.lower_adjusted)}/trade at least` : r.unranked_reason ?? "not ranked");
/** "vs random +0.10 R (interim, 95% CI ...)" or "(read, 90% CI ...)"; "—" without a paired control. */
export function edgeWords(f: RankFigures): string {
  if (f.edge_vs_control_r == null) return "—";
  return `${signedR(f.edge_vs_control_r)} (${f.edge_source ?? "interim"}${f.edge_ci ? `, ${ci(f.edge_ci, f.edge_ci_level ?? 0.95)}` : ""})`;
}
export const pfWords = (f: RankFigures) => (f.no_losses ? "no losses yet" : f.profit_factor == null ? "—" : f.profit_factor.toFixed(2));
export const trendWords = (f: RankFigures) =>
  `7${NBSP}d ${signedR(f.last_7d.net_r_per_trade)} (n${NBSP}${f.last_7d.n}) · 30${NBSP}d ${signedR(f.last_30d.net_r_per_trade)} (n${NBSP}${f.last_30d.n})${f.trend ? ` · ${f.trend}` : ""}`;

// ------------------------------------------------------------------------------------------------ polls (§3)
export const POLL = { now: 5_000, list: 15_000, strategies: 60_000, today: 15_000, detail: 15_000 } as const;
export const PAGE_SIZE = 50;
export const STALE_S = 60;

// ------------------------------------------------------------------------------------------------ words
const titleCase = (s: string) => { const w = s.replace(/[_-]+/g, " "); return w.charAt(0).toUpperCase() + w.slice(1); };

/** "Box break + close · 15m": the label, never the slug; the timeframe once. */
export function strategyLabel(label: string | null | undefined, family: string, timeframe: string | null | undefined, variant?: string | null): string {
  const name = label || titleCase(family);
  const tf = variant || timeframe;
  return tf ? `${name} · ${tf}` : name;
}

/** "22 min", "1 h 05 min", "2 d 3 h". */
export function duration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return "—";
  const m = Math.floor(s / 60);
  if (m < 1) return withUnit(Math.round(s), "s");
  if (m < 60) return withUnit(m, "min");
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${withUnit(h, "h")} ${withUnit(String(m % 60).padStart(2, "0"), "min")}` : withUnit(h, "h");
  return `${withUnit(Math.floor(h / 24), "d")} ${withUnit(h % 24, "h")}`;
}

/** "22 min" or "4 of 10 min" against the hold limit. */
export function heldWords(held: number | null | undefined, limit: number | null | undefined): string {
  if (held == null) return "—";
  if (limit == null || limit <= 0) return duration(held);
  if (limit < 3600) return `${Math.floor(held / 60)} of ${withUnit(Math.round(limit / 60), "min")}`;
  return `${duration(held)} of ${duration(limit)}`;
}

export const price = (x: number | null | undefined) =>
  x == null ? "—" : x.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Signed money or "£ —": unknown is a dash with its currency, never zero and never "not known" on every row. */
export function moneyOrDash(x: number | null | undefined, ccy: string | null | undefined, signed = true): string {
  if (x == null) return `${ccy === "GBP" || ccy == null ? "£" : `${ccy} `}${NBSP}—`;
  return money(x, ccy, signed);
}

export const sideWords = (s: Side) => (s === "long" ? "LONG" : "SHORT");
export const sideArrow = (s: Side) => (s === "long" ? "▲" : "▼");

/** The kind badge is shown for every kind but Strategy (§3.1): the word carries the meaning, not the colour. */
export const kindBadge = (k: TradeKind | null | undefined): string | null => (!k || k === "strategy" ? null : KIND_WORDS[k]);

/** A row's result now: closed P&L or the open mark; R the same. */
export const resultOf = (t: TradeRow) => (t.status === "open"
  ? { pnl: t.pnl_now, r: t.r_now } : { pnl: t.pnl, r: t.r });

/** The row's accessible name: "Box break + close 15m, long, plus £2.10, closed at target". */
export function rowName(t: TradeRow): string {
  const { pnl } = resultOf(t);
  const res = pnl == null ? "result not known" : `${pnl >= 0 ? "plus" : "minus"} ${money(Math.abs(pnl), t.currency)}`;
  const end = t.status === "open" ? "open" : `closed at ${exitWords(t.exit_reason)}`;
  return `${strategyLabel(t.family_label, t.family, t.timeframe, t.variant).replace(" · ", " ")}, ${t.side}, ${res}, ${end}`;
}

/** An open row's accessible name: "Box break + close 15m, long, plus £3.40, open". */
export function openRowName(p: OpenPositionX): string {
  const res = p.floating_pnl == null ? "P&L not known" : `${p.floating_pnl >= 0 ? "plus" : "minus"} ${money(Math.abs(p.floating_pnl), p.currency)}`;
  return `${strategyLabel(p.family_label, p.family, p.timeframe).replace(" · ", " ")}, ${p.side}, ${res}, open`;
}

/** A net that is not known for some closed trades is never shown as a total: "net incomplete: 3 trades without
 *  costs yet (known trades +£12.40)". The partial sum is labelled as the known trades only. */
export function incompleteWords(n: number, partial: number | null | undefined, ccy: string | null | undefined): string {
  const known = partial == null ? "" : ` (known trades ${money(partial, ccy, true)})`;
  return `net incomplete: ${n} trade${n === 1 ? "" : "s"} without costs yet${known}`;
}

/** Where an open row leads. An engine position is one ledger trade ("position:<account>:<id>"); an arm's or a
 *  probe's open trade is keyed by its order in the ledger, which the open-positions view does not carry, so it opens
 *  the List filtered to its account, strategy and open trades (the trade is the top row there). */
export type OpenTarget = { id: string } | { query: string };
export function openTarget(p: OpenPositionX): OpenTarget {
  if (p.kind === "strategy" || p.kind === "real") return { id: `position:${p.account}:${p.position_id}` };
  return { query: filtersQuery({ ...DEFAULT_FILTERS, acct: [p.account], fam: [p.family], status: "open" }) };
}

/** The totals line of a filtered list (§3.2), as the server sends it: money not summed across currencies; counts and
 *  open risk always shown. */
export function totalsLine(t: TradeTotals): { text: string; note: string | null } {
  const n = `${t.n.toLocaleString("en-GB")} trade${t.n === 1 ? "" : "s"}${t.n_open ? ` (${t.n_open} open)` : ""}`;
  const parts = [n];
  if (t.net_pnl != null) parts.push(`net ${money(t.net_pnl, t.currency, true)} after costs`);
  else if ((t.net_unknown ?? 0) > 0) parts.push(incompleteWords(t.net_unknown ?? 0, t.net_partial, t.currency));
  if (t.costs != null) parts.push(`costs ${money(t.costs, t.currency)}`);
  if (t.open_risk != null && t.n_open) parts.push(`open risk ${money(t.open_risk, t.currency)}`);
  const note = t.mixed_currencies ? "Money not summed: more than one currency." : null;
  return { text: parts.join(" · "), note };
}

// ------------------------------------------------------------------------------------------------ Now grouping
export type OpenGroup = {
  key: string;
  label: string;
  mode: Mode | null;
  rows: OpenPositionX[];
  risk: number | null;                   // null when any row's risk is unknown (a partial total is refused)
  currency: string | null;
  stale: boolean;
  noRate: boolean;                       // money unknown on at least one row: "(no rate)" once per group
};

/** One group per account, real money first, then by label; rows by largest risk first (the first 10 shown). */
export function groupOpen(positions: OpenPositionX[]): OpenGroup[] {
  const by = new Map<string, OpenPositionX[]>();
  for (const p of positions) {
    const k = p.account;
    by.set(k, [...(by.get(k) ?? []), p]);
  }
  const groups = [...by.entries()].map(([key, rows]) => {
    const ccys = new Set(rows.map((r) => r.currency));
    const one = ccys.size === 1 ? rows[0].currency : null;
    const sum = (f: (r: OpenPositionX) => number | null | undefined) =>
      one == null || rows.some((r) => f(r) == null) ? null : rows.reduce((s, r) => s + (f(r) as number), 0);
    const sorted = [...rows].sort((a, b) => (b.risk_amount ?? b.open_risk ?? -1) - (a.risk_amount ?? a.open_risk ?? -1));
    return {
      key, label: rows[0].account_label || key, mode: rows[0].mode ?? null, rows: sorted, currency: one,
      risk: sum((r) => r.risk_amount ?? r.open_risk),
      stale: rows.some((r) => r.stale), noRate: rows.some((r) => r.floating_pnl == null),
    };
  });
  return groups.sort((a, b) => Number(b.mode === "live") - Number(a.mode === "live") || a.label.localeCompare(b.label));
}

// ------------------------------------------------------------------------------------------------ List filters (URL)
export type Period = "today" | "7d" | "30d" | "all";
export type Sort = "newest" | "best" | "worst";
export const SORTS: Sort[] = ["newest", "best", "worst"];
export const SORT_WORDS: Record<Sort, string> = { newest: "Newest", best: "Best R", worst: "Worst R" };
export type Filters = {
  period: Period;
  sort: Sort;
  acct: string[];
  fam: string[];
  tf: string[];
  status: "" | "open" | "closed";
  kind: TradeKind[];
  exit: string[];
  evidence: Evidence[];
};
export const DEFAULT_KINDS: TradeKind[] = KINDS.filter((k) => k !== "shadow");
export const PERIODS: Period[] = ["today", "7d", "30d", "all"];
export const DEFAULT_FILTERS: Filters = { period: "today", sort: "newest", acct: [], fam: [], tf: [], status: "", kind: [], exit: [], evidence: [] };

const list = (p: URLSearchParams, k: string) => (p.get(k) ?? "").split(",").filter(Boolean);

export function parseFilters(query: string): Filters {
  const p = new URLSearchParams(query.replace(/^\?/, ""));
  const period = p.get("period");
  const status = p.get("status");
  const sort = p.get("sort");
  return {
    period: period === "7d" || period === "30d" || period === "all" ? period : "today",
    sort: sort === "best" || sort === "worst" ? sort : "newest",
    acct: list(p, "acct"), fam: list(p, "fam"), tf: list(p, "tf"),
    status: status === "open" || status === "closed" ? status : "",
    kind: list(p, "kind").filter((k): k is TradeKind => (KINDS as readonly string[]).includes(k)),
    exit: list(p, "exit").filter((k) => (EXIT_REASONS as readonly string[]).includes(k)),
    evidence: list(p, "evidence").filter((k): k is Evidence => k in EVIDENCE_WORDS),
  };
}

/** "acct=scalp&fam=box_break": only what differs from the defaults, so a shared link stays short. */
export function filtersQuery(f: Filters): string {
  const p = new URLSearchParams();
  if (f.period !== "today") p.set("period", f.period);
  if (f.sort !== "newest") p.set("sort", f.sort);
  for (const k of ["acct", "fam", "tf", "kind", "exit", "evidence"] as const) if (f[k].length) p.set(k, f[k].join(","));
  if (f.status) p.set("status", f.status);
  return p.toString().replace(/%2C/g, ",");
}

/** The API query (GET /api/trades: repeated parameters): the URL's filters, the cursor, and by default every kind
 *  but Shadow. */
export function apiQuery(f: Filters, cursor?: string | null): string {
  const p = new URLSearchParams();
  p.set("period", f.period);
  const many: [string, readonly string[]][] = [["account", f.acct], ["family", f.fam], ["timeframe", f.tf],
    ["kind", f.kind.length ? f.kind : DEFAULT_KINDS], ["exit_reason", f.exit], ["evidence", f.evidence]];
  for (const [k, vs] of many) for (const v of vs) p.append(k, v);
  if (f.status) p.set("status", f.status);
  if (f.sort !== "newest") p.set("sort", f.sort);
  p.set("limit", String(PAGE_SIZE));
  if (cursor) p.set("cursor", cursor);
  return `?${p.toString()}`;
}

export const activeFilterCount = (f: Filters) =>
  (["acct", "fam", "tf", "kind", "exit", "evidence"] as const).reduce((n, k) => n + (f[k].length ? 1 : 0), 0) + (f.status ? 1 : 0);

export const PERIOD_WORDS: Record<Period, string> = { today: "Today", "7d": "7 days", "30d": "30 days", all: "All" };

// ------------------------------------------------------------------------------------------------ board words
// The order is the server's (strategy_board.py: state, then closeness to the read, never by result); filters keep it.

/** "read ~Thu 5 Nov" from the server's ETA (the client never projects a pace of its own). */
export function etaWords(iso: string | null | undefined): string {
  if (!iso) return "read date not known yet";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "read date not known yet";
  // a read most of a year away (a slow arm) carries its year: "read ~Tue 20 Jul 2032", never a misleading "Jul"
  const year = d.getTime() - Date.now() > 300 * 86_400_000 ? { year: "numeric" as const } : {};
  return `read ~${d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", ...year })}`;
}

/** "90% CI +0.05…+0.37 R" (the board's and the reads' level), or at `level` (the ranking's 95%). */
export const ci = (c: [number, number] | null | undefined, level = 0.9) =>
  c ? `${Math.round(level * 100)}% CI ${signedR(c[0]).replace(`${NBSP}R`, "")}…${signedR(c[1])}` : "CI not known";

export const pct = (x: number | null | undefined) => (x == null ? "—" : withUnit(Math.round(x * 100), "%"));

// ------------------------------------------------------------------------------------------------ Today helpers
/** Fills against the plan's target: "148 of 200", pace words, and whether behind (below half the target pace). */
export function fillsWords(a: TodayAccountX): { text: string; pace: string | null } | null {
  if (a.fills_today == null) return null;
  const text = a.fills_target ? `${a.fills_today} of ${a.fills_target}` : `${a.fills_today}`;
  const pace = a.pace == null ? null : `pace ~${a.pace} by day end`;
  return { text, pace };
}

/** A 14-day strip scaled to the largest absolute value and the target, so bars and target share one scale. */
export function stripScale(days: StripDay[], target: number | null | undefined): number {
  return Math.max(1e-9, Math.abs(target ?? 0), ...days.map((d) => Math.abs(d.net_pnl ?? 0)));
}

export const capWords = (c: CapUse | null | undefined, ccy: string | null) =>
  c ? `${money(c.used, ccy)} of ${money(c.limit, ccy)}` : "not reported";

export const KIND_ORDER: TradeKind[] = ["real", "strategy", "exploration", "control", "cost_probe", "shadow"];
export { signedR };
