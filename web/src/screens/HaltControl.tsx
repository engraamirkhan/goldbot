import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, hasRole, type Status } from "../lib/api";
import { viaChannel } from "../lib/approval";

// Halt stops new entries on every engine (open positions keep their stops and exits). Any approver may halt;
// re-arming needs the owner and a fresh authenticator code.
export function HaltControl({ status }: { status: Status }) {
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
        <p>Entries halted{status.halted_by ? ` via ${viaChannel(status.halted_by)}` : ""}{status.halt_reason ? `: ${status.halt_reason}` : ""}.</p>
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
