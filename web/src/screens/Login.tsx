import { FormEvent, useState } from "react";
import { api, setToken } from "../lib/api";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const { token } = await api.login(code);
      setToken(token);
      onLogin();
    } catch (ex) {
      setErr((ex as Error).message);
    }
  }
  return (
    <form className="login" onSubmit={submit}>
      <h1>goldbot</h1>
      <label>Authenticator code<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoFocus /></label>
      <button type="submit">Sign in</button>
      {err && <p className="err">{err}</p>}
    </form>
  );
}
