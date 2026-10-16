import type { AccountSummary } from "../lib/api";
import { budgetTone, cash, signedCash } from "../lib/account";

const signedPct = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(2)}%`;
const round2 = (x: number) => Number(x.toFixed(2));
const TF_WORDS: Record<string, string> = { "15m": "15-minute", "1h": "1-hour", "4h": "4-hour", "1d": "daily" };
const tfWords = (tf: string) => TF_WORDS[tf] ?? tf;
const list = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** Today's 00:00 UTC (the risk day's start) in the viewer's local time, e.g. "01:00". */
function dayStartLocal(now = new Date()): string {
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  return start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** The live account (ADR 0004), from the engine's broker reading: balance vs equity, today's change, margin level,
 *  the loss budget and the next milestone. Every amount is in the account's own currency. */
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
          <small>{a.day_pnl != null ? `${signedPct(a.day_pnl_pct)} ` : ""}since {dayStartLocal()} your time (00:00 UTC)</small></dd></div>
        <div><dt>Margin level</dt><dd>{a.margin_level_pct != null ? `${Math.round(a.margin_level_pct)}%`
          : <span className="muted">{a.balance == null ? "not reported" : "no margin in use"}</span>}</dd></div>
      </dl>
      <LossBudget a={a} />
      <Milestone a={a} />
    </section>
  );
}

/** Placeholder with the card's shape while the accounts load, so nothing jumps when they arrive. */
export function BalanceCardSkeleton() {
  return (
    <section className="acct skeleton" aria-label="Loading account" aria-busy="true">
      <header className="acct-head"><span className="sk sk-title" /></header>
      <div className="acct-figs">{[0, 1, 2, 3].map((i) => <span key={i} className="sk sk-fig" />)}</div>
      <span className="sk sk-line" />
      <span className="sk sk-line" />
    </section>
  );
}

function LossBudget({ a }: { a: AccountSummary }) {
  const b = a.loss_budget;
  if (!b) return <p className="budget idle muted small">Loss budget not reported by this engine yet.</p>;
  const notes = b.notes.length > 0 && <ul className="notes small muted">{b.notes.map((n) => <li key={n}>{n}</li>)}</ul>;
  if (b.unknown) {   // the account cap alone would look roomier than the combined cap the supervisor enforces
    return (
      <div className={`budget ${budgetTone(b)}`}>
        <p><strong>Loss budget: can't check</strong></p>
        <p className="small">{b.unknown}</p>
        {notes}
      </div>
    );
  }
  const ccy = a.currency ?? null;
  const tone = budgetTone(b);
  const used = Math.min(b.used, b.limit);
  const whose = b.period === "this week" ? "this week's" : "today's";
  return (
    <div className={`budget ${tone}`}>
      <p><span className="k">Loss budget</span>{" "}
        <strong>{cash(b.remaining, ccy)} left</strong>{" "}
        <span className="small">limit {cash(b.limit, ccy)} {b.period}</span></p>
      <p className="small">{`Limit set by: ${b.binding_label}`}</p>
      <div className="bar budget-bar" role="meter" aria-label={`${b.period === "this week" ? "Weekly" : "Daily"} loss budget used`} aria-valuemin={0}
           aria-valuemax={round2(b.limit)} aria-valuenow={round2(used)}>
        <span style={{ transform: `scaleX(${b.limit > 0 ? used / b.limit : 1})` }} />
      </div>
      <p className="small">{tone === "bad" ? `New entries are stopped while ${whose} loss is at the limit; exits always run.`
        : `Lost ${cash(b.used, ccy)} ${b.period}. New entries stop when nothing is left; exits always run.`}</p>
      {notes}
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
      <p><span className="k">Milestones</span>{" "}
        {m.tradeable.length ? `Your balance is big enough for ${list(m.tradeable.map(tfWords))} trades.`
          : "Your balance is not yet big enough for the smallest trade on any timeframe."}</p>
      {m.next_timeframe && m.next_equity != null && pct != null ? (
        <>
          <p>Next: {tfWords(m.next_timeframe)} trades at {cash(m.next_equity, ccy)}</p>
          <div className="bar" role="progressbar" aria-label={`Progress to ${tfWords(m.next_timeframe)} trades`}
               aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <span style={{ transform: `scaleX(${m.progress})` }} />
          </div>
          <p className="small muted">{cash(a.equity, ccy)} of {cash(m.next_equity, ccy)} ({pct}%). A milestone allows a
            timeframe; a strategy on it still has to pass its tests.</p>
        </>
      ) : <p className="small muted">Every milestone reached.</p>}
      <details className="small muted"><summary>How this is worked out</summary>A timeframe counts once {m.rule}.</details>
    </div>
  );
}
