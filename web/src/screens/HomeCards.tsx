// The cards of the Home status card stack (docs/ux/home-redesign-2026-10-19.md, "Decision 2026-10-19"). Each card
// runs its own query and sits in its own error boundary, so one failing source never blanks Home.
import { useQuery } from "@tanstack/react-query";
import { Component, useEffect, useState, type ReactNode } from "react";
import { api, type OpenPosition, type TodayAccount } from "../lib/api";
import {
  digest, HOME_POLL_MS, meterWords, modeBadge, shadowTiles, strategyName, WATCH_POLL_MS,
} from "../lib/home";
import { signedR, TRADES_POLL_MS } from "../lib/tradesOverview";
import { Badge, Button, Card, EmptyState, ErrorState, Meter, MetricTile, Skeleton, TimeText } from "../ui";
import { money } from "../ui/format";
import { Mt5Chart } from "./Mt5Chart";

/** One card's crash shows its own error; the other cards keep working. */
export class CardBoundary extends Component<{ name: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Card title={this.props.name} label={this.props.name}>
        <ErrorState title={`${this.props.name} could not be shown.`} stillWorks="The other cards and trading are not affected."
                    onRetry={() => this.setState({ failed: false })} />
      </Card>
    );
  }
}

function ModeBadge({ mode }: { mode: string | null | undefined }) {
  const b = modeBadge(mode);
  return <Badge tone={b === "REAL" ? "warn" : "info"}>{b}</Badge>;
}

