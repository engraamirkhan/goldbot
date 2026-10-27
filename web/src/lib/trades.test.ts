import { describe, expect, it } from "vitest";
import { board, openPositions } from "../test/tradesFixtures";
import { parseRoute, routeHash } from "./route";
import {
  apiQuery, bandWords, DEFAULT_FILTERS, filtersQuery, groupOpen, heldWords, kindBadge, orderBoard, parseFilters, rowName,
  totalsLine, type BoardFamily,
} from "./trades";
import { tradeRow } from "../test/tradesFixtures";

const NB = " ";

describe("trades routes", () => {
  it("redirects the old tabs and keeps the trade id and the filters", () => {
    expect(parseRoute("#/trades/open").tab).toBe("now");
    expect(parseRoute("#/trades/activity").tab).toBe("list");
    expect(parseRoute("#/trades/summary").tab).toBe("today");
    const r = parseRoute("#/trades/list/exploration%3Aicm-demo%3A41?acct=icm-probe&fam=box_break_close");
    expect(r).toEqual({ section: "trades", tab: "list", sub: "exploration:icm-demo:41", query: "acct=icm-probe&fam=box_break_close" });
    expect(parseRoute(routeHash("trades", "list", "a:b:1", "fam=x"))).toMatchObject({ tab: "list", sub: "a:b:1", query: "fam=x" });
    expect(parseRoute("#/nowhere/list?x=1")).toEqual({ section: "home", tab: null, sub: null, query: "" });
  });
});

describe("List filters live in the URL", () => {
  it("round-trips and writes only what differs from the defaults", () => {
    expect(filtersQuery(DEFAULT_FILTERS)).toBe("");
    const f = parseFilters("acct=icm-probe&fam=box_break_close,opening_range&tf=15m&status=closed&kind=control&period=7d");
    expect(f).toMatchObject({ acct: ["icm-probe"], fam: ["box_break_close", "opening_range"], tf: ["15m"], status: "closed", kind: ["control"], period: "7d" });
    expect(parseFilters(filtersQuery(f))).toEqual(f);
  });

  it("drops unknown kinds and periods instead of passing them on", () => {
    expect(parseFilters("kind=bogus,real&period=1y&status=maybe")).toMatchObject({ kind: ["real"], period: "today", status: "" });
  });

  it("asks the API for every kind but Shadow by default, 50 a page, with the cursor", () => {
    const q = apiQuery(DEFAULT_FILTERS, "c2");
    expect(q).toContain("kind=real,strategy,exploration,control,cost_probe");
    expect(q).not.toContain("shadow");
    expect(q).toContain("limit=50");
    expect(q).toContain("cursor=c2");
  });
});

describe("ADR 0050 in the client", () => {
  it("a blinded totals line shows count and costs only, and says why", () => {
    const t = totalsLine({ n: 64, net_pnl: null, costs: 3.2, currency: "GBP", blinded: true, unread_arms: 1, excluded: [] });
    expect(t.text).toBe("64 trades · costs £3.20");
    expect(t.note).toMatch(/Results hidden: this view holds 1 unread arm \(fewer than 3\)/);
  });

  it("an open totals line shows net after costs", () => {
    const t = totalsLine({ n: 412, net_pnl: 18.4, costs: 22.1, currency: "GBP", blinded: false, unread_arms: 12, excluded: ["Vantage lab"] });
    expect(t.text).toBe("412 trades · net +£18.40 after costs · costs £22.10 · excl. Vantage lab");
    expect(t.note).toBeNull();
  });

  it("never words a band as near its stop (only running or retired once fired)", () => {
    expect(bandWords("near_stop")).toBe("running");
    expect(bandWords("running")).toBe("running");
    expect(bandWords("retired_at_stop")).toBe("retired at its stop");
  });

  it("orders the board by state and closeness to the read, never by result", () => {
    const fams = board().families;
    expect(orderBoard(fams).map((f) => f.family)).toEqual(["opening_range", "box_break_close", "random_bracket"]);
    // two running families: the one closer to its read first, whatever their figures
    const a: BoardFamily = { ...fams[1], family: "a", family_label: "A", variants: [{ ...fams[1].variants[1], n_counted: 50 }] };
    const b: BoardFamily = { ...fams[1], family: "b", family_label: "B", variants: [{ ...fams[1].variants[1], n_counted: 250 }] };
    expect(orderBoard([a, b]).map((f) => f.family)).toEqual(["b", "a"]);
  });

  it("a blinded row's accessible name hides its result", () => {
    expect(rowName(tradeRow({ blinded: true, pnl: null, r: null }))).toContain("result hidden until the arm's read");
    expect(rowName(tradeRow())).toBe("Box break + close 15m, long, plus £2.10, closed at target");
  });
});

describe("Now groups", () => {
  it("one group per account with risk and P&L totals; real money first", () => {
    const v = openPositions().positions;
    const live = { ...v[0], account: "icm-live", account_label: "Live (ICM)", mode: "live" as const, position_id: 9 };
    const g = groupOpen([...v, live]);
    expect(g.map((x) => x.label)).toEqual(["Live (ICM)", "Day/swing lab", "Scalp lab"]);
    const scalp = g[2];
    expect(scalp.risk).toBeCloseTo(3.1);
    expect(scalp.pnl).toBeCloseTo(0.5);
    expect(scalp.rows[0].risk_amount).toBe(2);           // largest risk first
  });

  it("refuses a partial total when any row's money is unknown, and flags it once", () => {
    const v = openPositions().positions.map((p) => (p.position_id === 2 ? { ...p, floating_pnl: null } : p));
    const scalp = groupOpen(v).find((x) => x.label === "Scalp lab")!;
    expect(scalp.pnl).toBeNull();
    expect(scalp.noRate).toBe(true);
  });
});

describe("words", () => {
  it("time held against the hold limit", () => {
    expect(heldWords(240, 600)).toBe(`4 of 10${NB}min`);
    expect(heldWords(22 * 60, null)).toBe(`22${NB}min`);
    expect(heldWords(null, 600)).toBe("—");
  });
  it("badges every kind but Strategy, in words", () => {
    expect(kindBadge("strategy")).toBeNull();
    expect(kindBadge("control")).toBe("Control");
    expect(kindBadge("cost_probe")).toBe("Cost probe");
  });
});
