import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { TvIndicatorStatus, TvStatusView } from "../lib/limits";
import { TradingViewCard, TradingViewChip } from "./TradingViewCard";

const FIX = "python -m goldbot.ops.accounts set tradingview-sessionid && python -m goldbot.ops.accounts set tradingview-sessionid-sign";
const ind = (over: Partial<TvIndicatorStatus> = {}): TvIndicatorStatus => ({
  indicator: "Supertrend", version: "7", symbol: "XAUUSD", tf: "15", last_bar_time: "", alerts_24h: 0,
  plots_seen: ["plot_0"], gaps_24h: 0, csv_bars: 0, csv_rows: 0, repaint_status: "validated", pull_live_24h: 288,
  pull_history_bars: 18000, ...over,
});
const tv = (over: Partial<TvStatusView> = {}): TvStatusView => ({
  now: new Date().toISOString(), indicators: [ind()], signin: "ok",
  last_pull_utc: new Date(Date.now() - 8 * 3600_000).toISOString(), stale: false, fix_command: null,
  live_bars_24h: 288, history_bars: 18000, ...over,
});

function renderIt(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

describe("TradingView card", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says signed in with the last pull and counts per indicator", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv());
    renderIt(<TradingViewCard />);
    expect(await screen.findByText("Signed in")).toHaveClass("ok");
    expect(screen.getByText(/Last successful pull: .*8 h ago/)).toBeInTheDocument();
    expect(screen.getByText(/Indicators found: 1/)).toBeInTheDocument();
    expect(screen.getAllByText("Passed: history matches live").length).toBeGreaterThan(0);
    expect(screen.getAllByText("18,000").length).toBeGreaterThan(0);
  });

  it("an expired cookie is amber, says trading is not affected and copies the one-line fix", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({ signin: "expired", fix_command: FIX }));
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    renderIt(<TradingViewCard />);
    expect(await screen.findByText("Expired")).toHaveClass("warn");
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/TradingView sign-in expired\. Trading is not affected\./);
    expect(status).toHaveTextContent(/no new TradingView data arrives/);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenCalledWith(FIX);
    expect(document.querySelector(".bad")).toBeNull();
  });

  it("an unreadable sign-in is amber unknown, treated as expired", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({ signin: "unknown", fix_command: FIX }));
    renderIt(<TradingViewCard />);
    expect(await screen.findByText("Unknown")).toHaveClass("warn");
    expect(screen.getByRole("status")).toHaveTextContent(/could not be read/);
  });

  it("not set up is grey and says there is nothing to do", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({ signin: "not_set_up", last_pull_utc: null, indicators: [],
                                                                   live_bars_24h: 0, history_bars: 0 }));
    renderIt(<TradingViewCard />);
    expect(await screen.findByText("Not set up")).toHaveClass("idle");
    expect(screen.getByText(/TradingView is optional; nothing to do/)).toBeInTheDocument();
  });

  it("says when no pull has happened for over 36 hours", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({
      stale: true, last_pull_utc: new Date(Date.now() - 40 * 3600_000).toISOString() }));
    renderIt(<TradingViewCard />);
    expect(await screen.findByText(/No pull for 40 h; one is expected daily\./)).toBeInTheDocument();
  });

  it("says when no indicator has arrived yet", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({ indicators: [], live_bars_24h: 0, history_bars: 0 }));
    renderIt(<TradingViewCard />);
    expect(await screen.findByText("No indicators yet. Alerts or a chart export have not arrived.")).toBeInTheDocument();
  });

  it("counts missed bars in words", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({ indicators: [ind({ gaps_24h: 3 })] }));
    renderIt(<TradingViewCard />);
    expect((await screen.findAllByText("3 missed bars in 24 h")).length).toBeGreaterThan(0);
  });

  it("shows an error without failing the page", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockRejectedValue(new apiModule.ApiError(500, "boom"));
    renderIt(<TradingViewCard />);
    expect(await screen.findByText("Could not read TradingView status")).toBeInTheDocument();
  });

  it("shows a skeleton while loading", () => {
    vi.spyOn(apiModule.api, "tradingview").mockReturnValue(new Promise(() => {}));
    renderIt(<TradingViewCard />);
    expect(screen.getByLabelText("Loading TradingView status")).toHaveAttribute("aria-busy", "true");
  });

  it("never renders plot names or values", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv());
    const { container } = renderIt(<TradingViewCard />);
    await screen.findByText("Signed in");
    expect(container.textContent).not.toMatch(/plot_0/);
  });

  it("the Overview chip is amber when expired, never red", async () => {
    vi.spyOn(apiModule.api, "tradingview").mockResolvedValue(tv({ signin: "expired", fix_command: FIX }));
    renderIt(<TradingViewChip />);
    const chip = await screen.findByText("TradingView: expired");
    expect(chip.closest(".chip")).toHaveClass("warn");
  });
});
