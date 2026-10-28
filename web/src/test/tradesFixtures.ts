// Typed fixtures for the trades screens' unit tests, in the API contract's shapes (schema.d.ts: TradeRow, TradesPage,
// TodayAccount, OpenPosition, StrategyBoardView, RankingView, TradeBars). The e2e and screenshots use the real seeded server.
// Illustrative figures.
import type {
  Bar, BoardVariant, OpenPositionX, RankFigures, RankingView, RankRow, StrategyBoard, StripDay, TodayAccountX, TradeBars,
  TradeRow, TradesPage,
} from "../lib/trades";

const NOW = Date.UTC(2026, 9, 27, 9, 0, 0);
const iso = (minAgo: number) => new Date(NOW - minAgo * 60_000).toISOString();

const pos = (o: Partial<OpenPositionX>): OpenPositionX => ({
  account: "icm-demo", mode: "demo", probe: true, position_id: 1, agent_id: "ex:BXR-15m", family: "box_break_close",
  timeframe: "15m", side: "long", lots: 0.01, entry: 4012.4, stop: 4006.1, target: 4024, opened_utc: iso(22), price: 4015.8,
  floating_pnl: 3.4, currency: "GBP", r_now: 0.63, open_risk: 5.4, age_s: 2, stale: false, risk_amount: 5.4, held_s: 22 * 60,
  hold_limit_s: 7200, account_label: "Day/swing lab", kind: "exploration", family_label: "Box break + close", fx_age_s: 3, ...o,
});

export const openPositions = (): { as_of: string; unreadable: string[]; positions: OpenPositionX[] } => ({
  as_of: iso(0), unreadable: [], positions: [
    pos({}),
    pos({ position_id: 2, account: "icm-probe", account_label: "Scalp lab", family: "random_bracket", family_label: "Random control (scalp)",
          timeframe: "1m", side: "short", entry: 4016, stop: 4017.3, target: 4014.7, floating_pnl: -0.3, r_now: -0.27, risk_amount: 1.1,
          open_risk: 1.1, held_s: 240, hold_limit_s: 600, kind: "control" }),
    pos({ position_id: 3, account: "icm-probe", account_label: "Scalp lab", family: "opening_range", family_label: "Opening range",
          timeframe: "5m", floating_pnl: 0.8, r_now: 0.4, risk_amount: 2, open_risk: 2, held_s: 360, hold_limit_s: 1800,
          kind: "strategy", probe: false }),
  ],
});

export const tradeRow = (o: Partial<TradeRow> = {}): TradeRow => ({
  trade_id: "exploration:icm-demo:41", kind: "exploration", status: "closed", account: "icm-demo", account_label: "Day/swing lab",
  mode: "demo", currency: "GBP", family: "box_break_close", family_label: "Box break + close", variant: "15m", timeframe: "15m",
  arm_id: "KA15", is_control: false, side: "long", lots: 0.01, entry: 4012.4, stop: 4006.1, stop_now: 4012.4, target: 4024,
  exit: 4024, exit_utc: iso(19), exit_reason: "target", censored: false, opened_utc: iso(51), held_s: 32 * 60, hold_limit_s: 7200,
  risk_amount: 1.4, pnl: 2.1, gross: 2.37, pnl_now: null, r: 1.5, r_now: null, price_now: null,
  costs: { spread: 0.18, slippage: 0.02, commission: 0.07, swap: 0, total: 0.27, complete: true, spread_exit_estimated: false, slippage_exit_known: true },
  why: "Close above the 15m box after a squeeze", evidence: "counted", excluded_reason: null, stale: false, ...o,
});

const totals = (o: Partial<TradesPage["totals"]> = {}): TradesPage["totals"] => ({
  n: 412, n_open: 3, n_closed: 409, currency: "GBP", mixed_currencies: false, net_pnl: 18.4, costs: 22.1, open_pnl: 3.9,
  open_risk: 8.5, net_unknown: 0, net_partial: null, n_probes: 0, ...o,
});

