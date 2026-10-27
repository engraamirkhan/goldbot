// Trades > Today (docs/design/trades-ux-2026-10-27.md §3.5, backlog 131): one card per account, real money first and
// on its own card. Is the day on plan? Fills against the plan's target and pace, net £ after costs (closed) by kind,
// open risk and P&L, caps used, the 14-day £ strip against the daily target (ADR 0004), week and month to date with the
// costs split. Never summed across accounts, demo and real, or currencies. Fields an older server doesn't send are
// left out (never shown as zero); the broker's balance and today's risk per trade follow below.
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { meterWords, modeBadge } from "../../lib/home";
import {
  capWords, type DayNet, fillsWords, KIND_ORDER, KIND_WORDS, moneyOrDash, type PeriodNet, POLL, stripScale, type TodayAccountX,
} from "../../lib/trades";
import { Badge, Card, EmptyState, ErrorState, Meter, Skeleton, TimeText } from "../../ui";
import { money, seconds } from "../../ui/format";
import { BalanceCard, BalanceCardSkeleton } from "../BalanceCard";
import { CardBoundary, RiskTodayCard } from "../HomeCards";
import "./trades.css";

export function TradesToday() {
  return (
    <div className="trades-page">
      <CardBoundary name="Today"><TodayAccounts /></CardBoundary>
      <CardBoundary name="Balance"><Balances /></CardBoundary>
      <CardBoundary name="Risk per trade today"><RiskTodayCard /></CardBoundary>
    </div>
  );
}

function TodayAccounts() {
  const q = useQuery({ queryKey: ["today"], queryFn: api.today, refetchInterval: POLL.today });
  if (q.isLoading) return <Skeleton height={220} label="Loading today's money" />;
  if (q.isError && !q.data) return <ErrorState title="Couldn't load today's money." stillWorks="Trading and the loss limits are not affected." onRetry={() => q.refetch()} />;
  const accts = [...q.data!.accounts].sort((a, b) => Number(b.mode === "live") - Number(a.mode === "live"));
  if (accts.length === 0) return <Card title="Today" label="Today"><EmptyState title="No engine is reporting">Today's money shows once an engine reports.</EmptyState></Card>;
  return <>{accts.map((a) => <AccountDay key={a.account} a={a} updated={q.dataUpdatedAt} />)}</>;
}

function AccountDay({ a, updated }: { a: TodayAccountX; updated: number }) {
  const ccy = a.currency;
  const label = a.account_label || a.account;
  const fills = fillsWords(a);
  const m = a.loss_meter ? meterWords(a.loss_meter, (x) => money(x, ccy)) : null;
  const totalCosts = a.costs ? a.costs.spread + a.costs.commission - a.costs.swap : null;
  const kinds = KIND_ORDER.filter((k) => a.by_kind?.[k]);
  return (
    <Card label={`Today ${label}`} title={<><Badge tone={modeBadge(a.mode) === "REAL" ? "warn" : "info"}>{modeBadge(a.mode)}</Badge> {label}</>}
          aside={updated ? <>updated <TimeText iso={new Date(updated).toISOString()} /></> : undefined}>
      {a.stale && <p className="small state warn" role="status">{a.age_s == null ? "Figures of unknown age: not shown as current."
        : `Figures are ${seconds(Math.round(a.age_s))} old: the engine isn't reporting; not shown as current.`}</p>}
      {(a.halted || a.paused) && <p className="small state bad">{a.halted ? "Halted: no new entries." : "Paused at −8%: no new entries."}</p>}
      <p className="day-head"><span className="display num">{moneyOrDash(a.day_pnl, ccy)}</span> <span className="muted small">day P&amp;L after costs (broker)</span></p>
      <p className="small num">Closed today: <strong>{moneyOrDash(a.closed_pnl, ccy)}</strong> net after costs from {a.closed_n} trade{a.closed_n === 1 ? "" : "s"}
        {totalCosts != null && <> (costs {money(totalCosts, ccy)})</>}{a.closed_complete ? "" : ", some records left out"} · open {moneyOrDash(a.open_pnl, ccy)}</p>
      {kinds.length > 0 && (
        <p className="small num by-kind">{kinds.map((k, i) => {
          const d = a.by_kind![k]!;
          return <span key={k}>{i > 0 && " · "}{KIND_WORDS[k]} {d.net_pnl == null ? "hidden" : money(d.net_pnl, ccy, true)} <span className="muted">({d.fills} fill{d.fills === 1 ? "" : "s"})</span></span>;
        })}</p>
      )}
      {fills && (
        <div className="fills">
          <p className="num"><strong>Fills {fills.text}</strong>{fills.pace && <span className="muted"> · {fills.pace}</span>}</p>
          {a.fills_target ? <Meter value={a.fills_today ?? 0} max={a.fills_target} label={`Fills today against the target of ${a.fills_target}`}
                                   text={`${a.fills_today} of ${a.fills_target}`} tone="info" /> : null}
          {a.quiet_day && <p className="small muted">Quiet day: fewer trades is expected.</p>}
        </div>
      )}
      {a.open_n != null && <p className="small num">Open: {a.open_n} trade{a.open_n === 1 ? "" : "s"} · risk {moneyOrDash(a.open_risk, ccy, false)} · P&amp;L now {moneyOrDash(a.open_pnl, ccy)}</p>}
      {(a.day_cap_used || a.week_cap_used || a.exploration_pot_used) ? (
        <p className="small num">Caps: day {capWords(a.day_cap_used, ccy)} used · week {capWords(a.week_cap_used, ccy)}
          {a.exploration_pot_used && <> · exploration pot {capWords(a.exploration_pot_used, ccy)}</>}</p>
      ) : a.loss_meter && m ? (
        <div className="today-meter">
          <Meter value={a.loss_meter.used} max={m.limit} tone={m.tone} label={`Loss today against the ${a.loss_meter.binding === "probe_stop" ? "probe stop" : "daily loss limit"}`}
                 text={`${money(a.loss_meter.used, ccy)} lost of ${money(m.limit, ccy)}`} />
          <p className={`small ${m.tone === "ok" ? "muted" : `state ${m.tone}`}`}>{m.text}</p>
        </div>
      ) : <p className="small muted">Loss limit not reported by this engine yet.</p>}
      {a.days && a.days.length > 0 && <DayStrip days={a.days} target={a.day_target ?? null} ccy={ccy} label={label} />}
      {(a.wtd || a.mtd) && (
        <dl className="period-net small num">
          {a.wtd && <PeriodLine name="Week to date" p={a.wtd} ccy={ccy} />}
          {a.mtd && <PeriodLine name="Month to date" p={a.mtd} ccy={ccy} />}
        </dl>
      )}
    </Card>
  );
}

