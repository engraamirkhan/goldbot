// The Trades screen (docs/ux/home-v2-2026-10-19.md): everything about trades, moved off Home on the owner's request
// (2026-10-19). Three tabs: Open (decisions waiting or just made, then the open trades with stop, target, R and
// floating money), Activity (every shadow, demo, live and probe trade and every refused entry, newest first) and
// Summary (today's money in full, the account balance and the trades summary). Each card its own query and boundary.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { ErrorState } from "../ui";
import { ActivityFeed } from "./Activity";
import { Approvals } from "./Approvals";
import { BalanceCard, BalanceCardSkeleton } from "./BalanceCard";
import { CardBoundary, OpenTradesCard, TodayCard } from "./HomeCards";
import { TradesSummaryCard } from "./TradesOverview";
import "./home.css";

export function TradesOpen() {
  return (
    <div className="trades-page">
      <CardBoundary name="Decisions"><Approvals /></CardBoundary>
      <CardBoundary name="Open trades"><OpenTradesCard /></CardBoundary>
    </div>
  );
}

export function TradesActivity() {
  return (
    <div className="trades-page">
      <p className="muted small">Shadow, demo and live trades, cost probes and refused entries, newest first. Refused
        entries are the risk check doing its job: no order was sent.</p>
      <CardBoundary name="Activity"><ActivityFeed /></CardBoundary>
    </div>
  );
}

export function TradesSummary() {
  return (
    <div className="trades-page">
      <CardBoundary name="Today"><TodayCard /></CardBoundary>
      <CardBoundary name="Balance"><Balances /></CardBoundary>
      <CardBoundary name="Trades summary"><TradesSummaryCard /></CardBoundary>
    </div>
  );
}

/** The broker's balance, loss budget and milestones per account (BalanceCard, unchanged). */
function Balances() {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  if (accounts.isLoading) return <div className="accts"><BalanceCardSkeleton /></div>;
  if (accounts.isError && !accounts.data) {
    return <ErrorState title="Could not load the balances." stillWorks="Trading and the loss limits are not affected." onRetry={() => accounts.refetch()} />;
  }
  if ((accounts.data?.length ?? 0) === 0) return null;
  return <div className="accts">{accounts.data!.map((a) => <BalanceCard key={a.account_id} a={a} />)}</div>;
}