export const tradesPage = (o: Partial<TradesPage> = {}): TradesPage => ({
  as_of: iso(0), next_cursor: "c2", unreadable: [], totals: totals(),
  rows: [
    tradeRow(),
    tradeRow({ trade_id: "exploration:icm-probe:7", kind: "control", is_control: true, account: "icm-probe", account_label: "Scalp lab",
               family: "random_bracket", family_label: "Random control (scalp)", variant: "1m", timeframe: "1m", side: "short",
               exit_reason: "stop", pnl: -0.4, r: -1, why: "Control: random entry, same bracket as the scalp arms", opened_utc: iso(53), held_s: 180 }),
    tradeRow({ trade_id: "cost_probe:icm-probe:9", kind: "cost_probe", account: "icm-probe", account_label: "Scalp lab", family: "cost_probe",
               family_label: "Cost probe", variant: null, timeframe: null, exit_reason: "time_limit", pnl: -0.05, r: -0.1,
               why: "Measures spread and slippage", evidence: "not_evidence", arm_id: null, opened_utc: iso(70), held_s: 60 }),
  ], ...o,
});

export const bars = (): Bar[] => {
  const out = [];
  let p = 4008;
  for (let i = 0; i < 40; i++) {
    const o = p, c = p + Math.sin(i / 3) * 2 + (i > 30 ? 1.2 : 0.3);
    out.push({ t: new Date(NOW - (60 - i) * 15 * 60_000).toISOString(), o, h: Math.max(o, c) + 1, l: Math.min(o, c) - 1, c });
    p = c;
  }
  return out;
};

const variant = (o: Partial<BoardVariant>): BoardVariant => ({
  arm_id: "KA15", timeframe: "15m", variant: "15m", account_labels: ["Day/swing lab"], is_control: false, control_arm_id: "S0",
  status: "running", anneal_state: "E", n_counted: 213, n_read_at: 300, fills_today: 14, fills_7d: 70, open_now: 0,
  eta_read_date: "2026-11-05", read: null, pooled: false, ...o, read_unit: o.read_unit ?? o.arm_id ?? "KA15",
});

/** In the server's order (strategy_board.py): proven, then running by closeness to the read, then controls. */
export const board = (): StrategyBoard => ({
  as_of: iso(0),
  learning: { running: 44, read: 4, proven: 1, retired: 3, counted_7d: 2960 },
  lab_totals: [
    { account_label: "Scalp lab", style: "scalp", period: "7 days", arms: 7, n: 2960, n_closed: 2950, net_pnl: -41.2, costs: 66.1,
      currency: "GBP" },
    { account_label: "Vantage lab", style: "intraday", period: "7 days", arms: 2, n: 310, n_closed: 300, net_pnl: null, costs: null,
      currency: "GBP" },
  ],
  families: [
    { family: "opening_range", family_label: "Opening range", style: "intraday", accounts: ["Scalp lab"], state: "proven", anneal_state: "P", pooled: [],
      variants: [
        variant({ arm_id: "OR15", account_labels: ["Scalp lab"], status: "read", anneal_state: "P", n_counted: 364, fills_today: 9,
                  fills_7d: 64, eta_read_date: null,
                  read: { read_utc: "2026-10-20T00:00:00Z", verdict: "proven", n: 300, net_r_per_trade: 0.21, ci90: [0.05, 0.37],
                          hit_rate: 0.46, net_pnl: 41.2, currency: "GBP", vs_control_r: 0.24, vs_control_ci90: [0.06, 0.42],
                          since_read: { net_r_per_trade: 0.18, n: 64 } } }),
        variant({ arm_id: "OR1H", timeframe: "1h", variant: "1h", account_labels: ["Scalp lab"], n_counted: 120, fills_today: 2,
                  fills_7d: 15, eta_read_date: "2026-11-30" }),
      ] },
    { family: "box_break_close", family_label: "Box break + close", style: "intraday", accounts: ["Day/swing lab"], state: "running", anneal_state: "E", pooled: [],
      variants: [
        variant({ open_now: 1 }),
        variant({ arm_id: "KA1H", timeframe: "1h", variant: "1h", n_counted: 61, fills_today: 3, fills_7d: 18, eta_read_date: "2026-12-15" }),
        variant({ arm_id: "KA4H", timeframe: "4h", variant: "4h", status: "retired", anneal_state: "R", n_counted: 18, fills_today: 0,
                  fills_7d: 2, eta_read_date: null }),
      ] },
    { family: "random_bracket", family_label: "Random control (scalp)", style: "scalp", accounts: ["Scalp lab"], state: "control", anneal_state: "E", pooled: [],
      variants: [variant({ arm_id: "S0", timeframe: "1m", variant: "1m", account_labels: ["Scalp lab"], is_control: true,
                           control_arm_id: null, n_read_at: null, n_counted: 412, fills_today: 80, fills_7d: 412, eta_read_date: null, open_now: 1 })] },
  ],
  engine: [
    { name: "Box break + MFI", family: "box_break_close", timeframe: "1h", kind: "strategy", n: 42, net_r_per_trade: 0.12,
      ci90: [-0.08, 0.32], hit_rate: 0.52, net_pnl: 12.3, currency: "GBP", n_today: 2, n_7d: 11 },
  ],
  unreadable: [],
});

