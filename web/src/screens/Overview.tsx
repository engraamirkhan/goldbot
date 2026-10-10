import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, hasRole, type Status } from "../lib/api";
import { AutoModeCard } from "./AutoMode";

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;

export function Overview() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  return (
    <section>
      <div className="tiles">
        <div className="tile"><span>Mode</span><strong>{status.data?.mode ?? "…"}</strong></div>
        <div className={`tile ${status.data?.halted ? "bad" : ""}`}><span>Halted</span><strong>{status.data ? (status.data.halted ? "yes" : "no") : "…"}</strong></div>
        <div className="tile"><span>Pending approvals</span><strong>{status.data?.pending ?? "…"}</strong></div>
      </div>
      {status.data && <HaltControl status={status.data} />}
      <AutoModeCard />
      <table>
        <thead><tr><th>Account</th><th>Mode</th><th>Class</th><th>Equity</th><th>Today</th><th>Week</th><th>Drawdown</th><th>Stage</th><th>Open</th></tr></thead>
        <tbody>
          {(accounts.data ?? []).map((a) => (
            <tr key={a.account_id} className={a.stage !== "normal" ? "bad" : ""}>
              <td>{a.account_id}</td><td>{a.mode}</td><td>{a.account_class}</td>
              <td>{a.equity.toFixed(2)}</td><td>{pct(a.day_pnl_pct)}</td><td>{pct(a.week_pnl_pct)}</td>
              <td>{pct(a.drawdown_pct)}</td><td>{a.stage}</td><td>{a.open_positions}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {accounts.data?.length === 0 && <p className="muted">No engines reporting yet.</p>}
    </section>
  );
}

// Halt stops new entries on every engine (open positions keep their stops and exits). Any approver may halt;
// re-arming needs the owner and a fresh authenticator code.
function HaltControl({ status }: { status: Status }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const done = (s: Status) => { qc.setQueryData(["status"], s); setReason(""); setCode(""); };
  const halt = useMutation({ mutationFn: () => api.halt(reason), onSuccess: done });
  const rearm = useMutation({ mutationFn: () => api.rearm(code), onSuccess: done });
  const err = (halt.error ?? rearm.error) as Error | null;
  if (status.halted) {
    return (
      <div className="halt bad">
        <p>Entries halted{status.halted_by ? ` by ${status.halted_by}` : ""}{status.halt_reason ? `: ${status.halt_reason}` : ""}.</p>
        {hasRole("owner") ? (
          <form onSubmit={(e) => { e.preventDefault(); rearm.mutate(); }}>
            <input aria-label="Authenticator code" inputMode="numeric" placeholder="authenticator code" value={code} onChange={(e) => setCode(e.target.value)} />
            <button type="submit" disabled={code.length < 6 || rearm.isPending}>Re-arm</button>
          </form>
        ) : <p className="muted">Only the owner can re-arm.</p>}
        {err && <p className="err">{err.message}</p>}
      </div>
    );
  }
  if (!hasRole("approver")) return null;
  return (
    <form className="halt" onSubmit={(e) => { e.preventDefault(); halt.mutate(); }}>
      <input aria-label="Halt reason" placeholder="reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button type="submit" className="danger" disabled={halt.isPending}>Halt new entries</button>
      {err && <p className="err">{err.message}</p>}
    </form>
  );
}
