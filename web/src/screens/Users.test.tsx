import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { Me, UserRow } from "../lib/api";
import { Users } from "./Users";

// ADR 0028 ruling 2 (docs/ux/sign-in-email-code.md "Step-up screens"): after an email-code sign-in, invite, role,
// disable, enable, reset link and sign-out-everywhere also need the authenticator (or a recovery code). The server
// decides from the session's stored method; the screen asks for the code up front and sends it with the action.
const ROWS: UserRow[] = [
  { email: "owner@example.com", role: "owner", enabled: true, last_login: null },
  { email: "viewer@example.com", role: "viewer", enabled: true, last_login: null },
  { email: "old@example.com", role: "approver", enabled: false, last_login: null },
];
const FIELD = "Authenticator code (or a recovery code)";
const HINT = "Signed in with an email code: this action also needs your authenticator.";

function renderAs(signedInWith: Me["signed_in_with"]) {
  apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: signedInWith });
  vi.spyOn(apiModule.api, "users").mockResolvedValue(ROWS);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={qc}><Users /></QueryClientProvider>);
}

const row = (email: string) => screen.getByRole("row", { name: new RegExp(email) });
const code = (v: string) => fireEvent.change(screen.getByLabelText(FIELD), { target: { value: v } });

describe("Users step-up after an email-code sign-in", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("asks for the authenticator code and sends it with each of the six owner actions", async () => {
    renderAs("email_code");
    await screen.findByText("viewer@example.com");
    expect(screen.getByLabelText(FIELD)).toHaveAttribute("autocomplete", "one-time-code");
    expect(screen.getByText(HINT)).toBeInTheDocument();
    const a = apiModule.api;
    const invite = vi.spyOn(a, "invite").mockResolvedValue({ invite_token: "i", expires_h: 72 });
    const setRole = vi.spyOn(a, "setRole").mockResolvedValue({ ok: true });
    const disable = vi.spyOn(a, "disable").mockResolvedValue({ ok: true });
    const enable = vi.spyOn(a, "enable").mockResolvedValue({ ok: true });
    const revoke = vi.spyOn(a, "revokeSessions").mockResolvedValue({ ok: true, sessions: 1 });
    const reset = vi.spyOn(a, "resetLink").mockResolvedValue({ reset_token: "r", expires_h: 24 });

    code("111111");
    fireEvent.change(screen.getByPlaceholderText("email to invite"), { target: { value: "new@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Create invite link" }));
    await waitFor(() => expect(invite).toHaveBeenCalledWith("new@example.com", "viewer", "111111"));
    await waitFor(() => expect(screen.getByLabelText(FIELD)).toHaveValue(""));   // spent once used: ask afresh

    code("222222");
    fireEvent.change(within(row("viewer@example.com")).getByRole("combobox"), { target: { value: "approver" } });
    await waitFor(() => expect(setRole).toHaveBeenCalledWith("viewer@example.com", "approver", "222222"));
    code("333333");
    fireEvent.click(within(row("viewer@example.com")).getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(disable).toHaveBeenCalledWith("viewer@example.com", "333333"));
    code("444444");
    fireEvent.click(within(row("old@example.com")).getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(enable).toHaveBeenCalledWith("old@example.com", "444444"));
    code("555555");
    fireEvent.click(within(row("viewer@example.com")).getByRole("button", { name: "Sign out everywhere" }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("viewer@example.com", "555555"));
    code("abcd-efgh-ijkl-mnop");                                                 // a recovery code works too
    fireEvent.click(within(row("viewer@example.com")).getByRole("button", { name: "Reset link" }));
    await waitFor(() => expect(reset).toHaveBeenCalledWith("viewer@example.com", "abcd-efgh-ijkl-mnop"));
  }, 15_000);                                                                   // six actions in turn: slow on a loaded host

  it("shows the server's refusal in words when the code is missing or wrong", async () => {
    renderAs("email_code");
    await screen.findByText("viewer@example.com");
    vi.spyOn(apiModule.api, "disable").mockRejectedValue(new Error("authenticator code required"));
    fireEvent.click(within(row("viewer@example.com")).getByRole("button", { name: "Disable" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This action needs a current code from your authenticator app (or a recovery code), because you signed in " +
      "with an email code. Type it above and try again.");
    expect(screen.getByLabelText(FIELD)).toHaveAttribute("aria-describedby", screen.getByRole("alert").id);
  });

  it("says in plain words when too many wrong codes have locked user management", async () => {
    renderAs("email_code");
    await screen.findByText("viewer@example.com");
    vi.spyOn(apiModule.api, "disable").mockRejectedValue(
      new Error("too many attempts: user management, re-arm and auto mode are locked for 15 minutes"));
    fireEvent.click(within(row("viewer@example.com")).getByRole("button", { name: "Disable" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many wrong codes. Managing users, re-arm and auto mode are locked for 15 minutes; Halt still works.");
  });

  it("does not ask for a code after an authenticator sign-in", async () => {
    renderAs("password_totp");
    await screen.findByText("viewer@example.com");
    expect(screen.queryByLabelText(FIELD)).toBeNull();
    const disable = vi.spyOn(apiModule.api, "disable").mockResolvedValue({ ok: true });
    fireEvent.click(within(row("viewer@example.com")).getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(disable).toHaveBeenCalledWith("viewer@example.com", ""));
  });

  it("asks for the code when the server requires it even if this tab did not know how the session signed in", async () => {
    renderAs("password_totp");
    await screen.findByText("viewer@example.com");
    vi.spyOn(apiModule.api, "enable").mockRejectedValue(new Error("authenticator code required"));
    fireEvent.click(within(row("old@example.com")).getByRole("button", { name: "Enable" }));
    expect(await screen.findByLabelText(FIELD)).toBeInTheDocument();
  });
});
