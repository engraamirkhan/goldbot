import { FormEvent, useEffect, useState } from "react";
import { api, setToken, setUser } from "../lib/api";

type ModeT = "login" | "setup" | "accept" | "forgot" | "reset" | "recover";

const SUBMIT: Record<ModeT, string> = {
  login: "Sign in", setup: "Create owner account", accept: "Accept invite", forgot: "Set new password",
  reset: "Set password and authenticator", recover: "Sign in with recovery code",
};

export function Login({ onLogin }: { onLogin: () => void }) {
  const params = new URLSearchParams(location.search);
  const [mode, setMode] = useState<ModeT>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setupCode, setSetupCode] = useState("");
  const [inviteToken, setInviteToken] = useState(params.get("invite") ?? "");
  const [resetToken, setResetToken] = useState(params.get("reset") ?? "");
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [after, setAfter] = useState<(() => void) | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.authState().then((s) => {
      if (s.needs_setup) setMode("setup"); else if (inviteToken) setMode("accept"); else if (resetToken) setMode("reset");
    }).catch(() => {});
  }, [inviteToken, resetToken]);

  function go(m: ModeT) { setMode(m); setErr(null); setInfo(null); setPassword(""); setCode(""); }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null); setInfo(null);
    try {
      if (mode === "login") {
        const r = await api.login(email, password, code);
        setToken(r.token); setUser({ email: r.email, role: r.role }); onLogin();
      } else if (mode === "setup") {
        const r = await api.setup(setupCode, email, password);
        setRecoveryCodes(r.recovery_codes); setTotpUri(r.totp_uri);
      } else if (mode === "accept") {
        const r = await api.accept(inviteToken, password);
        setEmail(r.email); setTotpUri(r.totp_uri);
      } else if (mode === "reset") {
        const r = await api.reset(resetToken, password);
        setEmail(r.email); setTotpUri(r.totp_uri);
      } else if (mode === "forgot") {
        await api.forgotPassword(email, code, password);
        go("login"); setInfo("Password changed. Sign in with the new password.");
      } else {
        const r = await api.recoveryLogin(email, password, code);
        setToken(r.token); setUser({ email: r.email, role: r.role });
        setTotpUri(r.totp_uri); setInfo(`${r.recovery_codes_left} recovery codes left.`); setAfter(() => onLogin);
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
        {recoveryCodes.length > 0 && (
          <>
            <p>Recovery codes: each signs the owner in once if the authenticator is lost. Store them in your password manager now; they are not shown again.</p>
            <ul className="mono" aria-label="Recovery codes">{recoveryCodes.map((c) => <li key={c}>{c}</li>)}</ul>
          </>
        )}
        {info && <p className="muted">{info}</p>}
        <button onClick={() => {
          setTotpUri(null); setRecoveryCodes([]); setPassword(""); setInviteToken(""); setResetToken(""); history.replaceState(null, "", "/");
          if (after) after(); else go("login");
        }}>Continue to sign in</button>
      </div>
    );
  }

  const newPassword = mode === "setup" || mode === "accept" || mode === "reset" || mode === "forgot";
  return (
    <form className="login" onSubmit={submit}>
      <h1>goldbot</h1>
      {mode === "setup" && <p>First run: create the owner account. The setup code is printed in the server log (and sent to Telegram when configured).</p>}
      {mode === "accept" && <p>You have been invited. Choose a password (12+ characters); you will then enrol an authenticator.</p>}
      {mode === "reset" && <p>Choose a new password (12+ characters); you will then enrol a new authenticator.</p>}
      {mode === "forgot" && <p>Forgot your password? Enter your email, a current authenticator code and a new password (12+ characters). Lost the authenticator too? Ask the owner for a reset link.</p>}
      {mode === "recover" && <p>Owner only: sign in with a recovery code instead of the authenticator; you will enrol a new authenticator.</p>}
      {mode === "setup" && <label>Setup code<input value={setupCode} onChange={(e) => setSetupCode(e.target.value)} autoFocus /></label>}
      {mode !== "accept" && mode !== "reset" && <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></label>}
      <label>{mode === "forgot" ? "New password" : "Password"}<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={newPassword ? "new-password" : "current-password"} /></label>
      {(mode === "login" || mode === "forgot") && <label>Authenticator code<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" /></label>}
      {mode === "recover" && <label>Recovery code<input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" /></label>}
      <button type="submit">{SUBMIT[mode]}</button>
      {info && <p className="muted">{info}</p>}
      {err && <p className="err">{err}</p>}
      {mode === "login" && (
        <p className="muted">
          <button type="button" className="link" onClick={() => go("forgot")}>Forgot password</button>{" · "}
          <button type="button" className="link" onClick={() => go("recover")}>Use a recovery code</button>
        </p>
      )}
      {(mode === "forgot" || mode === "recover") && <button type="button" className="link" onClick={() => go("login")}>Back to sign in</button>}
    </form>
  );
}
