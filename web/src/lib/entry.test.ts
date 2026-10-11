import { describe, expect, it } from "vitest";
import type { AccountSummary, EntryStatus } from "./api";
import { BADGE, TONE, entryBlockers, whenText } from "./entry";

// local wall clock, so the test reads the same in every time zone
const at = (y: number, mo: number, d: number, h: number, mi: number) => new Date(y, mo - 1, d, h, mi).getTime();
const NOW = at(2026, 10, 14, 14, 0);     // a Wednesday

describe("whenText", () => {
  it("shows today's time in the owner's zone with how long until it, and UTC for the hover", () => {
    const iso = new Date(at(2026, 10, 14, 14, 30)).toISOString();
    const w = whenText(iso, "until", NOW);
    expect(w.text).toBe("until 14:30 your time (in 30 min)");
    expect(w.utc).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$/);
  });

  it("names the weekday when it is not today and counts hours", () => {
    const iso = new Date(at(2026, 10, 18, 23, 2)).toISOString();
    expect(whenText(iso, "opens", NOW).text).toBe("opens Sun 23:02 your time (in 4 d 9 h)");
    const soon = new Date(at(2026, 10, 14, 16, 15)).toISOString();
    expect(whenText(soon, "resets", NOW).text).toBe("resets 16:15 your time (in 2 h 15 min)");
  });

  it("says how long ago for a past time", () => {
    const iso = new Date(at(2026, 10, 14, 13, 55)).toISOString();
    expect(whenText(iso, "since", NOW).text).toBe("since 13:55 your time (5 min ago)");
  });
});

describe("badges", () => {
  it("never relies on colour alone: every state has a word", () => {
    expect(BADGE).toEqual({ open: "Open", partial: "Partly open", waiting: "Paused", blocked: "Blocked" });
    expect(TONE.blocked).toBe("bad");
    expect(TONE.waiting).toBe("warn");
    expect(TONE.open).toBe("ok");
  });
});

const es = (over: Partial<EntryStatus> = {}): EntryStatus => ({
  state: "open", headline: "Can take new trades", blocks: [], notes: [], exits: "Exits keep running.", as_of: null, ...over,
});
const acct = (id: string, entry_status: EntryStatus | null): AccountSummary => ({
  account_id: id, broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
  stage: "normal", open_positions: 0, account_class: "raw", entry_status,
});
const blk = (code: string, text: string, over: Partial<EntryStatus["blocks"][number]> = {}) => ({
  code, text, fix: "x", kind: "wait" as const, scope: "account" as const, agent_id: null, at: null, at_label: null,
  runbook: null, runbook_url: null, ...over,
});

describe("entryBlockers", () => {
  it("reads the API's reasons, and a strategy's own block only for that strategy's proposal", () => {
    const s = es({ state: "partial", blocks: [blk("throttle_loss_streak", "trend-g0 paused", { scope: "agent", agent_id: "trend-g0" })] });
    expect(entryBlockers([acct("icm-demo", s)], "icm-demo", "trend-g0")).toEqual(["trend-g0 paused"]);
    expect(entryBlockers([acct("icm-demo", s)], "icm-demo", "tsmom-g0")).toEqual([]);
    expect(entryBlockers([acct("icm-demo", s)])).toEqual([]);
  });

  it("names the account when several report, and is null when the server does not send the status", () => {
    const closed = es({ state: "waiting", blocks: [blk("market_closed", "Market closed")] });
    expect(entryBlockers([acct("a", closed), acct("b", es())])).toEqual(["Market closed (a)"]);
    expect(entryBlockers([acct("a", closed)])).toEqual(["Market closed"]);
    expect(entryBlockers([acct("a", null)])).toBeNull();
    expect(entryBlockers([])).toBeNull();
  });
});