const price = (x: number | null | undefined) => (x == null ? "—" : x.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const signed = (x: number | null | undefined, ccy: string | null | undefined) => (x == null ? "not known" : money(x, ccy, true));

function Stale({ age }: { age: number | null | undefined }) {
  return <p className="small state warn" role="status">{age == null ? "Figures of unknown age: not shown as current."
    : `Figures are ${Math.round(age)} s old (over 60 s): can't reach the engine, not shown as current.`}</p>;
}

// ------------------------------------------------------------------------------------------------ Today
/** Today's money per account: after costs, split closed / open, today's costs, and the limit that binds first. */
export function TodayCard() {
  const q = useQuery({ queryKey: ["today"], queryFn: api.today, refetchInterval: HOME_POLL_MS });
  return (
    <Card title="Today" label="Today" aside={q.dataUpdatedAt ? <>updated <TimeText iso={new Date(q.dataUpdatedAt).toISOString()} /></> : undefined}>
      {q.isLoading ? <Skeleton height={96} label="Loading today's money" />
        : q.isError && !q.data ? <ErrorState title="Could not load today's money." stillWorks="Trading and the loss limits are not affected." onRetry={() => q.refetch()} />
        : q.data!.accounts.length === 0 ? <EmptyState title="No engine is reporting">Today's money shows once an engine reports.</EmptyState>
        : q.data!.accounts.map((a) => <TodayRow key={a.account} a={a} />)}
    </Card>
  );
}

function TodayRow({ a }: { a: TodayAccount }) {
  const ccy = a.currency;
  const m = a.loss_meter ? meterWords(a.loss_meter, (x) => money(x, ccy)) : null;
  return (
    <div className="today-row" aria-label={`Today ${a.account}`} role="group">
      <div className="today-head"><ModeBadge mode={a.mode} /><strong>{a.account}</strong></div>
      {a.stale && <Stale age={a.age_s} />}
      <div className="today-figs">
        <MetricTile label="Day P&L after costs" value={signed(a.day_pnl, ccy)} />
        <MetricTile label="Closed" value={signed(a.closed_pnl, ccy)}
                    sub={`${a.closed_n} trade${a.closed_n === 1 ? "" : "s"}${a.closed_complete ? "" : ", some records left out"}`} />
        <MetricTile label="Open" value={signed(a.open_pnl, ccy)} sub="floating" />
      </div>
      {a.costs && (
        <p className="small muted">Costs today: spread {money(a.costs.spread, ccy)}{a.costs.spread_complete ? "" : " (where recorded)"} ·
          commission {money(a.costs.commission, ccy)} · swap {money(a.costs.swap, ccy, true)}</p>
      )}
      {a.loss_meter && m ? (
        <div className="today-meter">
          <Meter value={a.loss_meter.used} max={m.limit} tone={m.tone} label={`Loss today against the ${a.loss_meter.binding === "probe_stop" ? "probe stop" : "daily loss limit"}`}
                 text={`${money(a.loss_meter.used, ccy)} lost of ${money(m.limit, ccy)}`} />
          <p className={`small ${m.tone === "ok" ? "muted" : `state ${m.tone}`}`}>{m.text}</p>
        </div>
      ) : <p className="small muted">Loss limit not reported by this engine yet.</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Open trades
export function OpenTradesCard() {
  const q = useQuery({ queryKey: ["positionsOpen"], queryFn: api.openPositions, refetchInterval: HOME_POLL_MS });
  const v = q.data;
  return (
    <Card title={`Open trades${v ? ` (${v.positions.length})` : ""}`} label="Open trades">
      {q.isLoading ? <Skeleton height={72} label="Loading open trades" />
        : q.isError && !v ? <ErrorState title="Could not load open trades." stillWorks="Open trades keep their stop, target and automatic exits." onRetry={() => q.refetch()} />
        : <>
            {v!.unreadable.length > 0 && <p className="small state warn">{`Couldn't read the open trades of ${v!.unreadable.join(", ")}: missing, not zero.`}</p>}
            {v!.positions.length === 0 ? <p className="muted">No open trades.</p> : (
              <ul className="open-list" aria-label="Open trades">{v!.positions.map((p) => <OpenRow key={`${p.account}-${p.position_id}`} p={p} />)}</ul>
            )}
          </>}
    </Card>
  );
}

function OpenRow({ p }: { p: OpenPosition }) {
  return (
    <li className="open-row">
      <span className="open-what"><ModeBadge mode={p.mode} />{p.probe && <Badge tone="idle">probe</Badge>}
        <strong className={p.side}>{p.side.toUpperCase()}</strong> {p.lots} lot · {strategyName(p.family, p.timeframe)}</span>
      <span className="open-money">{p.floating_pnl == null ? "money not known" : money(p.floating_pnl, p.currency, true)} · {signedR(p.r_now)}{p.stale ? " · stale" : ""}</span>
      <span className="open-levels small muted">entry {price(p.entry)} · stop {price(p.stop)} · target {price(p.target)} USD/oz · opened <TimeText iso={p.opened_utc} /></span>
    </li>
  );
}

// ------------------------------------------------------------------------------------------------ Your strategies
const STATE_TONE = { in_trade: "info", signal: "ok", watching: "idle", idle: "idle", unknown: "warn" } as const;
const STATE_WORD = { in_trade: "in a trade", signal: "signal", watching: "watching", idle: "idle", unknown: "unknown" } as const;

export function StrategiesCard() {
  const q = useQuery({ queryKey: ["strategyWatch"], queryFn: api.strategyWatch, refetchInterval: WATCH_POLL_MS });
  const v = q.data;
  return (
    <Card title="Your strategies" label="Your strategies">
      {q.isLoading ? <Skeleton height={96} label="Loading strategies" />
        : q.isError && !v ? <ErrorState title="Could not load the strategies' states." stillWorks="The strategies keep running." onRetry={() => q.refetch()} />
        : <>
            {v!.unreadable.length > 0 && <p className="small state warn">{`Couldn't read: ${v!.unreadable.join(", ")}. Those states are unknown.`}</p>}
            {v!.agents.length === 0 ? <p className="muted">No strategy agent is running yet.</p> : (
              <ul className="watch-list" aria-label="Strategy states">
                {v!.agents.map((w) => (
                  <li key={w.agent_id} className="watch-row">
                    <span className="watch-name">{strategyName(w.family, w.timeframe)}</span>
                    <Badge tone={STATE_TONE[w.state]}>{STATE_WORD[w.state]}</Badge>
                    <span className="small">{w.text}{w.signal_utc && <> at <TimeText iso={w.signal_utc} withDay /></>}</span>
                  </li>
                ))}
              </ul>
            )}
          </>}
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ Learning
export function LearningCard({ onOpenProgress }: { onOpenProgress?: () => void }) {
  const q = useQuery({ queryKey: ["tradesOverview"], queryFn: api.tradesOverview, refetchInterval: TRADES_POLL_MS });
  const v = q.data;
  return (
    <Card title="Learning" label="Learning">
      {q.isLoading ? <Skeleton height={96} label="Loading learning" />
        : q.isError && !v ? <ErrorState title="Could not load what the system learned." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
        : <>
            <div className="tiles-grid learning-tiles">
              {shadowTiles(v!).map((t) => (
                <MetricTile key={t.label} label={t.label} value={signedR(t.avgR)} sub={`n ${t.n} · shadow, not money`} />
              ))}
            </div>
            {(() => {
              const lines = digest(v!);
              return lines.length === 0 ? <p className="small muted">Nothing changed enough to report since last week.</p> : (
                <ul className="digest small" aria-label="Since you last looked">{lines.map((l) => <li key={l.family}>{l.text} (shadow, not money)</li>)}</ul>
              );
            })()}
            {onOpenProgress && <Button variant="ghost" onClick={onOpenProgress}>Road to live ›</Button>}
          </>}
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ MT5 chart
const WIDE = "(min-width: 1080px)";

/** The broker's own chart (MT5 picture), collapsed by default and only on a desktop: nothing is fetched on a phone. */
export function ChartCard() {
  const [wide, setWide] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(WIDE).matches);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.(WIDE);
    if (!mq) return;
    const on = () => setWide(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  if (!wide) return null;
  return (
    <section className="ui-card home-chart" aria-label="MT5 chart">
      <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
        <summary><h2>MT5 chart, 15-minute</h2></summary>
        {open && <Mt5Chart tf="15" />}
      </details>
    </section>
  );
}
