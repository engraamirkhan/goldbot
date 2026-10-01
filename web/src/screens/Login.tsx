import { FormEvent, useEffect, useState } from "react";
import { api, setToken, setUser } from "../lib/api";

type ModeT = "login" | "setup" | "accept";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<ModeT>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setupCode, setSetupCode] = useState("");
  const [inviteToken, setInviteToken] = useState(new URLSearchParams(location.search).get("invite") ?? "");
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.authState().then((s) => { if (s.needs_setup) setMode("setup"); else if (inviteToken) setMode("accept"); }).catch(() => {});
  }, [inviteToken]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      if (mode === "login") {
        const r = await api.login(email, password, code);
        setToken(r.token); setUser({ email: r.email, role: r.role }); onLogin();
      } else if (mode === "setup") {
        const r = await api.setup(setupCode, email, password);
        setTotpUri(r.totp_uri);
      } else {
        const r = await api.accept(inviteToken, password);
        setEmail(r.email); setTotpUri(r.totp_uri);
      }
    } catch (ex) { setErr((ex as Error).message); }
  }

  if (totpUri) {
    const secret = totpUri.split("secret=")[1]?.split("&")[0];
    return (
      <div className="login">
        <h1>goldbot</h1>
        <p>Add this account to your authenticator app (Google Authenticator, 1Password, Authy…), then sign in.</p>
        <p className="mono">{secret}</p>
        <a href={totpUri}>Open in authenticator app</a>
        <button onClick={() => { setTotpUri(null); setMode("login"); setPassword(""); setInviteToken(""); history.replaceState(null, "", "/"); }}>Continue to sign in</button>
      </div>
    );
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>goldbot</h1>
      {mode === "setup" && <p>First run: create the owner account. The setup code is printed in the server log (and sent to Telegram when configured).</p>}
      {mode === "accept" && <p>You have been invited. Choose a password (12+ characters); you will then enrol an authenticator.</p>}
      {mode === "setup" && <label>Setup code<input value={setupCode} onChange={(e) => setSetupCode(e.target.value)} autoFocus /></label>}
      {mode !== "accept" && <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></label>}
      <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
      {mode === "login" && <label>Authenticator code<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" /></label>}
      <button type="submit">{mode === "login" ? "Sign in" : mode === "setup" ? "Create owner account" : "Accept invite"}</button>
      {err && <p className="err">{err}</p>}
    </form>
  );
}
