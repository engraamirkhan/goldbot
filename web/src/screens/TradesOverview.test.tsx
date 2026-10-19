import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { stats } from "../test/tradeStats";
import { TradesSummaryCard } from "./TradesOverview";

const type = (t: apiModule.TradeTypeSummary["type"], s = stats(), fam = {}, tf = {}): apiModule.TradeTypeSummary =>
  ({ type: t, stats: s, by_family: fam, by_timeframe: tf });

const overview: apiModule.TradesOverview = { as_of: "2026-10-19T14:00:00Z", unreadable: [], periods: [
  { period: "today", since: "2026-10-18T22:00:00Z", types: [
    type("shadow_taken", stats({ open: 1, closed: 1, wins: 1, win_rate: 1, n_r: 1, total_r: 1.5 }),
         { box_break_close: stats({ open: 1, closed: 1, wins: 1, win_rate: 1, total_r: 1.5, exits: { target: 1, stop: 0, time: 0, other: 0 } }) },
         { "1h": stats({ open: 1, closed: 1, total_r: 1.5 }) }),
    type("shadow_all"), type("demo", stats({ open: 1 }), { tsmom: stats({ open: 1 }) }, { "4h": stats({ open: 1 }) }),
    type("live"), type("probe", stats({ closed: 1, losses: 1, win_rate: 0, net_pnl: { GBP: -2 }, total_r: -0.5 })),
  ] },
  { period: "7d", since: "2026-10-12T22:00:00Z", types: [
    type("demo", stats({ open: 1, closed: 2, wins: 1, losses: 1, win_rate: 0.5, net_pnl: { GBP: -9 } }),
         { mfi: stats({ closed: 1 }), tsmom: stats({ open: 1, closed: 1, net_pnl: { GBP: -9 } }) }),
  ] },
  { period: "all", since: null, types: [] },
] };

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TradesSummaryCard /></QueryClientProvider>);
}

describe("Trades summary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows one tile per trade type for today, with open, closed, win rate and the result", async () => {
    vi.spyOn(apiModule.api, "tradesOverview").mockResolvedValue(overview);
    renderCard();
    expect(await screen.findByRole("heading", { name: "Trades summary" })).toBeInTheDocument();
    expect((await screen.findByText("Shadow (taken)", { selector: ".metric-label" })).parentElement).toHaveTextContent("+1.50 R1 open · 1 closed · 100% won");
    expect(screen.getByText("Cost probes", { selector: ".metric-label" }).parentElement).toHaveTextContent("−£2.00");
    expect(screen.getAllByText(/^(Shadow|Demo|Live|Cost)/, { selector: ".metric-label" })).toHaveLength(5);
  });

  it("switches period and breakdown", async () => {
    vi.spyOn(apiModule.api, "tradesOverview").mockResolvedValue(overview);
    renderCard();
    const table = await screen.findByRole("table", { name: "Trades by strategy and timeframe" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);                 // header + box_break_close + tsmom
    fireEvent.click(screen.getByRole("tab", { name: "7 days" }));
    expect(screen.getByRole("table")).toHaveTextContent("−£9.00");
    fireEvent.click(screen.getByRole("tab", { name: "By timeframe" }));
    expect(screen.getByText("No trades in this period")).toBeInTheDocument();     // the 7d fixture has no timeframes
    fireEvent.click(screen.getByRole("tab", { name: "Today" }));
    expect(screen.getByRole("table")).toHaveTextContent("4h");
  });

  it("asks again every 30 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const spy = vi.spyOn(apiModule.api, "tradesOverview").mockResolvedValue(overview);
      renderCard();
      await screen.findByRole("heading", { name: "Trades summary" });
      await screen.findByRole("table");
      expect(spy).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(spy).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("names a source it could not read as missing, not zero", async () => {
    vi.spyOn(apiModule.api, "tradesOverview").mockResolvedValue({ ...overview, unreadable: ["shadow"] });
    renderCard();
    expect(await screen.findByText(/Couldn't read: shadow trades. Those trades are missing, not zero./)).toBeInTheDocument();
  });

  it("shows an error that says trading is not affected", async () => {
    vi.spyOn(apiModule.api, "tradesOverview").mockRejectedValue(new Error("down"));
    renderCard();
    expect(await screen.findByRole("alert")).toHaveTextContent("Trading is not affected");
  });
});
