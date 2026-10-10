import { FormEvent, useState } from "react";
import { api } from "../lib/api";

// Change password (any signed-in user): current password + authenticator code. Other sessions are signed out.
export function Account() {
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
