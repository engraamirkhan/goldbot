// Trades > Now (docs/design/trades-ux-2026-10-27.md §3.1, backlog 128 and 130): what needs me, then what is open.
// A waiting real-money proposal sits on top (the approval flow, unchanged); then the open trades grouped by account,
// real money first, each row with its money: P&L now and risk in £, R, levels and time held. Rows are fixed height
// and every live number sits in a tabular box, so a 5 s poll never moves the page.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../lib/api";
import { modeBadge } from "../../lib/home";
import {
  groupOpen, heldWords, kindBadge, KIND_WORDS, moneyOrDash, type OpenGroup, type OpenPositionX, openRowName, type OpenTarget, openTarget, POLL, price, sideArrow,
  sideWords, signedR, strategyLabel, type TradeKind,
} from "../../lib/trades";
import { useWide } from "../../lib/useMedia";
import { Badge, Card, EmptyState, ErrorState, TimeText } from "../../ui";
import { lots, seconds } from "../../ui/format";
import { Approvals } from "../Approvals";
import { CardBoundary } from "../HomeCards";
import "./trades.css";

const FIRST = 10;

export function TradesNow({ onOpen }: { onOpen?: (t: OpenTarget) => void }) {
  return (
    <div className="trades-page">
      <CardBoundary name="Decisions"><Approvals /></CardBoundary>
      <CardBoundary name="Open trades"><OpenNow onOpen={onOpen} /></CardBoundary>
    </div>
  );
}

const kindOf = (p: OpenPositionX): TradeKind => p.kind ?? (p.mode === "live" ? "real" : p.probe ? "exploration" : "strategy");

