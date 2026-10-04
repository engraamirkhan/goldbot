import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Agents } from "./Agents";

function renderAgents() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Agents /></QueryClientProvider>);
}

describe("Agents", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the league sorted by fitness and the staff reports", async () => {
    vi.spyOn(apiModule.api, "agents").mockResolvedValue([
      { agent_id: "weak", family: "session_open", generation: 1, parent_id: null, status: "shadow", n_trades: 70,
        expectancy_r: 0.01, hit_rate: 0.4, calibration_ece: 0.1, fitness: 0.1, capital_weight: 0 },
      { agent_id: "strong", family: "session_open", generation: 0, parent_id: null, status: "live", n_trades: 120,
        expectancy_r: 0.12, hit_rate: 0.5, calibration_ece: 0.03, fitness: 0.9, capital_weight: 1 },
    ]);
    vi.spyOn(apiModule.api, "agentRuns").mockResolvedValue([
      { role: "risk_officer", started_utc: "2026-10-05T23:45:00Z", status: "ok", turns: 3, cost_usd: 0.21, detail: null,
        report: "Drawdown 2.1%, no limits tripped." },
      { role: "data_steward", started_utc: "2026-10-05T23:46:00Z", status: "monthly_cap", turns: 0, cost_usd: 0,
        detail: "monthly agent budget 40.00 USD used up", report: null },
    ]);
    renderAgents();
    const cells = await screen.findAllByRole("cell", { name: /^(strong|weak)$/ });
    expect(cells.map((c) => c.textContent)).toEqual(["strong", "weak"]);
    expect(screen.getByText("Risk officer")).toBeInTheDocument();
    expect(screen.getByText("monthly_cap")).toHaveClass("bad");
    await userEvent.click(screen.getByText("Risk officer"));
    expect(screen.getByText(/no limits tripped/)).toBeVisible();
  });
});
