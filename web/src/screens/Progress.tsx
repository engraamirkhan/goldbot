import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, hasRole, type AttributionReportView, type AttributionView, type GateItemView, type GatesView, type GateView,
  type ResultCell, type StopRuleView, type TradesSummary } from "../lib/api";
import { cash, signedCash } from "../lib/account";
import { localHm } from "../lib/entry";
import { pct, utcTitle } from "../lib/explain";
import { DIMENSIONS, GATE_TITLE, PHASE_WORDS, dayLabel, dayText, gateStates, headlineVerdict,
  intervalLabel, itemLabel, nextGate, orderedRows, rowLabel, signedPct, signedR, stopBadge, verdictBadge, whenShort,
  type BadgeTone, type GateState, type Verdict } from "../lib/progress";
import { CopyCommand } from "./Health";

// The Progress tab (docs/ux/progress-screens.md, ROADMAP W2-3): the stop rule, the road to live and the results, read
// only. "Nothing on these screens records a gate, unlocks live, halts or changes a setting", and "nothing here is red":
// nothing on it stops trading. Every verdict, flag and status comes from the API; this lays them out in words.

const RUNBOOK_LIVE = "https://github.com/engraamirkhan/goldbot/blob/main/docs/RUNBOOK.md#5-going-live-demo---tiny-live";
const GUIDE_DECISIONS = "https://github.com/engraamirkhan/goldbot/blob/main/docs/OWNER_GUIDE.md#4-decisions-waiting-for-you";
const OLD_SERVER = "Not Found";                        // FastAPI's 404 detail: a server without the route
const GATES_POLL = 60_000;                             // "Gates and stop rule refresh every 60 s"
const RESULTS_POLL = 300_000;                          // "Results refresh every 5 min, because the data is daily"

// the functions are looked up on each call (not captured here), so a test can stand in for the API
const gatesQuery = { queryKey: ["gates"], queryFn: () => api.gates(), refetchInterval: GATES_POLL } as const;

type Props = { onOpenOverview?: () => void };

