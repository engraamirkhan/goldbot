import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { ResearchView } from "../lib/explain";
import { Research } from "./Research";

function renderResearch() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Research /></QueryClientProvider>);
}

const EMPTY: ResearchView = {
  generated_utc: "2026-10-10T08:00:00Z", budget: { quarter: "2026Q4", budget: 20, used: 0, left: 20 }, trials: [], trials_total: 0,
  plan: null, plan_error: null,
  hypotheses: { path: "docs/research/hypotheses.md", updated_utc: null, tables: [], note: "hypotheses.md not found next to the API" },
};

const gates = [{ name: "dsr", passed: false, detail: "deflated Sharpe n/a" }, { name: "events", passed: true, detail: "1498 events" }];
const FULL: ResearchView = {
  ...EMPTY,
  budget: { quarter: "2026Q4", budget: 20, used: 20, left: 0 },
  trials_total: 2,
  trials: [
    { trial: 2, ts: "2026-10-05T00:00:00Z", family: "tsmom", timeframe: "1h", status: "evaluated", agent_id: "tsmom-x", gross_r: 0.061,
      gross_t: 2.63, net_r: -0.079, net_t: -2.1, n: 900, gates_passed: false, gates, rationale: "H-01 slow TSMOM" },
    { trial: 1, ts: "2026-10-01T00:00:00Z", family: "tsmom", timeframe: "1h", status: "holdout", agent_id: "tsmom-x", gross_r: null,
      gross_t: null, net_r: null, net_t: null, n: null, gates_passed: true,
      gates: [{ name: "holdout", passed: true, detail: "mean R > 0" }], rationale: "holdout" },
  ],
  plan: { created_utc: "2026-09-01T00:00:00Z", stale: true, quarter: "2026Q4", quarter_budget: 20, quarter_used: 2, total_budget: 18,
    budget: { tsmom: 10 }, grid_budget: { tsmom: 0 }, unallocated: 0, holdout_from: "2025-10-01", holdout_to: "2026-10-01",
    focus: [{ rank: 1, family: "tsmom", budget: 10, evidence: 1.2, reasons: ["gross t 2.63"] }],
    evidence: [{ family: "tsmom", trials: 2, evidence: 1.2, blocked: false, flags: ["few_filtered_trades"], median_auc: 0.52, best_dsr: null, shadow_trades: 0 }] },
  hypotheses: { path: "docs/research/hypotheses.md", updated_utc: "2026-10-10T00:00:00Z", note: null, tables: [
    { title: "A. Ranked portfolio", columns: ["Rank", "ID", "Hypothesis"], rows: [["1", "H-01", "Slow TSMOM: 1d signal"]] },
    { title: "B. Retired", columns: ["ID", "Idea"], rows: [["R-01", "mean_reversion"]] },
  ] },
};

describe("Research", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the empty states before any research has run", async () => {
    vi.spyOn(apiModule.api, "research").mockResolvedValue(EMPTY);
    renderResearch();
    expect(screen.getByRole("status")).toHaveTextContent("Loading research");
    expect(await screen.findByText("0 of 20 used · 20 left")).toBeInTheDocument();
    expect(screen.getByText(/No plan yet/)).toBeInTheDocument();
    expect(screen.getByText(/No trials recorded yet/)).toBeInTheDocument();
    expect(screen.getByText("hypotheses.md not found next to the API")).toBeInTheDocument();
  });

  it("shows the budget, the registry with gates, the plan and the hypothesis tables", async () => {
    vi.spyOn(apiModule.api, "research").mockResolvedValue(FULL);
    renderResearch();
    const meter = await screen.findByRole("meter", { name: "2026Q4 trials used" });
    expect(meter).toHaveAttribute("aria-valuetext", "20 of 20 trials used, 0 left");
    expect(screen.getByText("20 of 20 used · 0 left")).toHaveClass("state", "bad");

    const table = screen.getAllByRole("table").find((t) => t.classList.contains("trials"))!;
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("+0.061 (t 2.63)");
    expect(rows[1]).toHaveTextContent("−0.079 (t -2.10)");
    expect(within(rows[1]).getByText("failed: dsr")).toHaveAttribute("title", expect.stringContaining("FAIL dsr: deflated Sharpe n/a"));
    expect(within(rows[2]).getByText("passed")).toBeInTheDocument();

    expect(screen.getByText(/more than 21 days old/)).toBeInTheDocument();
    expect(screen.getByText("gross t 2.63")).toBeInTheDocument();
    expect(screen.getByText("few_filtered_trades")).toBeInTheDocument();
    expect(screen.getByText("Slow TSMOM: 1d signal")).toBeVisible();
    expect(screen.getByText("B. Retired")).toBeInTheDocument();
  });

  it("reports a corrupt plan instead of the empty state", async () => {
    vi.spyOn(apiModule.api, "research").mockResolvedValue({ ...EMPTY, plan_error: "research_plan.json unreadable (KeyError)" });
    renderResearch();
    expect(await screen.findByText(/research_plan.json unreadable/)).toBeInTheDocument();
    expect(screen.queryByText(/No plan yet/)).not.toBeInTheDocument();
  });
});
