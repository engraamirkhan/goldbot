// UI spec 2026-11-02: "fmtPrice USDJPY 3 / EURUSD 5 / US500 2, no USD/oz off gold".
import { describe, expect, it } from "vitest";
import { fmtPrice, GROUP_ORDER, registerInstruments, stateTone, termsOf, TERMS, unitOf } from "./instruments";

const NBSP = " ";

describe("fmtPrice", () => {
  it("formats each instrument in its own digits", () => {
    expect(fmtPrice("USDJPY", 150.1234)).toBe("150.123");
    expect(fmtPrice("EURUSD", 1.084523)).toBe("1.08452");
    expect(fmtPrice("US500", 5012.5)).toBe("5,012.50");
    expect(fmtPrice("XAUUSD", 4012.4)).toBe("4,012.40");
    expect(fmtPrice("XAGUSD", 30.1)).toBe("30.100");
  });

  it("adds the unit only when asked, and never for an FX rate", () => {
    expect(fmtPrice("XAUUSD", 4012.4, { unit: true })).toBe(`4,012.40${NBSP}USD/oz`);
    expect(fmtPrice("US500", 5012.5, { unit: true })).toBe(`5,012.50${NBSP}pts`);
    expect(fmtPrice("EURUSD", 1.08452, { unit: true })).toBe("1.08452");
  });

  it("says USD/oz for gold and never for an instrument that is not a precious metal", () => {
    for (const [sym, t] of Object.entries(TERMS)) {
      const s = fmtPrice(sym, 1, { unit: true });
      if (t.group === "gold" || sym === "XAGUSD" || sym === "XPTUSD") continue;
      expect(s, sym).not.toMatch(/\/oz/);
    }
    expect(unitOf("USDJPY")).toBe("");
    expect(unitOf("US500")).not.toContain("oz");
  });

  it("is a dash without a price and two digits for an instrument it does not know", () => {
    expect(fmtPrice("US500", null)).toBe("—");
    expect(fmtPrice("US500", Number.NaN)).toBe("—");
    expect(fmtPrice("ODD1", 1.5)).toBe("1.50");
    expect(fmtPrice(null, 4012.4)).toBe("4,012.40");            // a row without a symbol is the account's gold
  });

  it("takes the server's digits over the snapshot once the list has answered", () => {
    registerInstruments([{ symbol: "ODD2", label: "Odd", account: "icm-probe", group: "fx", digits: 4, unit: "", quote_ccy: "USD", state: "can_enter" }]);
    expect(fmtPrice("ODD2", 1.5)).toBe("1.5000");
    expect(termsOf("ODD2").label).toBe("Odd");
  });
});

describe("groups and tones", () => {
  it("keeps the spec's fixed group order", () => {
    expect(GROUP_ORDER).toEqual(["gold", "metals", "equity", "fx", "energy", "rates", "ags"]);
  });

  it("is grey when closed or disabled, amber when stale or unknown, red only for a spent cap", () => {
    expect(stateTone("market_closed")).toBe("muted");
    expect(stateTone("disabled")).toBe("muted");
    expect(stateTone("stale")).toBe("warn");
    expect(stateTone("terms_missing")).toBe("warn");
    expect(stateTone("swap_unknown")).toBe("warn");
    expect(stateTone("unknown")).toBe("warn");
    expect(stateTone("group_cap")).toBe("bad");
    expect(stateTone("can_enter")).toBe("ok");
  });
});
