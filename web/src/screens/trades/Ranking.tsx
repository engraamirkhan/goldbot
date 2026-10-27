// Trades > Strategies, "Ranked" (docs/design/trades-ux-2026-10-27.md §7, backlog 137-138; ADR 0053): the strategy
// ranking, the dashboard's daily review of it. Families in the server's order (the Bonferroni-adjusted lower bound of
// net R per trade after costs), every column of §7.3, variants expandable, rows too few to rank greyed below with
// why, then the controls (the yardstick) and the retired families in their own bands. Each row keeps its progress to
// the read (from the board) and says "early, not proven" until it. The client sorts nothing and adds nothing up.
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import {
  ANNEAL_WORDS, type BoardFamily, boundWords, type BoardQuery, ci, edgeWords, familyProgress, pct, pfWords, POLL, RANK_STATUS_WORDS,
  rankingQuery, type RankingView, type RankingWindow, type RankRow, signedR, trendWords, WINDOW_WORDS, WINDOWS,
} from "../../lib/trades";
import { useWide } from "../../lib/useMedia";
import { Badge, Card, EmptyState, ErrorState, Meter, Segmented, Skeleton } from "../../ui";
import { money, NBSP } from "../../ui/format";
import "./trades.css";

const STYLES = ["scalp", "intraday", "swing"] as const;

type Props = { b: BoardQuery; set: (next: Partial<BoardQuery>) => void; families: Map<string, BoardFamily>; onFamily: (f: string) => void };

