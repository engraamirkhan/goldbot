// A TradeStats for tests (GET /api/trades/overview): all zero, with the fields a test sets.
import type { TradeStats } from "../lib/api";

export const stats = (over: Partial<TradeStats> = {}): TradeStats => ({
  open: 0, closed: 0, wins: 0, losses: 0, win_rate: null, n_r: 0, total_r: null, avg_r: null, net_pnl: {},
  best: null, worst: null, exits: { target: 0, stop: 0, time: 0, other: 0 }, ...over,
});
