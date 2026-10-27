// Trades > Strategies (docs/design/trades-ux-2026-10-27.md §3.4, backlog 132): the leaderboard, honestly. One row per
// family with its timeframes, progress to each read unit (an arm, or the family pooled: n of its read size, the server's
// ETA, status, anneal state, the control it is read against) and, only after the read, the recorded verdict with its
// frozen figures and the evaluator's recorded difference against its control (ADR 0050, 0051). The server orders it by state and closeness to the read, never by
// result; filters keep that order. Engine strategies (not pre-registered) show full figures with n and a 90% CI.
// Money per lab only (3+ arms); per-arm money waits for the read.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../lib/api";
import {
  ANNEAL_WORDS, type BoardFamily, type BoardVariant, ci, type EngineStrategy, etaWords, type LabTotal, pct, POLL,
  type ReadFigures, type ReadProgress, signedR, type StrategyBoard, statusWords, TIMEFRAMES,
} from "../../lib/trades";
import { useWide } from "../../lib/useMedia";
import { Badge, Card, EmptyState, ErrorState, Meter, Skeleton, TimeText } from "../../ui";
import { money, NBSP } from "../../ui/format";
import "./trades.css";

const STATE_WORDS: Record<BoardFamily["state"], string> = {
  proven: "READ · proven", candidate: "READ · candidate", running: "running", retired: "retired", control: "CONTROL",
};

type Controls = Map<string, string>;     // control arm id -> "Random control (scalp) 1m"

export function TradesStrategies({ onFamily }: { onFamily: (family: string) => void }) {
  const q = useQuery({ queryKey: ["strategyBoard"], queryFn: api.strategyBoard, refetchInterval: POLL.strategies, retry: false });
  return (
    <div className="trades-page">
      {q.isLoading ? <Skeleton height={320} label="Loading strategies" />
        : q.isError && !q.data ? <ErrorState title="Couldn't load strategies." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
        : <Board v={q.data!} onFamily={onFamily} updated={q.dataUpdatedAt} />}
    </div>
  );
}

