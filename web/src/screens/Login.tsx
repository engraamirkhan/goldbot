import { FormEvent, useEffect, useRef, useState } from "react";
import { api, NETWORK_TEXT, SERVER_SAYS, setToken, setUser, type Me } from "../lib/api";
import { takeLinkTokens } from "../lib/links";
import { useNow } from "../lib/useNow";
import { CODE_COPY, CodeChannel } from "../lib/codeCopy";

// Every way in, one screen. Sign-in needs a second step: the authenticator code, or (owner only, while the server's
// mail is ready) a 6-digit code emailed to the owner (ADR 0028, docs/ux/sign-in-email-code.md states A-H). The
// emailed LINK (ADR 0013) replaces the password, never the authenticator code.
type ModeT = "login" | "setup" | "accept" | "forgot" | "reset" | "recover" | "emailLink" | "signin" | "emailCode";
type Method = "email" | "authenticator";

const TITLE: Record<ModeT, string> = {
  login: "Sign in", setup: "Create the owner account", accept: "Accept your invite", forgot: "Reset your password",
  reset: "Set a new password", recover: "Sign in with a recovery code", emailLink: "Email me a sign-in link",
  signin: "Finish signing in", emailCode: "Enter the code we emailed",
};

const SUBMIT: Record<ModeT, string> = {
  login: "Sign in", setup: "Create owner account", accept: "Accept invite", forgot: "Set new password",
  reset: "Set password and authenticator", recover: "Sign in with recovery code", emailLink: "Send the link",
  signin: "Sign in", emailCode: "Sign in",
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

// The method choice is the only thing this screen stores (no secret); the code, the challenge and the password stay in
// memory and are gone with the tab.
const METHOD_KEY = "signin.method";
const TRIES = 3;                    // EMAIL_CODE_TRIES: the server never says how many are left, so the screen counts
const RESEND_S = 60;                // RESEND_AFTER_S: a longer wait in an answer means the hourly limit was reached
const NO_MAIL_HINT_S = 120;
const LOCK_MS = 15 * 60_000;        // the sign-in lockout: 5 failures per email in 15 minutes, shared by both methods

const BURNED = "That code can no longer be used. Send a new one.";
const EXPIRED = "This code has expired or was already used. Send a new code.";
const SEND_FAILED = "Could not send a code just now. Try again in a minute, or use the authenticator app.";
const RESEND_FAILED = "Could not send a new code just now. Try again in a minute, or use the authenticator app.";
const CHECK_FAILED = "Could not check the code just now. Try again in a minute.";

/** The HTTP status of a failed request (lib/api.ts ApiError: 0 when the server was not reached). */
function statusOf(ex: unknown): number | undefined {
  const s = (ex as { status?: unknown } | null)?.status;
  return typeof s === "number" ? s : undefined;
}

/** Plain words for a failed request: never the browser's text, never a bare status line. */
function failure(ex: unknown, otherwise: string): string {
  return statusOf(ex) === 0 ? NETWORK_TEXT : otherwise;
}

function storedMethod(): Method {
  try { return localStorage.getItem(METHOD_KEY) === "authenticator" ? "authenticator" : "email"; } catch { return "email"; }
}

/** "o•••@g•••.com", built from what the owner typed: the server never echoes the address. */
function maskEmail(e: string): string {
  const [local = "", domain = ""] = e.trim().split("@");
  const dot = domain.lastIndexOf(".");
  const name = dot > 0 ? domain.slice(0, dot) : domain;
  return `${local.slice(0, 1)}•••@${name.slice(0, 1)}•••${dot > 0 ? domain.slice(dot) : ""}`;
}

const pad = (n: number) => String(n).padStart(2, "0");
/** A local time with UTC in the title (every time shown on the sign-in screens). */
function Clock({ at }: { at: number }) {
  const d = new Date(at);
  return <time dateTime={d.toISOString()} title={`${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`}>{`${pad(d.getHours())}:${pad(d.getMinutes())}`}</time>;
}

/** One emailed code as this browser knows it: the server's answer stamped with when it arrived. `limited`: the wait is the
 * hourly limit, not the 60 s pace; `paced`: a resend got this same challenge back (nothing sent, wait `resendAt`). */
type Challenge = { id: string; expiresAt: number; resendAt: number; sentAt: number; at: number; limited: boolean; resent: boolean; paced: boolean };

function challengeFrom(r: { challenge: string; expires_in_s: number; resend_after_s: number }, resent: boolean): Challenge {
  const now = Date.now();
  return { id: r.challenge, expiresAt: now + r.expires_in_s * 1000, resendAt: now + r.resend_after_s * 1000, sentAt: now,
           at: now, limited: r.resend_after_s > RESEND_S, resent, paced: false };
}

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
  const [emailCodeOn, setEmailCodeOn] = useState(false);
  const [codeChannel, setCodeChannel] = useState<CodeChannel>("telegram");
  const [method, setMethod] = useState<Method>(storedMethod);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [tries, setTries] = useState(0);
  const [lockedUntil, setLockedUntil] = useState(0);
  const [recoveryCode, setRecoveryCode] = useState("");
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [after, setAfter] = useState<(() => void) | null>(null);
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);                // the server's sign-in options are known
  const [showPw, setShowPw] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
  const checking = useRef(false);                           // one code check at a time: auto-verify and Enter can race
  const now = useNow();

  useEffect(() => {
    api.authState().then((s) => {
      setEmailLinkOn(s.email_link); setEmailCodeOn(s.code_sign_in); setCodeChannel(s.code_channel);
      if (s.needs_setup) setMode("setup");
      else if (inviteToken) setMode("accept");
      else if (resetToken) setMode("reset");
      else if (signinToken) setMode("signin");
    }).catch(() => {}).finally(() => setReady(true));
  }, [inviteToken, resetToken, signinToken]);

  function go(m: ModeT) { setMode(m); setErr(null); setInfo(null); setPassword(""); setShowPw(false); setCode(""); setRecoveryCode(""); setChallenge(null); }

  /** State F: the sign-in lockout covers both methods, so it is shown on the sign-in screen with its end time. */
  function lockOut() {
    setLockedUntil(Date.now() + LOCK_MS);
    setMode("login"); setChallenge(null); setCode(""); setErr(null);
  }

  function choose(m: Method) {
    setMethod(m);
    try { localStorage.setItem(METHOD_KEY, m); } catch { /* storage blocked: the choice lasts for this visit */ }
  }

  function signedIn(r: { token: string; email: string; role: Me["role"]; signed_in_with: Me["signed_in_with"] }) {
    setToken(r.token); setUser({ email: r.email, role: r.role, signed_in_with: r.signed_in_with }); onLogin();
  }

  const byEmail = emailCodeOn && method === "email";
  const copy = CODE_COPY[codeChannel];
  const t = Math.max(now, challenge?.at ?? 0);                       // never behind the last answer
  const secsTo = (at: number) => Math.max(0, Math.round((at - t) / 1000));
  const expired = !!challenge && secsTo(challenge.expiresAt) === 0;
  const burned = tries >= TRIES;
  const locked = secsTo(lockedUntil) > 0;
  const codeErr = !challenge ? null : expired ? EXPIRED : burned ? BURNED : tries > 0 ? copy.wrong(TRIES - tries) : null;
  const fieldErr = err ?? codeErr;                          // one message under the code field, joined to it

  async function verify(digits: string) {
    if (!challenge || checking.current || expired || burned) return;
    checking.current = true;
    setErr(null); setBusy(true);
    try {
      signedIn(await api.verifyEmailCode(challenge.id, digits));
    } catch (ex) {
      const status = statusOf(ex);
      if ((ex as Error).message === SERVER_SAYS.signInLocked) lockOut();
      else if (status === 401 || status === 403) setTries((n) => n + 1);   // a refusal: every one reads the same
      else setErr(failure(ex, CHECK_FAILED));                                // the network or the server: not a try
      codeRef.current?.focus(); codeRef.current?.select();
    } finally {
      checking.current = false;
      setBusy(false);
    }
  }

  async function resend() {
    if (!challenge) return;
    setErr(null); setBusy(true);
    try {
      const r = await api.resendEmailCode(challenge.id);
      if (r.challenge === challenge.id) {
        // the same challenge back: nothing was sent, wait this long (the 60 s pace or the hourly limit)
        const at = Date.now();
        setChallenge({ ...challenge, at, resendAt: at + r.resend_after_s * 1000, limited: r.resend_after_s > RESEND_S, paced: true });
      } else {
        // a new challenge: only its code works now, with three fresh tries and a new 10 minutes
        setChallenge(challengeFrom(r, true)); setTries(0); setCode("");
        codeRef.current?.focus();
      }
    } catch (ex) {
      setErr(failure(ex, RESEND_FAILED));
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (mode === "emailCode") {
      const digits = code.replace(/\s/g, "");
      if (/^\d{6}$/.test(digits)) await verify(digits);
      else if (!checking.current) { setErr(copy.partial); codeRef.current?.focus(); }   // a partial code spends no try
      return;
    }
    setErr(null); setInfo(null); setBusy(true);
    try {
      if (mode === "login" && byEmail) {
        setChallenge(challengeFrom(await api.emailCode(email, password), false));
        setTries(0); setCode(""); setMode("emailCode");
      } else if (mode === "login") {
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
      } else if (mode === "login" && emailCodeOn && msg === SERVER_SAYS.signInLocked) {
        lockOut();                                                      // State F: one counter for both methods
      } else if (mode === "login" && byEmail) {
        setErr(failure(ex, SEND_FAILED));
      } else {
        setErr(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  // until the server says which sign-in options it offers, show only the name: the form then appears whole, so the
  // method choice never pushes it down under a thumb (an unreachable server shows today's form)
  if (!ready) return <div className="login" aria-busy="true"><h1>goldbot</h1></div>;

  if (totpUri) {
    const secret = totpUri.split("secret=")[1]?.split("&")[0];
    return (
      <div className="login">
        <h1>goldbot</h1>
        <h2>Add goldbot to your authenticator</h2>
        {recoveryCodes.length > 0
          ? <p>Add goldbot to your authenticator app. You will need it to re-arm after a halt, switch auto mode and manage users. {copy.setupHint}</p>
          : <p>Add this account to your authenticator app (Google Authenticator, 1Password, Authy…), then sign in.</p>}
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
          // the owner may exist now, so email codes may be ready
          api.authState().then((s) => { setEmailLinkOn(s.email_link); setEmailCodeOn(s.code_sign_in); setCodeChannel(s.code_channel); }).catch(() => {});
          if (after) after(); else go("login");
        }}>Continue to sign in</button>
      </div>
    );
  }

  const live = !challenge || mode !== "emailCode" ? ""
    : expired ? "Code expired" : secsTo(challenge.expiresAt) <= 60 ? "1 minute left" : challenge.resent ? "New code sent" : "Code sent";
  // one polite region, outside the forms so it outlives the switch between them: state changes, never the seconds
  const liveRegion = <p className="sr-only" aria-live="polite" data-testid="live">{live}</p>;

  if (mode === "emailCode" && challenge) {
    const wait = secsTo(challenge.resendAt);
    const resendLine = challenge.limited && wait > 0
      ? <p className="resend">You have asked for 3 codes in the last hour. Try again after <Clock at={challenge.resendAt} />, or use the authenticator app.</p>
      : wait === 0 || ((burned || expired) && !challenge.paced)   // a spent code: at once, until the server says wait
        ? <p className="resend"><button type="button" className="link" onClick={resend} disabled={busy}>Resend code</button></p>
        : <p className="resend"><span className="countdown">{`Resend code in ${Math.floor(wait / 60)}:${pad(wait % 60)}`}</span></p>;
    return (<>
      <form className="login" onSubmit={submit} aria-labelledby="login-title">
        <h1>goldbot</h1>
        <h2 id="login-title">{copy.title}</h2>
        <p className="muted">
          {copy.sentLine(maskEmail(email))} It works once, for{" "}
          {Math.round((challenge.expiresAt - challenge.sentAt) / 60_000)} minutes (until <Clock at={challenge.expiresAt} />).
        </p>
        <label>Code
          <input ref={codeRef} className="code" value={code} autoFocus inputMode="numeric" pattern="[0-9 ]*" maxLength={7}
            autoComplete="one-time-code" aria-invalid={fieldErr ? true : undefined} aria-describedby={fieldErr ? "code-err" : undefined}
            onChange={(e) => {
              setCode(e.target.value); setErr(null);
              const digits = e.target.value.replace(/\s/g, "");
              if (/^\d{6}$/.test(digits)) void verify(digits);           // the 6th digit, typed or pasted; never a partial code
            }} />
        </label>
        {fieldErr && <p className="err" role="alert" id="code-err">{fieldErr}</p>}
        {secsTo(challenge.sentAt + NO_MAIL_HINT_S * 1000) === 0 && <p className="muted">{copy.noMessage}</p>}
        <button type="submit" disabled={busy || expired || burned}>{busy ? "Checking..." : SUBMIT.emailCode}</button>
        {resendLine}
        <nav className="alt" aria-label="Other ways to sign in">
          <button type="button" className="link" onClick={() => {
            choose("authenticator"); setMode("login"); setChallenge(null); setCode(""); setErr(null);
          }}>Use the authenticator app instead</button>
          <button type="button" className="link" onClick={() => {
            setMode("login"); setChallenge(null); setPassword(""); setCode(""); setErr(null);
          }}>Start again</button>
        </nav>
      </form>
      {liveRegion}
    </>);
  }

  const newPassword = mode === "setup" || mode === "accept" || mode === "reset" || mode === "forgot";
  const showEmail = mode === "login" || mode === "setup" || mode === "forgot" || mode === "recover" || mode === "emailLink";
  const showPassword = mode !== "emailLink" && mode !== "signin";
  const showCode = mode === "login" || mode === "forgot" || mode === "signin";
  // with the email code chosen the authenticator field keeps its slot, hidden and inert, so switching moves nothing
  const codeSlotHidden = mode === "login" && byEmail;
  const submitLabel = mode === "login" && byEmail ? (busy ? "Sending..." : copy.choose) : SUBMIT[mode];
  return (<>
    <form className="login" onSubmit={submit} aria-labelledby="login-title">
      <h1>goldbot</h1>
      <h2 id="login-title">{TITLE[mode]}</h2>
      {INTRO[mode] && <p className="muted">{INTRO[mode]}</p>}
      {mode === "setup" && <label>Setup code<input value={setupCode} onChange={(e) => setSetupCode(e.target.value)} autoFocus /></label>}
      {showEmail && <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} required /></label>}
      {showPassword && (
        <div className="pw-field">
          <label htmlFor="login-pw">{newPassword && mode !== "setup" ? "New password" : "Password"}</label>
          <div className="pw-row">
            <input id="login-pw" type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete={newPassword ? "new-password" : "current-password"} autoCapitalize="none" spellCheck={false} required />
            <button type="button" className="pw-toggle" aria-controls="login-pw"
              onClick={() => setShowPw((v) => !v)}>{showPw ? "Hide" : "Show"}</button>
          </div>
        </div>
      )}
      {showCode && (
        <label style={codeSlotHidden ? { visibility: "hidden" } : undefined} aria-hidden={codeSlotHidden || undefined}>Authenticator code
          <input className="code" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" pattern="[0-9 ]*" autoComplete="one-time-code"
            autoFocus={mode === "signin"} required={!codeSlotHidden} disabled={codeSlotHidden} tabIndex={codeSlotHidden ? -1 : undefined} />
        </label>
      )}
      {mode === "login" && emailCodeOn && (
        <fieldset className="method">
          <legend>Second step</legend>
          <label><input type="radio" name="method" checked={method === "email"} onChange={() => choose("email")} />{copy.choose}</label>
          <label><input type="radio" name="method" checked={method === "authenticator"} onChange={() => choose("authenticator")} />Authenticator app</label>
        </fieldset>
      )}
      {mode === "forgot" && <label>Recovery code (owner account only)<input value={recoveryCode} onChange={(e) => setRecoveryCode(e.target.value)} autoComplete="off" autoCapitalize="none" /></label>}
      {mode === "recover" && <label>Recovery code<input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" autoCapitalize="none" required /></label>}
      <button type="submit" disabled={busy || (mode === "login" && locked)}>{submitLabel}</button>
      {info && <p className="muted" role="status">{info}</p>}
      {mode === "login" && locked && (
        <p className="err" role="alert">
          Too many attempts. Sign-in for this account is paused for 15 minutes (until <Clock at={lockedUntil} />). This
          applies to the email code and the authenticator alike. If you did not cause it, someone may know your password:
          change it once you are back in.
        </p>
      )}
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
    {liveRegion}
  </>);
}
