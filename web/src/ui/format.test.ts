import { describe, expect, it } from "vitest";
import { lots, points, seconds, signedR, withUnit, NBSP } from "./format";

// Backlog 95: a number and its unit are one word; at 360 px they must never split across lines.
describe("number-unit formatters", () => {
  it("join the number and the unit with a no-break space", () => {
    expect(signedR(0.56)).toBe(`+0.56${NBSP}R`);
    expect(signedR(-0.4)).toBe(`−0.40${NBSP}R`);
    expect(points(22)).toBe(`22${NBSP}pt`);
    expect(lots(0.1)).toBe(`0.1${NBSP}lots`);
    expect(lots(1)).toBe(`1${NBSP}lot`);
    expect(seconds(75)).toBe(`75${NBSP}s`);
    expect(withUnit("5", "%")).toBe(`5${NBSP}%`);
  });
  it("never contain an ordinary space between number and unit", () => {
    for (const s of [signedR(1.5), points(3), lots(2), seconds(9)]) expect(s).not.toMatch(/\d [A-Za-z]/);
  });
  it("show a dash for a missing R", () => { expect(signedR(null)).toBe("—"); });
});
