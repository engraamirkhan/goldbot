// Multi-instrument UI spec 2026-11-02: sym/grp URL round trip, the grouped instrument filter, per-symbol entry state
// grouped by reason with tones, group exposure in the fixed order with "cap not published", movers only on a
// multi-instrument account, "By instrument" on Today.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { apiQuery, DEFAULT_FILTERS, filtersQuery, parseFilters } from "../lib/trades";
import type { LimitAccount } from "../lib/limits";
import { EntrySymbols } from "./EntrySymbols";
import { GroupExposureCard } from "./GroupExposureCard";
import { TopMovers } from "./TopMovers";
import { ByInstrument } from "./trades/Today";
import { SymbolFilter } from "./trades/SymbolFilter";

const NOW = Date.UTC(2026, 10, 3, 14, 0, 0);

describe("instrument filters in the URL", () => {
  it("round-trips sym= and grp= and sends them to the API", () => {
    const f = { ...DEFAULT_FILTERS, sym: ["US500", "XAGUSD"], grp: ["fx" as const] };
    const q = filtersQuery(f);
    expect(q).toBe("sym=US500,XAGUSD&grp=fx");
    expect(parseFilters(q)).toEqual(f);
    expect(apiQuery(f)).toContain("symbol=US500&symbol=XAGUSD&group=fx");
  });

  it("drops an unknown group and a malformed symbol from a hand-edited link", () => {
    const f = parseFilters("sym=US500,../x,_y&grp=fx,bonds");
    expect(f.sym).toEqual(["US500"]);
    expect(f.grp).toEqual(["fx"]);
  });
});

describe("the grouped instrument filter", () => {
  it("offers the facet grouped in the fixed order and a group header picks the whole group", () => {
    const set = vi.fn();
    render(<SymbolFilter f={DEFAULT_FILTERS} set={set} symbols={["EURUSD", "XAUUSD", "US500", "USDJPY"]} />);
    const groups = screen.getAllByRole("group").map((g) => g.getAttribute("aria-label")).filter(Boolean);
    expect(groups).toEqual(["Gold instruments", "Equity instruments", "FX instruments"]);
    fireEvent.click(within(screen.getByRole("group", { name: "FX instruments" })).getByRole("checkbox", { name: "FX" }));
    expect(set).toHaveBeenCalledWith({ grp: ["fx"], sym: [] });
  });

  it("unticking one symbol of a picked group keeps the rest of the group", () => {
    const set = vi.fn();
    render(<SymbolFilter f={{ ...DEFAULT_FILTERS, grp: ["fx"] }} set={set} symbols={["EURUSD", "USDJPY"]} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /EURUSD/ }));
    expect(set).toHaveBeenCalledWith({ grp: [], sym: ["USDJPY"] });
  });
});

describe("per-symbol entry state", () => {
  it("counts who can enter, groups the rest by reason with their tone, and expands to all", () => {
    render(<EntrySymbols now={NOW} symbols={[
      { symbol: "XAUUSD", state: "can_enter", text: "Can enter" }, { symbol: "EURUSD", state: "can_enter", text: "Can enter" },
      { symbol: "US500", state: "market_closed", text: "Market closed", clears_at: "2026-11-03T14:30:00Z" },
      { symbol: "DE40", state: "group_cap", text: "Equity cap spent" },
      { symbol: "USDJPY", state: "terms_missing", text: "Terms not loaded" },
      { symbol: "XNGUSD", state: "stale", text: "Feed stale 3 min" },
    ]} />);
    expect(screen.getByText(/2 of 6/)).toBeInTheDocument();
    expect(screen.getByText(/Group cap spent: DE40/).closest("li")).toHaveClass("bad");
    expect(screen.getByText(/Terms not loaded: entries refused, exits still close: USDJPY/).closest("li")).toHaveClass("warn");
    expect(screen.getByText(/Price feed stale: XNGUSD/).closest("li")).toHaveClass("warn");
    expect(screen.getByText(/Market closed: US500/).closest("li")).toHaveClass("muted");
    fireEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    expect(screen.getByText("Feed stale 3 min")).toBeInTheDocument();
  });
});

