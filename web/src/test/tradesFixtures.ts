// Typed fixtures for the trades screens (docs/design/trades-ux-2026-10-27.md), shared by the vitest units and the
// Playwright e2e/screenshots until the backend lane's ledger API lands on the seeded e2e server. Illustrative figures.
import type { OpenPositionX, StrategyBoard, TodayAccountX, TradeBars, TradeRow, TradesPage } from "../lib/trades";

const NOW = Date.UTC(2026, 9, 27, 9, 0, 0);
const iso = (minAgo: number) => new Date(NOW - minAgo * 60_000).toISOString();

const pos = (o: Partial<OpenPositionX>): OpenPositionX => ({
  account: "icm-demo", mode: "demo", probe: true, position_id: 1, agent_id: "ex:BXR-15m", family: "box_break_close",
  timeframe: "15m", side: "long", lots: 0.01, entry: 4012.4, stop: 4006.1, target: 4024, opened_utc: iso(22), price: 4015.8,
  floating_pnl: 3.4, currency: "GBP", r_now: 0.63, open_risk: 5.4, age_s: 2, stale: false, risk_amount: 5.4, held_s: 22 * 60,
  hold_limit_s: 7200, account_label: "Day/swing lab", kind: "exploration", family_label: "Box break + close",
  why: "Close above the 15m box after a squeeze", trade_id: "exploration:icm-demo:1", ...o,
});

export const openPositions = (): { as_of: string; unreadable: string[]; positions: OpenPositionX[] } => ({
  as_of: iso(0), unreadable: [], positions: [
    pos({}),
    pos({ position_id: 2, account: "icm-probe", account_label: "Scalp lab", family: "random_bracket", family_label: "Random control (scalp)",
          timeframe: "1m", side: "short", entry: 4016, stop: 4017.3, target: 4014.7, floating_pnl: -0.3, r_now: -0.27, risk_amount: 1.1,
          open_risk: 1.1, held_s: 240, hold_limit_s: 600, kind: "control", trade_id: "control:icm-probe:2", why: "Random entry, same bracket" }),
    pos({ position_id: 3, account: "icm-probe", account_label: "Scalp lab", family: "opening_range", family_label: "Opening range",
          timeframe: "5m", floating_pnl: 0.8, r_now: 0.4, risk_amount: 2, open_risk: 2, held_s: 360, hold_limit_s: 1800,
          trade_id: "exploration:icm-probe:3" }),
  ],
});

export const tradeRow = (o: Partial<TradeRow> = {}): TradeRow => ({
  trade_id: "exploration:icm-demo:41", kind: "exploration", status: "closed", account_label: "Day/swing lab", mode: "demo",
  currency: "GBP", family: "box_break_close", family_label: "Box break + close", variant: null, timeframe: "15m",
  arm_id: "KA15", is_control: false, side: "long", lots: 0.01, entry: 4012.4, stop: 4006.1, stop_now: 4012.4, target: 4024,
  exit: 4024, exit_utc: iso(19), exit_reason: "target", censored: false, opened_utc: iso(51), held_s: 32 * 60, hold_limit_s: 7200,
  risk_amount: 1.4, pnl: 2.1, pnl_now: null, r: 1.5, r_now: null,
  costs: { spread: 0.18, slippage: 0.02, commission: 0.07, swap: 0 }, costs_total: 0.27, costs_r: 0.19,
  why: "Close above the 15m box after a squeeze", blinded: false, evidence: "counted", excluded_reason: null,
  read_n: 213, read_at: 300, price_now: null, as_of: iso(0), stale: false, ...o,
});

export const tradesPage = (o: Partial<TradesPage> = {}): TradesPage => ({
  as_of: iso(0), next_cursor: "c2", unreadable: [],
  totals: { n: 412, net_pnl: 18.4, costs: 22.1, currency: "GBP", blinded: false, unread_arms: 12, excluded: [] },
  rows: [
    tradeRow(),
    tradeRow({ trade_id: "control:icm-probe:7", kind: "control", is_control: true, account_label: "Scalp lab", family: "random_bracket",
               family_label: "Random control (scalp)", timeframe: "1m", side: "short", exit_reason: "stop", pnl: -0.4, r: -1,
               why: "Random entry, same bracket as the scalp arms", opened_utc: iso(53), held_s: 180 }),
    tradeRow({ trade_id: "cost_probe:icm-probe:9", kind: "cost_probe", account_label: "Scalp lab", family: "cost_probe",
               family_label: "Cost probe", timeframe: null, exit_reason: "time", pnl: -0.05, r: -0.1, why: "Measures spread and slippage",
               evidence: "not_evidence", opened_utc: iso(70), held_s: 60 }),
  ], ...o,
});

/** The list filtered to a partly-read family: fewer than 3 unread arms, so the server blinds rows and totals. */
export const blindedPage = (): TradesPage => tradesPage({
  next_cursor: null,
  totals: { n: 64, net_pnl: null, costs: 3.2, currency: "GBP", blinded: true, unread_arms: 1, excluded: [] },
  rows: [tradeRow({ trade_id: "exploration:icm-probe:88", family: "opening_range", family_label: "Opening range", timeframe: "1h",
                    blinded: true, pnl: null, r: null })],
});