export function RankingCard({ b, set, families, onFamily }: Props) {
  const q = useQuery({ queryKey: ["strategyRanking", rankingQuery(b)], queryFn: () => api.strategyRanking(rankingQuery(b)),
                       refetchInterval: POLL.strategies, retry: false });
  const v = q.data;
  return (
    <Card title="Ranking" label="Strategy ranking">
      <div className="now-filters">
        <Segmented<RankingWindow> label="Window" items={WINDOWS.map((id) => ({ id, label: WINDOW_WORDS[id] }))}
                                  value={b.window} onChange={(window) => set({ window })} />
        <select aria-label="Ranking account" value={b.acct} onChange={(e) => set({ acct: e.target.value })}>
          <option value="">All accounts</option>
          {(v?.account_choices ?? []).map((a) => <option key={a.account} value={a.account}>{a.label}</option>)}
        </select>
        <select aria-label="Ranking style" value={b.style} onChange={(e) => set({ style: e.target.value })}>
          <option value="">All styles</option>{STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      {q.isLoading ? <Skeleton height={240} label="Loading the ranking" />
        : q.isError && !v ? <ErrorState title="Couldn't load the ranking." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
        : <RankingBody v={v!} families={families} onFamily={onFamily} />}
    </Card>
  );
}

function RankingBody({ v, families, onFamily }: { v: RankingView; families: Map<string, BoardFamily>; onFamily: (f: string) => void }) {
  const wide = useWide();
  const ranked = v.rows.filter((r) => r.rank != null).length;
  return (
    <>
      <p><strong>{v.heading}</strong></p>
      <p className="small muted">
        {ranked} ranked{v.t_adjusted != null && <>, each bound {v.t_adjusted.toFixed(1)} standard errors below its average (adjusted for {v.k_ranked} at once)</>};
        {` a family needs ${v.min_fills} fills on ${v.min_days} days to be ranked. Intervals count each account's day once, so a day's similar trades are not independent evidence. This ranking changes no trade, budget, stop or read.`}
      </p>
      {v.unreadable.length > 0 && <p className="small state warn">{`Couldn't read ${v.unreadable.join(", ")}: missing, not zero.`}</p>}
      {v.rows.length === 0 ? <EmptyState title="Nothing to rank yet." />
        : <Rows rows={v.rows} wide={wide} families={families} onFamily={onFamily} caption="Strategies ranked by the least each trade is likely earning after costs" />}
      {v.controls.length > 0 && (
        <section aria-label="Controls">
          <h3 className="band-head">Random controls: what no skill earned</h3>
          <Rows rows={v.controls} wide={wide} families={families} onFamily={onFamily} caption="Random controls, not ranked" />
        </section>
      )}
      {v.retired.length > 0 && (
        <section aria-label="Retired">
          <h3 className="band-head">Retired by their own rule</h3>
          <Rows rows={v.retired} wide={wide} families={families} onFamily={onFamily} caption="Retired strategies, not ranked" />
        </section>
      )}
    </>
  );
}

const name = (r: RankRow) => `${r.rank != null ? `${r.rank}. ` : ""}${r.family_label}`;
const moneyPair = (r: RankRow) => (r.figures.net_pnl == null ? "—"
  : `${money(r.figures.net_pnl, r.figures.currency, true)} (${money(r.figures.net_per_trade ?? 0, r.figures.currency, true)}/trade)`);
const costPair = (r: RankRow) => `${r.figures.cost_per_trade == null ? "—" : money(r.figures.cost_per_trade, r.figures.currency)}${
  r.figures.cost_per_trade_r != null ? ` (${r.figures.cost_per_trade_r.toFixed(2)}${NBSP}R)` : ""}`;
const nWords = (r: RankRow) => `${r.figures.n.toLocaleString("en-GB")} fills, ${r.figures.days} days`;

function Progress({ f }: { f: BoardFamily | undefined }) {
  const p = familyProgress(f);
  if (!p) return null;
  return (
    <span className="rank-progress">
      <Meter value={p.n} max={p.size} label={`${f!.family_label}: ${p.n} of ${p.size} trades toward its read`}
             text={`${p.n} of ${p.size} to the read`} tone="info" />
      <span className="small muted num">{p.n} of {p.size} to the read</span>
    </span>
  );
}

function StatusLine({ r, f }: { r: RankRow; f: BoardFamily | undefined }) {
  return (
    <span className="small">
      <Badge tone={r.status === "proven" ? "ok" : "idle"}>{RANK_STATUS_WORDS[r.status]}</Badge>
      {f && <span className="muted"> · {ANNEAL_WORDS[f.anneal_state]}</span>}
      {r.unranked_reason && <span className="muted"> · {r.unranked_reason}</span>}
    </span>
  );
}

function Variants({ r }: { r: RankRow }) {
  if (r.variants.length < 2) return null;
  return (
    <details className="rank-variants">
      <summary>{r.variants.length} timeframes</summary>
      <ul>{r.variants.map((x) => (
        <li key={`${x.timeframe}-${x.variant}`} className="small num">
          <strong>{x.variant}</strong> · {signedR(x.figures.net_r_per_trade)}/trade ({ci(x.figures.ci95, 0.95)}) · n{NBSP}{x.figures.n} ·
          hit {pct(x.figures.hit_rate)} · {x.figures.net_pnl == null ? "—" : money(x.figures.net_pnl, x.figures.currency, true)} ·
          vs random {edgeWords(x.figures)} · {RANK_STATUS_WORDS[x.status]}
        </li>
      ))}</ul>
    </details>
  );
}

function Rows({ rows, wide, families, onFamily, caption }: {
  rows: RankRow[]; wide: boolean; families: Map<string, BoardFamily>; onFamily: (f: string) => void; caption: string;
}) {
  if (!wide) {
    return (
      <ul className="rank-list">
        {rows.map((r) => {
          const f = families.get(r.family);
          return (
            <li key={r.key} className={r.rank == null && r.band === "too_few" ? "greyed" : undefined}>
              <button type="button" className="family-card" onClick={() => onFamily(r.family)} aria-label={`${name(r)}, open its trades`}>
                <span className="family-head"><strong>{name(r)}</strong><span className="muted small">{r.style} · {r.kind}</span></span>
                <span className="num"><strong>{boundWords(r)}</strong></span>
                <span className="small num">avg {signedR(r.figures.net_r_per_trade)} ({ci(r.figures.ci95, 0.95)}) · {nWords(r)}</span>
                <span className="small num">hit {pct(r.figures.hit_rate)} · {moneyPair(r)} · PF {pfWords(r.figures)} · max drawdown {r.figures.max_drawdown_r == null ? "—" : `${r.figures.max_drawdown_r.toFixed(1)}${NBSP}R`}</span>
                <span className="small num">vs random {edgeWords(r.figures)} · cost {costPair(r)}</span>
                <span className="small num muted">{trendWords(r.figures)}</span>
                <Progress f={f} />
                <StatusLine r={r} f={f} />
              </button>
              <Variants r={r} />
            </li>
          );
        })}
      </ul>
    );
  }
  return (
    <div className="trade-table-wrap">
      <table className="trade-table board-table rank-table">
        <caption className="sr-only">{caption}</caption>
        <colgroup><col className="w-name" /><col /><col className="w-hit" /><col /><col className="w-pf" /><col /><col /><col /></colgroup>
        <thead><tr>
          <th scope="col">Strategy</th><th scope="col" className="num">Net R/trade at least</th><th scope="col" className="num">Hit · n</th>
          <th scope="col" className="num">Net</th><th scope="col" className="num">PF · max DD</th><th scope="col" className="num">Vs random</th>
          <th scope="col" className="num">Cost/trade</th><th scope="col" className="num">7 d / 30 d</th>
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const f = families.get(r.family);
            return (
              <tr key={r.key} className={`clickable${r.rank == null && r.band === "too_few" ? " greyed" : ""}`} tabIndex={0}
                  aria-label={`${name(r)}, open its trades`} onClick={() => onFamily(r.family)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onFamily(r.family); } }}>
                <th scope="row"><strong>{name(r)}</strong><br /><span className="muted small">{r.style} · {r.kind} · {r.timeframes.join(", ")}</span>
                  <Progress f={f} /><StatusLine r={r} f={f} /><Variants r={r} /></th>
                <td className="num"><strong>{boundWords(r)}</strong><br /><span className="small muted">avg {signedR(r.figures.net_r_per_trade)} · {ci(r.figures.ci95, 0.95)}</span></td>
                <td className="num">{pct(r.figures.hit_rate)}<br /><span className="small muted">{nWords(r)}</span></td>
                <td className="num">{moneyPair(r)}</td>
                <td className="num">{pfWords(r.figures)}<br /><span className="small muted">{r.figures.max_drawdown_r == null ? "—" : `${r.figures.max_drawdown_r.toFixed(1)}${NBSP}R`}</span></td>
                <td className="num small">{edgeWords(r.figures)}</td>
                <td className="num small">{costPair(r)}</td>
                <td className="num small">{trendWords(r.figures)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
