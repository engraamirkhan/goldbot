import { describe, expect, it } from "vitest";
import { roleLabel } from "./roles";

describe("roleLabel", () => {
  it("spells each role as one capitalised word, the same everywhere it is shown", () => {
    expect(roleLabel("owner")).toBe("Owner");
    expect(roleLabel("approver")).toBe("Approver");
    expect(roleLabel("viewer")).toBe("Viewer");
  });
});
