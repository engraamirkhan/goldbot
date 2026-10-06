import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Overview } from "./Overview";

const status = (over: Partial<apiModule.Status>): apiModule.Status => ({
  mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {}, ...over,
});

function renderOverview(role: apiModule.Role) {
  apiModule.setUser({ email: "a@x.io", role });
  vi.spyOn(apiModule.api, "accounts").mockResolvedValue([]);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Overview /></QueryClientProvider>);
}

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
