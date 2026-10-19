import { useQuery } from "@tanstack/react-query";
import { api, hasRole } from "../lib/api";
import { ActivityFeed } from "./Activity";
import { blockers } from "../lib/approval";
import { useNow } from "../lib/useNow";
import { BalanceCard, BalanceCardSkeleton } from "./BalanceCard";
import { EntryStatusPanel } from "./EntryStatus";
import { ProgressLine } from "./Progress";
import { LimitsStrip } from "./Limits";
import { ProposalCard } from "./ProposalCard";
import { ResearchLine } from "./ResearchToday";
import { useDecide } from "./useDecide";
import "./home.css";

/** Home answers three questions in this order: is anything waiting for me, how am I doing today, is it safe.
 *  A waiting trade is the card itself (same Approve / Reject wording as Trades), not a count. Below, full width, what
 *  the system is doing (the activity feed, owner request 2026-10-19). */
export function Home({ onOpenProgress, onOpenLimits }: { onOpenProgress?: () => void; onOpenLimits?: () => void } = {}) {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  const pending = useQuery({ queryKey: ["proposals"], queryFn: api.proposals, refetchInterval: 2000 });
  const recent = useQuery({ queryKey: ["proposals-recent"], queryFn: api.recentProposals, refetchInterval: 2000 });
  const decide = useDecide();
  const now = useNow();
  const decidedIds = new Set((recent.data ?? []).map((d) => d.proposal_id));
  const open = (pending.data ?? []).filter((p) => !decidedIds.has(p.proposal_id))
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const first = open[0];
  return (
    <section className="home">
      <div className="home-col">
        {pending.isError && <p className="err" role="alert">Could not load waiting trades. Telegram approvals still work.</p>}
        {first ? (
          <div className="home-waiting">
            <h2 className="section-title">Trade waiting{open.length > 1 ? ` (1 of ${open.length})` : ""}</h2>
            <ProposalCard p={first} now={now} canDecide={hasRole("approver")}
                          blockers={blockers(status.data, accounts.data, first.account_id, first.agent_id)}
                          busy={decide.busyId === first.proposal_id} error={decide.errors[first.proposal_id]}
                          onApprove={() => decide.approve(first)} onReject={(r) => decide.reject(first, r)} />
          </div>
        ) : !pending.isLoading && (
          <p className="empty muted home-none">No trade waiting. You'll get a Telegram ping when one needs you.</p>
        )}
        <EntryStatusPanel loading={accounts.isLoading} error={accounts.isError} accounts={accounts.data}
                          updatedAt={accounts.dataUpdatedAt || undefined} />
      </div>
      <div className="home-col">
        {/* Safety is said once: the entry status block (left) and the header pill, which also holds Halt */}
        {accounts.isLoading ? <div className="accts"><BalanceCardSkeleton /></div> : (accounts.data?.length ?? 0) > 0 && (
          <div className="accts">{accounts.data!.map((a) => <BalanceCard key={a.account_id} a={a} />)}</div>
        )}
        {onOpenLimits && <LimitsStrip onOpen={onOpenLimits} />}
        {onOpenProgress && <ProgressLine onOpen={onOpenProgress} />}
        <ResearchLine />
      </div>
      <div className="home-wide"><ActivityFeed /></div>
    </section>
  );
}
