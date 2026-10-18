import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { ResearchView, TrialRow } from "../lib/explain";
import { ResearchLine, ResearchTodayCard } from "./ResearchToday";

const trial = (over: Partial<TrialRow> = {}): TrialRow => ({
  trial: 41, ts: "2026-10-18T11:00:00Z", family: "trend", timeframe: "4h", status: "evaluated", agent_id: "trend-x",
  gross_r: 0.02, gross_t: 1.1, net_r: -0.03, net_t: -0.8, n: 300, gates_passed: false, pre_swap: false,
  gates: [{ name: "costs", passed: false, detail: "net -0.03 R" }], rationale: "4h trend, session filter", ...over,
});

export const today = (over: Partial<ResearchView["today"]> = {}): ResearchView["today"] => ({
  date_utc: "2026-10-18", run: 6, passed: 0, not_counted: 0, failed: 5, no_verdict: 1, latest: trial(),
  quarter_end: "2026-12-31", ...over,
});

const view = (t = today(), budget = { quarter: "2026Q4", budget: 60, used: 38, left: 22 }): ResearchView => ({
  generated_utc: "2026-10-18T15:00:00Z", budget, trials: [], trials_total: 0, plan: null, plan_error: null,
  hypotheses: { path: "docs/research/hypotheses.md", updated_utc: null, tables: [], note: null }, today: t,
});

describe("Research today", () => {
  afterEach(() => vi.restoreAllMocks());

  it("counts today's trials by verdict and the quarter's budget", () => {
    render(<ResearchTodayCard v={view()} />);
    const card = screen.getByRole("region", { name: "Research today" });
    expect(card).toHaveTextContent("Trials run: 6");
    expect(card).toHaveTextContent("Passed all gates: 0");
    expect(card).toHaveTextContent("Failed: 5");
    expect(card).toHaveTextContent("Needs more data: 1");
    expect(card).toHaveTextContent(/38 of 60 trials used this quarter \(22 left, quarter ends 31 Dec\)/);
    expect(card).toHaveTextContent(/Latest: "4h trend, session filter" — Failed: costs \(net -0\.03 R\)/);
  });

  it("names a pass without its costs as not counted", () => {
    render(<ResearchTodayCard v={view(today({ passed: 0, not_counted: 1, latest: trial({ pre_swap: true }) }))} />);
    expect(screen.getByText("Passed, but costs not recorded: not counted: 1")).toHaveClass("warn-text");
    expect(screen.getByText(/— Passed, but costs not recorded: not counted$/)).toBeInTheDocument();
  });

  it("warns in amber with 10% or less of the budget left", () => {
    render(<ResearchTodayCard v={view(today(), { quarter: "2026Q4", budget: 60, used: 55, left: 5 })} />);
    expect(screen.getByText(/Plan the rest carefully/)).toHaveClass("warn");
  });

  it("says when the budget is used up and until when", () => {
    render(<ResearchTodayCard v={view(today(), { quarter: "2026Q4", budget: 60, used: 60, left: 0 })} />);
    expect(screen.getByText(/Budget used up: no new trials until 1 Jan/)).toBeInTheDocument();
  });

  it("says when there were no trials today", () => {
    render(<ResearchTodayCard v={view(today({ run: 0, failed: 0, no_verdict: 0, latest: null }))} />);
    expect(screen.getByText("No trials today.")).toBeInTheDocument();
  });

  it("the Overview line repeats the counts in one sentence", async () => {
    vi.spyOn(apiModule.api, "research").mockResolvedValue(view());
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ResearchLine /></QueryClientProvider>);
    expect(await screen.findByText("Research today: 6 trials, 0 passed, 5 failed, 1 need more data. 38 of 60 used (22 left).")).toBeInTheDocument();
  });
});
