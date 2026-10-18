import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { validLayoutUrl } from "../lib/chart";
import { Chart } from "./Chart";

const GOOD = "https://www.tradingview.com/chart/AbCd1234/";
const view = (over: Partial<apiModule.ChartLayoutsView> = {}): apiModule.ChartLayoutsView => ({ layouts: [], can_edit: true, ...over });

function renderChart() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Chart /></QueryClientProvider>);
}
const frame = () => screen.getByTestId("tv-widget") as HTMLIFrameElement;
const config = () => JSON.parse(decodeURIComponent(new URL(frame().src).hash.slice(1)));

describe("Chart tab", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the widget for OANDA:XAUUSD and changes interval", async () => {
    vi.spyOn(apiModule.api, "chartLayouts").mockResolvedValue(view());
    renderChart();
    expect(screen.getByTestId("tv-widget")).toBeInTheDocument();
    expect(frame().src).toMatch(/^https:\/\/www\.tradingview-widget\.com\/embed-widget\/advanced-chart\//);
    expect(frame().getAttribute("sandbox")).toBe("allow-scripts allow-same-origin allow-popups");
    expect(frame().getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(document.querySelector("script[src*=tradingview]")).toBeNull();
    expect(config()).toMatchObject({ symbol: "OANDA:XAUUSD", interval: "15" });
    fireEvent.click(screen.getByRole("button", { name: "4h" }));
    expect(screen.getByRole("button", { name: "4h" })).toHaveAttribute("aria-pressed", "true");
    expect(config().interval).toBe("240");
    expect(screen.getByText(/Your invite-only indicators show in TradingView itself; their signals appear here once alerts are connected\./)).toBeInTheDocument();
    await screen.findByRole("button", { name: "Open my TradingView chart" });
  });

  it("shows plain text when the widget script is blocked", async () => {
    vi.spyOn(apiModule.api, "chartLayouts").mockResolvedValue(view());
    vi.useFakeTimers();
    try {
      renderChart();
      act(() => { vi.advanceTimersByTime(1000); });
      expect(screen.queryByText(/The live chart could not load/)).toBeNull();
      act(() => { vi.advanceTimersByTime(15000); });
      expect(screen.getByText(/The live chart could not load/)).toBeInTheDocument();
      fireEvent.load(frame());
      expect(screen.queryByText(/The live chart could not load/)).toBeNull();
    } finally { vi.useRealTimers(); }
  });

  it("keeps the open button disabled until a layout is saved, then opens it safely", async () => {
    vi.spyOn(apiModule.api, "chartLayouts").mockResolvedValue(view());
    vi.spyOn(apiModule.api, "setChartLayouts").mockResolvedValue(view({ layouts: [{ name: "Gold", url: GOOD }] }));
    renderChart();
    expect(await screen.findByRole("button", { name: "Open my TradingView chart" })).toBeDisabled();
    fireEvent.click(screen.getByText("Add my saved TradingView layout"));
    fireEvent.change(screen.getAllByLabelText("Name")[0], { target: { value: "Gold" } });
    fireEvent.change(screen.getAllByLabelText("TradingView link")[0], { target: { value: GOOD } });
    fireEvent.click(screen.getByRole("button", { name: "Save layouts" }));
    const link = await screen.findByRole("link", { name: /Open my TradingView chart/ });
    expect(link).toHaveAttribute("href", GOOD);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(apiModule.api.setChartLayouts).toHaveBeenCalledWith([{ name: "Gold", url: GOOD }], "");
  });

  it("rejects a link that is not a TradingView chart before calling the server", async () => {
    vi.spyOn(apiModule.api, "chartLayouts").mockResolvedValue(view());
    const save = vi.spyOn(apiModule.api, "setChartLayouts");
    renderChart();
    fireEvent.click(await screen.findByText("Add my saved TradingView layout"));
    fireEvent.change(screen.getAllByLabelText("Name")[0], { target: { value: "X" } });
    fireEvent.change(screen.getAllByLabelText("TradingView link")[0], { target: { value: "https://evil.io/chart/AbCd1234/" } });
    fireEvent.click(screen.getByRole("button", { name: "Save layouts" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Layout 1: the link must look like");
    await waitFor(() => expect(save).not.toHaveBeenCalled());
  });

  it("gives a non-owner the chart but no layout links or editor", async () => {
    vi.spyOn(apiModule.api, "chartLayouts").mockResolvedValue(view({ can_edit: false, layouts: [{ name: "Gold", url: null }] }));
    renderChart();
    await waitFor(() => expect(apiModule.api.chartLayouts).toHaveBeenCalled());
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText(/saved TradingView layout/)).toBeNull();
    expect(screen.getByTestId("tv-widget")).toBeInTheDocument();
  });

  it("validates layout URLs", () => {
    expect(validLayoutUrl(GOOD)).toBe(true);
    for (const bad of ["http://www.tradingview.com/chart/AbCd1234/", "https://www.tradingview.com.evil.io/chart/AbCd1234/", "https://www.tradingview.com/chart/", ""])
      expect(validLayoutUrl(bad)).toBe(false);
  });
});
