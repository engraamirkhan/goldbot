import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, currentUser, GrantableRole } from "../lib/api";
import { linkUrl } from "../lib/links";
import { roleLabel } from "../lib/roles";

// The server's 403 when a session opened with an emailed code tries a user-management action without (or with a
// wrong) authenticator or recovery code (goldbot/api/app.py STEPUP_REQUIRED).
const STEPUP_REQUIRED = "authenticator code required";
const STEPUP_TEXT = "This action needs a current code from your authenticator app (or a recovery code), because you " +
  "signed in with an email code. Type it above and try again.";

// Owner only. The owner role is never granted: invites and role changes offer approver or viewer.
// ADR 0028 ruling 2: after an email-code sign-in, invite, role change, disable, enable, reset link and sign out
// everywhere also need the authenticator (or a recovery code). The server decides from the session's stored sign-in
// method; this screen asks up front and sends the code with the action. Each code is spent once, so it is cleared.
export function Users() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: api.users, refetchInterval: 30000 });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<GrantableRole>("viewer");
  const [link, setLink] = useState<{ kind: "invite" | "reset"; url: string; who?: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [stepup, setStepup] = useState("");
  const [asked, setAsked] = useState(false);                // the server asked although this tab did not know
  const [fail, setFail] = useState<string | null>(null);
  const needCode = currentUser()?.signed_in_with === "email_code" || asked;
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const common = {
    onMutate: () => setFail(null),
    onError: (e: Error) => {
      if (e.message === STEPUP_REQUIRED) setAsked(true);
      setFail(e.message === STEPUP_REQUIRED ? STEPUP_TEXT : e.message);
    },
    onSettled: () => setStepup(""),
  };
  const invite = useMutation({
    ...common,
    mutationFn: () => api.invite(email, role, stepup),
    onSuccess: (r) => { setLink({ kind: "invite", url: linkUrl("invite", r.invite_token) }); setEmail(""); refresh(); },
  });
  const setRoleM = useMutation({ ...common, mutationFn: (v: { email: string; role: GrantableRole }) => api.setRole(v.email, v.role, stepup), onSuccess: refresh });
  const disable = useMutation({ ...common, mutationFn: (e: string) => api.disable(e, stepup), onSuccess: refresh });
  const enable = useMutation({ ...common, mutationFn: (e: string) => api.enable(e, stepup), onSuccess: refresh });
  const revoke = useMutation({ ...common, mutationFn: (e: string) => api.revokeSessions(e, stepup), onSuccess: (r, e) => setNote(`${e}: ${r.sessions} session(s) signed out.`) });
  const reset = useMutation({
    ...common,
    mutationFn: (e: string) => api.resetLink(e, stepup),
    onSuccess: (r, e) => setLink({ kind: "reset", url: linkUrl("reset", r.reset_token), who: e }),
  });

  function submit(e: FormEvent) { e.preventDefault(); invite.mutate(); }

  return (
    <section>
      {needCode && (
        <div className="stepup">
          <label>Authenticator code (or a recovery code)
            <input value={stepup} onChange={(e) => setStepup(e.target.value)} autoComplete="one-time-code" autoCapitalize="none"
              spellCheck={false} aria-describedby={fail ? "users-err" : undefined} />
          </label>
          <p className="muted">Signed in with an email code: this action also needs your authenticator.</p>
        </div>
      )}
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
      {fail && <p className="err" role="alert" id="users-err">{fail}</p>}
      <table>
        <thead><tr><th>Email</th><th>Role</th><th>Enabled</th><th>Last login</th><th></th></tr></thead>
        <tbody>
          {(q.data ?? []).map((u) => (
            <tr key={u.email} className={u.enabled ? "" : "retired"}>
              <td>{u.email}</td>
              <td>
                {u.role === "owner" ? roleLabel("owner") : (
                  <select aria-label={`Role of ${u.email}`} value={u.role} onChange={(e) => setRoleM.mutate({ email: u.email, role: e.target.value as GrantableRole })}>
                    <option value="viewer">{roleLabel("viewer")}</option><option value="approver">{roleLabel("approver")}</option>
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
