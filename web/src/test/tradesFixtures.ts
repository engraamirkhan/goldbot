// Typed fixtures for the trades screens' unit tests, in the API contract's shapes (schema.d.ts: TradeRow, TradesPage,
// TodayAccount, OpenPosition). The e2e and screenshots use the real seeded server. The strategy board is not served
// yet (backlog 132): its fixture feeds the board's unit tests only. Illustrative figures.
import type { Bar, OpenPositionX, StrategyBoard, StripDay, TodayAccountX, TradeRow, TradesPage } from "../lib/trades";

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
  why: "Close above the 15m box after a squeeze", blinded: false, evidence: "counted", excluded_reason: null, stale: false, ...o,
});

const totals = (o: Partial<TradesPage["totals"]> = {}): TradesPage["totals"] => ({
  n: 412, n_open: 3, n_closed: 409, currency: "GBP", mixed_currencies: false, net_pnl: 18.4, costs: 22.1, open_pnl: 3.9,
  open_risk: 8.5, withheld: false, withheld_reason: null, net_unknown: 0, net_partial: null, open_pnl_withheld: false, ...o,
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

/** A filter narrowed to a strategy whose arms are unread: the server withholds the money and blinds the rows. */
export const blindedPage = (): TradesPage => tradesPage({
  next_cursor: null,
  totals: totals({ n: 64, n_open: 0, n_closed: 64, net_pnl: null, costs: null, open_pnl: null, open_risk: null, withheld: true,
                   withheld_reason: "the filter narrows to strategies whose arms are not read yet (ADR 0050): totals return at the read" }),
  rows: [tradeRow({ trade_id: "exploration:icm-probe:88", family: "opening_range", family_label: "Opening range", variant: "1h",
                    timeframe: "1h", blinded: true, pnl: null, r: null, gross: null, exit_reason: null })],
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

const strip: StripDay[] = Array.from({ length: 14 }, (_, i) => ({
  day: new Date(NOW - (13 - i) * 86_400_000).toISOString().slice(0, 10), n: 30 + i,
  net_pnl: [12, -8, 31, 55, -20, 4, 0, 62, -3, 18, 9, -14, 41, -3.1][i], costs: 9, target: 50,
  opportunity: [2, 3, 7, 10, 12].includes(i), withheld: false, net_unknown: 0,
}));

export const todayAccount = (o: Partial<TodayAccountX> = {}): TodayAccountX => ({
  account: "icm-probe", mode: "demo", currency: "GBP", day_pnl: -1.0, open_pnl: 2.1, closed_pnl: -3.1, closed_n: 133,
  closed_complete: true, costs: { spread: 7.6, spread_complete: true, commission: 2.2, swap: 0 }, loss_meter: null, age_s: 3, stale: false,
  account_label: "Scalp lab", fills_today: 148, fills_target: 200, pace: 240,
  by_kind: { exploration: { fills: 120, closed: 110, net_pnl: -4.2, costs: 8.1, net_r: -2.1, withheld: false, net_unknown: 0 },
             control: { fills: 20, closed: 18, net_pnl: 0.9, costs: 1.3, net_r: 0.4, withheld: false, net_unknown: 0 },
             cost_probe: { fills: 8, closed: 5, net_pnl: 0.2, costs: 0.4, net_r: 0.1, withheld: false, net_unknown: 0 } },
  net_today: -3.1, costs_today: { spread: 7.4, slippage: 0.2, commission: 2.2, swap: 0, total: 9.8, complete: true, spread_exit_estimated: false, slippage_exit_known: true },
  open_n: 15, open_risk: 18.2, day_cap_used: { used: 30, limit: 500, share: 0.06 }, week_cap_used: { used: 61, limit: 1000, share: 0.061 },
  exploration_pot_used: { used: 30, limit: 150, share: 0.2 }, quiet_day: false, daily_target: 50, strip,
  week_to_date: { since: "2026-10-26", n: 300, net_pnl: 12.4, costs: { spread: 40.1, slippage: 1, commission: 11.2, swap: -0.4, total: 51.9, complete: true, spread_exit_estimated: false, slippage_exit_known: true }, withheld: false, net_unknown: 0 },
  month_to_date: { since: "2026-10-01", n: 3000, net_pnl: 88.1, costs: { spread: 160.2, slippage: 4, commission: 44.8, swap: -2.1, total: 206.9, complete: true, spread_exit_estimated: false, slippage_exit_known: true }, withheld: false, net_unknown: 0 },
  withheld: [], net_unknown: 0, net_today_partial: null, ...o,
});