export function OpenNow({ onOpen }: { onOpen?: (t: OpenTarget) => void }) {
  const q = useQuery({ queryKey: ["positionsOpen"], queryFn: api.openPositions, refetchInterval: POLL.now });
  const wide = useWide();
  const [acct, setAcct] = useState("");
  const [kind, setKind] = useState<"" | TradeKind>("");
  const v = q.data;
  const all = v?.positions ?? [];
  const shown = all.filter((p) => (!acct || p.account === acct) && (!kind || kindOf(p) === kind));
  const groups = groupOpen(shown);
  const accounts = groupOpen(all).map((g) => ({ key: g.key, label: g.label }));
  const kinds = [...new Set(all.map(kindOf))];
  const total = overall(groups);
  // P&L now per account is the broker's (equity less balance, /api/today), never a client sum of the rows: the
  // ledger may withhold an open-P&L total (ADR 0050). Shown only while the group holds every open trade (no kind filter).
  const today = useQuery({ queryKey: ["today"], queryFn: api.today, refetchInterval: POLL.today });
  const accountPnl = (key: string) => (kind ? undefined : today.data?.accounts.find((a) => a.account === key)?.open_pnl ?? null);
  const aside = q.dataUpdatedAt ? <>updated <TimeText iso={new Date(q.dataUpdatedAt).toISOString()} /></> : undefined;
  return (
    <Card title="Open trades" label="Open trades" aside={aside}>
      {q.isLoading ? <SkeletonRows n={3} />
        : q.isError && !v ? <ErrorState title="Couldn't load open trades." stillWorks="Open trades keep their stop, target and automatic exits." onRetry={() => q.refetch()} />
        : <>
            {v!.unreadable.length > 0 && <p className="small state warn">{`Couldn't read the open trades of ${v!.unreadable.join(", ")}: missing, not zero.`}</p>}
            {all.length === 0 ? (
              <EmptyState title="No open trades.">The labs open trades on their own signals; the next one shows here.</EmptyState>
            ) : <>
              <div className="now-sum" role="group" aria-label="Open now summary">
                <p className="num"><strong>Open now · {shown.length} trade{shown.length === 1 ? "" : "s"}</strong>
                  {total && <> · risk {moneyOrDash(total.risk, total.currency, false)}</>}
                  {groups.length === 1 && accountPnl(groups[0].key) !== undefined && <> · P&amp;L now {moneyOrDash(accountPnl(groups[0].key), groups[0].currency)}</>}
                  {!total && groups.length > 1 && <span className="muted"> · totals per account below (never summed across real and demo or currencies)</span>}
                  {v!.unreadable.length > 0 && <span className="muted"> · excl. {v!.unreadable.join(", ")}</span>}</p>
                <div className="now-filters">
                  <label><span className="sr-only">Account</span>
                    <select value={acct} onChange={(e) => setAcct(e.target.value)} aria-label="Account">
                      <option value="">All accounts</option>
                      {accounts.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
                    </select></label>
                  <label><span className="sr-only">Kind</span>
                    <select value={kind} onChange={(e) => setKind(e.target.value as TradeKind | "")} aria-label="Kind">
                      <option value="">All kinds</option>
                      {kinds.map((k) => <option key={k} value={k}>{KIND_WORDS[k]}</option>)}
                    </select></label>
                </div>
              </div>
              {shown.length === 0 ? <EmptyState title="No open trades match.">Choose all accounts and all kinds to see every open trade.</EmptyState>
                : wide ? <OpenTable groups={groups} onOpen={onOpen} pnl={accountPnl} />
                : groups.map((g) => <OpenGroupBlock key={g.key} g={g} onOpen={onOpen} pnl={accountPnl(g.key)} />)}
              <p className="small muted">P&amp;L now is after the costs already paid; risk is the loss at the stop set at entry; prices in USD/oz.</p>
            </>}
          </>}
    </Card>
  );
}

/** One total across groups only when they share a currency and none is real money (never summed across classes). */
function overall(groups: OpenGroup[]): { risk: number | null; currency: string | null } | null {
  if (groups.length === 0) return null;
  if (groups.length === 1) return groups[0];
  const ccys = new Set(groups.map((g) => g.currency));
  if (ccys.size !== 1 || groups[0].currency == null || groups.some((g) => g.mode === "live")) return null;
  const add = (f: (g: OpenGroup) => number | null) => (groups.some((g) => f(g) == null) ? null : groups.reduce((s, g) => s + (f(g) as number), 0));
  return { risk: add((g) => g.risk), currency: groups[0].currency };
}

export function SkeletonRows({ n, height = 72 }: { n: number; height?: number }) {
  return <div aria-busy="true" aria-label="Loading">{Array.from({ length: n }, (_, i) => <div key={i} className="skeleton-row" style={{ height }} />)}</div>;
}

function GroupHead({ g, pnl }: { g: OpenGroup; pnl: number | null | undefined }) {
  const age = Math.max(0, ...g.rows.map((r) => r.age_s ?? 0));
  return (
    <>
      <Badge tone={modeBadge(g.mode) === "REAL" ? "warn" : "info"}>{modeBadge(g.mode)}</Badge>
      <strong>{g.label}</strong>
      <span className="num">{g.rows.length} open · risk {moneyOrDash(g.risk, g.currency, false)}
        {pnl !== undefined && <> · P&amp;L now {moneyOrDash(pnl, g.currency)}</>}
        {g.noRate && <span className="muted"> (no rate)</span>}</span>
      {g.stale && <span className="small state warn">figures {seconds(Math.round(age))} old: engine not reporting</span>}
    </>
  );
}

function OpenGroupBlock({ g, onOpen, pnl }: { g: OpenGroup; onOpen?: (t: OpenTarget) => void; pnl: number | null | undefined }) {
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  const rows = all ? g.rows : g.rows.slice(0, FIRST);
  return (
    <section className={`now-group${g.stale ? " stale" : ""}`} aria-label={`Open trades ${g.label}`}>
      <button type="button" className="now-group-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <GroupHead g={g} pnl={pnl} />
      </button>
      {open && (
        <>
          <ul className="trade-rows" aria-label={`Open trades, ${g.label}`}>
            {rows.map((p) => <li key={`${p.account}-${p.position_id}`}><OpenRowPhone p={p} onOpen={onOpen} /></li>)}
          </ul>
          {g.rows.length > FIRST && !all && (
            <button type="button" className="btn ghost more" onClick={() => setAll(true)}>Show {g.rows.length - FIRST} more</button>
          )}
        </>
      )}
    </section>
  );
}


function RowShell({ p, onOpen, children, className }: { p: OpenPositionX; onOpen?: (t: OpenTarget) => void; children: React.ReactNode; className: string }) {
  if (onOpen) {
    return <button type="button" className={className} aria-label={openRowName(p)} onClick={() => onOpen(openTarget(p))}>{children}</button>;
  }
  return <div className={className} aria-label={openRowName(p)} role="group">{children}</div>;
}

function OpenRowPhone({ p, onOpen }: { p: OpenPositionX; onOpen?: (t: OpenTarget) => void }) {
  const badge = kindBadge(kindOf(p));
  const risk = p.risk_amount ?? p.open_risk;
  return (
    <RowShell p={p} onOpen={onOpen} className={`trade-row${p.stale ? " stale" : ""}`}>
      {/* the group header names the account, so line 1 carries the kind instead (a word, never colour alone) */}
      <span className="tr-l1"><span className="tr-name">{strategyLabel(p.family_label, p.family, p.timeframe)}</span>
        {badge && <Badge tone="idle">{badge.toUpperCase()}</Badge>}</span>
      <span className="tr-l2 num"><span className={`tr-side ${p.side}`}><span aria-hidden="true">{sideArrow(p.side)}</span> {sideWords(p.side)}</span>
        <span className="tr-pnl">{moneyOrDash(p.floating_pnl, p.currency)}</span>
        <span className="tr-r">{signedR(p.r_now)}</span>
        <span className="tr-risk muted">risk {moneyOrDash(risk, p.currency, false)}</span></span>
      <span className="tr-l3 small muted num">{price(p.entry)} → stop {price(p.stop)} USD/oz · {heldWords(p.held_s, p.hold_limit_s)}</span>
    </RowShell>
  );
}

function OpenTable({ groups, onOpen, pnl }: { groups: OpenGroup[]; onOpen?: (t: OpenTarget) => void; pnl: (key: string) => number | null | undefined }) {
  return (
    <div className="trade-table-wrap">
      <table className="trade-table">
        <caption className="sr-only">Open trades, real money first; prices in USD per ounce, money in the account currency</caption>
        {/* fixed layout: the columns never push the page sideways; the account is the group's header row */}
        <colgroup><col /><col className="w-side" /><col className="w-price" /><col className="w-price" /><col className="w-price" />
          <col className="w-money" /><col className="w-money" /><col className="w-r" /><col className="w-held" /><col className="w-kind" /></colgroup>
        <thead><tr>
          <th scope="col">Strategy</th><th scope="col">Side, lots</th>
          <th scope="col" className="num">Entry</th><th scope="col" className="num">Stop</th><th scope="col" className="num">Target</th>
          <th scope="col" className="num">Risk</th><th scope="col" className="num">P&amp;L now</th><th scope="col" className="num">R</th>
          <th scope="col" className="num">Held</th><th scope="col">Kind</th>
        </tr></thead>
        {groups.map((g) => (
          <tbody key={g.key} aria-label={`Open trades ${g.label}`}>
            <tr className="group-row"><th scope="rowgroup" colSpan={10}><span className="group-line"><GroupHead g={g} pnl={pnl(g.key)} /></span></th></tr>
            {g.rows.map((p) => {
              const risk = p.risk_amount ?? p.open_risk;
              const go = onOpen ? () => onOpen(openTarget(p)) : undefined;
              return (
                <tr key={`${p.account}-${p.position_id}`} className={`${p.stale ? "stale " : ""}${go ? "clickable" : ""}`}
                    tabIndex={go ? 0 : undefined} aria-label={openRowName(p)} onClick={go}
                    onKeyDown={(e) => { if (go && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); go(); } }}>
                  <td title={strategyLabel(p.family_label, p.family, p.timeframe)}>{strategyLabel(p.family_label, p.family, p.timeframe)}</td>
                  <td><span className={`tr-side ${p.side}`}><span aria-hidden="true">{sideArrow(p.side)}</span> {sideWords(p.side)}</span> {lots(p.lots)}</td>
                  <td className="num">{price(p.entry)}</td><td className="num">{price(p.stop)}</td><td className="num">{price(p.target)}</td>
                  <td className="num">{moneyOrDash(risk, p.currency, false)}</td>
                  <td className="num">{moneyOrDash(p.floating_pnl, p.currency)}</td>
                  <td className="num">{signedR(p.r_now)}</td>
                  <td className="num">{heldWords(p.held_s, p.hold_limit_s)}</td>
                  <td>{KIND_WORDS[kindOf(p)]}</td>
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}
