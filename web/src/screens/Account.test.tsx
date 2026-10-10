import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Account } from "./Account";

const CODES = Array.from({ length: 10 }, (_, i) => `abcd-ef${i}0`);

describe("Account recovery codes", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("is offered to the owner only", () => {
    apiModule.setUser({ email: "v@x.io", role: "approver" });
    render(<Account />);
    expect(screen.getByRole("heading", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recovery codes" })).toBeNull();
  });

  it("needs the password, a code and the confirmation, then shows the new codes once with copy and download", async () => {
    apiModule.setUser({ email: "o@x.io", role: "owner" });
    const regen = vi.spyOn(apiModule.api, "recoveryCodes").mockResolvedValue({ recovery_codes: CODES });
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    const create = vi.fn().mockReturnValue("blob:x");
    Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});   // jsdom cannot navigate
    render(<Account />);
    expect(screen.getByText(/every code you saved before stops working at once/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Generate new recovery codes" });
    fireEvent.change(screen.getByLabelText("Your password"), { target: { value: "owner password 123" } });
    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "123456" } });
    expect(button).toBeDisabled();                                // not until the owner confirms the old set dies
    fireEvent.click(screen.getByLabelText(/My current recovery codes will stop working/));
    fireEvent.click(button);
    await waitFor(() => expect(regen).toHaveBeenCalledWith("owner password 123", "123456"));

    expect(await screen.findByRole("heading", { name: "Your new recovery codes" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Shown once");
    expect(screen.getByRole("alert")).toHaveTextContent("Your old codes no longer work");
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: "Copy all" }));
    await waitFor(() => expect(write).toHaveBeenCalledWith(CODES.join("\n")));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download .txt" }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "I have saved them" }));
    expect(screen.queryByText(CODES[0])).toBeNull();               // gone from the screen: never shown again
    expect(screen.getByRole("heading", { name: "Recovery codes" })).toBeInTheDocument();
  });

  it("shows a refusal and keeps the form", async () => {
    apiModule.setUser({ email: "o@x.io", role: "owner" });
    vi.spyOn(apiModule.api, "recoveryCodes").mockRejectedValue(new Error("invalid credentials"));
    render(<Account />);
    fireEvent.change(screen.getByLabelText("Your password"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "000000" } });
    fireEvent.click(screen.getByLabelText(/My current recovery codes will stop working/));
    fireEvent.click(screen.getByRole("button", { name: "Generate new recovery codes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid credentials");
    expect(screen.getByRole("button", { name: "Generate new recovery codes" })).toBeInTheDocument();
  });
});
