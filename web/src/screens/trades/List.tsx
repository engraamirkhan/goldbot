// Trades > List (docs/design/trades-ux-2026-10-27.md §3.2, backlog 130): every trade of every kind, one row per trade,
// findable. Filters live in the URL (#/trades/list?acct=..&fam=..) so a Telegram link or a reload keeps them; paging is
// a cursor and a "Load 50 more" button (no infinite scroll: position stays put, screen readers cope). The totals line
// is the server's (after costs), blinded per ADR 0050; the client never adds results up itself. While the list is
// scrolled down, a poll that brings new trades shows a "N new · show" pill instead of pushing the rows down.
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../../lib/api";
import {
  activeFilterCount, apiQuery, DEFAULT_KINDS, EVIDENCE_WORDS, type Evidence, EXIT_REASONS, EXIT_WORDS, exitWords, type Filters,
  filtersQuery, heldWords, KIND_WORDS, KINDS, kindBadge, moneyOrDash, parseFilters, type Period, PERIOD_WORDS, POLL, resultOf,
  rowName, sideWords, signedR, strategyLabel, TIMEFRAMES, totalsLine, type TradeRow, type TradesPage,
} from "../../lib/trades";
import { useWide } from "../../lib/useMedia";
import { Badge, Button, Card, EmptyState, ErrorState, Segmented, TimeText } from "../../ui";
import { money } from "../../ui/format";
import { ActivityFeed } from "../Activity";
import { SkeletonRows } from "./Now";
import "./trades.css";

type Props = { query: string; onQuery: (q: string) => void; onOpen: (id: string) => void };

export function TradesList({ query, onQuery, onOpen }: Props) {
  const f = parseFilters(query);
  const wide = useWide();
  const [sheet, setSheet] = useState(false);
  const set = (next: Partial<Filters>) => onQuery(filtersQuery({ ...f, ...next }));
  const q = useInfiniteQuery({
    queryKey: ["trades", apiQuery(f)],
    queryFn: ({ pageParam }) => api.trades(apiQuery(f, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last: TradesPage) => last.next_cursor ?? undefined,
    refetchInterval: POLL.list,
  });
  const notServed = q.error instanceof ApiError && q.error.status === 404 && !q.data;
  if (notServed) return <LedgerNotServed />;
  const n = activeFilterCount(f);
  const panel = <FilterPanel f={f} set={set} />;
  return (
    <div className="trades-page list-page">
      <div className="list-main">
        <div className="list-bar">
          <Segmented label="Period" items={(["today", "7d", "30d"] as Period[]).map((id) => ({ id, label: PERIOD_WORDS[id] }))}
                     value={f.period} onChange={(period) => set({ period })} />
          {wide ? <Button onClick={() => setSheet(!sheet)} aria-expanded={sheet} aria-controls="trade-filters">Filters{n ? ` (${n})` : ""}</Button>
            : <Button onClick={() => setSheet(true)} aria-haspopup="dialog">Filters{n ? ` (${n})` : ""}</Button>}
        </div>
        {wide && sheet && <section id="trade-filters" className="filter-inline" aria-label="Filters">{panel}</section>}
        <Chips f={f} set={set} />
        <Card title="Trades" label="Trade list">
          <ListBody q={q} f={f} onOpen={onOpen} wide={wide} onClear={() => onQuery(filtersQuery({ ...f, acct: [], fam: [], tf: [], status: "", kind: [], exit: [], evidence: [] }))} />
        </Card>
      </div>
      {sheet && !wide && (
        <>
          <div className="sheet-scrim" aria-hidden="true" onClick={() => setSheet(false)} />
          <div className="trade-sheet filter-sheet" role="dialog" aria-modal="true" aria-label="Filters">
            <div className="detail-head"><h2>Filters</h2><Button variant="primary" onClick={() => setSheet(false)}>Done</Button></div>
            {panel}
          </div>
        </>
      )}
    </div>
  );
}

function LedgerNotServed() {
  return (
    <div className="trades-page">
      <p className="small state warn" role="status">This server doesn't serve the trade list yet (it arrives with the trade
        ledger). Until then the activity feed below shows engine, shadow and probe trades; exploration trades are counted
        in the morning report.</p>
      <ActivityFeed />
    </div>
  );
}

