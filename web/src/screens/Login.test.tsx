import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// links.ts reads the URL fragment once per module instance: each test gets fresh modules, with the URL set first.
async function setup(hash = "", emailLink = true) {
  history.replaceState(null, "", `/${hash}`);
  vi.resetModules();
  const apiModule = await import("../lib/api");
  vi.spyOn(apiModule.api, "authState").mockResolvedValue({ needs_setup: false, users: 2, email_link: emailLink, email_code: false });
  const { Login } = await import("./Login");
  const onLogin = vi.fn();
  render(<Login onLogin={onLogin} />);
  return { api: apiModule.api, onLogin };
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const SESSION = { token: "t", expires_in: 43200, role: "owner" as const, email: "o@x.io", signed_in_with: "password_totp" as const };

describe("Login", () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => { vi.restoreAllMocks(); history.replaceState(null, "", "/"); });

  it("signs in with email, password and the authenticator code", async () => {
    const { api, onLogin } = await setup();
    const login = vi.spyOn(api, "login").mockResolvedValue(SESSION);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    type("Email", "o@x.io");
    type("Password", "a long password here");
    type("Authenticator code", "123456");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Authenticator code")).toHaveAttribute("inputmode", "numeric");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("o@x.io", "a long password here", "123456"));
    expect(onLogin).toHaveBeenCalled();
  });

  it("resets a forgotten password with the authenticator code, and the recovery code for the owner", async () => {
    const { api } = await setup();
    const forgot = vi.spyOn(api, "forgotPassword").mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    expect(screen.getByText(/Lost the authenticator too\? Ask the owner for a reset link/)).toBeInTheDocument();
    type("Email", "o@x.io");
    type("Authenticator code", "123456");
    type("New password", "a brand new password");
    type(/Recovery code/, "abcd-efgh-ijkl-mnop");
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    await waitFor(() => expect(forgot).toHaveBeenCalledWith("o@x.io", "123456", "a brand new password", "abcd-efgh-ijkl-mnop"));
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("emails the owner a sign-in link and shows the same answer whatever the address", async () => {
    const { api } = await setup();
    const ask = vi.spyOn(api, "emailLink").mockResolvedValue({ ok: true, detail: "If this address can use email sign-in, a link is on its way" });
    fireEvent.click(await screen.findByRole("button", { name: "Email me a sign-in link" }));
    expect(screen.getByRole("heading", { name: "Email me a sign-in link" })).toBeInTheDocument();
    expect(screen.getByText(/still need the code from your authenticator app/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).toBeNull();
    type("Email", "o@x.io");
    fireEvent.click(screen.getByRole("button", { name: "Send the link" }));
    await waitFor(() => expect(ask).toHaveBeenCalledWith("o@x.io"));
    expect(await screen.findByRole("status")).toHaveTextContent("If this address can use email sign-in, a link is on its way");
  });

  it("finishes an emailed sign-in link with the authenticator code and clears the token from the address bar", async () => {
    const { api, onLogin } = await setup("#signin=tok-1");
    expect(location.hash).toBe("");
    const redeem = vi.spyOn(api, "redeemEmailLink").mockResolvedValue(SESSION);
    expect(await screen.findByRole("heading", { name: "Finish signing in" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).toBeNull();
    type("Authenticator code", "654321");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(redeem).toHaveBeenCalledWith("tok-1", "654321"));
    expect(onLogin).toHaveBeenCalled();
  });

  it("after a failed sign-in link goes straight to asking for a new one (the old link is spent)", async () => {
    const { api } = await setup("#signin=tok-2");
    vi.spyOn(api, "redeemEmailLink").mockRejectedValue(new Error("sign-in link invalid or expired, or wrong authenticator code"));
    await screen.findByRole("heading", { name: "Finish signing in" });
    type("Authenticator code", "000000");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Email me a sign-in link" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("invalid or expired");
    expect(screen.getByRole("alert")).toHaveTextContent("Ask for a new link");
    expect(screen.getByRole("button", { name: "Send the link" })).toBeInTheDocument();
  });

  it("after a failed sign-in link goes back to sign in when the server cannot send links", async () => {
    const { api } = await setup("#signin=tok-3", false);
    vi.spyOn(api, "redeemEmailLink").mockRejectedValue(new Error("sign-in link invalid or expired, or wrong authenticator code"));
    await screen.findByRole("heading", { name: "Finish signing in" });
    type("Authenticator code", "000000");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("invalid or expired");
  });

  it("does not offer the email link when the server has no mail configured", async () => {
    await setup("", false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Forgot password?" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Email me a sign-in link" })).toBeNull();
  });

  it("opens the owner's reset link on the set-new-password screen", async () => {
    await setup("#reset=r-1");
    expect(await screen.findByRole("heading", { name: "Set a new password" })).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).toBeNull();
  });
});