function PeriodLine({ name, p, ccy }: { name: string; p: PeriodNet; ccy: string | null }) {
  return (
    <div><dt>{name}</dt><dd><strong>{moneyOrDash(p.net_pnl, ccy)}</strong> after costs
      {p.costs && <span className="muted"> (spread {money(p.costs.spread, ccy)} · commission {money(p.costs.commission, ccy)} · swap {money(p.costs.swap, ccy, true)})</span>}</dd></div>
  );
}

const SW = 336;
const SH = 72;

/** 14 trading days of net £ after costs against the daily target: bars above or below the zero line (the sign is the
 *  direction, not a colour), a dashed target line, a dot under each opportunity day, and the same figures as a table
 *  for screen readers. */
export function DayStrip({ days, target, ccy, label }: { days: DayNet[]; target: number | null; ccy: string | null; label: string }) {
  const scale = stripScale(days, target);
  const mid = SH / 2 - 4;
  const half = mid - 4;
  const w = SW / days.length;
  const y = (v: number) => mid - (v / scale) * half;
  const day = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  return (
    <figure className="day-strip">
      <figcaption className="small">Last {days.length} trading days, net after costs{target != null && <> · target {money(target, ccy)} a day (dashed)</>} · <span aria-hidden="true">•</span> opportunity day</figcaption>
      <svg viewBox={`0 0 ${SW} ${SH}`} width="100%" height={SH} role="img"
           aria-label={`${label}: net after costs per day for the last ${days.length} trading days; the table below has the figures`}>
        <line className="zero" x1={0} x2={SW} y1={mid} y2={mid} />
        {target != null && <line className="target" x1={0} x2={SW} y1={y(target)} y2={y(target)} strokeDasharray="4 3" />}
        {days.map((d, i) => {
          const v = d.net_pnl;
          const x = i * w + w * 0.2;
          if (v == null) return <text key={d.date} className="na" x={i * w + w / 2} y={mid + 4} textAnchor="middle">?</text>;
          const top = Math.min(y(v), mid);
          return (
            <g key={d.date}>
              <rect className={v >= 0 ? "pos" : "neg"} x={x} y={top} width={w * 0.6} height={Math.max(1, Math.abs(y(v) - mid))} />
              {d.opportunity && <circle className="opp" cx={i * w + w / 2} cy={SH - 3} r={2.2} />}
            </g>
          );
        })}
      </svg>
      <table className="sr-only">
        <caption>{label}: net after costs per trading day</caption>
        <thead><tr><th>Day</th><th>Net</th><th>Opportunity day</th></tr></thead>
        <tbody>{days.map((d) => <tr key={d.date}><td>{day(d.date)}</td><td>{d.net_pnl == null ? "not known" : money(d.net_pnl, ccy, true)}</td><td>{d.opportunity ? "yes" : "no"}</td></tr>)}</tbody>
      </table>
    </figure>
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
