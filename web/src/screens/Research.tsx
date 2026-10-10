import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import {
  failedGates, localDay, localTime, num, signed, utcTitle,
  type HypothesisTable, type ResearchPlanView, type ResearchView, type TrialRow,
} from "../lib/explain";

function Budget({ b }: { b: ResearchView["budget"] }) {
  const share = b.budget ? Math.min(1, b.used / b.budget) : 1;
  const band = b.left === 0 ? "bad" : b.left <= Math.max(2, b.budget * 0.2) ? "warn" : "ok";
  return (
    <div className="budget">
      <div className="ddhead">
        <strong>{b.quarter} trial budget</strong>
        <span className={`state ${band}`}>{b.used} of {b.budget} used · {b.left} left</span>
      </div>
      <div className="ddtrack" role="meter" aria-label={`${b.quarter} trials used`} aria-valuemin={0} aria-valuemax={b.budget}
        aria-valuenow={b.used} aria-valuetext={`${b.used} of ${b.budget} trials used, ${b.left} left`}>
        <span className={`ddfill ${band}`} style={{ width: `${share * 100}%` }} />
      </div>
      <p className="muted small">Every trial raises the bar (deflated Sharpe) for every later one, so the quarter's budget is fixed in advance.</p>
    </div>
  );
}

function Gates({ t }: { t: TrialRow }) {
  if (t.gates_passed == null) return <span className="muted">—</span>;
  const failed = failedGates(t);
  const title = t.gates.map((g) => `${g.passed ? "pass" : "FAIL"} ${g.name}: ${g.detail}`).join("\n");
  return t.gates_passed
    ? <span className="state ok" title={title}>passed</span>
    : <span className="state warn" title={title}>failed{failed.length ? `: ${failed.join(", ")}` : ""}</span>;
}

const r = (v: number | null | undefined, t: number | null | undefined) => (v == null ? "—" : `${signed(v)}${t == null ? "" : ` (t ${num(t)})`}`);

function Trials({ rows, total }: { rows: TrialRow[]; total: number }) {
  if (rows.length === 0) return <p className="empty muted">No trials recorded yet. The registry fills as research trials run.</p>;
  return (
    <>
      <div className="scroll"><table className="trials">
        <thead><tr><th>#</th><th>Date</th><th>Family</th><th>TF</th><th>Status</th><th>Gross R (t)</th><th>Net R (t)</th><th>Events</th><th>Gates</th><th>Rationale</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.trial}>
              <td>{t.trial}</td>
              <td className="nowrap">{t.ts ? <time title={utcTitle(t.ts)}>{localDay(t.ts)}</time> : "—"}</td>
              <td>{t.family}</td><td>{t.timeframe ?? "—"}</td><td>{t.status}</td>
              <td className="num">{r(t.gross_r, t.gross_t)}</td>
              <td className={`num ${t.net_r != null && t.net_r < 0 ? "neg" : ""}`}>{r(t.net_r, t.net_t)}</td>
              <td className="num">{t.n ?? "—"}</td>
              <td><Gates t={t} /></td>
              <td className="rationale" title={t.rationale}>{t.rationale}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {total > rows.length && <p className="muted small">Showing the newest {rows.length} of {total} trials.</p>}
    </>
  );
}

function Plan({ p }: { p: ResearchPlanView }) {
  return (
    <div className="plan">
      <p className="muted small">
        Made <time title={utcTitle(p.created_utc)}>{localTime(p.created_utc)}</time> for {p.quarter}: {p.quarter_used} of {p.quarter_budget} trials
        used then, {p.total_budget} planned{p.unallocated ? `, ${p.unallocated} unallocated` : ""}. Holdout {p.holdout_from} to {p.holdout_to} is never read.
      </p>
      {p.stale && <p className="warn-note">This plan is more than 21 days old: the monthly loop uses the flat budget until the director runs again (Saturdays).</p>}
      {p.focus.length === 0 ? <p className="muted">No focus families in this plan.</p> : (
        <ol className="focus">
          {p.focus.map((f) => (
            <li key={f.family}>
              <strong>{f.family}</strong> · {f.budget} trial{f.budget === 1 ? "" : "s"} · evidence {f.evidence.toFixed(2)} / 3
              {f.reasons.length > 0 && <ul>{f.reasons.map((x) => <li key={x} className="muted">{x}</li>)}</ul>}
            </li>
          ))}
        </ol>
      )}
      {p.evidence.length > 0 && (
        <details className="as-table">
          <summary>Evidence per family</summary>
          <div className="scroll"><table>
            <thead><tr><th>Family</th><th>Trials</th><th>Evidence</th><th>Median AUC</th><th>Best DSR</th><th>Shadow trades</th><th>Flags</th></tr></thead>
            <tbody>
              {p.evidence.map((e) => (
                <tr key={e.family}>
                  <td>{e.family}{e.blocked && <> <span className="state bad">blocked</span></>}</td><td>{e.trials}</td><td>{e.evidence.toFixed(2)}</td>
                  <td>{num(e.median_auc, 3)}</td><td>{num(e.best_dsr, 3)}</td><td>{e.shadow_trades}</td><td>{e.flags.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </details>
      )}
    </div>
  );
}

function Hypotheses({ tables }: { tables: HypothesisTable[] }) {
  return (
    <>
      {tables.map((t, i) => (
        <details key={`${t.title}-${i}`} className="hyp" open={i === 0}>
          <summary>{t.title || "Table"} <span className="muted">({t.rows.length})</span></summary>
          {/* one card per row: long prose columns read on a phone without sideways scrolling */}
          <ul className="hyp-cards">
            {t.rows.map((row, j) => {
              const head = Math.min(2, Math.max(1, t.columns.length - 1));
              return (
                <li key={j} className="hyp-card">
                  <p className="hyp-head">{row.slice(0, head).filter(Boolean).join(" · ")}</p>
                  <dl>
                    {t.columns.slice(head).map((c, k) => row[head + k] ? (
                      <div key={c}><dt>{c}</dt><dd>{row[head + k]}</dd></div>
                    ) : null)}
                  </dl>
                </li>
              );
            })}
          </ul>
        </details>
      ))}
    </>
  );
}

export function Research() {
  const q = useQuery({ queryKey: ["research-view"], queryFn: api.research, refetchInterval: 300000 });
  if (q.isLoading) return <section className="explain"><p className="muted" role="status">Loading research…</p></section>;
  if (q.error || !q.data) {
    return <section className="explain"><p className="err" role="alert">Could not load research ({(q.error as Error | null)?.message ?? "no data"}).</p></section>;
  }
  const v = q.data;
  const h = v.hypotheses;
  return (
    <section className="explain">
      <Budget b={v.budget} />
      <h2>Research director's plan</h2>
      {v.plan_error && <p className="warn-note">{v.plan_error}</p>}
      {v.plan ? <Plan p={v.plan} /> : !v.plan_error && <p className="empty muted">No plan yet. The research director writes one every Saturday.</p>}
      <h2>Trial registry</h2>
      <Trials rows={v.trials} total={v.trials_total} />
      <h2>Hypothesis portfolio</h2>
      <p className="muted small">
        Read-only from <span className="mono">{h.path}</span>{h.updated_utc && <>, updated <time title={utcTitle(h.updated_utc)}>{localDay(h.updated_utc)}</time></>}.
      </p>
      {h.tables.length === 0 ? <p className="empty muted">{h.note ?? "No tables in the hypothesis file."}</p> : <Hypotheses tables={h.tables} />}
    </section>
  );
}
