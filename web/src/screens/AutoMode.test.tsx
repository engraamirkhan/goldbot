import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { AutoModeCard } from "./AutoMode";

const view = (over: Partial<apiModule.AutoModeView> = {}): apiModule.AutoModeView => ({
  owner_mode: null, mode_by: null, mode_since: null, mode_reason: null, engine_modes: { "icm-demo": "propose" },
  eligible: false, reasons: ["37 decided proposals since the last mode change; 100 needed"], decided: 37, approved: 30, rejected: 7,
  min_proposals: 100, min_outcomes_per_side: 10, breaches: [],
  test: { n_approved: 20, n_rejected: 5, mean_r_approved: 0.2, mean_r_rejected: -0.1, diff: 0.3, ci_low: -0.1, ci_high: 0.7,
    p_value: 0.21, alpha: 0.1, indistinguishable: true },
  forced_propose: [], can_enable: false, error: null, ...over,
});

const ELIGIBLE = view({ eligible: true, reasons: [], decided: 120, approved: 70, rejected: 50, can_enable: true });

function renderCard(role: apiModule.Role) {
  apiModule.setUser({ email: "o@x.io", role });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AutoModeCard /></QueryClientProvider>);
}

describe("Auto-mode card", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("shows the mode, why auto is not offered and the evidence, read-only for a viewer", async () => {
    vi.spyOn(apiModule.api, "automode").mockResolvedValue(view());
    renderCard("viewer");
    expect(screen.getByRole("status")).toHaveTextContent("Loading entry mode");
    expect(await screen.findByRole("heading", { name: /Entry mode: Propose-and-approve/ })).toBeInTheDocument();
    expect(screen.getByText("auto mode not offered")).toBeInTheDocument();
    expect(screen.getByText("37 decided proposals since the last mode change; 100 needed")).toBeInTheDocument();
    expect(screen.getByText(/37 of 100 needed/)).toBeInTheDocument();
    expect(screen.getByText(/\[−0.10R, \+0.70R\]/)).toBeInTheDocument();
    expect(screen.getByText("Only the owner can change the mode.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable auto mode" })).toBeNull();
  });

  it("shows that the re-arm lock and the kill switch still force propose", async () => {
    vi.spyOn(apiModule.api, "automode").mockResolvedValue(view({ forced_propose: [
      { account_id: "icm-demo", kind: "rearm_lock", detail: "propose-and-approve for 30 days after a re-arm", until: "2026-11-01T00:00:00Z" },
      { account_id: "icm-live", kind: "kill_switch", detail: "12% drawdown kill switch: halted and back to propose until re-armed", until: null }] }));
    renderCard("owner");
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("Entries wait for your click on these accounts whatever the mode says");
    expect(note).toHaveTextContent("icm-demo: propose-and-approve for 30 days after a re-arm until");
    expect(note).toHaveTextContent("icm-live: 12% drawdown kill switch");
  });

  it("lets the owner enable auto mode only with a 6-digit authenticator code", async () => {
    vi.spyOn(apiModule.api, "automode").mockResolvedValue(ELIGIBLE);
    const set = vi.spyOn(apiModule.api, "setMode").mockResolvedValue({ message: "Mode: AUTO. Entries that pass the RiskGate are placed without asking you.",
      view: { ...ELIGIBLE, owner_mode: "auto", can_enable: false } });
    renderCard("owner");
    expect(await screen.findByText("auto mode offered")).toBeInTheDocument();
    expect(screen.getByText(/places every entry that passes the RiskGate without asking you/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Enable auto mode" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Code to enable auto mode"), { target: { value: "12a34" } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Code to enable auto mode"), { target: { value: "123456" } });
    fireEvent.click(button);
    await waitFor(() => expect(set).toHaveBeenCalledWith("auto", "123456"));
    expect(await screen.findByRole("heading", { name: /Entry mode: Auto/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Mode: AUTO");
    expect(screen.getByRole("button", { name: "Switch to propose" })).toBeInTheDocument();
  });

  it("shows a refused code or missing evidence from the server", async () => {
    vi.spyOn(apiModule.api, "automode").mockResolvedValue(ELIGIBLE);
    vi.spyOn(apiModule.api, "setMode").mockRejectedValue(new Error("authenticator code required to enable auto mode"));
    renderCard("owner");
    fireEvent.change(await screen.findByLabelText("Code to enable auto mode"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Enable auto mode" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("authenticator code required");
  });

  it("switches back to propose with no code", async () => {
    vi.spyOn(apiModule.api, "automode").mockResolvedValue(view({ owner_mode: "auto", engine_modes: { "icm-demo": "auto" } }));
    const set = vi.spyOn(apiModule.api, "setMode").mockResolvedValue({ message: "Mode: propose-and-approve.", view: view({ owner_mode: "propose" }) });
    renderCard("owner");
    fireEvent.click(await screen.findByRole("button", { name: "Switch to propose" }));
    await waitFor(() => expect(set).toHaveBeenCalledWith("propose"));
    expect(await screen.findByRole("heading", { name: /Entry mode: Propose-and-approve/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Code to enable auto mode")).toBeNull();
  });

  it("reports a load failure", async () => {
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("boom"));
    renderCard("owner");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the entry mode (boom)");
  });
});
