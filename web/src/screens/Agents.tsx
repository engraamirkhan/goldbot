import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function Agents() {
  const q = useQuery({ queryKey: ["agents"], queryFn: api.agents, refetchInterval: 30000 });
  const rows = [...(q.data ?? [])].sort((a, b) => b.fitness - a.fitness);
  if (rows.length === 0) return <p className="muted">No agents ranked yet — an agent needs 60 shadow trades before it appears.</p>;
  return (
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
  );
}
