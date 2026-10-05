import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Feeds } from "./Feeds";

const job = (over: Partial<apiModule.JobRow>): apiModule.JobRow => ({
  name: "nightly_costs", last_slot: "2026-10-01T23:10:00Z", last_finished: "2026-10-01T23:11:00Z", last_ok: true,
  last_error: null, next_slot: "2026-10-02T23:10:00Z", runs: 3, failures: 0, heartbeat_age_s: 12, ...over,
});

function renderFeeds() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Feeds /></QueryClientProvider>);
}

describe("Feeds", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists scheduler jobs and flags failures and a silent scheduler", async () => {
    vi.spyOn(apiModule.api, "feeds").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "jobs").mockResolvedValue([
      job({}),
      job({ name: "saturday_retrain", last_ok: false, last_error: "RuntimeError: no bars", failures: 1 }),
      job({ name: "monthly_research", last_ok: null, runs: 0, last_slot: null, last_finished: null, heartbeat_age_s: 900 }),
    ]);
    renderFeeds();
    expect(await screen.findByText("saturday_retrain")).toBeInTheDocument();
    expect(screen.getByText("RuntimeError: no bars").closest("tr")).toHaveClass("bad");
    expect(screen.getByText("ok").closest("tr")).not.toHaveClass("bad");
    expect(screen.getByText("scheduler silent").closest("tr")).toHaveClass("bad");
  });

  it("says when the scheduler has not reported", async () => {
    vi.spyOn(apiModule.api, "feeds").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "jobs").mockResolvedValue([]);
    renderFeeds();
    expect(await screen.findByText(/scheduler has not reported/)).toBeInTheDocument();
  });
});
