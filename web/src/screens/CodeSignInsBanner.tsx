import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, currentUser, SERVER_SAYS, setToken } from "../lib/api";

// Shown to the owner after EVERY sign-in while a Telegram code has signed anyone in during the last 7 days (ADR 0028
// as amended 2026-10-17, security condition 6): when, from what rough network, and a one-tap "Sign out everywhere"
// (the existing revoke-sessions). After a code sign-in that call also needs the authenticator (the step-up), so the
// field appears only then. Neutral, not red: a code sign-in is the normal way in; the banner is for spotting a stranger.
const STEPUP_TEXT = "Type a current code from your authenticator app (or a recovery code), then sign out everywhere.";

export function CodeSignInsBanner({ onSignedOut }: { onSignedOut: () => void }) {
  const q = useQuery({ queryKey: ["code-sign-ins"], queryFn: api.codeSignIns, staleTime: Infinity, retry: false });
  const [hidden, setHidden] = useState(false);
  const [stepup, setStepup] = useState("");
  const [askCode, setAskCode] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const me = currentUser();
  const rows = q.data?.sign_ins ?? [];
  if (hidden || !me || q.data?.channel !== "telegram" || rows.length === 0) return null;
  const needCode = me.signed_in_with === "email_code" || askCode;

  async function signOutEverywhere() {
    if (!me) return;
    setBusy(true); setFail(null);
    try {
      await api.revokeSessions(me.email, stepup);
      setToken(null);
      onSignedOut();
    } catch (ex) {
      const msg = (ex as Error).message;
      if (msg === SERVER_SAYS.stepupRequired) { setAskCode(true); setFail(STEPUP_TEXT); }
      else setFail(msg === SERVER_SAYS.stepupLocked ? "Too many wrong codes. Try again in 15 minutes." : "Could not sign out everywhere just now. Try again.");
    } finally {
      setStepup(""); setBusy(false);
    }
  }

  return (
    <section className="banner" aria-labelledby="csi-h">
      <h2 id="csi-h">Sign-ins with a Telegram code, last 7 days</h2>
      <ul>
        {rows.map((r, i) => <li key={`${r.at_utc}-${i}`}>{r.at_utc} from {r.network}</li>)}
      </ul>
      <p className="muted">Not you? Someone has your password. Sign out everywhere, then change your password.</p>
      {needCode && (
        <div className="stepup">
          <label>Authenticator code to sign out everywhere
            <input value={stepup} onChange={(e) => setStepup(e.target.value)} autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} />
          </label>
        </div>
      )}
      {fail && <p className="err" role="alert">{fail}</p>}
      <button type="button" onClick={signOutEverywhere} disabled={busy}>Sign out everywhere</button>
      <button type="button" className="link" onClick={() => setHidden(true)}>Hide</button>
    </section>
  );
}
