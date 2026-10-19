/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

/** Red (`className="err"`) is reserved for what stops trading or a refused action the owner just took (backlog 100).
 *  Every remaining use is listed with its reason; a new one fails here until it is converted to the amber ErrorState
 *  or added with a reason. `convert` marks a load error that does not stop trading, owed to the lane that owns the file. */
const ALLOWED: Record<string, { n: number; why: string }> = {
  "screens/CodeSignInsBanner.tsx": { n: 1, why: "the owner's action failed (red: they must act)" },
  "screens/Health.tsx": { n: 1, why: "convert: load error, trading unaffected" },
  "screens/News.tsx": { n: 2, why: "convert: load errors, trading unaffected" },
  "screens/AutoMode.tsx": { n: 2, why: "action failure, and convert: load error (AutoMode lane)" },
  "screens/ProposalCard.tsx": { n: 1, why: "the approval or rejection failed: red" },
  "screens/Login.tsx": { n: 3, why: "sign-in field errors" },
  "screens/Chart.tsx": { n: 2, why: "convert: chart and layout load errors, trading unaffected" },
  "screens/Account.tsx": { n: 1, why: "the owner's account action failed" },
  "screens/Approvals.tsx": { n: 1, why: "convert: waiting-trades load error, Telegram still works (approvals lane)" },
  "screens/Research.tsx": { n: 1, why: "convert: load error (Research lane)" },
  "screens/Limits.tsx": { n: 2, why: "convert: load error (Limits lane)" },
  "screens/Users.tsx": { n: 1, why: "the owner's user action failed" },
  "screens/HaltControl.tsx": { n: 2, why: "halt or re-arm failed: stops-trading control, red" },
};

const sources = import.meta.glob<string>(["./**/*.tsx", "!./**/*.test.tsx"], { query: "?raw", import: "default", eager: true });

describe("red error paragraphs", () => {
  it("every className=\"err\" use is listed with a reason, and Home has none", () => {
    const found: Record<string, number> = {};
    for (const [f, text] of Object.entries(sources)) {
      const n = (text.match(/className="err"/g) ?? []).length;
      if (n) found[f.slice(2)] = n;
    }
    expect(found).toEqual(Object.fromEntries(Object.entries(ALLOWED).map(([k, v]) => [k, v.n])));
  });
});
