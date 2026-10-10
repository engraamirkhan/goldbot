import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DecidedProposal, Proposal, Status } from "../lib/api";
import * as apiModule from "../lib/api";
import { Approvals } from "./Approvals";

const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  proposal_id: "p1", account_id: "icm-demo", agent_id: "session_open-g0-abc", side: "long", lots: 0.05,
  entry: 2400.25, stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22, risk_usd: 21.25,
  top_features: [["atr_14", 0.4], ["adx_14", -0.2]], expires_at: new Date(Date.now() + 60_000).toISOString(), ...over,
});
const status: Status = { mode: "propose", halted: false, pending: 1, supervisor: {}, supervisor_halt: false, supervisor_reasons: [],
                         drift_halt: false, drift_reasons: [], blackout: null };

function renderWithClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Approvals /></QueryClientProvider>);
}

describe("Approvals", () => {
  beforeEach(() => {
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal()]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status);
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([{ account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1e4,
      day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0, stage: "normal", open_positions: 0, account_class: "raw" }]);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    apiModule.setUser(null);
  });

  it("shows the safety strip above the proposal card", async () => {
    apiModule.setUser({ email: "v@x.io", role: "viewer" });
    renderWithClient();
    expect(await screen.findByText("New entries allowed")).toBeInTheDocument();
    expect(await screen.findByText("LONG")).toBeInTheDocument();
    expect(screen.getByText("2396.00")).toBeInTheDocument();
    expect(screen.getByText(/Volatility \(ATR 14\)/)).toBeInTheDocument();
  });

  it("does not offer approve to a viewer", async () => {
    apiModule.setUser({ email: "v@x.io", role: "viewer" });
    renderWithClient();
    await screen.findByText("LONG");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByText(/Viewer role/)).toBeInTheDocument();
  });

  it("lets an approver approve and reject with a reason", async () => {
    apiModule.setUser({ email: "a@x.io", role: "approver" });
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal(), proposal({ proposal_id: "p2", side: "short" })]);
    const decide = vi.spyOn(apiModule.api, "decide").mockResolvedValue({ outcome: "SUBMITTED" });
    renderWithClient();
    const cards = await screen.findAllByRole("article");
    await userEvent.click(within(cards[0]!).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("p1", "approve", undefined));
    await userEvent.click(within(cards[1]!).getByRole("button", { name: "Reject…" }));
    await userEvent.click(within(cards[1]!).getByRole("button", { name: "Cost" }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("p2", "reject", "cost"));
  });

  it("shows a refused decision on the card", async () => {
    apiModule.setUser({ email: "a@x.io", role: "approver" });
    vi.spyOn(apiModule.api, "decide").mockRejectedValue(new Error("proposal expired"));
    renderWithClient();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("proposal expired");
  });

  it("counts the 90 s window down without a refetch, turns urgent, then expires", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      apiModule.setUser({ email: "a@x.io", role: "approver" });
      vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal({ expires_at: new Date(Date.now() + 20_000).toISOString() })]);
      renderWithClient();
      const secs = () => Number(screen.getByRole("timer").getAttribute("aria-label")?.split(" ")[0]);
      await screen.findByRole("timer");
      const before = secs();
      await act(async () => { vi.advanceTimersByTime(5_000); });
      expect(before - secs()).toBeGreaterThanOrEqual(4);
      expect(screen.getByRole("article")).toHaveClass("expiring");
      await act(async () => { vi.advanceTimersByTime(16_000); });
      expect(screen.getByRole("article")).toHaveClass("expired");
      expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("lists recently decided proposals once, as decided", async () => {
    const done: DecidedProposal = { ...proposal(), status: "rejected", reason_code: "news", refusal: [] };
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([done]);
    renderWithClient();
    expect(await screen.findByText("Decided in the last 10 minutes")).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByText(/No proposals waiting/)).toBeInTheDocument();
    expect(screen.getByText(/· News/)).toBeInTheDocument();
  });

  it("says so when nothing is waiting", async () => {
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    renderWithClient();
    expect(await screen.findByText(/No proposals waiting/)).toBeInTheDocument();
  });
});
