import type { AccountSummary } from "../lib/api";
import { budgetTone, cash, signedCash } from "../lib/account";

const signedPct = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(2)}%`;
const round2 = (x: number) => Number(x.toFixed(2));

/** The live account (ADR 0004), from the engine's broker reading: balance vs equity, today's change, margin level,
 *  today's loss budget and the next milestone. Every amount is in the account's own currency. */
export function BalanceCard({ a }: { a: AccountSummary }) {
  const ccy = a.currency ?? null;
  const open = a.balance != null ? a.equity - a.balance : null;
  return (
    <section className="acct" aria-label={`Account ${a.account_id}`}>
      <header className="acct-head">
        <strong>{a.account_id}</strong>
        <span className="tag">{a.mode} · {a.account_class}</span>
        {ccy && <span className="muted small">{ccy}</span>}
      </header>
      <dl className="acct-figs">
        <div><dt>Balance</dt><dd>{a.balance != null ? cash(a.balance, ccy) : <span className="muted">not reported</span>}</dd></div>
        <div><dt>Equity</dt><dd>{cash(a.equity, ccy)}{open != null && <small>{signedCash(open, ccy)} open</small>}</dd></div>
        <div><dt>Today</dt><dd>{a.day_pnl != null ? signedCash(a.day_pnl, ccy) : signedPct(a.day_pnl_pct)}
          <small>{a.day_pnl != null ? `${signedPct(a.day_pnl_pct)} ` : ""}since 00:00 UTC</small></dd></div>
        <div><dt>Margin level</dt><dd>{a.margin_level_pct != null ? `${Math.round(a.margin_level_pct)}%`
          : <span className="muted">{a.balance == null ? "not reported" : "no margin in use"}</span>}</dd></div>
      </dl>
      <LossBudget a={a} />
      <Milestone a={a} />
    </section>
  );
}

function LossBudget({ a }: { a: AccountSummary }) {
  const b = a.loss_budget;
  if (!b) return <p className="budget idle muted small">Loss budget not reported by this engine yet.</p>;
  const ccy = a.currency ?? null;
  const tone = budgetTone(b);
  const used = Math.min(b.used, b.limit);
  return (
    <div className={`budget ${tone}`}>
      <p><span className="k">Today's loss budget</span>{" "}
        <strong>{cash(b.remaining, ccy)} left</strong>{" "}
        <span>of {cash(b.limit, ccy)} · {b.binding}</span></p>
      <div className="bar budget-bar" role="meter" aria-label="Daily loss budget used" aria-valuemin={0}
           aria-valuemax={round2(b.limit)} aria-valuenow={round2(used)}>
        <span style={{ transform: `scaleX(${b.limit > 0 ? used / b.limit : 1})` }} />
      </div>
      <p className="small">{tone === "bad" ? "New entries are stopped for today; exits always run."
        : `Used ${cash(b.used, ccy)} today. New entries stop when it runs out; exits always run.`}</p>
      {b.notes.length > 0 && <ul className="notes small muted">{b.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    </div>
  );
}

function Milestone({ a }: { a: AccountSummary }) {
  const m = a.milestones;
  if (!m) return null;
  const ccy = a.currency ?? null;
  if (m.pending) {
    return (
      <div className="milestone">
        <p><span className="k">Next milestone</span> {m.pending}</p>
        <p className="small muted">Shown once the engine reads it from the broker; nothing is estimated.</p>
      </div>
    );
  }
  const pct = m.progress != null ? Math.round(m.progress * 100) : null;
  return (
    <div className="milestone">
      <p><span className="k">Milestones</span> Trades now: {m.tradeable.length ? m.tradeable.join(", ") : "none yet"}</p>
      {m.next_timeframe && m.next_equity != null && pct != null ? (
        <>
          <p>Next: {m.next_timeframe} at {cash(m.next_equity, ccy)}</p>
          <div className="bar" role="progressbar" aria-label={`Progress to the ${m.next_timeframe} milestone`}
               aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <span style={{ transform: `scaleX(${m.progress})` }} />
          </div>
          <p className="small muted">{cash(a.equity, ccy)} of {cash(m.next_equity, ccy)} ({pct}%). A milestone allows a
            timeframe; a strategy on it still has to pass its tests.</p>
        </>
      ) : <p className="small muted">Every milestone reached.</p>}
      <p className="small muted">Rule: {m.rule}.</p>
    </div>
  );
}
