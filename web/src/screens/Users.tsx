import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, GrantableRole } from "../lib/api";
import { linkUrl } from "../lib/links";

// Owner only. The owner role is never granted: invites and role changes offer approver or viewer.
export function Users() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: api.users, refetchInterval: 30000 });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<GrantableRole>("viewer");
  const [link, setLink] = useState<{ kind: "invite" | "reset"; url: string; who?: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const invite = useMutation({
    mutationFn: () => api.invite(email, role),
    onSuccess: (r) => { setLink({ kind: "invite", url: linkUrl("invite", r.invite_token) }); setEmail(""); refresh(); },
  });
  const setRoleM = useMutation({ mutationFn: (v: { email: string; role: GrantableRole }) => api.setRole(v.email, v.role), onSuccess: refresh });
  const disable = useMutation({ mutationFn: (e: string) => api.disable(e), onSuccess: refresh });
  const enable = useMutation({ mutationFn: (e: string) => api.enable(e), onSuccess: refresh });
  const revoke = useMutation({ mutationFn: (e: string) => api.revokeSessions(e), onSuccess: (r, e) => setNote(`${e}: ${r.sessions} session(s) signed out.`) });
  const reset = useMutation({
    mutationFn: (e: string) => api.resetLink(e),
    onSuccess: (r, e) => setLink({ kind: "reset", url: linkUrl("reset", r.reset_token), who: e }),
  });
  const error = [invite, setRoleM, disable, enable, revoke, reset].map((m) => m.error).find(Boolean) as Error | undefined;

  function submit(e: FormEvent) { e.preventDefault(); invite.mutate(); }

  return (
    <section>
      <form className="inline" onSubmit={submit}>
        <input type="email" placeholder="email to invite" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <select value={role} onChange={(e) => setRole(e.target.value as GrantableRole)}>
          <option value="viewer">viewer — read only</option>
          <option value="approver">approver — can confirm entries</option>
        </select>
        <button type="submit">Create invite link</button>
      </form>
      {link?.kind === "invite" && <p className="muted">Send this link to them (valid 72 h, single use): <code className="mono">{link.url}</code></p>}
      {link?.kind === "reset" && <p className="muted">Reset link for {link.who} (valid 24 h, single use; sets a new password and authenticator): <code className="mono">{link.url}</code></p>}
      {note && <p className="muted">{note}</p>}
      {error && <p className="err">{error.message}</p>}
      <table>
        <thead><tr><th>Email</th><th>Role</th><th>Enabled</th><th>Last login</th><th></th></tr></thead>
        <tbody>
          {(q.data ?? []).map((u) => (
            <tr key={u.email} className={u.enabled ? "" : "retired"}>
              <td>{u.email}</td>
              <td>
                {u.role === "owner" ? "owner" : (
                  <select aria-label={`Role of ${u.email}`} value={u.role} onChange={(e) => setRoleM.mutate({ email: u.email, role: e.target.value as GrantableRole })}>
                    <option value="viewer">viewer</option><option value="approver">approver</option>
                  </select>
                )}
              </td>
              <td>{u.enabled ? "yes" : "no"}</td>
              <td>{u.last_login ? new Date(u.last_login * 1000).toLocaleString() : "—"}</td>
              <td>
                {u.role !== "owner" && (u.enabled
                  ? <button onClick={() => disable.mutate(u.email)}>Disable</button>
                  : <button onClick={() => enable.mutate(u.email)}>Enable</button>)}
                <button onClick={() => revoke.mutate(u.email)}>Sign out everywhere</button>
                {u.role !== "owner" && <button onClick={() => reset.mutate(u.email)}>Reset link</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