/** The chart of the fixture trade (tradeRow: entry 4,012.40, stop 4,006.10, target 4,024.00): its stop moved once. */
export const tradeBars = (o: Partial<TradeBars> = {}): TradeBars => {
  const b = bars();
  return {
    trade_id: "exploration:icm-demo:41", timeframe: "15m", feed: "broker", bars: b, missing: null, side: "long", entry: 4012.4,
    entry_utc: b[30].t, stop: 4006.1, stop_moves: [{ at: b[34].t, price: 4012.4 }], target: 4024, exit: 4024, exit_utc: b[36].t, ...o,
  };
};

const strip: StripDay[] = Array.from({ length: 14 }, (_, i) => ({
  day: new Date(NOW - (13 - i) * 86_400_000).toISOString().slice(0, 10), n: 30 + i,
  net_pnl: [12, -8, 31, 55, -20, 4, 0, 62, -3, 18, 9, -14, 41, -3.1][i], costs: 9, target: 50,
  opportunity: [2, 3, 7, 10, 12].includes(i), net_unknown: 0,
}));

export const todayAccount = (o: Partial<TodayAccountX> = {}): TodayAccountX => ({
  account: "icm-probe", mode: "demo", currency: "GBP", day_pnl: -1.0, open_pnl: 2.1, closed_pnl: -3.1, closed_n: 133,
  closed_complete: true, costs: { spread: 7.6, spread_complete: true, commission: 2.2, swap: 0 }, loss_meter: null, age_s: 3, stale: false,
  account_label: "Scalp lab", fills_today: 148, fills_target: 200, pace: 240,
  by_kind: { exploration: { fills: 120, closed: 110, net_pnl: -4.2, costs: 8.1, net_r: -2.1, net_unknown: 0 },
             control: { fills: 20, closed: 18, net_pnl: 0.9, costs: 1.3, net_r: 0.4, net_unknown: 0 },
             cost_probe: { fills: 8, closed: 5, net_pnl: 0.2, costs: 0.4, net_r: 0.1, net_unknown: 0 } },
  net_today: -3.1, costs_today: { spread: 7.4, slippage: 0.2, commission: 2.2, swap: 0, total: 9.8, complete: true, spread_exit_estimated: false, slippage_exit_known: true },
  open_n: 15, open_risk: 18.2, day_cap_used: { used: 30, limit: 500, share: 0.06 }, week_cap_used: { used: 61, limit: 1000, share: 0.061 },
  exploration_pot_used: { used: 30, limit: 150, share: 0.2 }, quiet_day: false, daily_target: 50, strip,
  week_to_date: { since: "2026-10-26", n: 300, net_pnl: 12.4, costs: { spread: 40.1, slippage: 1, commission: 11.2, swap: -0.4, total: 51.9, complete: true, spread_exit_estimated: false, slippage_exit_known: true }, net_unknown: 0 },
  month_to_date: { since: "2026-10-01", n: 3000, net_pnl: 88.1, costs: { spread: 160.2, slippage: 4, commission: 44.8, swap: -2.1, total: 206.9, complete: true, spread_exit_estimated: false, slippage_exit_known: true }, net_unknown: 0 },
  net_unknown: 0, net_today_partial: null, ...o,
});

