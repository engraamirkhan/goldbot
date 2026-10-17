import { FormEvent, useEffect, useState } from "react";
import { api, setToken, setUser, type Me } from "../lib/api";
import { takeLinkTokens } from "../lib/links";

// Every way in, one screen. Sign-in always needs the authenticator code: the emailed link (owner only, when the
// server has mail configured) replaces the password, never the code, because this account approves real-money entries.
type ModeT = "login" | "setup" | "accept" | "forgot" | "reset" | "recover" | "emailLink" | "signin";

const TITLE: Record<ModeT, string> = {
  login: "Sign in", setup: "Create the owner account", accept: "Accept your invite", forgot: "Reset your password",
  reset: "Set a new password", recover: "Sign in with a recovery code", emailLink: "Email me a sign-in link",
  signin: "Finish signing in",
};

const SUBMIT: Record<ModeT, string> = {
  login: "Sign in", setup: "Create owner account", accept: "Accept invite", forgot: "Set new password",
  reset: "Set password and authenticator", recover: "Sign in with recovery code", emailLink: "Send the link",
  signin: "Sign in",
};

const INTRO: Partial<Record<ModeT, string>> = {
  setup: "First run: create the owner account. The setup code is printed in the server log (and sent to Telegram when configured).",
  accept: "You have been invited. Choose a password (12+ characters); you will then add goldbot to your authenticator app.",
  reset: "Your owner sent you this link. Choose a new password (12+ characters); you will then add goldbot to your authenticator app again.",
  forgot: "Enter your email, a code from your authenticator app and a new password (12+ characters). The owner account also needs one of its recovery codes. Lost the authenticator too? Ask the owner for a reset link.",
  recover: "Owner only: sign in with a recovery code instead of the authenticator; you will then add goldbot to a new authenticator.",
  emailLink: "Owner account only. We email a one-time link that works for 15 minutes. You will still need the code from your authenticator app.",
  signin: "Enter the code from your authenticator app to finish signing in. The link works once, for 15 minutes.",
};

