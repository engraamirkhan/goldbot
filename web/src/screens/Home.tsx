import { useQuery } from "@tanstack/react-query";
import { api, hasRole } from "../lib/api";
import { blockers } from "../lib/approval";
import { notOpen, orderWaiting } from "../lib/home";
import { useNow } from "../lib/useNow";
import { EntryStatusPanel } from "./EntryStatus";
import { CardBoundary, LearningCard, StrategiesCard, SystemStatusLine, TodayBrief } from "./HomeCards";
import { ProposalCard } from "./ProposalCard";
import { useDecide } from "./useDecide";
import { Skeleton } from "../ui";
import "./home.css";

type Props = { onOpenProgress?: () => void; onOpenLimits?: () => void; onOpenTrades?: () => void };

/** Home is a calm status page (docs/ux/home-v2-2026-10-19.md, owner 2026-10-19): it answers in five seconds "is it OK
 *  and does it need me". Five cards, nothing else: what needs the owner (the unchanged ProposalCard, or the blocks with
 *  their fixes, or "Nothing needs you"), a one-line system status, today's money, the strategies in words and the
 *  learning in one line. Everything about trades lives on the Trades screen (Open, Activity, Summary). */
export function Home({ onOpenProgress, onOpenTrades }: Props = {}) {
  return (
    <section className="home home-calm">
      <div className="home-needs"><CardBoundary name="Needs you"><NeedsYou onOpenTrades={onOpenTrades} /></CardBoundary></div>
      <div className="home-status"><CardBoundary name="System status"><SystemStatusLine /></CardBoundary></div>
      <div className="home-today"><CardBoundary name="Today"><TodayBrief onOpenTrades={onOpenTrades} /></CardBoundary></div>
      <div className="home-strategies"><CardBoundary name="Your strategies"><StrategiesCard /></CardBoundary></div>
      <div className="home-learning"><CardBoundary name="Learning"><LearningCard onOpenProgress={onOpenProgress} /></CardBoundary></div>
    </section>
  );
}

/** Card 1: the waiting trade itself (real money first); else every account that can't take new trades, with why and
 *  what fixes it; else one calm line. */
function NeedsYou({ onOpenTrades }: { onOpenTrades?: () => void }) {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  const pending = useQuery({ queryKey: ["proposals"], queryFn: api.proposals, refetchInterval: 2000 });
  const recent = useQuery({ queryKey: ["proposals-recent"], queryFn: api.recentProposals, refetchInterval: 2000 });
  const decide = useDecide();
  const now = useNow();
  const decidedIds = new Set((recent.data ?? []).map((d) => d.proposal_id));
  const open = orderWaiting((pending.data ?? []).filter((p) => !decidedIds.has(p.proposal_id)), accounts.data);
  const first = open[0];
  const held = accounts.data ? notOpen(accounts.data) : undefined;
  return (
    <section className="needs" aria-label="Needs you">
      {pending.isError && <p className="err" role="alert">Could not load waiting trades. Telegram approvals still work.</p>}
      {first ? (
        <div className="home-waiting">
          <h2 className="section-title">Trade waiting{open.length > 1 ? ` (1 of ${open.length})` : ""}</h2>
          <ProposalCard p={first} now={now} canDecide={hasRole("approver")}
                        blockers={blockers(status.data, accounts.data, first.account_id, first.agent_id)}
                        busy={decide.busyId === first.proposal_id} error={decide.errors[first.proposal_id]}
                        onApprove={() => decide.approve(first)} onReject={(r) => decide.reject(first, r)} />
          {open.length > 1 && onOpenTrades && (
            <button type="button" className="btn ghost home-link" onClick={onOpenTrades}>{open.length - 1} more waiting →</button>
          )}
        </div>
      ) : accounts.isLoading || accounts.isError || !held || held.length > 0 || accounts.data!.length === 0 ? (
        <EntryStatusPanel loading={accounts.isLoading} error={accounts.isError}
                          accounts={held && held.length > 0 ? held : accounts.data}
                          updatedAt={accounts.dataUpdatedAt || undefined} />
      ) : pending.isLoading ? <Skeleton height={96} label="Loading waiting trades" /> : (
        <div className="ui-card home-none">
          <h2>Nothing needs you</h2>
          <p className="muted">No trade waiting, and every account can take new trades. You'll get a Telegram ping when one needs you.</p>
        </div>
      )}
    </section>
  );
}
