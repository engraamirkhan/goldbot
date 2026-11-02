import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Chart } from "./Chart";

function renderChart() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Chart /></QueryClientProvider>);
}

describe("chart tab after the TradingView chart path was retired (ADR 0066)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the MT5 chart and the key levels, with no TradingView widget or saved layouts", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:one", capturedAt: null, cropped: true, symbol: "XAUUSD", isGold: true });
    const keyLevels = vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue({ timeframe: "15m", account_id: null, feed: "broker", bars: [], accounts: [] });
    const { container } = renderChart();
    expect(screen.getByText(/MT5 · IC Markets/)).toBeInTheDocument();
    expect(screen.getByText(/Key levels · support and resistance/)).toBeInTheDocument();
    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.queryByText(/TradingView/)).toBeNull();
    await vi.waitFor(() => expect(keyLevels.mock.calls.map((c) => c[0])).toContain("15m"));
  });

  it("switches both charts to the chosen interval", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:one", capturedAt: null, cropped: true, symbol: "XAUUSD", isGold: true });
    const keyLevels = vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue({ timeframe: "4h", account_id: null, feed: "broker", bars: [], accounts: [] });
    renderChart();
    fireEvent.click(screen.getByRole("button", { name: "4h" }));
    expect(screen.getByRole("button", { name: "4h" })).toHaveAttribute("aria-pressed", "true");
    await vi.waitFor(() => expect(keyLevels.mock.calls.map((c) => c[0])).toContain("4h"));
  });
});
