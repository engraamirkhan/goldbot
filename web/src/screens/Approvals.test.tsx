import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Proposal } from "../lib/api";
import * as apiModule from "../lib/api";
import { Approvals } from "./Approvals";

const proposal: Proposal = {
  proposal_id: "p1", account_id: "icm-demo", agent_id: "session_open-g0-abc", side: "long", lots: 0.05,
  entry: 2400.25, stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22,
  top_features: [["atr_14", 0.4], ["adx_14", -0.2]], expires_at: new Date(Date.now() + 60_000).toISOString(),
};

function renderWithClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Approvals /></QueryClientProvider>);
}

describe("Approvals", () => {
  beforeEach(() => {
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal]);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    apiModule.setUser(null);
  });

  it("shows the proposal with its levels and top features", async () => {
    apiModule.setUser({ email: "v@x.io", role: "viewer" });
    renderWithClient();
    expect(await screen.findByText(/LONG 0.05 lots/)).toBeInTheDocument();
    expect(screen.getByText("2396.00")).toBeInTheDocument();
    expect(screen.getByText(/atr_14 \+0.40, adx_14 -0.20/)).toBeInTheDocument();
  });

  it("does not offer approve to a viewer", async () => {
    apiModule.setUser({ email: "v@x.io", role: "viewer" });
    renderWithClient();
    await screen.findByText(/LONG 0.05 lots/);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByText(/Viewer role/)).toBeInTheDocument();
  });

  it("lets an approver approve and reject with a reason", async () => {
    apiModule.setUser({ email: "a@x.io", role: "approver" });
    const decide = vi.spyOn(apiModule.api, "decide").mockResolvedValue({ outcome: "APPROVED" });
    renderWithClient();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("p1", "approve", undefined));
    await userEvent.click(screen.getByRole("button", { name: "Reject: cost" }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("p1", "reject", "cost"));
  });

  it("counts the approval window down without a refetch", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      apiModule.setUser({ email: "v@x.io", role: "viewer" });
      renderWithClient();
      const before = Number((await screen.findByText(/s left/)).textContent?.replace("s left", ""));
      await act(async () => { vi.advanceTimersByTime(5_000); });
      const after = Number(screen.getByText(/s left/).textContent?.replace("s left", ""));
      expect(before - after).toBeGreaterThanOrEqual(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so when nothing is waiting", async () => {
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    renderWithClient();
    expect(await screen.findByText(/No proposals waiting/)).toBeInTheDocument();
  });
});
