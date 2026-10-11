import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Overview } from "./Overview";

const status = (over: Partial<apiModule.Status>): apiModule.Status => ({
  mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null, ...over,
});

function renderOverview(role: apiModule.Role) {
  apiModule.setUser({ email: "a@x.io", role });
  vi.spyOn(apiModule.api, "accounts").mockResolvedValue([]);
  vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));   // AutoMode.test.tsx covers the card
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Overview /></QueryClientProvider>);
}

describe("Overview accounts", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("shows a loading placeholder until the accounts arrive", async () => {
    apiModule.setUser({ email: "a@x.io", role: "viewer" });
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "accounts").mockReturnValue(new Promise(() => {}));
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Overview /></QueryClientProvider>);
    expect(await screen.findByLabelText("Loading account")).toHaveAttribute("aria-busy", "true");
  });
});

describe("Overview entry status", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("puts the entry status first, one line per account from the API", async () => {
    apiModule.setUser({ email: "a@x.io", role: "viewer" });
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([{
      account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
      stage: "normal", open_positions: 0, account_class: "raw",
      entry_status: { state: "open", headline: "Can take new trades", blocks: [], notes: [], as_of: null,
                      exits: "Exits keep running: open trades keep their stop, target and automatic exits." } }]);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={qc}><Overview /></QueryClientProvider>);
    const row = await screen.findByRole("article", { name: "Entry status icm-demo" });
    expect(row).toHaveTextContent("Can take new trades");
    expect(container.querySelector("section > *")).toHaveClass("entry");
  });

  it("shows the empty state when no engine reports", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    renderOverview("viewer");
    expect(await screen.findByText(/No engine is reporting, so no new trades can open/)).toBeInTheDocument();
  });
});

describe("Overview halt control", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("lets an approver halt new entries with a reason", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    const halt = vi.spyOn(apiModule.api, "halt").mockResolvedValue(status({ halted: true, halted_by: "dashboard:a@x.io", halt_reason: "fomc" }));
    renderOverview("approver");
    fireEvent.change(await screen.findByLabelText("Halt reason"), { target: { value: "fomc" } });
    fireEvent.click(screen.getByRole("button", { name: "Halt new entries" }));
    await waitFor(() => expect(halt).toHaveBeenCalledWith("fomc"));
    expect(await screen.findByText(/Entries halted by dashboard:a@x.io: fomc/)).toBeInTheDocument();
    expect(screen.getByText("Only the owner can re-arm.")).toBeInTheDocument();
  });

  it("re-arms only for the owner with an authenticator code", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({ halted: true, halted_by: "telegram:42" }));
    const rearm = vi.spyOn(apiModule.api, "rearm").mockResolvedValue(status({}));
    renderOverview("owner");
    const button = await screen.findByRole("button", { name: "Re-arm" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(button);
    await waitFor(() => expect(rearm).toHaveBeenCalledWith("123456"));
    expect(await screen.findByRole("button", { name: "Halt new entries" })).toBeInTheDocument();
  });

  it("shows viewers no halt button", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    renderOverview("viewer");
    expect(await screen.findByText("propose")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Halt new entries" })).toBeNull();
  });
});
