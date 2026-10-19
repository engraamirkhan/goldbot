import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, hasRole } from "../lib/api";
import { blockers } from "../lib/approval";
import { useNow } from "../lib/useNow";
import { ProposalCard } from "./ProposalCard";
import { SafetyStrip } from "./SafetyStrip";
import { useDecide } from "./useDecide";
import "./home.css";

type Part = "waiting" | "recent";

/** Trades: Waiting (decide now) and Recent (decided in the last 10 minutes), one list at a time. */
export function Approvals() {
  const pending = useQuery({ queryKey: ["proposals"], queryFn: api.proposals, refetchInterval: 2000 });
  const recent = useQuery({ queryKey: ["proposals-recent"], queryFn: api.recentProposals, refetchInterval: 2000 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  const decide = useDecide();
  const [part, setPart] = useState<Part>("waiting");
  const now = useNow();
  const canDecide = hasRole("approver");
  const decided = recent.data ?? [];
  const decidedIds = new Set(decided.map((d) => d.proposal_id));
  // most urgent first; a proposal that has just been decided shows once, as decided
  const open = (pending.data ?? []).filter((p) => !decidedIds.has(p.proposal_id))
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));

  return (
    <div className="approvals">
      <SafetyStrip status={status.data} accounts={accounts.data} error={status.isError || accounts.isError}
                   updatedAt={status.dataUpdatedAt && accounts.dataUpdatedAt
                     ? Math.min(status.dataUpdatedAt, accounts.dataUpdatedAt) : undefined} now={now} />
      <div className="seg" role="tablist" aria-label="Decisions">
        <button type="button" role="tab" aria-selected={part === "waiting"} onClick={() => setPart("waiting")}>
          Waiting{open.length > 0 ? ` (${open.length})` : ""}
        </button>
        <button type="button" role="tab" aria-selected={part === "recent"} onClick={() => setPart("recent")}>
          Recent{decided.length > 0 ? ` (${decided.length})` : ""}
        </button>
      </div>
      {part === "waiting" && (<>
        {pending.isError && <p className="err" role="alert">Could not load proposals ({(pending.error as Error).message}). Telegram approvals still work.</p>}
        {pending.isLoading ? <p className="muted">Loading proposals…</p> : open.length === 0 ? (
          <p className="empty muted">No proposals waiting. Entries you approve here or on Telegram are managed automatically afterwards.</p>
        ) : (
          <section className="cards" aria-label="Waiting for your decision">
            {open.map((p) => (
              <ProposalCard key={p.proposal_id} p={p} now={now} canDecide={canDecide}
                            blockers={blockers(status.data, accounts.data, p.account_id, p.agent_id)}
                            busy={decide.busyId === p.proposal_id} error={decide.errors[p.proposal_id]}
                            onApprove={() => decide.approve(p)} onReject={(reason) => decide.reject(p, reason)} />
            ))}
          </section>
        )}
      </>)}
      {part === "recent" && (
        <section aria-label="Decided in the last 10 minutes">
          <h2 className="section-title">Decided in the last 10 minutes</h2>
          {decided.length === 0 ? <p className="empty muted">Nothing decided in the last 10 minutes.</p> : (
            <div className="cards decided">
              {decided.map((p) => <ProposalCard key={p.proposal_id} p={p} now={now} canDecide={false} blockers={[]} />)}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
