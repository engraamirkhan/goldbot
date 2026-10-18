// Fixture answers for the screenshot run (shots/capture.spec.ts): a populated account so every card has something to
// show, and one proposal in each card state. Shapes follow src/lib/schema.d.ts; nothing here reaches production.
const iso = (msFromNow: number) => new Date(Date.now() + msFromNow).toISOString();
const NOW_S = Date.now() / 1000;

const exits = "Exits keep running: open trades keep their stop, target and automatic exits.";
const entryOpen = { state: "open", headline: "Can take new trades", blocks: [], notes: [], exits, as_of: iso(-2000) };
const entryBlocked = {
  state: "blocked", headline: "No new trades until the news passes",
  blocks: [{ code: "news_blackout", text: "News blackout: US CPI", fix: "Nothing to do: it clears by itself.", kind: "wait",
             scope: "account", agent_id: null, at: iso(25 * 60_000), at_label: "clears", runbook: null, runbook_url: null }],
  notes: [], exits, as_of: iso(-2000),
};

export const account = (blocked = false) => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1012.4, day_pnl_pct: 0.0124, week_pnl_pct: 0.021,
  drawdown_pct: 0.008, stage: "normal", open_positions: 1, account_class: "raw", balance: 1000, currency: "GBP",
  margin_level_pct: 641, day_pnl: 12.4,
  loss_budget: { limit: 50, used: 8, remaining: 42, binding: "account 5.0% daily cap", period: "today", notes: [],
                 unknown_paused: false, unknown_mixed: false, binding_label: "this account (5.0% of today's start)",
                 caps: [{ name: "account 5.0% daily cap", label: "this account (5.0% of today's start)", period: "today",
                          limit: 50, used: 8, remaining: 42 }] },
  milestones: { rule: "the smallest trade risks at most 0.60% of equity", tradeable: ["15m"], next_timeframe: "1h",
                next_equity: 2744, progress: 1012.4 / 2744, pending: null,
                rungs: [{ timeframe: "15m", equity_needed: 900, reached: true, pending: null },
                        { timeframe: "1h", equity_needed: 2744, reached: false, pending: null }] },
  entry_status: blocked ? entryBlocked : entryOpen,
});

export const status = (over: Record<string, unknown> = {}) => ({
  mode: "propose", halted: false, pending: 2, supervisor: {}, supervisor_halt: false, supervisor_reasons: [],
  drift_halt: false, drift_reasons: [], blackout: null, ...over,
});

const base = {
  account_id: "icm-demo", agent_id: "session_open-g0-abc", lots: 0.05, p: 0.62, ev_r: 0.31, spread_points: 22,
  risk_usd: 10.4, currency: "GBP", risk_amount: 8.2, reward_amount: 12.3, risk_fraction: 0.0082, min_lot_exception: false,
  loss_left: 42, loss_left_period: "today",
  top_features: [["adx_14", 31.2], ["atr_14", 0.4], ["rv_20", -0.2]], created: NOW_S, tradingview_url: null,
};
export const pending = () => [
  { ...base, proposal_id: "fx-long", side: "long", entry: 2400.25, stop: 2396.0, target: 2406.5, expires_at: iso(78_000) },
  { ...base, proposal_id: "fx-short", side: "short", entry: 2410.5, stop: 2414.5, target: 2404.5, expires_at: iso(14_000) },
];
const decidedBase = { ...base, side: "long", entry: 2398.1, stop: 2394.0, target: 2404.2, expires_at: iso(-60_000),
                      refusal: [], refusal_text: [], decided_ts: NOW_S - 120, decided_channel: "telegram" };
export const decided = [
  { ...decidedBase, proposal_id: "fx-ok", status: "approved" },
  { ...decidedBase, proposal_id: "fx-rej", status: "rejected", reason_code: "news", decided_channel: "dashboard" },
  { ...decidedBase, proposal_id: "fx-ref", status: "refused", refusal: ["daily_loss_cap"], refusal_text: ["today's loss limit reached"] },
  { ...decidedBase, proposal_id: "fx-exp", status: "expired", decided_channel: null },
];

export const limits = {
  generated_utc: iso(0), error: null,
  supervisor: { daily_cap: 0.0375, weekly_cap: 0.075, default_daily: 0.015, default_weekly: 0.04, envelope_daily: 0.0375, envelope_weekly: 0.075 },
  accounts: [{
    account_id: "icm-demo", mode: "demo", profile: "demo-2026-10-18", source: "static", adr: "0032", updated_utc: iso(-5000),
    stale: false, currency: "GBP", balance: 1000, day_start_equity: 1000, week_start_equity: 1000, daily_cap_pct: 0.05,
    daily_limit: 50, daily_used: 8, daily_resets_utc: iso(8 * 3600_000), absolute_daily_limit: 50,
    weekly_cap_pct: 0.1, weekly_limit: 100, weekly_used: 8, weekly_resets_utc: iso(5 * 86400_000), min_lot: 0.01,
    min_lot_risk: 8, min_lot_risk_cap: 0.01, revert_active: false, revert_since: null, revert_reason: null,
    cap_hit: "none", error: null,
    last_change: { account_id: "icm-demo", ts: iso(-86400_000), field: "daily_cap", old_value: 0.04, new_value: 0.05,
                   unit: "fraction_of_equity", source: "static", reason: "The owner's approved demo ceilings apply.", adr: "0032" },
  }],
};

const ind = (indicator: string, tf: string) => ({
  indicator, version: "1", symbol: "XAUUSD", tf, last_bar_time: "", alerts_24h: 0, plots_seen: ["plot_0"], gaps_24h: 0,
  csv_bars: 0, csv_rows: 0, repaint_status: "validated", pull_live_24h: 96, pull_history_bars: 18000,
});
export const tradingview = {
  now: iso(0), indicators: [ind("Trend ribbon", "15"), ind("Momentum band", "60")], signin: "ok",
  last_pull_utc: iso(-3 * 3600_000), stale: false, fix_command: null, live_bars_24h: 192, history_bars: 36000,
};