const figures = (o: Partial<RankFigures> = {}): RankFigures => ({
  n: 412, days: 18, clusters: 30, net_r_per_trade: 0.12, ci95: [0.05, 0.19], lower_adjusted: 0.03, hit_rate: 0.48,
  net_pnl: 38.1, net_per_trade: 0.09, currency: "GBP", profit_factor: 1.31, no_losses: false, max_drawdown_r: 6.4,
  cost_per_trade: 0.27, cost_per_trade_r: 0.11, edge_vs_control_r: 0.1, edge_ci: [-0.02, 0.22], edge_ci_level: 0.95,
  edge_source: "interim", last_7d: { n: 120, net_r_per_trade: 0.15 }, last_30d: { n: 412, net_r_per_trade: 0.12 }, trend: "up", ...o,
});

const rankRow = (o: Omit<Partial<RankRow>, "figures"> & { figures?: Partial<RankFigures> } = {}): RankRow => {
  const { figures: f, ...rest } = o;
  return {
    key: "opening_range", family: "opening_range", family_label: "Opening range", version: 1, style: "intraday",
    kind: "exploration", timeframes: ["15m", "1h"], accounts: ["Scalp lab"], band: "ranked", rank: 1, unranked_reason: null,
    status: "early", read_progress: { unit: "OR1H", n_counted: 120, n_read_at: 300 }, variants: [
      { variant: "15m", timeframe: "15m", status: "early", figures: figures({ n: 300 }) },
      { variant: "1h", timeframe: "1h", status: "early", figures: figures({ n: 112 }) },
    ], ...rest, figures: figures(f),
  };
};

/** In the server's order (strategy_ranking.py): ranked by the adjusted bound, then too few to rank by n. */
export const ranking = (): RankingView => ({
  as_of: iso(0), window: "all", account: null, style: null, k_ranked: 2, t_adjusted: 2.32, min_fills: 30, min_days: 10,
  heading: "Ranked by the least each trade is likely earning after costs. Early results move a lot; nothing is proven before its read.",
  rows: [
    rankRow(),
    rankRow({ key: "box_break_close", family: "box_break_close", family_label: "Box break + close", rank: 2,
              figures: { lower_adjusted: -0.09, net_r_per_trade: -0.02, hit_rate: 0.7, net_pnl: -12.5, no_losses: false } }),
    rankRow({ key: "sweep_absorb", family: "sweep_absorb", family_label: "Sweep + absorb", rank: null, band: "too_few",
              unranked_reason: "too few to rank (12 of 30 fills, 4 of 10 days)", variants: [],
              read_progress: { unit: "SA15", n_counted: 12, n_read_at: 300 },
              figures: { n: 12, days: 4, lower_adjusted: null, net_r_per_trade: 0.8 } }),
  ],
  controls: [rankRow({ key: "control:random_bracket", family: "random_bracket", family_label: "Random control", kind: "control",
                       band: "control", rank: null, variants: [], figures: { lower_adjusted: null, edge_vs_control_r: null } })],
  retired: [rankRow({ key: "ema_pullback", family: "ema_pullback", family_label: "EMA pullback", band: "retired", rank: null,
                      status: "retired", variants: [], figures: { lower_adjusted: null } })],
  account_choices: [{ account: "icm-probe", label: "Scalp lab" }, { account: "icm-demo", label: "Day/swing lab" }],
  unreadable: [],
});