export function Login({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<ModeT>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setupCode, setSetupCode] = useState("");
  const [inviteToken, setInviteToken] = useState(() => takeLinkTokens().invite);
  const [resetToken, setResetToken] = useState(() => takeLinkTokens().reset);
  const [signinToken, setSigninToken] = useState(() => takeLinkTokens().signin);
  const [emailLinkOn, setEmailLinkOn] = useState(false);
  const [recoveryCode, setRecoveryCode] = useState("");
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [after, setAfter] = useState<(() => void) | null>(null);
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.authState().then((s) => {
      setEmailLinkOn(s.email_link);
      if (s.needs_setup) setMode("setup");
      else if (inviteToken) setMode("accept");
      else if (resetToken) setMode("reset");
      else if (signinToken) setMode("signin");
    }).catch(() => {});
  }, [inviteToken, resetToken, signinToken]);

  function go(m: ModeT) { setMode(m); setErr(null); setInfo(null); setPassword(""); setCode(""); setRecoveryCode(""); }

  function signedIn(r: { token: string; email: string; role: Me["role"]; signed_in_with: Me["signed_in_with"] }) {
    setToken(r.token); setUser({ email: r.email, role: r.role, signed_in_with: r.signed_in_with }); onLogin();
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null); setInfo(null); setBusy(true);
    try {
      if (mode === "login") {
        signedIn(await api.login(email, password, code));
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
        await api.forgotPassword(email, code, password, recoveryCode);
        go("login"); setInfo("Password changed. Sign in with the new password.");
      } else if (mode === "emailLink") {
        const r = await api.emailLink(email);
        setInfo(r.detail);
      } else if (mode === "signin") {
        const r = await api.redeemEmailLink(signinToken, code);
        setSigninToken("");
        signedIn(r);
      } else {
        const r = await api.recoveryLogin(email, password, code);
        setToken(r.token); setUser({ email: r.email, role: r.role, signed_in_with: "recovery" });
        setTotpUri(r.totp_uri); setInfo(`${r.recovery_codes_left} recovery codes left.`); setAfter(() => onLogin);
      }
    } catch (ex) {
      const msg = (ex as Error).message;
      if (mode === "signin") {
        // any failed redeem spends the link on the server: go straight to asking for a new one
        setSigninToken("");
        go(emailLinkOn ? "emailLink" : "login");
        setErr(emailLinkOn ? `${msg}. Ask for a new link below.` : msg);
      } else {
        setErr(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  if (totpUri) {
    const secret = totpUri.split("secret=")[1]?.split("&")[0];
    return (
      <div className="login">
        <h1>goldbot</h1>
        <h2>Add goldbot to your authenticator</h2>
        <p>Add this account to your authenticator app (Google Authenticator, 1Password, Authy…), then sign in.</p>
        <p className="mono">{secret}</p>
        <a href={totpUri}>Open in authenticator app</a>
        {recoveryCodes.length > 0 && (
          <>
            <p>Recovery codes: each signs the owner in once if the authenticator is lost. Store them in your password manager now; they are not shown again.</p>
            <ul className="mono" aria-label="Recovery codes">{recoveryCodes.map((c) => <li key={c}>{c}</li>)}</ul>
          </>
        )}
        {info && <p className="muted" role="status">{info}</p>}
        <button onClick={() => {
          setTotpUri(null); setRecoveryCodes([]); setPassword(""); setInviteToken(""); setResetToken(""); history.replaceState(null, "", "/");
          if (after) after(); else go("login");
        }}>Continue to sign in</button>
      </div>
    );
  }

  const newPassword = mode === "setup" || mode === "accept" || mode === "reset" || mode === "forgot";
  const showEmail = mode === "login" || mode === "setup" || mode === "forgot" || mode === "recover" || mode === "emailLink";
  const showPassword = mode !== "emailLink" && mode !== "signin";
  const showCode = mode === "login" || mode === "forgot" || mode === "signin";
  return (
    <form className="login" onSubmit={submit} aria-labelledby="login-title">
      <h1>goldbot</h1>
      <h2 id="login-title">{TITLE[mode]}</h2>
      {INTRO[mode] && <p className="muted">{INTRO[mode]}</p>}
      {mode === "setup" && <label>Setup code<input value={setupCode} onChange={(e) => setSetupCode(e.target.value)} autoFocus /></label>}
      {showEmail && <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} required /></label>}
      {showPassword && <label>{newPassword && mode !== "setup" ? "New password" : "Password"}<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={newPassword ? "new-password" : "current-password"} required /></label>}
      {showCode && <label>Authenticator code<input className="code" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" pattern="[0-9 ]*" autoComplete="one-time-code" autoFocus={mode === "signin"} required /></label>}
      {mode === "forgot" && <label>Recovery code (owner account only)<input value={recoveryCode} onChange={(e) => setRecoveryCode(e.target.value)} autoComplete="off" autoCapitalize="none" /></label>}
      {mode === "recover" && <label>Recovery code<input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" autoCapitalize="none" required /></label>}
      <button type="submit" disabled={busy}>{SUBMIT[mode]}</button>
      {info && <p className="muted" role="status">{info}</p>}
      {err && <p className="err" role="alert">{err}</p>}
      {mode === "login" && (
        <nav className="alt" aria-label="Other ways to sign in">
          <button type="button" className="link" onClick={() => go("forgot")}>Forgot password?</button>
          {emailLinkOn && <button type="button" className="link" onClick={() => go("emailLink")}>Email me a sign-in link</button>}
          <button type="button" className="link" onClick={() => go("recover")}>Use a recovery code</button>
        </nav>
      )}
      {(mode === "forgot" || mode === "recover" || mode === "emailLink" || mode === "signin") && (
        <button type="button" className="link" onClick={() => { setSigninToken(""); go("login"); }}>Back to sign in</button>
      )}
    </form>
  );
}
