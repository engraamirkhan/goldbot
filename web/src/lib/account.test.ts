import { describe, expect, it } from "vitest";
import { budgetTone, cash, signedCash } from "./account";

describe("cash", () => {
  it("uses the account currency's symbol", () => {
    expect(cash(1000, "GBP")).toBe("£1,000.00");
    expect(cash(1330.5, "USD")).toBe("$1,330.50");
    expect(cash(12.3, "EUR")).toBe("€12.30");
  });
  it("shows a plain amount when the currency is unknown or not a code", () => {
    expect(cash(990, null)).toBe("990.00");
    expect(cash(990, "pounds")).toBe("990.00 pounds");
  });
  it("signs a change with a true minus", () => {
    expect(signedCash(-10, "GBP")).toBe("−£10.00");
    expect(signedCash(12, "GBP")).toBe("+£12.00");
    expect(signedCash(0, "GBP")).toBe("£0.00");
  });
});

describe("budgetTone", () => {
  it("is red only when the budget is spent (new entries stop), amber under a quarter left", () => {
    expect(budgetTone({ limit: 15, used: 15, remaining: 0 })).toBe("bad");
    expect(budgetTone({ limit: 15, used: 12, remaining: 3 })).toBe("warn");
    expect(budgetTone({ limit: 15, used: 2, remaining: 13 })).toBe("ok");
    expect(budgetTone(null)).toBe("idle");
  });
});
