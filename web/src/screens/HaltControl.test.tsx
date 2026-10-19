import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { HaltControl } from "./HaltControl";

// Moved from Home.test.tsx: Halt now lives in the header pill's menu and Settings (docs/ux/dashboard-v2.md section 6).
const status = (over: Partial<apiModule.Status>): apiModule.Status => ({
  mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null, ...over,
});

function Host() {
  const q = useQuery({ queryKey: ["status"], queryFn: api.status });
  return q.data ? <HaltControl status={q.data} /> : null;
}
const { api } = apiModule;

function renderIt(role: apiModule.Role) {
  apiModule.setUser({ email: "a@x.io", role, signed_in_with: "password_totp" });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Host /></QueryClientProvider>);
}

describe("HaltControl", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("lets an approver halt new entries with a reason", async () => {
    vi.spyOn(api, "status").mockResolvedValue(status({}));
    const halt = vi.spyOn(api, "halt").mockResolvedValue(status({ halted: true, halted_by: "dashboard", halt_reason: "fomc" }));
    renderIt("approver");
    fireEvent.change(await screen.findByLabelText("Halt reason"), { target: { value: "fomc" } });
    fireEvent.click(screen.getByRole("button", { name: "Halt new entries" }));
    await waitFor(() => expect(halt).toHaveBeenCalledWith("fomc"));
    expect(await screen.findByText("Entries halted via the dashboard: fomc.")).toBeInTheDocument();
    expect(screen.getByText("Only the owner can re-arm.")).toBeInTheDocument();
  });

  it("re-arms only for the owner with an authenticator code", async () => {
    vi.spyOn(api, "status").mockResolvedValue(status({ halted: true, halted_by: "telegram" }));
    const rearm = vi.spyOn(api, "rearm").mockResolvedValue(status({}));
    renderIt("owner");
    expect(await screen.findByText("Entries halted via Telegram.")).toBeInTheDocument();
    const button = await screen.findByRole("button", { name: "Re-arm" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(button);
    await waitFor(() => expect(rearm).toHaveBeenCalledWith("123456"));
    expect(await screen.findByRole("button", { name: "Halt new entries" })).toBeInTheDocument();
  });

  it("shows viewers no halt button", async () => {
    vi.spyOn(api, "status").mockResolvedValue(status({}));
    renderIt("viewer");
    await waitFor(() => expect(api.status).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Halt new entries" })).toBeNull();
  });
});
