import { describe, expect, it } from "vitest";
import type { KeyLevelZone } from "./api";
import { TF_OF_INTERVAL, bandOpacity, bandStroke, zoneLabel } from "./keyLevels";

const Z: KeyLevelZone = { price: 2412.5, lo: 2411, hi: 2414, strength: 9.5, strength_pct: 88, tfs: ["1h", "1d"], touches: 3, role: "resistance", flipped: false };

describe("key-level chart helpers", () => {
  it("draws a stronger zone with a thicker, more solid band", () => {
    expect(bandStroke(0)).toBe(1);
    expect(bandStroke(100)).toBe(4);
    expect(bandStroke(88)).toBeGreaterThan(bandStroke(40));
    expect(bandOpacity(90)).toBeGreaterThan(bandOpacity(10));
    expect(bandStroke(null)).toBe(1);
  });

  it("labels a zone with its side, price, timeframes and strength in words", () => {
    expect(zoneLabel(Z, true)).toBe("Resistance 2412.50 · 1h 1d · strength 88%");
    expect(zoneLabel({ ...Z, tfs: ["round"], strength_pct: null }, false)).toBe("Support 2412.50 · $ round · strength warming up");
    expect(zoneLabel({ ...Z, flipped: true }, false)).toBe("Support 2412.50 · 1h 1d · strength 88% · was resistance");
  });

  it("maps every chart interval to an API timeframe", () => {
    expect(TF_OF_INTERVAL).toEqual({ "15": "15m", "60": "1h", "240": "4h", D: "1d" });
  });
});