const limitAcct = (over: Partial<LimitAccount>): LimitAccount => ({
  account_id: "icm-probe", currency: "GBP", updated_utc: new Date(NOW).toISOString(), stale: false, groups: [], ...over,
} as LimitAccount);

describe("group exposure", () => {
  it("shows every group in order with a meter in words, lots by symbol and 'cap not published'", () => {
    const groups = (["gold", "metals", "equity", "fx", "energy", "rates", "ags"] as const).map((group) => ({
      group, notional: group === "fx" ? 1500 : 0, cap: group === "rates" ? null : 3000,
      lots_by_symbol: (group === "fx" ? { EURUSD: 0.01 } : {}) as Record<string, number>, open_n: group === "fx" ? 1 : 0 }));
    render(<GroupExposureCard a={limitAcct({ groups, heat_used: 4.2, heat_limit: 50 })} now={NOW} />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items.map((t) => t.split(" ")[0])).toEqual(["Gold", "Metals", "Equity", "FX", "Energy", "Rates", "Ags"]);
    expect(screen.getByRole("meter", { name: "FX notional against its cap" })).toHaveAttribute("aria-valuetext", "£1,500 of £3,000, 50%");
    expect(items[5]).toContain("cap not published");
    expect(screen.getByText(/EURUSD 0.01/)).toBeInTheDocument();
    expect(screen.getByText(/Heat at stops/).parentElement?.textContent).toContain("£4.20 of £50");
  });

  it("is absent on a single-symbol engine", () => {
    const { container } = render(<GroupExposureCard a={limitAcct({})} now={NOW} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("movers and by instrument", () => {
  const rows = [
    { symbol: "US500", day_pnl: 2.1, closed_pnl: 2.1, open_pnl: 0, closed_n: 2, open_n: 0 },
    { symbol: "XAGUSD", day_pnl: -5, closed_pnl: -5, open_pnl: 0, closed_n: 1, open_n: 0 },
    { symbol: "EURUSD", day_pnl: 0.4, closed_pnl: 0, open_pnl: 0.4, closed_n: 0, open_n: 1 },
    { symbol: "XAUUSD", day_pnl: 0.1, closed_pnl: 0.1, open_pnl: 0, closed_n: 1, open_n: 0 },
  ];

  it("shows three movers with a sign and an arrow and links to the account's day", () => {
    render(<TopMovers account="icm-probe" rows={rows} instrumentsN={19} currency="GBP" now={NOW} />);
    const slots = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(slots).toEqual(["XAGUSD ▼ −£5.00", "US500 ▲ +£2.10", "EURUSD ▲ +£0.40"]);
    expect(screen.getByRole("link")).toHaveAttribute("href", "#/trades/today?acct=icm-probe");
    expect(screen.getByRole("link")).toHaveTextContent("4 of 19 instruments traded · all →");
  });

  it("shows no movers for a single-instrument account", () => {
    const { container } = render(<TopMovers account="icm-demo" rows={[rows[3]]} instrumentsN={null} currency="GBP" now={NOW} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the day by instrument, by net or by group", () => {
    render(<ByInstrument rows={rows} ccy="GBP" label="Scalp lab" />);
    const firstCol = () => screen.getAllByRole("row").slice(1).map((r) => r.querySelector(".symbol-tag-ticker")?.textContent);
    expect(firstCol()).toEqual(["XAGUSD", "US500", "EURUSD", "XAUUSD"]);
    fireEvent.click(screen.getByRole("tab", { name: "Group" }));
    expect(firstCol()).toEqual(["XAUUSD", "XAGUSD", "US500", "EURUSD"]);
  });
});
