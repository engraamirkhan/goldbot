import { useQuery } from "@tanstack/react-query";
import { api, hasRole } from "../lib/api";
import { ActivityFeed } from "./Activity";
import { TradesSummaryCard } from "./TradesOverview";
import { blockers } from "../lib/approval";
import { orderWaiting } from "../lib/home";
import { useNow } from "../lib/useNow";
import { BalanceCard, BalanceCardSkeleton } from "./BalanceCard";
import { EntryStatusPanel } from "./EntryStatus";
import { CardBoundary, ChartCard, LearningCard, OpenTradesCard, StrategiesCard, TodayCard } from "./HomeCards";
import { ProposalCard } from "./ProposalCard";
import { useDecide } from "./useDecide";
import "./home.css";

/** Home is the status card stack (docs/ux/home-redesign-2026-10-19.md, "Decision 2026-10-19"): one question per card,
 *  in this order on a phone and the same cards in a grid on a desktop. "A waiting real-money proposal is always card 1"
 *  (the unchanged ProposalCard); then can it trade, today's money, open trades, the owner's strategies, learning, recent
 *  activity and the trades summary. "Each card its own query (one failing source never blanks Home)". */
export function Home({ onOpenProgress }: { onOpenProgress?: () => void; onOpenLimits?: () => void } = {}) {
  return (
    <section className="home home-stack">
      <div className="home-needs"><CardBoundary name="Needs you"><NeedsYou /></CardBoundary></div>
      <div className="home-side">
        <CardBoundary name="Can it trade?"><CanItTrade /></CardBoundary>
        <CardBoundary name="Today"><TodayCard /></CardBoundary>
      </div>
      <div className="home-open">
        <CardBoundary name="Open trades"><OpenTradesCard /></CardBoundary>
        <CardBoundary name="MT5 chart"><ChartCard /></CardBoundary>
      </div>
      <div className="home-strategies"><CardBoundary name="Your strategies"><StrategiesCard /></CardBoundary></div>
      <div className="home-learning"><CardBoundary name="Learning"><LearningCard onOpenProgress={onOpenProgress} /></CardBoundary></div>
      <div className="home-recent"><CardBoundary name="Activity"><ActivityFeed /></CardBoundary></div>
      <div className="home-wide"><CardBoundary name="Trades summary"><TradesSummaryCard /></CardBoundary></div>
    </section>
  );
}

/** Card 1: the waiting trade itself (real money first), or one line saying nothing needs the owner. */
function NeedsYou() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  const pending = useQuery({ queryKey: ["proposals"], queryFn: api.proposals, refetchInterval: 2000 });
  const recent = useQuery({ queryKey: ["proposals-recent"], queryFn: api.recentProposals, refetchInterval: 2000 });
  const decide = useDecide();
  const now = useNow();
  const decidedIds = new Set((recent.data ?? []).map((d) => d.proposal_id));
  const open = orderWaiting((pending.data ?? []).filter((p) => !decidedIds.has(p.proposal_id)), accounts.data);
  const first = open[0];
  return (
    <>
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
    </>
  );
}

/** Card 2: can each account take new trades (entry status: the reasons and fixes), with the broker's balance. */
function CanItTrade() {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  return (
    <>
      <EntryStatusPanel loading={accounts.isLoading} error={accounts.isError} accounts={accounts.data}
                        updatedAt={accounts.dataUpdatedAt || undefined} />
      {accounts.isLoading ? <div className="accts"><BalanceCardSkeleton /></div> : (accounts.data?.length ?? 0) > 0 && (
        <div className="accts">{accounts.data!.map((a) => <BalanceCard key={a.account_id} a={a} />)}</div>
      )}
    </>
  );
}
