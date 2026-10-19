// Home trades summary (owner request 2026-10-19: "a summary of all types of trades"): GET /api/trades/overview, asked
// for every 30 s. Read-only. One tile per trade type (open, closed, win rate, net money or R), a period switch, and a
// compact table by strategy or timeframe. "Open" is the trades open now, the same in every period.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type TradesOverview } from "../lib/api";
import { SOURCE_WORDS } from "../lib/activity";
import {
  BREAKDOWNS, bestWorst, type Breakdown, counts, exits, headline, netPnl, type Period, PERIODS, resultTone, rows,
  signedR, TRADES_POLL_MS, TYPE_WORDS, winRate,
} from "../lib/tradesOverview";
import { Card, EmptyState, ErrorState, MetricTile, Segmented, Skeleton, TimeText } from "../ui";

export function TradesSummaryCard() {
  const q = useQuery({ queryKey: ["tradesOverview"], queryFn: api.tradesOverview, refetchInterval: TRADES_POLL_MS });
  const aside = q.dataUpdatedAt ? <>updated <TimeText iso={new Date(q.dataUpdatedAt).toISOString()} /></> : undefined;
  return (
    <Card title="Trades summary" aside={aside} label="Trades summary">
      {q.isLoading ? <Skeleton height={160} label="Loading trades summary" />
        : q.isError && !q.data ? <ErrorState title="Could not load the trades summary." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
        : <Summary v={q.data!} />}
    </Card>
  );
}

function Summary({ v }: { v: TradesOverview }) {
  const [period, setPeriod] = useState<Period>("today");
  const [by, setBy] = useState<Breakdown>("family");
  const p = v.periods.find((x) => x.period === period);
  const table = rows(v, period, by);
  return (
    <>
      {v.unreadable.length > 0 && (
        <p className="small state warn">{`Couldn't read: ${v.unreadable.map((s) => SOURCE_WORDS[s]).join(", ")}. Those trades are missing, not zero.`}</p>
      )}
      <Segmented items={PERIODS} value={period} onChange={setPeriod} label="Period" />
      <div className="tiles-grid trades-tiles">
        {p?.types.map((t) => (
          <MetricTile key={t.type} label={TYPE_WORDS[t.type]} value={headline(t.stats)} tone={resultTone(t.stats)}
                      sub={<>{counts(t.stats)}{bestWorst(t.stats) && <><br />{bestWorst(t.stats)}</>}</>} />
        ))}
      </div>
      <Segmented items={BREAKDOWNS} value={by} onChange={setBy} label="Breakdown" />
      {table.length === 0 ? (
        <EmptyState title="No trades in this period">Shadow, demo, live and cost-probe trades are counted here as they open and close.</EmptyState>
      ) : (
        <div className="trades-table">
          <table aria-label="Trades by strategy and timeframe">
            <thead>
              <tr><th>Type</th><th>{by === "family" ? "Strategy" : "Timeframe"}</th><th className="num">Open</th>
                <th className="num">Closed</th><th className="num">Won</th><th className="num">R</th><th className="num">Net</th>
                <th className="trades-exits">Exits</th></tr>
            </thead>
            <tbody>
              {table.map((r) => (
                <tr key={`${r.type}-${r.key}`}>
                  <td>{TYPE_WORDS[r.type]}</td><td>{r.key}</td><td className="num">{r.stats.open}</td>
                  <td className="num">{r.stats.closed}</td><td className="num">{winRate(r.stats)}</td>
                  <td className="num">{signedR(r.stats.total_r)}</td><td className="num">{netPnl(r.stats)}</td>
                  <td className="trades-exits small">{exits(r.stats)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
