// UI spec 2026-11-02: "re-rank at most every 60 s in 3 fixed-height slots"; "single-instrument account shows no movers".
import { describe, expect, it } from "vitest";
import { rankMovers, RERANK_MS, showsMovers, type SymbolDay } from "./movers";

const d = (symbol: string, day_pnl: number | null): SymbolDay =>
  ({ symbol, day_pnl, closed_pnl: day_pnl, open_pnl: 0, closed_n: 1, open_n: 0 });

describe("top movers", () => {
  it("ranks by the size of the day's P&L, losses as much as gains, unknown last", () => {
    const r = rankMovers([d("US500", 1), d("XAGUSD", -5), d("EURUSD", null), d("USDJPY", 3)], 0);
    expect(r.order).toEqual(["XAGUSD", "USDJPY", "US500"]);
  });

  it("keeps the same order within 60 s however the figures move", () => {
    const first = rankMovers([d("US500", 1), d("XAGUSD", -5), d("USDJPY", 3)], 0);
    const later = rankMovers([d("US500", 9), d("XAGUSD", -1), d("USDJPY", 3)], RERANK_MS - 1, first);
    expect(later.order).toEqual(first.order);
    const after = rankMovers([d("US500", 9), d("XAGUSD", -1), d("USDJPY", 3)], RERANK_MS, first);
    expect(after.order).toEqual(["US500", "USDJPY", "XAGUSD"]);
  });

  it("re-ranks at once when an instrument in a slot is no longer listed", () => {
    const first = rankMovers([d("US500", 1), d("XAGUSD", -5), d("USDJPY", 3)], 0);
    expect(rankMovers([d("US500", 1), d("USDJPY", 3), d("DE40", 2)], 1000, first).order).toEqual(["USDJPY", "DE40", "US500"]);
  });

  it("shows no movers on a single-instrument account", () => {
    expect(showsMovers([d("XAUUSD", 2)], null)).toBe(false);
    expect(showsMovers([d("XAUUSD", 2)], 1)).toBe(false);
    expect(showsMovers([d("XAUUSD", 2)], 20)).toBe(true);
    expect(showsMovers([d("XAUUSD", 2), d("US500", 1)], null)).toBe(true);
  });
});
