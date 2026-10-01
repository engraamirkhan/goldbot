import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Role } from "../lib/api";

export function Users() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: api.users, refetchInterval: 30000 });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [link, setLink] = useState<string | null>(null);
  const invite = useMutation({
    mutationFn: () => api.invite(email, role),
    onSuccess: (r) => { setLink(`${location.origin}/?invite=${r.invite_token}`); setEmail(""); qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  const setRoleM = useMutation({ mutationFn: (v: { email: string; role: Role }) => api.setRole(v.email, v.role), onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }) });
  const disable = useMutation({ mutationFn: (e: string) => api.disable(e), onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }) });

  function submit(e: FormEvent) { e.preventDefault(); invite.mutate(); }

  return (
    <section>
      <form className="inline" onSubmit={submit}>
        <input type="email" placeholder="email to invite" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="viewer">viewer — read only</option>
          <option value="approver">approver — can confirm entries</option>
          <option value="owner">owner — full control</option>
        </select>
        <button type="submit">Create invite link</button>
      </form>
      {link && <p className="muted">Send this link to them (valid 72 h, single use): <code className="mono">{link}</code></p>}
      {invite.error && <p className="err">{(invite.error as Error).message}</p>}
      <table>
        <thead><tr><th>Email</th><th>Role</th><th>Enabled</th><th>Last login</th><th></th></tr></thead>
        <tbody>
          {(q.data ?? []).map((u) => (
            <tr key={u.email} className={u.enabled ? "" : "retired"}>
              <td>{u.email}</td>
              <td>
                <select value={u.role} onChange={(e) => setRoleM.mutate({ email: u.email, role: e.target.value as Role })}>
                  <option value="viewer">viewer</option><option value="approver">approver</option><option value="owner">owner</option>
                </select>
              </td>
              <td>{u.enabled ? "yes" : "no"}</td>
              <td>{u.last_login ? new Date(u.last_login * 1000).toLocaleString() : "—"}</td>
              <td>{u.enabled && <button onClick={() => disable.mutate(u.email)}>Disable</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
