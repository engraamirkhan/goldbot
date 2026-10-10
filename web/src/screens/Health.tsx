import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { localTime, num, pct, utcTitle, type AgentHealthRow, type HealthCheckRow, type HealthView } from "../lib/explain";
import { CusumChart, DrawdownBars, PsiHeatmap, ReliabilityChart } from "./Charts";

const CHECK_TITLES: Record<string, string> = { deploy: "Deploy", data_quality: "Data quality", drift: "Drift watch" };
const CHECK_WORDS: Record<HealthCheckRow["status"], string> = { ok: "ok", warn: "warning", fail: "FAILING" };

function CopyCommand({ cmd }: { cmd: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="cmd">
      <code>{cmd}</code>
      <button type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(cmd); setDone(true); setTimeout(() => setDone(false), 2000); } catch { /* clipboard blocked: the text is selectable */ }
      }}>{done ? "Copied" : "Copy"}</button>
    </div>
  );
}

function SystemHaltBanner({ v }: { v: HealthView }) {
  const sh = v.system_halt;
  if (!sh) return null;
  return (
    <div className="banner bad halt-banner" role="alert">
      <strong>{v.drift_error ? "Drift state unreadable: new entries are stopped" : "System halt: every new entry is stopped pending your review"}</strong>
      {sh.since && <span> since <time title={utcTitle(sh.since)}>{localTime(sh.since)}</time></span>}. Open positions keep their stops and exits.
      <ul>{sh.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      <p>On the VPS, look at the drift report, then record what you checked to lift the halt:</p>
      <CopyCommand cmd={sh.review_command} />
      <CopyCommand cmd={sh.clear_command} />
    </div>
  );
}

function Checks({ checks }: { checks: HealthCheckRow[] }) {
  return (
    <ul className="strip checks" aria-label="Health checks">
      {checks.map((c) => (
        <li key={c.name} className={`chip ${c.status === "fail" ? "bad" : c.status}`}>
          <span className="k">{CHECK_TITLES[c.name] ?? c.name}</span>
          <strong>{CHECK_WORDS[c.status]}</strong>
          <span className="detail">{c.reason}</span>
        </li>
      ))}
    </ul>
  );
}

function AgentState({ a }: { a: AgentHealthRow }) {
  if (a.halted) {
    return <span className="state bad">Halted{a.halted_since && <> since <time title={utcTitle(a.halted_since)}>{localTime(a.halted_since)}</time></>}</span>;
  }
  return <span className="state ok">Trading</span>;
}

function AgentsTable({ agents }: { agents: AgentHealthRow[] }) {
  if (agents.length === 0) {
    return <p className="empty muted">No drift check yet. The drift watch runs daily on the VPS once a champion model is live.</p>;
  }
  return (
    <div className="scroll"><table className="agents-health">
      <thead><tr><th>Agent</th><th>State</th><th>Size</th><th>Calibration (ECE)</th><th>Capital share</th><th>Why</th></tr></thead>
      <tbody>
        {agents.map((a) => {
          const reasons = [...new Set([...a.halt_reasons, ...a.notes])];
          return (
            <tr key={a.agent_id}>
              <td data-label="Agent"><strong>{a.agent_id}</strong><br /><span className="muted small mono">{a.version}</span></td>
              <td data-label="State"><AgentState a={a} /></td>
              <td data-label="Size">{a.size_factor < 1 ? <span className="state warn">{pct(a.size_factor, 0)}: sized down</span> : <span>100%</span>}</td>
              <td data-label="Calibration (ECE)" title={`over ${a.n_calib} trades; sizes down above 0.08`}>{a.ece == null ? <span className="muted">n/a ({a.n_calib} trades)</span> : num(a.ece, 3)}</td>
              <td data-label="Capital share">{a.capital_weight == null ? <span className="muted">—</span> : pct(a.capital_weight, 0)}</td>
              <td data-label="Why">{reasons.length ? <ul className="reasons-list">{reasons.map((r) => <li key={r}>{r}</li>)}</ul> : <span className="muted">no findings</span>}</td>
            </tr>
          );
        })}
      </tbody>
    </table></div>
  );
}

export function Health() {
  const q = useQuery({ queryKey: ["health-view"], queryFn: api.health, refetchInterval: 60000 });
  if (q.isLoading) return <section className="explain"><p className="muted" role="status">Loading health…</p></section>;
  if (q.error || !q.data) {
    return <section className="explain"><p className="err" role="alert">Could not load health ({(q.error as Error | null)?.message ?? "no data"}). Safety state is still on Overview and Approvals.</p></section>;
  }
  const v = q.data;
  return (
    <section className="explain">
      <SystemHaltBanner v={v} />
      <p className="muted small">
        {v.drift_ts ? <>Drift watch last ran <time title={utcTitle(v.drift_ts)}>{localTime(v.drift_ts)}</time>.</> : "The drift watch has not run yet."}
        {" "}Halts and size-downs act on new entries only; exits are never affected.
      </p>
      <h2>Health checks</h2>
      <Checks checks={v.checks} />
      <h2>Agents</h2>
      <AgentsTable agents={v.agents} />
      {Object.keys(v.errors).length > 0 && (
        <p className="warn-note">Not checked: {Object.entries(v.errors).map(([a, e]) => `${a} (${e})`).join("; ")}</p>
      )}
      <div className="chart-grid">
        <div>
          <h2>Feature drift (PSI)</h2>
          <PsiHeatmap data={v.psi} />
        </div>
        <div>
          <h2>Calibration</h2>
          <ReliabilityChart curves={v.reliability} />
        </div>
        <div>
          <h2>CUSUM on trade residuals</h2>
          {v.cusum.length === 0
            ? <p className="empty muted">No closed shadow trades yet. The trace climbs when trades do worse than their p implied.</p>
            : v.cusum.map((t) => <CusumChart key={t.version} trace={t} />)}
        </div>
        <div>
          <h2>30-day drawdown vs {v.dd_mult}× backtest</h2>
          <DrawdownBars agents={v.agents} mult={v.dd_mult} />
        </div>
      </div>
    </section>
  );
}
