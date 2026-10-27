import { describe, expect, it } from "vitest";
import { board, openPositions } from "../test/tradesFixtures";
import { parseRoute, routeHash } from "./route";
import {
  apiQuery, bandWords, DEFAULT_FILTERS, filtersQuery, groupOpen, heldWords, kindBadge, openTarget, orderBoard, parseFilters, rowName,
  totalsLine, type BoardFamily,
} from "./trades";
import { blindedPage, tradeRow, tradesPage } from "../test/tradesFixtures";

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
    expect(parseFilters("kind=bogus,real&period=1y&status=maybe&exit=time,stop")).toMatchObject({ kind: ["real"], period: "today", status: "", exit: ["stop"] });
  });

  it("asks the API with its own parameter names, repeated; every kind but Shadow by default; 50 a page", () => {
    const q = new URLSearchParams(apiQuery({ ...DEFAULT_FILTERS, acct: ["icm-probe"], fam: ["a", "b"], exit: ["time_limit"], status: "closed" }, "c2"));
    expect(q.get("period")).toBe("today");
    expect(q.getAll("account")).toEqual(["icm-probe"]);
    expect(q.getAll("family")).toEqual(["a", "b"]);
    expect(q.getAll("exit_reason")).toEqual(["time_limit"]);
    expect(q.getAll("kind")).toEqual(["real", "strategy", "exploration", "control", "cost_probe"]);
    expect(q.get("status")).toBe("closed");
    expect(q.get("limit")).toBe("50");
    expect(q.get("cursor")).toBe("c2");
  });
});

describe("ADR 0050 in the client", () => {
  it("withheld totals show the counts only, with the server's reason", () => {
    const t = totalsLine(blindedPage().totals);
    expect(t.text).toBe("64 trades");
    expect(t.note).toBe("Totals hidden: the filter narrows to strategies whose arms are not read yet (ADR 0050): totals return at the read.");
  });

  it("totals as served: net after costs, costs and open risk; mixed currencies never summed", () => {
    const t = totalsLine(tradesPage().totals);
    expect(t.text).toBe("412 trades (3 open) · net +£18.40 after costs · costs £22.10 · open risk £8.50");
    expect(t.note).toBeNull();
    const m = totalsLine({ ...tradesPage().totals, net_pnl: null, costs: null, open_risk: null, currency: null, mixed_currencies: true });
    expect(m.text).toBe("412 trades (3 open)");
    expect(m.note).toBe("Money not summed: more than one currency.");
  });

  it("an engine position opens its ledger trade; an arm's open trade opens the List filtered to it", () => {
    const [arm, , strat] = openPositions().positions;
    expect(openTarget(strat)).toEqual({ id: "position:icm-probe:3" });
    expect(openTarget(arm)).toEqual({ query: "acct=icm-demo&fam=box_break_close&status=open" });
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
