import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Home } from "./Home";

const status = (over: Partial<apiModule.Status>): apiModule.Status => ({
  mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null, ...over,
});

function renderHome(role: apiModule.Role) {
  apiModule.setUser({ email: "a@x.io", role, signed_in_with: "password_totp" });
  vi.spyOn(apiModule.api, "accounts").mockResolvedValue([]);
  vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));   // AutoMode.test.tsx covers the card
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
}

describe("Home accounts", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("shows a loading placeholder until the accounts arrive", async () => {
    apiModule.setUser({ email: "a@x.io", role: "viewer", signed_in_with: "password_totp" });
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "accounts").mockReturnValue(new Promise(() => {}));
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
    expect(await screen.findByLabelText("Loading account")).toHaveAttribute("aria-busy", "true");
  });
});

describe("Home entry status", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("puts the entry status first, one line per account from the API", async () => {
    apiModule.setUser({ email: "a@x.io", role: "viewer", signed_in_with: "password_totp" });
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([{
      account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
      stage: "normal", open_positions: 0, account_class: "raw",
      entry_status: { state: "open", headline: "Can take new trades", blocks: [], notes: [], as_of: null,
                      exits: "Exits keep running: open trades keep their stop, target and automatic exits." } }]);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
    const row = await screen.findByRole("article", { name: "Entry status icm-demo" });
    expect(row).toHaveTextContent("Can take new trades");
    expect(container.querySelector(".entry")).not.toBeNull();
  });

  it("shows the empty state when no engine reports", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    renderHome("viewer");
    expect(await screen.findByText(/No engine is reporting, so no new trades can open/)).toBeInTheDocument();
  });
});

describe("Home waiting trade", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });
  const proposal = (): apiModule.Proposal => ({
    proposal_id: "p1", account_id: "icm-demo", agent_id: "session_open-g0-abc", side: "long", lots: 0.05,
    entry: 2400.25, stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22, risk_usd: 21.25, min_lot_exception: false,
    top_features: [], expires_at: new Date(Date.now() + 60_000).toISOString(), created: 1760000000.125,
  });

  it("shows the waiting trade itself with Approve", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal()]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    renderHome("approver");
    expect(await screen.findByRole("heading", { name: "Trade waiting" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("says no trade is waiting, and that Telegram will ping, when the list is empty", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    renderHome("viewer");
    expect(await screen.findByText(/No trade waiting\. You'll get a Telegram ping/)).toBeInTheDocument();
  });
});

describe("Home halt control", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("lets an approver halt new entries with a reason", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    const halt = vi.spyOn(apiModule.api, "halt").mockResolvedValue(status({ halted: true, halted_by: "dashboard", halt_reason: "fomc" }));
    renderHome("approver");
    fireEvent.change(await screen.findByLabelText("Halt reason"), { target: { value: "fomc" } });
    fireEvent.click(screen.getByRole("button", { name: "Halt new entries" }));
    await waitFor(() => expect(halt).toHaveBeenCalledWith("fomc"));
    expect(await screen.findByText("Entries halted via the dashboard: fomc.")).toBeInTheDocument();
    expect(screen.getByText("Only the owner can re-arm.")).toBeInTheDocument();
  });

  it("re-arms only for the owner with an authenticator code", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({ halted: true, halted_by: "telegram" }));
    const rearm = vi.spyOn(apiModule.api, "rearm").mockResolvedValue(status({}));
    renderHome("owner");
    expect(await screen.findByText("Entries halted via Telegram.")).toBeInTheDocument();
    const button = await screen.findByRole("button", { name: "Re-arm" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(button);
    await waitFor(() => expect(rearm).toHaveBeenCalledWith("123456"));
    expect(await screen.findByRole("button", { name: "Halt new entries" })).toBeInTheDocument();
  });

  it("shows viewers no halt button", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    renderHome("viewer");
    expect(await screen.findByLabelText("Safety state")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Halt new entries" })).toBeNull();
  });
});