type Q = ReturnType<typeof useInfiniteQuery<TradesPage, Error, { pages: TradesPage[] }, string[], string | null>>;

function ListBody({ q, f, onOpen, wide, onClear }: { q: Q; f: Filters; onOpen: (id: string) => void; wide: boolean; onClear: () => void }) {
  const live = q.data?.pages;
  // the pages on screen: a poll that changes the first row while the owner is scrolled down is held behind a pill
  const [held, setHeld] = useState<{ src: TradesPage[] | undefined; pages: TradesPage[] | undefined; fresh: number }>({ src: undefined, pages: undefined, fresh: 0 });
  if (live !== held.src) {
    const scrolled = typeof window !== "undefined" && window.scrollY > 120;
    const before = held.pages?.[0]?.rows[0]?.trade_id;
    const after = live?.[0]?.rows[0]?.trade_id;
    if (scrolled && held.pages && live && before !== after) {
      const seen = new Set(held.pages.flatMap((p) => p.rows.map((r) => r.trade_id)));
      setHeld({ src: live, pages: held.pages, fresh: live[0].rows.filter((r) => !seen.has(r.trade_id)).length });
    } else setHeld({ src: live, pages: live, fresh: 0 });
  }
  const pages = held.pages ?? live;
  if (q.isLoading) return <SkeletonRows n={4} height={wide ? 40 : 72} />;
  if (q.isError && !live) return <ErrorState title="Couldn't load the trades." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />;
  const first = pages![0];
  const rows = pages!.flatMap((p) => p.rows);
  const tot = totalsLine(first.totals);
  return (
    <>
      {first.unreadable.length > 0 && <p className="small state warn">{`Couldn't read the trades of ${first.unreadable.join(", ")}: missing, not zero.`}</p>}
      <div className="list-totals" role="group" aria-label="Totals of this list">
        <p className="num"><strong>{tot.text}</strong></p>
        {tot.note && <p className="small muted">{tot.note}</p>}
      </div>
      {held.fresh > 0 && (
        <button type="button" className="new-pill" onClick={() => { setHeld({ src: live, pages: live, fresh: 0 }); window.scrollTo?.(0, 0); }}>
          {held.fresh} new · show
        </button>
      )}
      {rows.length === 0 ? (
        <EmptyState title="No trades match." action={activeFilterCount(f) ? <Button onClick={onClear}>Clear filters</Button> : undefined}>
          {f.period === "today" ? "Nothing yet today with these filters." : "Nothing in this period with these filters."}
        </EmptyState>
      ) : wide ? <ListTable rows={rows} onOpen={onOpen} period={f.period} /> : (
        <ul className="trade-rows" aria-label="Trades">
          {rows.map((t) => <li key={t.trade_id}><ListRowPhone t={t} onOpen={onOpen} /></li>)}
        </ul>
      )}
      {q.hasNextPage && (
        <Button className="more" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
          {q.isFetchingNextPage ? "Loading…" : "Load 50 more"}
        </Button>
      )}
      <p className="small muted">Net after costs (spread, slippage, commission, swap), in the account currency; newest first; times are your time, UTC on hover.</p>
    </>
  );
}

function result(t: TradeRow): { pnl: string; r: string } {
  if (t.blinded) return { pnl: "hidden", r: "until read" };
  const { pnl, r } = resultOf(t);
  return { pnl: moneyOrDash(pnl, t.currency), r: signedR(r) };
}

const endWords = (t: TradeRow) => (t.status === "open" ? "open" : exitWords(t.exit_reason));

