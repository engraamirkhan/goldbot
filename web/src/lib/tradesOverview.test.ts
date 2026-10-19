import { describe, expect, it } from "vitest";
import { stats } from "../test/tradeStats";
import type { TradesOverview } from "./api";
import { bestWorst, counts, exits, headline, netPnl, resultTone, rows, signedR, winRate } from "./tradesOverview";

describe("trades summary formatting", () => {
  it("writes the win rate as a whole percent and a dash with nothing closed", () => {
    expect(winRate(stats({ closed: 3, wins: 2, win_rate: 0.6667 }))).toBe("67%");
    expect(winRate(stats())).toBe("—");
  });

  it("signs R with a true minus and dashes a missing R", () => {
    expect(signedR(1.5)).toBe("+1.50 R");
    expect(signedR(-0.4)).toBe("−0.40 R");
    expect(signedR(null)).toBe("—");
  });

  it("keeps money per currency and never adds across currencies", () => {
    expect(netPnl(stats({ net_pnl: { GBP: 12.4, USD: -3 } }))).toBe("+£12.40 · −$3.00");
    expect(netPnl(stats())).toBe("—");
  });

  it("heads a type with its net money, or its R when it has no money (shadow)", () => {
    expect(headline(stats({ net_pnl: { GBP: -2 }, total_r: -0.5 }))).toBe("−£2.00");
    expect(headline(stats({ total_r: 1.5 }))).toBe("+1.50 R");
  });

  it("counts open and closed, and the win rate once something closed", () => {
    expect(counts(stats({ open: 2, closed: 5, wins: 3, win_rate: 0.6 }))).toBe("2 open · 5 closed · 60% won");
    expect(counts(stats({ open: 1 }))).toBe("1 open · 0 closed");
  });

  it("lists only the exit reasons that happened", () => {
    expect(exits(stats({ exits: { target: 3, stop: 2, time: 0, other: 1 } }))).toBe("3 target · 2 stop · 1 other");
    expect(exits(stats())).toBe("—");
  });

  it("names the best and worst by R, else by money", () => {
    const ref = { ts: "2026-10-19T10:00:00Z", agent_id: "mfi-g0", timeframe: "15m" };
    expect(bestWorst(stats({ best: { ...ref, r: 2, pnl: null, currency: null }, worst: { ...ref, r: -1, pnl: null, currency: null } })))
      .toBe("best +2.00 R · worst −1.00 R");
    expect(bestWorst(stats({ best: { ...ref, r: null, pnl: 3, currency: "GBP" }, worst: { ...ref, r: null, pnl: -2, currency: "GBP" } })))
      .toBe("best +£3.00 · worst −£2.00");
    expect(bestWorst(stats())).toBe("");
  });

  it("tones a gain green, a loss amber and nothing idle (never red)", () => {
    expect(resultTone(stats({ net_pnl: { GBP: 5 } }))).toBe("ok");
    expect(resultTone(stats({ total_r: -1 }))).toBe("warn");
    expect(resultTone(stats())).toBe("idle");
    expect(resultTone(stats({ net_pnl: { GBP: 5, USD: -9 }, total_r: -0.2 }))).toBe("warn");   // two currencies: by R
  });
});

describe("rows", () => {
  const v: TradesOverview = { as_of: "2026-10-19T14:00:00Z", unreadable: [], periods: [
    { period: "today", since: "2026-10-18T22:00:00Z", types: [
      { type: "shadow_taken", stats: stats({ closed: 1 }), by_family: { box_break_close: stats({ closed: 1 }) }, by_timeframe: { "1h": stats({ closed: 1 }) } },
      { type: "live", stats: stats(), by_family: {}, by_timeframe: {} },
      { type: "demo", stats: stats({ open: 1 }), by_family: { mfi: stats(), tsmom: stats({ open: 1 }) }, by_timeframe: { "4h": stats({ open: 1 }) } },
    ] },
  ] };

  it("lists each type's strategies in the API's order and skips empty types", () => {
    expect(rows(v, "today", "family").map((r) => `${r.type}/${r.key}`)).toEqual(["shadow_taken/box_break_close", "demo/mfi", "demo/tsmom"]);
  });

  it("switches to timeframes, and a missing period has no rows", () => {
    expect(rows(v, "today", "timeframe").map((r) => r.key)).toEqual(["1h", "4h"]);
    expect(rows(v, "7d", "family")).toEqual([]);
  });
});
