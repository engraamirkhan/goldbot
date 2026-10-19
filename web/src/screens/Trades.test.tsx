import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { TradesActivity, TradesOpen, TradesSummary } from "./Trades";

const status: apiModule.Status = { mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null };

function renderIt(ui: ReactNode) {
  apiModule.setUser({ email: "a@x.io", role: "viewer", signed_in_with: "password_totp" });
  const free = <K extends keyof typeof apiModule.api>(k: K, v: unknown) => {
    if (!vi.isMockFunction(apiModule.api[k])) vi.spyOn(apiModule.api, k).mockResolvedValue(v as never);
  };
  free("status", status); free("accounts", []); free("proposals", []); free("recentProposals", []);
  free("openPositions", { as_of: "2026-10-19T10:00:00Z", unreadable: [], positions: [] });
  free("activity", { events: [], unreadable: [] });
  free("today", { as_of: "2026-10-19T10:00:00Z", accounts: [] });
  free("tradesOverview", { as_of: "2026-10-19T10:00:00Z", periods: [], unreadable: [] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

describe("Trades: Open", () => {
  it("shows the decisions, then each open trade with stop, target, R and floating money, probes badged", async () => {
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], positions: [
      { account: "icm-live", mode: "live", probe: false, position_id: 1, agent_id: "box_break_close-g0", family: "box_break_close",
        timeframe: "1h", side: "long", lots: 0.05, entry: 2400, stop: 2396, target: 2420, opened_utc: "2026-10-19T09:00:00Z",
        price: 2410, floating_pnl: 40, currency: "GBP", r_now: 2.5, age_s: 2, stale: false },
      { account: "icm-demo", mode: "demo", probe: true, position_id: 2, agent_id: "probe", family: "probe", timeframe: null,
        side: "short", lots: 0.01, entry: 2400, stop: 2404, target: 2390, opened_utc: "2026-10-19T09:30:00Z", price: 2401,
        floating_pnl: -1, currency: "GBP", r_now: -0.25, age_s: 2, stale: false }] });
    renderIt(<TradesOpen />);
    expect(await screen.findByText(/No proposals waiting/)).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Open trades" });
    expect(list).toHaveTextContent("REAL");
    expect(list).toHaveTextContent("+£40.00 · +2.50 R");
    expect(list).toHaveTextContent("entry 2,400.00 · stop 2,396.00 · target 2,420.00 USD/oz");
    expect(list).toHaveTextContent("probe");
    expect(screen.getByRole("heading", { name: "Open trades (2)" })).toBeInTheDocument();
  });

  it("keeps the decisions when the open trades fail to load", async () => {
    vi.spyOn(apiModule.api, "openPositions").mockRejectedValue(new Error("down"));
    renderIt(<TradesOpen />);
    expect(await screen.findByText("Could not load open trades.")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Decisions" })).toBeInTheDocument();
  });
});

describe("Trades: Activity", () => {
  it("lists refusals and probe trades as they happen", async () => {
    vi.spyOn(apiModule.api, "activity").mockResolvedValue({ unreadable: [], events: [
      { ts: "2026-10-19T10:00:00Z", kind: "shadow_opened", agent_id: "box_break_close-g0", timeframe: "1h", side: "long", entry: 2400 },
    ] } as apiModule.ActivityView);
    renderIt(<TradesActivity />);
    expect(await screen.findByRole("list", { name: "Activity" })).toHaveTextContent("Shadow opened");
    expect(screen.getByText(/Refused entries are the risk check doing its job/)).toBeInTheDocument();
  });
});

describe("Trades: Summary", () => {
  it("holds today's money in full and the trades summary", async () => {
    renderIt(<TradesSummary />);
    expect(await screen.findByRole("heading", { name: "Trades summary" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Today" })).toBeInTheDocument();
  });
});