function ListRowPhone({ t, onOpen }: { t: TradeRow; onOpen: (id: string) => void }) {
  const badge = kindBadge(t.kind);
  const res = result(t);
  return (
    <button type="button" className={`trade-row${t.stale ? " stale" : ""}`} aria-label={rowName(t)} onClick={() => onOpen(t.trade_id)}>
      {/* line 1 what and how much; line 2 when, side, where, how it ended; line 3 the kind in a word and why */}
      <span className="tr-l1"><span className="tr-name">{strategyLabel(t.family_label, t.family, t.timeframe, t.variant)}</span>
        <span className="tr-pnl num">{res.pnl}</span></span>
      <span className="tr-l2"><span className="tr-meta"><TimeText iso={t.opened_utc} /> · {sideWords(t.side)} · {t.account_label} · {endWords(t)}
        {t.status === "closed" && t.held_s != null ? ` · ${heldWords(t.held_s, null)}` : ""}</span>
        <span className="tr-r num">{res.r}</span></span>
      <span className="tr-l3 small muted">{badge && <Badge tone="idle">{badge.toUpperCase()}</Badge>} {t.why}</span>
    </button>
  );
}

function ListTable({ rows, onOpen, period }: { rows: TradeRow[]; onOpen: (id: string) => void; period: Period }) {
  return (
    <div className="trade-table-wrap">
      <table className="trade-table">
        <caption className="sr-only">Trades, newest first; net after costs in the account currency</caption>
        {/* fixed layout, two lines a row (56 px): the why sits under the strategy, so nothing pushes the page sideways */}
        <colgroup><col className="w-time" /><col /><col className="w-acct" /><col className="w-side" /><col className="w-exit" />
          <col className="w-money" /><col className="w-r" /><col className="w-money" /><col className="w-kind" /></colgroup>
        <thead><tr>
          <th scope="col">Opened</th><th scope="col">Strategy and why</th><th scope="col">Account</th><th scope="col">Side</th>
          <th scope="col">Closed by, held</th><th scope="col" className="num">Net</th>
          <th scope="col" className="num">R</th><th scope="col" className="num">Costs</th><th scope="col">Kind</th>
        </tr></thead>
        <tbody>
          {rows.map((t) => {
            const res = result(t);
            const go = () => onOpen(t.trade_id);
            return (
              <tr key={t.trade_id} className={`clickable${t.stale ? " stale" : ""}`} tabIndex={0} aria-label={rowName(t)} onClick={go}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }}>
                <td className="num"><TimeText iso={t.opened_utc} withDay={period !== "today"} /></td>
                <td className="two-line" title={t.why}><span>{strategyLabel(t.family_label, t.family, t.timeframe, t.variant)}</span>
                  <span className="muted">{t.why}</span></td>
                <td>{t.account_label}</td>
                <td className={`tr-side ${t.side}`}>{sideWords(t.side)}</td>
                <td>{endWords(t)} · {heldWords(t.held_s, t.status === "open" ? t.hold_limit_s : null)}</td>
                <td className="num">{res.pnl}</td>
                <td className="num">{res.r}</td>
                <td className="num">{t.costs_total == null ? "—" : money(t.costs_total, t.currency)}</td>
                <td>{KIND_WORDS[t.kind]}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ filters
function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" className="chip-toggle" aria-pressed={on} onClick={onClick}>{children}</button>;
}

const flip = <T,>(xs: T[], x: T) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x]);

function FilterPanel({ f, set }: { f: Filters; set: (n: Partial<Filters>) => void }) {
  const today = useQuery({ queryKey: ["today"], queryFn: api.today, staleTime: POLL.today });
  const board = useQuery({ queryKey: ["strategyBoard"], queryFn: api.strategyBoard, staleTime: POLL.strategies, retry: false });
  const accts = (today.data?.accounts ?? []).map((a) => ({ id: a.account, label: a.account_label || a.account }));
  const fams = (board.data?.families ?? []).map((x) => ({ id: x.family, label: x.family_label }))
    .sort((a, b) => a.label.localeCompare(b.label));
  return (
    <div className="filter-panel">
      <fieldset><legend>Status</legend>
        <Segmented label="Status" items={[{ id: "", label: "Open + closed" }, { id: "open", label: "Open" }, { id: "closed", label: "Closed" }]}
                   value={f.status} onChange={(status) => set({ status })} />
      </fieldset>
      {accts.length > 0 && <fieldset><legend>Account</legend><div className="chip-set">
        {accts.map((a) => <Toggle key={a.id} on={f.acct.includes(a.id)} onClick={() => set({ acct: flip(f.acct, a.id) })}>{a.label}</Toggle>)}
      </div></fieldset>}
      {fams.length > 0 && <fieldset><legend>Strategy</legend><div className="chip-set">
        {fams.map((a) => <Toggle key={a.id} on={f.fam.includes(a.id)} onClick={() => set({ fam: flip(f.fam, a.id) })}>{a.label}</Toggle>)}
      </div></fieldset>}
      <fieldset><legend>Timeframe</legend><div className="chip-set">
        {TIMEFRAMES.map((t) => <Toggle key={t} on={f.tf.includes(t)} onClick={() => set({ tf: flip(f.tf, t) })}>{t}</Toggle>)}
      </div></fieldset>
      <fieldset><legend>Kind <span className="muted small">(none chosen: all but Shadow)</span></legend><div className="chip-set">
        {KINDS.map((k) => <Toggle key={k} on={f.kind.includes(k)} onClick={() => set({ kind: flip(f.kind, k) })}>{KIND_WORDS[k]}</Toggle>)}
      </div></fieldset>
      <fieldset><legend>Closed by</legend><div className="chip-set">
        {EXIT_REASONS.map((k) => <Toggle key={k} on={f.exit.includes(k)} onClick={() => set({ exit: flip(f.exit, k) })}>{EXIT_WORDS[k]}</Toggle>)}
      </div></fieldset>
      <fieldset><legend>Evidence</legend><div className="chip-set">
        {(["counted", "excluded", "censored"] as Evidence[]).map((k) => (
          <Toggle key={k} on={f.evidence.includes(k)} onClick={() => set({ evidence: flip(f.evidence, k) })}>{k === "censored" ? "censored" : EVIDENCE_WORDS[k]}</Toggle>))}
      </div></fieldset>
    </div>
  );
}

/** The active filters as removable chips (each a 44 px button: "Scalp lab ×"). */
function Chips({ f, set }: { f: Filters; set: (n: Partial<Filters>) => void }) {
  const today = useQuery({ queryKey: ["today"], queryFn: api.today, staleTime: POLL.today });
  const board = useQuery({ queryKey: ["strategyBoard"], queryFn: api.strategyBoard, staleTime: POLL.strategies, retry: false });
  const acctLabel = (id: string) => today.data?.accounts.find((a) => a.account === id)?.account_label || id;
  const famLabel = (id: string) => board.data?.families.find((x) => x.family === id)?.family_label || id;
  const chips: { key: string; label: string; drop: () => void }[] = [
    ...f.acct.map((a) => ({ key: `a-${a}`, label: acctLabel(a), drop: () => set({ acct: f.acct.filter((x) => x !== a) }) })),
    ...f.fam.map((a) => ({ key: `f-${a}`, label: famLabel(a), drop: () => set({ fam: f.fam.filter((x) => x !== a) }) })),
    ...f.tf.map((a) => ({ key: `t-${a}`, label: a, drop: () => set({ tf: f.tf.filter((x) => x !== a) }) })),
    ...f.kind.map((a) => ({ key: `k-${a}`, label: KIND_WORDS[a], drop: () => set({ kind: f.kind.filter((x) => x !== a) }) })),
    ...f.exit.map((a) => ({ key: `e-${a}`, label: exitWords(a), drop: () => set({ exit: f.exit.filter((x) => x !== a) }) })),
    ...f.evidence.map((a) => ({ key: `v-${a}`, label: a, drop: () => set({ evidence: f.evidence.filter((x) => x !== a) }) })),
    ...(f.status ? [{ key: "s", label: f.status === "open" ? "Open" : "Closed", drop: () => set({ status: "" }) }] : []),
  ];
  if (!chips.length) return <p className="small muted chips-none">All accounts · {DEFAULT_KINDS.length === KINDS.length ? "all kinds" : "all kinds but Shadow"}</p>;
  return (
    <ul className="chip-set chips-active" aria-label="Active filters">
      {chips.map((c) => <li key={c.key}><button type="button" className="filter-chip" onClick={c.drop} aria-label={`Remove filter ${c.label}`}>{c.label} <span aria-hidden="true">×</span></button></li>)}
    </ul>
  );
}
