import { describe, expect, it } from "vitest";
import { openPositions, tradeBars } from "../test/tradesFixtures";
import { parseRoute, routeHash } from "./route";
import { chartLevels } from "./tradeChart";
import {
  apiQuery, DEFAULT_FILTERS, etaWords, filtersQuery, groupOpen, heldWords, kindBadge, openTarget, parseFilters, rowName, statusWords,
  totalsLine,
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

  it("unknown nets are never a total: 'net incomplete: N trades without costs yet', the known sum labelled", () => {
    const t = totalsLine({ ...tradesPage().totals, net_pnl: null, net_unknown: 3, net_partial: 12.4 });
    expect(t.text).toBe("412 trades (3 open) · net incomplete: 3 trades without costs yet (known trades +£12.40) · costs £22.10 · open risk £8.50");
    expect(totalsLine({ ...blindedPage().totals, net_unknown: 2 }).text).toBe("64 trades");   // withheld wins: nothing partial leaks
  });

  it("an engine position opens its ledger trade; an arm's open trade opens the List filtered to it", () => {
    const [arm, , strat] = openPositions().positions;
    expect(openTarget(strat)).toEqual({ id: "position:icm-probe:3" });
    expect(openTarget(arm)).toEqual({ query: "acct=icm-demo&fam=box_break_close&status=open" });
  });

  it("never words an arm as near its stop (only warming, running, read or retired once fired)", () => {
    expect(statusWords("near_stop")).toBe("running");
    expect(statusWords("running")).toBe("running");
    expect(statusWords("retired")).toBe("retired at its stop");
    expect(statusWords("warming")).toBe("warming up: no counted trade yet");
  });

  it("an ETA most of a year away carries its year; a near one does not", () => {
    const far = new Date(Date.now() + 2 * 365 * 86_400_000);
    expect(etaWords(far.toISOString())).toContain(String(far.getFullYear()));
    const near = new Date(Date.now() + 10 * 86_400_000);
    expect(etaWords(near.toISOString())).not.toMatch(/\d{4}/);
    expect(etaWords(null)).toBe("read date not known yet");
  });

  it("a moved stop draws the first stop and the last journalled move; an unmoved one a single stop", () => {
    const moved = chartLevels(tradeRow({ stop_now: null }), tradeBars().stop_moves).map((l) => `${l.word} ${l.value}`);
    expect(moved).toEqual(["target 4024", "entry 4012.4", "first stop 4006.1", "last stop 4012.4", "exit 4024"]);
    const still = chartLevels(tradeRow({ stop_now: null })).map((l) => l.word);
    expect(still).toEqual(["target", "entry", "stop", "exit"]);
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
    expect(scalp).not.toHaveProperty("pnl");              // no client sum of open P&L (the server may withhold it)
    expect(scalp.rows[0].risk_amount).toBe(2);           // largest risk first
  });

  it("refuses a partial total when any row's money is unknown, and flags it once", () => {
    const v = openPositions().positions.map((p) => (p.position_id === 2 ? { ...p, floating_pnl: null } : p));
    const scalp = groupOpen(v).find((x) => x.label === "Scalp lab")!;
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
