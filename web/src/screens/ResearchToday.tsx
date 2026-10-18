// Research today (docs/ux/limits-and-tradingview.md, card C; top of the Research tab) and its Overview line. The
// server counts the UTC day (ResearchView.today); the quarter's budget is ResearchView.budget, the one count the
// registry charges. Read-only.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ResearchView } from "../lib/explain";
import { verdictWords } from "../lib/limits";

const UTC_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const day = (isoDate: string) => UTC_DAY.format(new Date(`${isoDate}T00:00:00Z`));
const nextDay = (isoDate: string) => UTC_DAY.format(new Date(new Date(`${isoDate}T00:00:00Z`).getTime() + 86400_000));

export function ResearchTodayCard({ v }: { v: ResearchView }) {
  const t = v.today;
  const b = v.budget;
  const low = b.left > 0 && b.left <= b.budget * 0.1;
  return (
    <section className="acct research-today" aria-label="Research today">
      <header className="acct-head"><strong>Research today</strong><span className="muted small">({day(t.date_utc)}, UTC day)</span></header>
      {t.run === 0 ? <p className="muted">No trials today.</p> : (
        <p className="tv-counts">
          <span>Trials run: {t.run}</span><span>Passed all gates: {t.passed}</span><span>Failed: {t.failed}</span>
          <span>Needs more data: {t.no_verdict}</span>
          {t.not_counted > 0 && <span className="warn-text">Passed, but costs not recorded: not counted: {t.not_counted}</span>}
        </p>
      )}
      <p className="small">
        {`${b.used} of ${b.budget} trials used this quarter (${b.left} left, quarter ends ${day(t.quarter_end)})`}
        {b.left === 0 && <span className="state warn">{` Budget used up: no new trials until ${nextDay(t.quarter_end)}`}</span>}
        {low && <span className="state warn"> Plan the rest carefully</span>}
      </p>
      {t.latest && <p className="small">Latest: "{t.latest.rationale || t.latest.family}" — {verdictWords(t.latest)}</p>}
    </section>
  );
}

/** Overview: the same counts in one sentence. */
export function ResearchLine() {
  const q = useQuery({ queryKey: ["research-view"], queryFn: api.research, refetchInterval: 300000 });
  if (!q.data) return null;
  const { today: t, budget: b } = q.data;
  const text = t.run === 0 ? "Research today: no trials yet."
    : `Research today: ${t.run} trials, ${t.passed} passed, ${t.failed} failed, ${t.no_verdict} need more data.`;
  return <p className="small research-line">{`${text} ${b.used} of ${b.budget} used (${b.left} left).`}</p>;
}