export const bars = (): TradeBars => {
  const out = [];
  let p = 4008;
  for (let i = 0; i < 40; i++) {
    const o = p, c = p + Math.sin(i / 3) * 2 + (i > 30 ? 1.2 : 0.3);
    out.push({ t: new Date(NOW - (60 - i) * 15 * 60_000 + 0).toISOString(), o, h: Math.max(o, c) + 1, l: Math.min(o, c) - 1, c });
    p = c;
  }
  return { timeframe: "15m", bars: out };
};

export const board = (): StrategyBoard => ({
  as_of: iso(0),
  learning: { running: 44, read: 4, proven: 1, retired: 3, counted_this_week: 2960 },
  lab_totals: [
    { account_label: "Scalp lab", style: "scalp", period: "7 days", arms: 7, n: 2960, net_pnl: -41.2, costs: 66.1, currency: "GBP", within_plan: true },
    { account_label: "Vantage lab", style: "intraday", period: "7 days", arms: 2, n: 310, net_pnl: null, costs: 8.4, currency: "GBP", within_plan: null },
  ],
  families: [
    { family: "random_bracket", family_label: "Random control (scalp)", style: "scalp", accounts: ["Scalp lab"], state: "control", anneal_state: "E",
      variants: [{ timeframe: "1m", arm_id: "S0", is_control: true, n_counted: 412, n_read_at: 300, fills_today: 80, fills_7d: 412,
                   eta_read_date: null, band: "running", open_now: 1, read: null }] },
    { family: "box_break_close", family_label: "Box break + close", style: "intraday", accounts: ["Day/swing lab"], state: "running", anneal_state: "E",
      variants: [
        { timeframe: "15m", arm_id: "KA15", is_control: false, n_counted: 213, n_read_at: 300, fills_today: 14, fills_7d: 70, eta_read_date: "2026-11-05", band: "running", open_now: 1, read: null },
        { timeframe: "1h", arm_id: "KA1H", is_control: false, n_counted: 61, n_read_at: 300, fills_today: 3, fills_7d: 18, eta_read_date: "2026-12-15", band: "running", open_now: 0, read: null },
        { timeframe: "4h", arm_id: "KA4H", is_control: false, n_counted: 18, n_read_at: 300, fills_today: 0, fills_7d: 2, eta_read_date: null, band: "retired_at_stop", open_now: 0, read: null },
      ] },
    { family: "opening_range", family_label: "Opening range", style: "intraday", accounts: ["Scalp lab"], state: "proven", anneal_state: "P",
      variants: [
        { timeframe: "15m", arm_id: "OR15", is_control: false, n_counted: 300, n_read_at: 300, fills_today: 9, fills_7d: 64, eta_read_date: null, band: "running", open_now: 0,
          read: { verdict: "proven", n: 300, net_r_per_trade: 0.21, ci90: [0.05, 0.37], hit_rate: 0.46, vs_control_r: 0.24,
                  vs_control_ci90: [0.06, 0.42], net_gbp: 41.2, since_read: { net_r_per_trade: 0.18, n: 64 } } },
        { timeframe: "1h", arm_id: "OR1H", is_control: false, n_counted: 120, n_read_at: 300, fills_today: 2, fills_7d: 15, eta_read_date: "2026-11-30", band: "running", open_now: 0, read: null },
      ] },
  ],
  engine: [
    { name: "Box break + MFI", timeframe: "1h", kind: "strategy", n: 42, net_r_per_trade: 0.12, ci90: [-0.08, 0.32], hit_rate: 0.52, net_pnl: 12.3, currency: "GBP", n_today: 2, n_7d: 11 },
  ],
  unreadable: [],
});

const days = Array.from({ length: 14 }, (_, i) => ({
  date: new Date(NOW - (13 - i) * 86_400_000).toISOString().slice(0, 10),
  net_pnl: [12, -8, 31, 55, -20, 4, 0, 62, -3, 18, 9, -14, 41, -3.1][i], opportunity: [2, 3, 7, 10, 12].includes(i),
}));

export const todayAccount = (o: Partial<TodayAccountX> = {}): TodayAccountX => ({
  account: "icm-probe", mode: "demo", currency: "GBP", day_pnl: -1.0, open_pnl: 2.1, closed_pnl: -3.1, closed_n: 133,
  closed_complete: true, costs: { spread: 7.6, spread_complete: true, commission: 2.2, swap: 0 }, loss_meter: null, age_s: 3, stale: false,
  account_label: "Scalp lab", fills_today: 148, fills_target: 200, pace: 240, day_end_utc: iso(-13 * 60),
  by_kind: { exploration: { fills: 120, closed: 110, net_pnl: -4.2, costs: 8.1, net_r: -2.1 },
             control: { fills: 20, closed: 18, net_pnl: 0.9, costs: 1.3, net_r: 0.4 },
             cost_probe: { fills: 8, closed: 5, net_pnl: 0.2, costs: 0.4, net_r: 0.1 } },
  open_n: 15, open_risk: 18.2, day_cap_used: { used: 30, limit: 500 }, week_cap_used: { used: 61, limit: 1000 },
  exploration_pot_used: { used: 30, limit: 150 }, quiet_day: false, day_target: 50, days,
  wtd: { net_pnl: 12.4, costs: { spread: 40.1, commission: 11.2, swap: -0.4 } },
  mtd: { net_pnl: 88.1, costs: { spread: 160.2, commission: 44.8, swap: -2.1 } }, paused: false, halted: false, ...o,
});
