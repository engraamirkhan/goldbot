import { useQuery } from "@tanstack/react-query";
import { api, type AgentRunRow } from "../lib/api";

const ROLE_TITLES: Record<string, string> = {
  data_steward: "Data steward", risk_officer: "Risk officer", journal_coach: "Journal coach", improvement_agent: "Improvement agent",
};

function StaffReports({ runs }: { runs: AgentRunRow[] }) {
  if (runs.length === 0) return <p className="muted">No staff-agent reports yet (they need an Anthropic API key in the VPS keyring).</p>;
  return (
    <ul className="reports">
      {runs.map((r) => (
        <li key={`${r.role}-${r.started_utc}`}>
          <details>
            <summary>
              <strong>{ROLE_TITLES[r.role] ?? r.role}</strong> · {new Date(r.started_utc).toLocaleString()} ·{" "}
              <span className={r.status === "ok" ? "" : "bad"}>{r.status}</span> · ${r.cost_usd.toFixed(2)}
            </summary>
            {r.detail && <p className="muted">{r.detail}</p>}
            <pre className="report">{r.report ?? "(no report written)"}</pre>
          </details>
        </li>
      ))}
    </ul>
  );
}

export function Agents() {
  const q = useQuery({ queryKey: ["agents"], queryFn: api.agents, refetchInterval: 30000 });
  const runs = useQuery({ queryKey: ["agent-runs"], queryFn: api.agentRuns, refetchInterval: 60000 });
  const rows = [...(q.data ?? [])].sort((a, b) => b.fitness - a.fitness);
  return (
    <section>
      <h2>League</h2>
      {rows.length === 0 ? <p className="muted">No agents ranked yet — an agent needs 60 shadow trades before it appears.</p> : (
        <table>
          <thead><tr><th>Agent</th><th>Family</th><th>Gen</th><th>Parent</th><th>Status</th><th>Trades</th><th>Expectancy (R)</th><th>Hit rate</th><th>Calibration ECE</th><th>Fitness</th><th>Capital</th></tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.agent_id} className={a.status}>
                <td>{a.agent_id}</td><td>{a.family}</td><td>{a.generation}</td><td className="muted">{a.parent_id ?? "—"}</td>
                <td>{a.status}</td><td>{a.n_trades}</td><td>{a.expectancy_r.toFixed(3)}</td><td>{(a.hit_rate * 100).toFixed(1)}%</td>
                <td>{a.calibration_ece.toFixed(3)}</td><td>{a.fitness.toFixed(2)}</td><td>{(a.capital_weight * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Staff reports</h2>
      <StaffReports runs={runs.data ?? []} />
    </section>
  );
}