export function Progress({ onOpenOverview }: Props) {
  const gates = useQuery(gatesQuery);
  const attr = useQuery({ queryKey: ["attribution"], queryFn: () => api.attribution(), refetchInterval: RESULTS_POLL });
  const trades = useQuery({ queryKey: ["trades-summary"], queryFn: () => api.tradesSummary(), refetchInterval: RESULTS_POLL });
  // "Server unreachable after data": badges keep their words and gain "(as of 14:02)"; nothing turns green on stale data
  const asOf = gates.isError && gates.data ? localHm(gates.dataUpdatedAt) : null;
  return (
    <div className="progress">
      <div className="progress-head">
        <h1>Progress</h1>
        {gates.data && <time className="muted small" dateTime={gates.data.generated_utc} title={utcTitle(gates.data.generated_utc)}>
          as of {localHm(Date.parse(gates.data.generated_utc))}</time>}
      </div>
      <nav className="jump" aria-label="Sections">
        <a href="#stop-rule">Stop rule</a><a href="#gates">Gates</a><a href="#results">Results</a>
      </nav>
      {asOf && <p className="entry-banner" role="alert">Can't reach the server since {asOf}; below is the last report.</p>}
      <div className="progress-top">
        <StopRuleSection q={gates} asOf={asOf} onOpenOverview={onOpenOverview} />
        <RoadToLive q={gates} asOf={asOf} />
      </div>
      <ResultsSection q={attr} />
      <YourTrades data={trades.data} />
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- shared bits
type Q<T> = { data?: T; isLoading: boolean; error: Error | null };

function VerdictBadge({ v }: { v: Verdict }) {
  const b = verdictBadge(v);
  return <Badge tone={b.tone}>{b.badge}</Badge>;
}

function Badge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return <span className={`pbadge ${tone}`}>{children}</span>;
}

function Meter({ label, value, max, text }: { label: string; value: number; max: number; text: string }) {
  const f = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div className="pmeter" role="meter" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-valuetext={text}>
      <span style={{ transform: `scaleX(${f})` }} />
    </div>
  );
}

function When({ iso, children }: { iso: string; children: ReactNode }) {
  return <time dateTime={iso} title={utcTitle(iso)}>{children}</time>;
}

/** Loading, "could not load" and "not reported by this server version", the same for every section. */
function Pending({ q, what }: { q: Q<unknown>; what: string }) {
  if (q.isLoading) return <div className="pcard skel" aria-busy="true" aria-label={`Loading ${what}…`}><span className="sk sk-title" /><span className="sk sk-line" /></div>;
  if (q.error?.message === OLD_SERVER) return <p className="pcard idle muted">Not reported by this server version.</p>;
  return (
    <p className="pcard warn" role="alert">
      Could not load {what} ({q.error?.message ?? "no data"}). Trading is not affected; safety state is on Overview and Approvals.
    </p>
  );
}

// ---------------------------------------------------------------------------------------------------- stop rule
function StopRuleSection({ q, asOf, onOpenOverview }: { q: Q<GatesView>; asOf: string | null } & Props) {
  return (
    <section id="stop-rule" className="psection" aria-labelledby="stop-h">
      <h2 id="stop-h">Stop rule</h2>
      {!q.data ? <Pending q={q} what="stop rule" /> : <StopRule s={q.data.stop_rule} asOf={asOf} onOpenOverview={onOpenOverview} />}
    </section>
  );
}

function StopRule({ s, asOf, onOpenOverview }: { s: StopRuleView; asOf: string | null } & Props) {
  const { badge: word, tone } = stopBadge(s.status);
  const breached = s.status === "breached";
  return (
    <div className={`pcard ${tone}`} role={breached ? "alert" : undefined} data-testid="stop-rule">
      <p className="pcard-head"><Badge tone={tone}>{word}{asOf && ` (as of ${asOf})`}</Badge></p>
      {s.status === "ok" && <p className="headline">Should the project stop? Not on the evidence so far.</p>}
      {s.status === "not_started" && <>
        <p className="headline">The stop rule starts counting at paper trading.</p>
        <p>The {s.after_months ?? 18}-month clock starts when the backtest → paper gate is recorded, or at the first closed demo trade.</p>
      </>}
      {s.status === "cant_check" && <>
        <p className="headline">Stop rule: can't check</p>
        {s.cant_check && <p>{s.cant_check}</p>}
        {s.months != null && <p className="muted small">Last known figures:</p>}
      </>}
      {breached && <Breach s={s} onOpenOverview={onOpenOverview} />}
      {(s.status !== "cant_check" || s.months != null) && <>
        <LongRun s={s} />
        <SingleLoss s={s} />
      </>}
      <p className="muted small">This check never halts trading by itself.</p>
    </div>
  );
}

function Breach({ s, onOpenOverview }: { s: StopRuleView } & Props) {
  const cap = s.weekly_cap ?? 0;
  return (
    <>
      <p className="headline">The design says the project stops.</p>
      <ul className="reasons-list">
        {s.breach.includes("long_run") && s.lower_bound != null && (
          <li>After {(s.months ?? 0).toFixed(1)} months and {s.pooled_trades ?? 0} trades, the cautious estimate of the edge (lower {pct(s.confidence ?? 0.9, 0)} bound)
            is still below zero ({signedPct(s.lower_bound, 3)} per trade).</li>
        )}
        {s.breach.includes("single_loss") && s.incidents.map((x, i) => (
          <li key={`${x.closed_utc}:${i}`}>
            {i === 0 ? "One trade" : "Another trade"} lost {pct(x.loss_frac, 1)} of equity, more than the {pct(cap, 1)} weekly limit
            {" · "}{x.account} · {x.mode} · closed <When iso={x.closed_utc}>{whenShort(x.closed_utc)}</When>
            {i === 0 && s.incident_count > 1 && ` (+${s.incident_count - 1} more)`}
          </li>
        ))}
      </ul>
      <p>Nothing was halted automatically. The RiskGate's limits still apply and exits keep running.</p>
      {hasRole("approver") && onOpenOverview
        ? <button type="button" className="pbutton" onClick={onOpenOverview}>Go to Halt</button>
        : <p>The owner decides whether to halt.</p>}
      <p className="muted small">Then review: ask Claude for options (Owner guide, alerts).</p>
    </>
  );
}

function LongRun({ s }: { s: StopRuleView }) {
  const after = s.after_months ?? 18, minPooled = s.min_pooled_trades ?? 500, months = s.months ?? 0, n = s.pooled_trades ?? 0;
  const conf = pct(s.confidence ?? 0.9, 0);
  // wording only (the "Judged only after…" sentence); the status and badge come from the API. Follow-up: the API
  // sends `checked_now` so this copy of the server's rule can go (HANDOFF progress-screens)
  const early = months < after || n <= minPooled;
  return (
    <div className="cond">
      <h3>Long-run edge · {months >= after ? "checked now" : `can trigger from month ${after}`}</h3>
      <div className="cond-row"><span className="cond-k">Time</span> <span>{months.toFixed(1)} of {after} months</span>
        <Meter label={`Time, ${months.toFixed(1)} of ${after} months`} value={months} max={after} text={`${months.toFixed(1)} of ${after} months`} /></div>
      <div className="cond-row"><span className="cond-k">Trades</span> <span>{n}, needs more than {minPooled}</span>
        <Meter label={`Trades, ${n} of ${minPooled}`} value={n} max={minPooled} text={`${n} of ${minPooled} trades`} /></div>
      <div className="cond-row"><span className="cond-k">Edge</span>{" "}
        {n < s.min_trades_for_estimate || s.lower_bound == null
          ? <span className="cond-wide">too few trades to estimate yet</span>
          : <span className="cond-wide">Cautious estimate {signedPct(s.lower_bound)} per trade (lower {conf} bound).
            {early && ` Judged only after month ${after} and more than ${minPooled} trades; below zero this early is expected.`}</span>}
      </div>
      {s.clock_start_utc && (
        <p className="muted small">Counts demo and live trades since <When iso={s.clock_start_utc}>{dayText(s.clock_start_utc, false)}</When>
          {" "}({s.clock_source === "paper_gate" ? "paper start" : "first trade"}).
          {s.imputed && s.imputed.n > 0 && ` ${s.imputed.n} trades had missing costs and were charged an estimate ($${s.imputed.charged_usd.toFixed(2)} in total).`}
          {s.imputed && s.imputed.unpriced > 0 && ` ${s.imputed.unpriced} trades could not be priced.`}
        </p>
      )}
    </div>
  );
}

function SingleLoss({ s }: { s: StopRuleView }) {
  const cap = s.weekly_cap ?? 0.05, w = s.worst_loss_frac;
  return (
    <div className="cond">
      <h3>Single big loss · any time</h3>
      <div className="cond-row"><span className="cond-k">Worst</span>{" "}
        {w == null ? <span className="cond-wide">no closed trade yet</span> : <>
          <span>{pct(w, 2)} of equity · limit {pct(cap, 1)}</span>
          <Meter label={`Worst loss, ${pct(w, 2)} of a ${pct(cap, 1)} limit`} value={w} max={cap} text={`${pct(w, 2)} of a ${pct(cap, 1)} limit`} />
        </>}
      </div>
      <p className="muted small">Checks every closed demo and live trade on record.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- road to live
function RoadToLive({ q, asOf }: { q: Q<GatesView>; asOf: string | null }) {
  const v = q.data;
  const states = v ? gateStates(v.gates) : [];
  const next = v ? nextGate(v.gates) : null;
  const phase = v ? v.phase ?? v.gates.filter((g) => g.recorded).length : 0;
  const suffix = [v?.cant_check ? "(unverified)" : "", asOf ? `(as of ${asOf})` : ""].filter(Boolean).join(" ");
  return (
    <section id="gates" className="psection" aria-labelledby="gates-h">
      <h2 id="gates-h">Road to live</h2>
      {!v ? <Pending q={q} what="gates" /> : (
        <div className="pcard">
          {v.cant_check && <p className="warn-note"><strong>Gates: can't check.</strong> {v.cant_check}</p>}
          {v.gates.length > 0 && (
            <p>Now: {PHASE_WORDS[phase] ?? `phase ${phase}`}.
              {next == null ? " All four gates are recorded." : ` Next: gate ${next + 1}, ${GATE_TITLE[v.gates[next].gate] ?? v.gates[next].gate}`}</p>
          )}
          {v.proposed_count > 0 && (
            <details className="info-note">
              <summary>{v.proposed_count} thresholds are proposed, not signed off. The design wants them agreed before the first paper trade.</summary>
              <ul>{v.proposed.map((p) => <li key={p.name}><code>{p.name}</code>: {p.value}</li>)}</ul>
              <a href={GUIDE_DECISIONS} target="_blank" rel="noreferrer noopener">Owner guide: Decisions waiting for you ↗</a>
            </details>
          )}
          <ol className="gates">
            {v.gates.map((g, i) => <Gate key={g.gate} g={g} i={i} st={states[i]} open={i === next} suffix={suffix} />)}
          </ol>
          <p className="muted small">Gates are recorded only by the owner, with a command on the server. This page never records a gate or turns on live.</p>
        </div>
      )}
    </section>
  );
}

function Gate({ g, i, st, open, suffix }: { g: GateView; i: number; st: GateState; open: boolean; suffix: string }) {
  return (
    <li>
      <details open={open} data-testid={`gate-${g.gate}`} className={`gate ${st.tone}`}>
        <summary>
          <span className="gnum">{i + 1}</span> <span className="gtitle">{GATE_TITLE[g.gate] ?? g.gate}</span>{" "}
          <Badge tone={st.tone}>{st.badge}</Badge>{suffix && ` ${suffix}`}{" "}
          {st.kind === "recorded" && g.recorded_utc && <When iso={g.recorded_utc}>{dayText(g.recorded_utc, false)}</When>}
          {st.line && <span className="muted">{st.line}</span>}
        </summary>
        {(st.kind === "met" || st.kind === "met_proposed") && <MetGate g={g} proposed={st.kind === "met_proposed"} />}
        <ul className="gitems">{g.items.map((it) => <Item key={it.name} it={it} />)}</ul>
      </details>
    </li>
  );
}

function MetGate({ g, proposed }: { g: GateView; proposed: boolean }) {
  return (
    <div className="gmet">
      {proposed
        ? <p>Met on the evidence, but on a threshold that is proposed and not signed off (config/settings.yaml <code>gates.signed_off</code>).
            Signing it off and recording the gate are your decisions.</p>
        : <p>Met on the evidence. Recording it is your decision.</p>}
      {hasRole("owner") ? <>
        <CopyCommand cmd="python -m goldbot.ops.run gates" />
        <CopyCommand cmd={`python -m goldbot.ops.run record-gate ${g.gate} --evidence state/gate_report.json`} />
        <a href={RUNBOOK_LIVE} target="_blank" rel="noreferrer noopener">Runbook: Going live ↗</a>
      </> : <p>The owner records it on the server.</p>}
      {g.gate === "paper_to_tiny_live" && (
        <p className="muted small">Recording this gate does not turn on live: that also needs <code>accounts unlock-live</code> and the typed phrase.</p>
      )}
    </div>
  );
}

function Item({ it }: { it: GateItemView }) {
  const label = itemLabel(it.name);
  const word = !it.required ? "for context only" : it.met ? "met" : "not met";
  const p = it.progress;
  if (!it.required) {
    return <li className="gitem context"><span className="mark" role="img" aria-label={`${label}: ${word}`}>·</span>
      <span className="muted">For context: {it.evidence}</span></li>;
  }
  return (
    <li className={`gitem ${it.met ? "met" : "unmet"}`}>
      <span className="mark" role="img" aria-label={`${label}: ${word}`}>{it.met ? "✓" : "✗"}</span>
      <div className="gbody">
        <span title={it.name}>{label}</span>
        {it.proposed && (
          <details className="ptag"><summary>Proposed</summary>
            This threshold is proposed and waits for your sign-off (config/settings.yaml <code>gates:</code>).</details>
        )}
        {p ? (
          <div className="cond-row">
            <span>have {Math.floor(p.value)} of {p.target} {p.unit}</span>
            <Meter label={`${label}, ${Math.floor(p.value)} of ${p.target}`} value={p.value} max={p.target} text={`${Math.floor(p.value)} of ${p.target} ${p.unit}`} />
          </div>
        ) : <span className="muted small">{it.evidence}</span>}
        {it.earliest_utc && <span className="muted small">earliest <When iso={it.earliest_utc}>{dayText(it.earliest_utc)}</When></span>}
        {it.ci95 && (
          <span className="small">Range {signedPct(it.ci95[0])} to {signedPct(it.ci95[1])} per trade (95%, {intervalLabel(it.ci95_basis)})</span>
        )}
        {it.need && !p && <span className="muted small">needs {it.need}</span>}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------------------------------- results
function ResultsSection({ q }: { q: Q<AttributionView> }) {
  const v = q.data;
  const rep = v?.report;
  const days = rep ? Math.round((Date.parse(rep.as_of) - Date.parse(rep.window_from)) / 86_400_000) : null;
  return (
    <section id="results" className="psection" aria-labelledby="results-h">
      <div className="progress-head">
        <h2 id="results-h" title="R = what one trade risks; +0.10 R means each trade made a tenth of its risk on average">
          Results{days != null && ` · last ${days} days`}</h2>
        {rep && <When iso={rep.as_of}>as of {whenShort(rep.as_of)}</When>}
      </div>
      {!v ? <Pending q={q} what="results" /> : <div data-testid="results">{v.cant_check ? (
        <div className="pcard warn"><p><strong>Results: can't check</strong></p><p>{v.cant_check}</p></div>
      ) : !rep ? (
        <p className="pcard idle muted">No results yet. The results job runs daily at{" "}
          {v.next_run_utc ? <When iso={v.next_run_utc}>{localHm(Date.parse(v.next_run_utc))}</When> : "its scheduled time"} your time once the shadow book has closed trades.</p>
      ) : (
        <div className="pcard">
          {v.stale && (
            <p className="warn-note">These results are from <When iso={rep.as_of}>{whenShort(rep.as_of)}</When>: the daily results job hasn't run since.
              {v.next_run_utc && <> Next run <When iso={v.next_run_utc}>{whenShort(v.next_run_utc)}</When> your time.</>} Health → Scheduled jobs shows why.</p>
          )}
          <p className="muted">Simulated trades: every signal the models took, traded at the quoted spread in the price data. Not your account's money.</p>
          {rep.overall.n === 0
            ? <p className="muted">No closed simulated trades in the last {days} days yet.</p>
            : <Report rep={rep} days={days ?? 0} />}
        </div>
      )}</div>}
    </section>
  );
}

function Report({ rep, days }: { rep: AttributionReportView; days: number }) {
  const o = rep.overall;
  const verdict = headlineVerdict(o);
  const cost = (v: number | null | undefined) => (v == null ? "—" : v === 0 ? "0.00 R" : signedR(-v));
  return (
    <>
      <p className="headline">{signedR(o.mean_r_net)} per trade after costs · {o.n} trades</p>
      <p><VerdictBadge v={verdict} />{" "}
        {(verdict === "positive" || verdict === "negative") && <span className="muted small">unlikely to be luck alone (|t| ≥ 2, today's {days} days)</span>}
        {verdict === "noise" && <span className="muted small">{o.n} of {rep.min_trades} needed</span>}</p>
      {o.ci95_net && <><p>Average per trade: {signedR(o.ci95_net[0]).replace(" R", "")} to {signedR(o.ci95_net[1])} (95%)</p>
        <RangeBar mean={o.mean_r_net ?? 0} lo={o.ci95_net[0]} hi={o.ci95_net[1]} /></>}
      <p className="small">Before costs {signedR(o.mean_r_gross)} · costs {cost(rep.costs.mean_r.total)} per trade</p>
      <Breakdowns rep={rep} />
      <h3>Where costs go (per trade)</h3>
      <p>Spread {cost(rep.costs.mean_r.spread)} · Slippage {cost(rep.costs.mean_r.slippage)} · Commission {cost(rep.costs.mean_r.commission)} · Swap {cost(rep.costs.mean_r.swap)}</p>
      {rep.costs.share_of_gross != null && <p>Costs take {pct(rep.costs.share_of_gross, 0)} of the result before costs.</p>}
      {rep.costs.killed > 0 && <p>{rep.costs.killed} trades won before costs and lost after them.</p>}
      {Object.keys(rep.live).length > 0 && <>
        <h3>Live fills vs what the backtest charged</h3>
        <ul className="plain">{Object.entries(rep.live).map(([acc, f]) => (
          <li key={acc}>{acc}: {f.fills} fills
            {f.mean_excess_usd != null && f.prior_usd != null &&
              `, $${Math.abs(f.mean_excess_usd).toFixed(3)}/oz ${f.mean_excess_usd >= 0 ? "worse" : "better"} than the $${f.prior_usd.toFixed(2)}/oz the backtest charged`}
            {" "}<VerdictBadge v={f.verdict_adj} /></li>
        ))}</ul>
      </>}
      <p className="muted small">These are findings, not settings. Each row must clear a stricter bar because {rep.m} rows are compared
        (|t| ≥ {rep.t_bar.toFixed(1)}), so on average fewer than 1 report in 20 shows a row as ahead or behind by luck. The report is
        recomputed daily: a row that crosses once and falls back is not a finding. A row can suggest an idea but cannot prove it.
        Testing the idea is a new registered trial, judged on data the row did not use: ask the research director.</p>
      <details className="as-table"><summary>Notes</summary>
        <ul className="plain small"><li>Costs from {rep.costs.source}.</li>{rep.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </details>
    </>
  );
}

/** The overall range as a whisker around 0 (the numbers sit beside it in text). */
function RangeBar({ mean, lo, hi }: { mean: number; lo: number; hi: number }) {
  const span = Math.max(Math.abs(lo), Math.abs(hi), Math.abs(mean), 1e-9);
  const x = (v: number) => 100 + (v / span) * 90;
  return (
    <svg className="rangebar" viewBox="0 0 200 16" role="img" aria-label={`Average per trade ${signedR(lo)} to ${signedR(hi)}, around 0`}>
      <title>Average per trade {signedR(lo)} to {signedR(hi)} (95%), mean {signedR(mean)}</title>
      <line className="zero" x1={100} x2={100} y1={0} y2={16} />
      <line className="whisker" x1={x(lo)} x2={x(hi)} y1={8} y2={8} />
      <circle className="dot" cx={x(mean)} cy={8} r={4} />
    </svg>
  );
}

const wide = () => typeof window.matchMedia === "function" && window.matchMedia("(min-width: 960px)").matches;

function Breakdowns({ rep }: { rep: AttributionReportView }) {
  // "by agent" only on desktop, as the last option, because agent ids are long
  const dims = DIMENSIONS.filter(([k]) => rep.breakdowns[k] && (k !== "agent" || wide()));
  const [picked, setPicked] = useState(dims[0]?.[0] ?? "timeframe");
  // a refresh that drops the picked breakdown, or a screen narrowed below 960 px, falls back to the first one, so
  // the select and the rows always agree
  const dim = dims.some(([k]) => k === picked) ? picked : dims[0]?.[0] ?? "timeframe";
  const rows = orderedRows(dim, rep.breakdowns[dim] ?? {});
  if (dims.length === 0) return null;
  return (
    <div className="breakdown">
      <label className="chart-pick">Break down by{" "}
        <select value={dim} onChange={(e) => setPicked(e.target.value)}>
          {dims.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      </label>
      <ul className="rows">{rows.map(([k, c]) => <Row key={k} dim={dim} k={k} c={c} m={rep.m} min={rep.min_trades} />)}</ul>
    </div>
  );
}

function Row({ dim, k, c, m, min }: { dim: string; k: string; c: ResultCell; m: number; min: number }) {
  const v = c.verdict_adj;
  const fixed = v === "by construction";
  return (
    <li data-testid="result-row" data-key={k} className={v === "noise" ? "muted" : ""}>
      <details>
        <summary>
          <span title={k}>{rowLabel(dim, k)}</span> {signedR(c.mean_r_net)} · {c.n} trades{" "}
          <VerdictBadge v={v} />
          {v === "noise" && <span className="small"> {c.n} of {min} needed</span>}
          {(v === "positive" || v === "negative") && c.t_bar != null &&
            <span className="small"> unlikely to be luck, even among {m} rows (|t| ≥ {c.t_bar.toFixed(1)})</span>}
        </summary>
        <div className="small">
          {fixed
            ? <p>A target exit wins and a stop exit loses 1 R by definition, so this row's sign is not evidence and it has no range.</p>
            : <>
              {c.ci_adj && <p>Average per trade: {signedR(c.ci_adj[0]).replace(" R", "")} to {signedR(c.ci_adj[1])} (95%, adjusted for {m} rows)</p>}
              {c.se != null && c.t_bar != null && <p>smallest average this row can tell from luck: ±{(c.t_bar * c.se).toFixed(2)} R</p>}
            </>}
          <p>Before costs {signedR(c.mean_r_gross)}
            {c.hit_rate != null && ` · winners ${pct(c.hit_rate, 0)}`}
            {c.profit_factor != null && ` · profit factor ${c.profit_factor.toFixed(2)}`}</p>
        </div>
      </details>
    </li>
  );
}

// ---------------------------------------------------------------------------------------------------- your trades
/** Your account's closed trades, apart from the simulated results ("the two are never added together or shown in one
 *  table"). Hidden until the server serves it. */
function YourTrades({ data }: { data?: TradesSummary[] }) {
  if (!data || data.length === 0) return null;
  const { days, trading_days } = data[0];
  // the opportunity day runs 22:00 to 22:00 UTC (ops/report_days.py), said in the owner's zone
  const now = new Date();
  const edge = localHm(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 22));
  return (
    <section className="psection" aria-labelledby="yours-h" data-testid="your-trades">
      <h2 id="yours-h">Your account's trades · last {days} calendar days ({trading_days} trading days)</h2>
      {data.map((a) => (
        <article key={a.account} className="pcard">
          <p className="pcard-head"><strong>{a.account}</strong>{a.mode && ` · ${a.mode}`}</p>
          {a.n === 0 ? <p className="muted">No closed trades on {a.account} in the last {a.days} days.</p> : <>
            <p className="headline">{signedCash(a.net, a.currency)} over {a.net_n === a.n ? a.n : `${a.net_n} of ${a.n}`} closed trades · {a.days_traded} days traded</p>
            {a.days_at_target != null && <p>Days with {cash(a.target, a.currency).replace(/\.00$/, "")} or more: {a.days_at_target} of {a.trading_days} trading days</p>}
            {a.best_day && a.worst_day && (
              <p>Best day {dayLabel(a.best_day.day)} {signedCash(a.best_day.net, a.currency)} · worst {dayLabel(a.worst_day.day)}{" "}
                {signedCash(a.worst_day.net, a.currency)} <span className="muted small">(day = {edge} to {edge} your time)</span></p>
            )}
            {a.gross_rows > 0 && <p className="warn-note">{a.gross_rows} trades have no recorded costs and are not in the total.</p>}
            {a.other_currency_rows > 0 && <p className="warn-note">{a.other_currency_rows} trades are in another currency and are not in the total.</p>}
          </>}
        </article>
      ))}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------- Overview line
const STOP_WORDS: Record<StopRuleView["status"], string> = {
  ok: "not breached", not_started: "not started", breached: "breached", cant_check: "can't check",
};

/** Overview's one line under the entry status, linking to Progress; an amber banner while the stop rule is breached. */
export function ProgressLine({ onOpen }: { onOpen: () => void }) {
  const q = useQuery(gatesQuery);
  const v = q.data;
  if (!v) return <div className="progress-line-slot" aria-busy={q.isLoading} />;
  const i = nextGate(v.gates);
  const st = gateStates(v.gates);
  const gate = i == null ? "all recorded" : `${GATE_TITLE[v.gates[i].gate] ?? v.gates[i].gate}, ${st[i].line || st[i].badge.toLowerCase()}`;
  return (
    <>
      {v.stop_rule.status === "breached" && (
        <div className="entry-banner" role="alert">
          Stop rule breached: the design says the project stops.{" "}
          <button type="button" className="pbutton" onClick={onOpen}>Open Progress</button>
        </div>
      )}
      <div className="progress-line-slot">
        <button type="button" className="progress-line" onClick={onOpen}>
          Stop rule: {STOP_WORDS[v.stop_rule.status]} · Next gate: {gate} <span aria-hidden="true">›</span>
        </button>
      </div>
    </>
  );
}
