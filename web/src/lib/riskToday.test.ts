import { describe, expect, it } from "vitest";
import type { LimitAccount } from "./limits";
import {
  cutWords, factorWords, leftState, leftToday, moneyRange, rangeText, riskTodayLine, strategyRows, whyShort,
  type RiskTodayView,
} from "./riskToday";

const gbp = (x: number) => `£${x.toFixed(2)}`;

const rt = (over: Partial<RiskTodayView> = {}): RiskTodayView => ({
  base: 0.005, ceiling: 0.01, k_vol: 1, k_dd: 1, k_day: 1, scale: 1, strategies: [], why: "", ...over,
});

const acct = (over: Partial<LimitAccount> = {}): LimitAccount => ({
  account_id: "icm-demo", mode: "demo", currency: "GBP", balance: 1000, day_start_equity: 1000,
  daily_limit: 50, daily_used: 10, cap_hit: "none", stale: false, risk_today: rt(), ...over,
} as LimitAccount);

describe("risk per trade today", () => {
  it("says each cut in plain words and nothing for no cut", () => {
    expect(cutWords(0.5)).toBe("halved");
    expect(cutWords(0.75)).toBe("cut to 75%");
    expect(cutWords(1)).toBeNull();
    expect(cutWords(null)).toBeNull();
    expect(factorWords(rt({ k_vol: 0.5, k_dd: 0.75 }))).toEqual(["high volatility: halved", "drawdown: cut to 75%"]);
    expect(whyShort(rt())).toBe("no cut today");
    expect(whyShort(rt({ k_day: 0.5 }))).toBe("half the daily allowance lost: halved");
  });

  it("turns each strategy's applied rate into money on the day-start equity, base x scale when none", () => {
    const a = acct({ risk_today: rt({ scale: 0.5, k_vol: 0.5, strategies: [
      { version: "box-1h", decided: 0.008, applied: 0.004, reason: "strong recent edge" },
      { version: "mfi-15m", decided: null, applied: null, reason: null },
    ] }) });
    const rows = strategyRows(a);
    expect(rows[0]).toMatchObject({ rate: 0.004, money: 4, base: false });
    expect(rows[1]).toMatchObject({ rate: 0.0025, money: 2.5, base: true });
    expect(moneyRange(a)).toEqual([2.5, 4]);
    expect(riskTodayLine(a, gbp)).toBe("Risk per trade today: ≈£2.50–£4.00 (why: high volatility: halved)");
  });

  it("shows one figure when every strategy trades the base and hides the line when dynamic limits are off", () => {
    expect(riskTodayLine(acct(), gbp)).toBe("Risk per trade today: ≈£5.00 (why: no cut today)");
    expect(rangeText([5, 5.001], gbp)).toBe("£5.00");
    expect(riskTodayLine(acct({ risk_today: null }), gbp)).toBeNull();
    expect(riskTodayLine(acct({ day_start_equity: null, balance: null }), gbp)).toBe("Risk per trade today: not known (why: no cut today)");
  });

  it("gives the money left before the daily limit; red only when a cap is spent", () => {
    expect(leftToday(acct())).toBe(40);
    expect(leftState(acct(), gbp)).toEqual({ tone: "ok", text: "£40.00 left today before the daily loss limit" });
    expect(leftState(acct({ daily_used: 40 }), gbp).tone).toBe("warn");
    expect(leftState(acct({ daily_used: 60 }), gbp)).toMatchObject({ tone: "bad" });
    expect(leftToday(acct({ daily_used: 60 }))).toBe(0);
    expect(leftState(acct({ cap_hit: "weekly" }), gbp).text).toMatch(/Weekly loss limit spent/);
    expect(leftState(acct({ daily_limit: null }), gbp).tone).toBe("idle");
  });

  it("prefers the binding cap of the account's loss budget (it may be the all-accounts cap)", () => {
    const b = { limit: 22.5, remaining: 12.5, binding_label: "all accounts (1.5% of today's start)", period: "today" };
    expect(leftState(acct(), gbp, b)).toEqual({ tone: "ok", text: "£12.50 left today before the binding limit: all accounts (1.5% of today's start)" });
    expect(leftState(acct(), gbp, { ...b, remaining: 0 }).tone).toBe("bad");
    expect(leftState(acct(), gbp, { ...b, unknown: "supervisor silent" }).text).toBe("£40.00 left today before the daily loss limit");
  });
});
