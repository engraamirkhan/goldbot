import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, hasRole, type ReasonCode } from "../lib/api";
import { blockers } from "../lib/approval";
import { useNow } from "../lib/useNow";
import { ProposalCard } from "./ProposalCard";
import { SafetyStrip } from "./SafetyStrip";

type Vars = { id: string; action: "approve" | "reject"; reason?: ReasonCode };

export function Approvals() {
  const qc = useQueryClient();
  const pending = useQuery({ queryKey: ["proposals"], queryFn: api.proposals, refetchInterval: 2000 });
  const recent = useQuery({ queryKey: ["proposals-recent"], queryFn: api.recentProposals, refetchInterval: 2000 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const m = useMutation({
    mutationFn: (v: Vars) => api.decide(v.id, v.action, v.reason),
    onMutate: (v) => setErrors(({ [v.id]: _, ...rest }) => rest),
    onError: (e, v) => setErrors((x) => ({ ...x, [v.id]: (e as Error).message })),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["proposals"] });
      qc.invalidateQueries({ queryKey: ["proposals-recent"] });
    },
  });
  const now = useNow();
  const canDecide = hasRole("approver");
  const decided = recent.data ?? [];
  const decidedIds = new Set(decided.map((d) => d.proposal_id));
  // most urgent first; a proposal that has just been decided shows once, as decided
  const open = (pending.data ?? []).filter((p) => !decidedIds.has(p.proposal_id))
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const busyId = m.isPending ? m.variables?.id : undefined;

  return (
    <div className="approvals">
      <SafetyStrip status={status.data} accounts={accounts.data} error={status.isError || accounts.isError} />
      {pending.isError && <p className="err" role="alert">Could not load proposals ({(pending.error as Error).message}). Telegram approvals still work.</p>}
      {pending.isLoading ? <p className="muted">Loading proposals…</p> : open.length === 0 ? (
        <p className="empty muted">No proposals waiting. Entries you approve here or on Telegram are managed automatically afterwards.</p>
      ) : (
        <section className="cards" aria-label="Waiting for your decision">
          {open.map((p) => (
            <ProposalCard key={p.proposal_id} p={p} now={now} canDecide={canDecide}
                          blockers={blockers(status.data, accounts.data, p.account_id)}
                          busy={busyId === p.proposal_id} error={errors[p.proposal_id]}
                          onApprove={() => m.mutate({ id: p.proposal_id, action: "approve" })}
                          onReject={(reason) => m.mutate({ id: p.proposal_id, action: "reject", reason })} />
          ))}
        </section>
      )}
      {decided.length > 0 && (
        <section aria-label="Decided in the last 10 minutes">
          <h2 className="section-title">Decided in the last 10 minutes</h2>
          <div className="cards decided">
            {decided.map((p) => <ProposalCard key={p.proposal_id} p={p} now={now} canDecide={false} blockers={[]} />)}
          </div>
        </section>
      )}
    </div>
  );
}