function Board({ v, onFamily, updated }: { v: StrategyBoard; onFamily: (f: string) => void; updated: number }) {
  const wide = useWide();
  const [style, setStyle] = useState("");
  const [acct, setAcct] = useState("");
  const styles = [...new Set(v.families.map((f) => f.style))];
  const accounts = [...new Set(v.families.flatMap((f) => f.accounts))].sort();
  const fams = v.families.filter((f) => (!style || f.style === style) && (!acct || f.accounts.includes(acct)));
  const arms = v.families.reduce((n, f) => n + f.variants.length, 0);
  const controls: Controls = new Map(v.families.flatMap((f) => f.variants.filter((x) => x.is_control)
    .map((x) => [x.arm_id, `${f.family_label} ${x.variant}`] as [string, string])));
  const L = v.learning;
  const tfs = TIMEFRAMES.filter((t) => fams.some((f) => f.variants.some((x) => x.timeframe === t)));
  return (
    <>
      <Card title={`${v.families.length} strategies · ${arms} arms`} label="Learning progress"
            aside={updated ? <>updated <TimeText iso={new Date(updated).toISOString()} /></> : undefined}>
        <p>Learning: <strong>{L.read}</strong> arm{L.read === 1 ? "" : "s"} read, <strong>{L.proven}</strong> proven,{" "}
          <strong>{L.retired}</strong> retired, <strong>{L.running}</strong> running · {L.counted_7d.toLocaleString("en-GB")} trades
          counted toward reads in 7 days</p>
        {v.unreadable.length > 0 && <p className="small state warn">{`Couldn't read ${v.unreadable.join(", ")}: missing, not zero.`}</p>}
        {v.lab_totals.length > 0 && <ul className="lab-totals">{v.lab_totals.map((t) => <LabLine key={`${t.account_label}-${t.style}`} t={t} />)}</ul>}
        <p className="small muted">Each arm is judged once, at its read (300 counted trades unless its plan says otherwise). Before
          then only progress shows: no per-arm money, R or rank, so the read stays fair. Lab money is the total of 3 or more arms.</p>
        <div className="now-filters">
          <select aria-label="Style" value={style} onChange={(e) => setStyle(e.target.value)}>
            <option value="">All styles</option>{styles.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select aria-label="Account" value={acct} onChange={(e) => setAcct(e.target.value)}>
            <option value="">All accounts</option>{accounts.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </Card>
      <Card title="Exploration strategies" label="Strategy board">
        {fams.length === 0 ? <EmptyState title="No strategy is registered yet." />
          : wide ? <BoardTable fams={fams} tfs={tfs} onFamily={onFamily} controls={controls} />
          : <ul className="board-list">{fams.map((f) => <li key={f.family}><FamilyCard f={f} onFamily={onFamily} controls={controls} /></li>)}</ul>}
      </Card>
      <EngineCard rows={v.engine} wide={wide} />
    </>
  );
}

function LabLine({ t }: { t: LabTotal }) {
  const money_ = t.net_pnl != null ? <><span className="nowrap">{money(t.net_pnl, t.currency, true)}</span> net</>
    : t.withheld_reason ? "net hidden (fewer than 3 arms)" : "net not known yet";
  return (
    <li className="num">{t.account_label} ({t.style}), {t.period}: {t.n.toLocaleString("en-GB")} trades · {money_}
      {t.costs != null && <> · costs <span className="nowrap">{money(t.costs, t.currency)}</span></>}</li>
  );
}

function Progress({ label, n, size, retired }: { label: string; n: number; size: number; retired: boolean }) {
  return (
    <Meter value={n} max={size} label={`${label}: ${n} of ${size} trades toward the read`} text={`${n} of ${size}`}
           tone={retired ? "idle" : "info"} />
  );
}

/** The frozen read figures, with the evaluator's recorded comparison with the control (never computed here). */
function ReadText({ r }: { r: ReadFigures }) {
  return (
    <>
      <strong>{r.verdict}</strong> · {signedR(r.net_r_per_trade)}/trade ({ci(r.ci90)}) · hit {pct(r.hit_rate)}
      {r.vs_control_r != null && <> · vs control {signedR(r.vs_control_r)}{r.vs_control_ci90 ? ` (${ci(r.vs_control_ci90)})` : ""}</>}
      {` · n${NBSP}${r.n}`}{r.net_pnl != null && ` · ${money(r.net_pnl, r.currency, true)}`}
      {r.since_read && <span className="muted"> · since read: {signedR(r.since_read.net_r_per_trade)}, n{NBSP}{r.since_read.n}</span>}
    </>
  );
}

/** A family read pooled (W1, W2, H-80, H-81): one progress, ETA and read for all its arms. */
function PooledLine({ p }: { p: ReadProgress }) {
  return (
    <span className="pooled-row small num">
      <strong>All timeframes pooled</strong>
      {!p.read && p.n_read_at != null && <Progress label="Pooled" n={p.n_counted} size={p.n_read_at} retired={p.status === "retired"} />}
      <span>{p.read ? <ReadText r={p.read} />
        : <>{p.n_counted}/{p.n_read_at} · {p.status === "running" ? etaWords(p.eta_read_date) : statusWords(p.status)}</>}</span>
    </span>
  );
}

const ownProgress = (x: BoardVariant) => !x.read && !x.pooled && x.n_read_at != null;

/** Words for one variant: progress before the read; the frozen read figures after; a pooled arm its own count. */
function VariantText({ x, controls, many }: { x: BoardVariant; controls: Controls; many: boolean }) {
  const where = many ? <span className="muted"> · {x.account_labels.join(", ")}</span> : null;
  const tag = x.variant !== x.timeframe ? <span className="muted">{x.variant.slice(x.timeframe.length)} </span> : null;
  if (x.read) return <span className="v-read num">{tag}<ReadText r={x.read} />{where}</span>;
  if (x.n_read_at == null) return <span className="num">{tag}{x.fills_7d.toLocaleString("en-GB")} trades in 7 days · random control, never retired{where}</span>;
  const vs = x.control_arm_id ? controls.get(x.control_arm_id) : undefined;
  const progress = x.pooled ? <>{x.n_counted} counted toward the family's pooled read</>
    : <>{x.n_counted}/{x.n_read_at} · {x.status === "running" ? etaWords(x.eta_read_date) : statusWords(x.status)}</>;
  return (
    <span className="num">{tag}{progress}
      {x.open_now > 0 && ` · ${x.open_now} open`}{vs && <span className="muted"> · read against {vs}</span>}{where}</span>
  );
}

const manyAccounts = (f: BoardFamily) => f.accounts.length > 1;

function FamilyCard({ f, onFamily, controls }: { f: BoardFamily; onFamily: (f: string) => void; controls: Controls }) {
  return (
    <button type="button" className="family-card" onClick={() => onFamily(f.family)}
            aria-label={`${f.family_label}, ${STATE_WORDS[f.state]}, open its trades`}>
      <span className="family-head"><strong>{f.family_label}</strong><span className="muted small">{f.style}</span>
        <Badge tone={f.state === "proven" ? "ok" : "idle"}>{STATE_WORDS[f.state]}</Badge></span>
      {f.pooled.map((p) => <PooledLine key={p.unit_id} p={p} />)}
      {f.variants.map((x) => (
        <span key={x.arm_id} className="variant-row">
          <span className="v-tf num">{x.timeframe}</span>
          {ownProgress(x) && <Progress label={x.variant} n={x.n_counted} size={x.n_read_at!} retired={x.status === "retired"} />}
          <span className="v-text small"><VariantText x={x} controls={controls} many={manyAccounts(f)} /></span>
        </span>
      ))}
      <span className="small muted">state: {ANNEAL_WORDS[f.anneal_state]} ({f.anneal_state})</span>
    </button>
  );
}

function BoardTable({ fams, tfs, onFamily, controls }: {
  fams: BoardFamily[]; tfs: string[]; onFamily: (f: string) => void; controls: Controls;
}) {
  return (
    <div className="trade-table-wrap">
      <table className="trade-table board-table">
        <caption className="sr-only">Strategies by timeframe: progress to each arm's read, or its verdict after the read</caption>
        <colgroup><col className="w-name" /><col className="w-state" />{tfs.map((t) => <col key={t} />)}</colgroup>
        <thead><tr><th scope="col">Strategy</th><th scope="col">State</th>{tfs.map((t) => <th key={t} scope="col">{t}</th>)}</tr></thead>
        <tbody>
          {fams.map((f) => (
            <tr key={f.family} className="clickable" tabIndex={0} aria-label={`${f.family_label}, ${STATE_WORDS[f.state]}, open its trades`}
                onClick={() => onFamily(f.family)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onFamily(f.family); } }}>
              <th scope="row"><strong>{f.family_label}</strong><br /><span className="muted small">{f.style} · {ANNEAL_WORDS[f.anneal_state]}</span>
                {f.pooled.map((p) => <PooledLine key={p.unit_id} p={p} />)}</th>
              <td><Badge tone={f.state === "proven" ? "ok" : "idle"}>{STATE_WORDS[f.state]}</Badge></td>
              {tfs.map((t) => {
                const xs = f.variants.filter((v) => v.timeframe === t);
                return (
                  <td key={t} className="board-cell">{xs.length === 0 ? <span className="muted">—</span> : xs.map((x) => (
                    <div key={x.arm_id} className="board-variant">
                      {ownProgress(x) && <Progress label={x.variant} n={x.n_counted} size={x.n_read_at!} retired={x.status === "retired"} />}
                      <span className="small"><VariantText x={x} controls={controls} many={manyAccounts(f)} /></span></div>
                  ))}</td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EngineCard({ rows, wide }: { rows: EngineStrategy[]; wide: boolean }) {
  return (
    <Card title="Engine strategies" label="Engine strategies">
      {rows.length === 0 ? <EmptyState title="No engine strategy has traded yet." /> : !wide ? (
        <ul className="engine-rows">
          {rows.map((s) => (
            <li key={`${s.kind}-${s.family}-${s.timeframe}`}>
              <strong>{s.name}{s.timeframe ? ` · ${s.timeframe}` : ""}{s.kind === "shadow" && <span className="muted small"> (shadow, not money)</span>}</strong>
              <span className="small num">{signedR(s.net_r_per_trade)}/trade ({ci(s.ci90)}) · n{NBSP}{s.n} · hit {pct(s.hit_rate)}
                {s.net_pnl != null && <> · <span className="nowrap">{money(s.net_pnl, s.currency, true)}</span></>} · today {s.n_today}, 7{NBSP}d {s.n_7d}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="trade-table-wrap scroll">
          <table className="trade-table">
            <caption className="sr-only">Engine strategies and shadow models: full figures with n and a 90% confidence interval</caption>
            <thead><tr><th scope="col">Strategy</th><th scope="col" className="num">n</th><th scope="col" className="num">Net R/trade</th>
              <th scope="col" className="num">Hit</th><th scope="col" className="num">Net</th><th scope="col" className="num">Today / 7 d</th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={`${s.kind}-${s.family}-${s.timeframe}`}>
                  <th scope="row">{s.name}{s.timeframe ? ` · ${s.timeframe}` : ""}{s.kind === "shadow" && <span className="muted small"> (shadow, not money)</span>}</th>
                  <td className="num">{s.n}</td>
                  <td className="num">{signedR(s.net_r_per_trade)}<br /><span className="muted small">{ci(s.ci90)}</span></td>
                  <td className="num">{pct(s.hit_rate)}</td>
                  <td className="num">{s.net_pnl == null ? "—" : money(s.net_pnl, s.currency, true)}</td>
                  <td className="num">{s.n_today} / {s.n_7d}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
