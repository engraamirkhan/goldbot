// The cards of Home (docs/ux/home-v2-2026-10-19.md: a calm status page) and of the Trades screen (Today: risk per trade). Each card runs its
// own query and sits in its own error boundary, so one failing source never blanks the page.
import { useQuery } from "@tanstack/react-query";
import { Component, type ReactNode } from "react";
import { api, type TodayAccount, type TradesOverview } from "../lib/api";
import {
  digest, HOME_POLL_MS, meterWords, modeBadge, openTradesWords, shadowTiles, strategyName, systemLine, WATCH_POLL_MS,
} from "../lib/home";
import { signedR, TRADES_POLL_MS } from "../lib/tradesOverview";
import { ageSeconds, type LimitAccount } from "../lib/limits";
import { type Budget, factorWords, leftState, rateText, riskTodayLine, strategyRows, whyShort } from "../lib/riskToday";
import { useNow } from "../lib/useNow";
import { Badge, Button, Card, EmptyState, ErrorState, Meter, Skeleton, TimeText } from "../ui";
import { money, NBSP, seconds } from "../ui/format";

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

const signed = (x: number | null | undefined, ccy: string | null | undefined) => (x == null ? "not known" : money(x, ccy, true));

function Stale({ age }: { age: number | null | undefined }) {
  return <p className="small state warn" role="status">{age == null ? "Figures of unknown age: not shown as current."
    : `Figures are ${seconds(Math.round(age))} old (over ${seconds(60)}): can't reach the engine, not shown as current.`}</p>;
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

// ------------------------------------------------------------------------------------------------ Learning (Home)
/** One compact card: the shadow book's 7 days (R with n, never money) and the largest change since last week. */
export function LearningCard({ onOpenProgress }: { onOpenProgress?: () => void }) {
  const q = useQuery({ queryKey: ["tradesOverview"], queryFn: api.tradesOverview, refetchInterval: TRADES_POLL_MS });
  const v = q.data;
  return (
    <Card title="Learning" label="Learning">
      {q.isLoading ? <Skeleton height={64} label="Loading learning" />
        : q.isError && !v ? <ErrorState title="Could not load what the system learned." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
        : <LearningBody v={v!} onOpenProgress={onOpenProgress} />}
    </Card>
  );
}

function LearningBody({ v, onOpenProgress }: { v: TradesOverview; onOpenProgress?: () => void }) {
  const [all, taken] = shadowTiles(v);
  const top = digest(v, 1)[0];
  return (
    <>
      <p className="learn-line">Shadow trades, 7 days: every candidate <strong className="num">{signedR(all.avgR)}</strong> per
        trade ({`n${NBSP}${all.n}`}) · taken <strong className="num">{signedR(taken.avgR)}</strong> ({`n${NBSP}${taken.n}`}) <span className="muted small">· shadow, not money</span></p>
      <p className="small muted">{top ? `Biggest change: ${top.text}.` : "Nothing changed enough to report since last week."}</p>
      {onOpenProgress && <Button variant="ghost" className="home-link" onClick={onOpenProgress}>Road to live ›</Button>}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ Today (Home)
/** Today in one line per account (DEMO/REAL, never summed), the loss meter, and the way to the trades. */
export function TodayBrief({ onOpenTrades }: { onOpenTrades?: () => void }) {
  const q = useQuery({ queryKey: ["today"], queryFn: api.today, refetchInterval: HOME_POLL_MS });
  const pos = useQuery({ queryKey: ["positionsOpen"], queryFn: api.openPositions, refetchInterval: HOME_POLL_MS });
  const limits = useQuery({ queryKey: ["limits"], queryFn: api.limits, refetchInterval: 30000 });
  const n = pos.data?.positions.length;
  const link = n == null ? (pos.isError ? "Open trades not readable · see trades" : "See trades")
    : n === 0 ? "No open trades · see trades" : openTradesWords(n);
  return (
    <Card title="Today" label="Today" aside={q.dataUpdatedAt ? <>updated <TimeText iso={new Date(q.dataUpdatedAt).toISOString()} /></> : undefined}>
      {q.isLoading ? <Skeleton height={72} label="Loading today's money" />
        : q.isError && !q.data ? <ErrorState title="Could not load today's money." stillWorks="Trading and the loss limits are not affected." onRetry={() => q.refetch()} />
        : q.data!.accounts.length === 0 ? <EmptyState title="No engine is reporting">Today's money shows once an engine reports.</EmptyState>
        : q.data!.accounts.map((a) => <TodayLine key={a.account} a={a}
            limits={limits.data?.accounts.find((l) => l.account_id === a.account)} />)}
      {onOpenTrades && <Button variant="ghost" className="home-link" onClick={onOpenTrades}>{link} →</Button>}
    </Card>
  );
}

function TodayLine({ a, limits }: { a: TodayAccount; limits?: LimitAccount }) {
  const ccy = a.currency;
  const risk = limits ? riskTodayLine(limits, (x) => money(x, limits.currency ?? ccy)) : null;
  const m = a.loss_meter ? meterWords(a.loss_meter, (x) => money(x, ccy)) : null;
  return (
    <div className="today-row" aria-label={`Today ${a.account}`} role="group">
      <p className="today-line"><ModeBadge mode={a.mode} /> <strong>{a.account}</strong>{" "}
        <span className="today-pnl num">{signed(a.day_pnl, ccy)}</span> <span className="muted small">today after costs</span></p>
      <p className="small muted num">closed {signed(a.closed_pnl, ccy)} ({a.closed_n} trade{a.closed_n === 1 ? "" : "s"}
        {a.closed_complete ? "" : ", some left out"}) · open {signed(a.open_pnl, ccy)}</p>
      {a.stale && <Stale age={a.age_s} />}
      {a.loss_meter && m ? (
        <div className="today-meter">
          <Meter value={a.loss_meter.used} max={m.limit} tone={m.tone} label={`Loss today against the ${a.loss_meter.binding === "probe_stop" ? "probe stop" : "daily loss limit"}`}
                 text={`${money(a.loss_meter.used, ccy)} lost of ${money(m.limit, ccy)}`} />
          <p className={`small ${m.tone === "ok" ? "muted" : `state ${m.tone}`}`}>{m.text}</p>
        </div>
      ) : <p className="small muted">Loss limit not reported by this engine yet.</p>}
      {risk && <p className="small num">{risk}{limits?.stale ? ` · not current (engine figures over ${seconds(60)} old)` : ""}</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Risk per trade today
/** Dynamic limits (ADR 0039): per account and strategy the risk per trade applied today, the day's cuts in plain
 *  words and the money left before the daily loss limit. Read-only; red only for a spent cap. */
export function RiskTodayCard() {
  const q = useQuery({ queryKey: ["limits"], queryFn: api.limits, refetchInterval: 30000 });
  const budgets = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: 5000 });
  const aside = q.dataUpdatedAt ? <>updated <TimeText iso={new Date(q.dataUpdatedAt).toISOString()} /></> : undefined;
  const accts = q.data?.accounts ?? [];
  return (
    <Card title="Risk per trade today" aside={aside} label="Risk per trade">
      {q.isLoading ? <Skeleton height={120} label="Loading risk per trade today" />
        : q.isError && !q.data ? <ErrorState title="Could not load today's risk per trade." stillWorks="Trading and the loss limits are not affected." onRetry={() => q.refetch()} />
        : accts.length === 0 ? <EmptyState title="No engine is reporting">Risk per trade shows once an engine reports.</EmptyState>
        : accts.map((a) => <RiskTodayAccount key={a.account_id} a={a}
            budget={budgets.data?.find((b) => b.account_id === a.account_id)?.loss_budget} />)}
    </Card>
  );
}

function RiskTodayAccount({ a, budget }: { a: LimitAccount; budget?: Budget | null }) {
  const fmt = (x: number) => money(x, a.currency);
  const r = a.risk_today;
  const left = leftState(a, fmt, budget);
  const rows = strategyRows(a);
  const cuts = r ? factorWords(r) : [];
  const age = ageSeconds(a.updated_utc, useNow(5_000));
  return (
    <div className="today-row" role="group" aria-label={`Risk today ${a.account_id}`}>
      <p className="today-line"><ModeBadge mode={a.mode} /> <strong>{a.account_id}</strong></p>
      {a.stale && <Stale age={age} />}
      <p className={`small num ${left.tone === "ok" || left.tone === "idle" ? "muted" : `state ${left.tone}`}`}>
        {left.tone === "bad" ? "Stopped: " : left.tone === "warn" ? "Near limit: " : ""}{left.text}</p>
      {!r ? <p className="small muted">Dynamic limits are off: every strategy risks the fixed rate per trade (see Limits).</p> : (
        <>
          <p className="small">{r.base != null && <>Base {rateText(r.base)} per trade{r.ceiling != null && <>, at most {rateText(r.ceiling)}</>}. </>}
            Today's cuts: {cuts.length ? cuts.join("; ") : "none"}{r.scale != null && r.scale < 1 ? ` (× ${r.scale.toFixed(2)} until the next risk day)` : ""}.</p>
          {rows.length === 0 ? <p className="small muted">Every strategy trades the base rate ({whyShort(r)}).</p> : (
            <div className="scroll"><table>
              <caption className="sr-only">Risk per trade today by strategy, {a.account_id}</caption>
              <thead><tr><th scope="col">Strategy</th><th scope="col" className="num">Risk</th>
                <th scope="col" className="num">{a.currency ?? "Money"} per trade</th><th scope="col">Why</th></tr></thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.version}>
                    <th scope="row" className="small">{s.version}</th>
                    <td className="num">{rateText(s.rate)}</td>
                    <td className="num">{s.money == null ? "—" : `≈${fmt(s.money)}`}</td>
                    <td className="small">{s.base ? "base rate" : s.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
          <p className="small muted">≈ money at the stop on the day-start equity; the allocator's multiplier and lot rounding set the final size.</p>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ System status
/** Engine, prices and market in one line, each a dot plus words (never colour alone); its own queries. Never red. */
export function SystemStatusLine() {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts, refetchInterval: HOME_POLL_MS });
  const feeds = useQuery({ queryKey: ["feeds"], queryFn: api.feeds, refetchInterval: HOME_POLL_MS * 3 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: HOME_POLL_MS });
  const now = useNow(5_000);
  const parts = systemLine({ accounts: accounts.data, accountsError: accounts.isError, feeds: feeds.data,
                             feedsError: feeds.isError, status: status.data, now });
  return (
    <section className="ui-card sys-line" aria-label="System status">
      <h2 className="sr-only">System status</h2>
      <ul>
        {parts.map((p) => (
          <li key={p.key} className={`sys-part ${p.tone}`}>
            <span className="sys-dot" aria-hidden="true" />
            <span className="sys-label">{p.label}</span> <span className="sys-word">{p.word}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
