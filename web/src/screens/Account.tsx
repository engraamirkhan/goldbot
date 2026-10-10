import { FormEvent, useState } from "react";
import { api, hasRole } from "../lib/api";

// Change password (any signed-in user): current password + authenticator code. Other sessions are signed out.
function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [code, setCode] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.changePassword(current, code, next);
      setCurrent(""); setCode(""); setNext("");
      setMsg({ ok: true, text: "Password changed. Your other sessions were signed out." });
    } catch (ex) { setMsg({ ok: false, text: (ex as Error).message }); }
  }

  return (
    <section>
      <h2>Change password</h2>
      <form className="login" onSubmit={submit}>
        <label>Current password<input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" /></label>
        <label>Authenticator code<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" /></label>
        <label>New password (12+ characters)<input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></label>
        <button type="submit">Change password</button>
        {msg && <p className={msg.ok ? "muted" : "err"}>{msg.text}</p>}
      </form>
    </section>
  );
}

function download(codes: string[]) {
  const text = `goldbot recovery codes (each works once; generated ${new Date().toISOString()})\n\n${codes.join("\n")}\n`;
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "goldbot-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  } catch { /* no download support: the codes are on screen to copy */ }
}

// Owner only: replace the 10 recovery codes (password + authenticator code). Shown once; the old set stops working.
function RecoveryCodes() {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [sure, setSure] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api.recoveryCodes(password, code);
      setCodes(r.recovery_codes);
      setPassword(""); setCode(""); setSure(false);
    } catch (ex) { setErr((ex as Error).message); } finally { setBusy(false); }
  }

  if (codes) {
    return (
      <section className="recovery" aria-labelledby="rc-h">
        <h2 id="rc-h">Your new recovery codes</h2>
        <p className="warn-note" role="alert">Shown once. Save them now (password manager or print them). Each code works once. Your old codes no longer work.</p>
        <ol className="codes mono">{codes.map((c) => <li key={c}>{c}</li>)}</ol>
        <div className="row">
          <button type="button" onClick={async () => {
            try { await navigator.clipboard.writeText(codes.join("\n")); setCopied(true); } catch { /* clipboard blocked: the codes are selectable */ }
          }}>{copied ? "Copied" : "Copy all"}</button>
          <button type="button" onClick={() => download(codes)}>Download .txt</button>
          <button type="button" className="secondary" onClick={() => { setCodes(null); setCopied(false); }}>I have saved them</button>
        </div>
      </section>
    );
  }
  return (
    <section className="recovery" aria-labelledby="rc-h">
      <h2 id="rc-h">Recovery codes</h2>
      <p className="small">
        Recovery codes let you sign in and enrol a new authenticator if you lose your phone. Generating a new set
        replaces all 10 codes: every code you saved before stops working at once.
      </p>
      <form className="login" onSubmit={submit}>
        <label>Your password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        <label>Code from your authenticator app<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" /></label>
        <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> My current recovery codes will stop working</label>
        <button type="submit" disabled={!sure || !password || code.length < 6 || busy}>Generate new recovery codes</button>
        {err && <p className="err" role="alert">{err}</p>}
      </form>
    </section>
  );
}

export function Account() {
  return (
    <>
      <ChangePassword />
      {hasRole("owner") && <RecoveryCodes />}
    </>
  );
}
