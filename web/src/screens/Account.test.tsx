import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Account } from "./Account";

const CODES = Array.from({ length: 10 }, (_, i) => `abcd-ef${i}0`);

describe("Account recovery codes", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("is offered to the owner only", () => {
    apiModule.setUser({ email: "v@x.io", role: "approver", signed_in_with: "password_totp" });
    render(<Account />);
    expect(screen.getByRole("heading", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recovery codes" })).toBeNull();
  });

  it("needs the password, a code and the confirmation, then shows the new codes once with copy and download", async () => {
    apiModule.setUser({ email: "o@x.io", role: "owner", signed_in_with: "password_totp" });
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
    apiModule.setUser({ email: "o@x.io", role: "owner", signed_in_with: "password_totp" });
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

// The only place a non-owner sees the address they signed in with (docs/ux/header-role.md, BACKLOG 64).
describe("Account: your sign-in", () => {
  afterEach(() => { apiModule.setUser(null); });

  it("comes first and lists the owner's role and email", () => {
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "password_totp" });
    render(<Account />);
    const mine = screen.getByRole("region", { name: "Your sign-in" });
    expect(within(mine).getByText("Owner")).toBeInTheDocument();
    expect(within(mine).getByText("owner@example.com")).toBeInTheDocument();
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings[0]).toBe("Your sign-in");
    expect(headings[1]).toBe("Change password");
  });

  it("shows a viewer their own role and email", () => {
    apiModule.setUser({ email: "v@x.io", role: "viewer", signed_in_with: "password_totp" });
    render(<Account />);
    const mine = screen.getByRole("region", { name: "Your sign-in" });
    expect(within(mine).getByText("Viewer")).toBeInTheDocument();
    expect(within(mine).getByText("v@x.io")).toBeInTheDocument();
  });

  it("says the email is not known in this tab when the session record is missing", () => {
    apiModule.setUser(null);
    render(<Account />);
    const mine = screen.getByRole("region", { name: "Your sign-in" });
    expect(within(mine).getByText(/not known in this tab: sign out and in again to see it/)).toBeInTheDocument();
    expect(mine.textContent).not.toContain("@");
  });
});

// docs/ux/sign-in-email-code.md State H: a neutral (never red) row, owner only; email codes do not stop trading.
describe("Account: email codes row", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });
  const state = (email_code: boolean) =>
    vi.spyOn(apiModule.api, "authState").mockResolvedValue({ needs_setup: false, users: 1, email_link: false, code_sign_in: email_code, code_channel: "mail" });

  it("tells the owner email codes are off, and why, in a neutral row when mail is not set up", async () => {
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "password_totp" });
    state(false);
    render(<Account />);
    const row = await screen.findByText(/^Email codes: off\./);
    expect(row).toHaveTextContent(
      'Email codes: off. Mail is not set up on the server (RUNBOOK 2.10, "Email sign-in"). Sign-in uses your authenticator app.');
    expect(row).not.toHaveClass("err");
    expect(row).not.toHaveAttribute("role", "alert");
  });

  it("tells the owner email codes are on and what still needs the authenticator", async () => {
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "email_code" });
    state(true);
    render(<Account />);
    expect(await screen.findByText(/^Email codes: on\./)).toHaveTextContent(
      "Email codes: on. You can sign in with your password and a code sent to your email. Re-arm, auto mode, " +
      "password change, recovery codes and managing users still need your authenticator.");
  });

  it("is not shown to an approver", async () => {
    apiModule.setUser({ email: "a@example.com", role: "approver", signed_in_with: "password_totp" });
    const s = state(true);
    render(<Account />);
    await screen.findByRole("heading", { name: "Change password" });
    expect(s).not.toHaveBeenCalled();
    expect(screen.queryByText(/Email codes:/)).toBeNull();
  });
});
