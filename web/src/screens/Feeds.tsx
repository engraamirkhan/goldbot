import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function Feeds() {
  const q = useQuery({ queryKey: ["feeds"], queryFn: api.feeds, refetchInterval: 5000 });
  const rows = q.data ?? [];
  if (rows.length === 0) return <p className="muted">No feeds reporting yet.</p>;
  return (
    <table>
      <thead><tr><th>Account</th><th>Terminal</th><th>Last tick</th><th>Spread</th><th>Webhook p99</th><th>Supervisor heartbeat</th></tr></thead>
      <tbody>
        {rows.map((f) => (
          <tr key={f.account_id} className={!f.terminal_connected || f.last_tick_age_s > 20 || f.supervisor_heartbeat_age_s > 60 ? "bad" : ""}>
            <td>{f.account_id}</td><td>{f.terminal_connected ? "connected" : "down"}</td>
            <td>{f.last_tick_age_s.toFixed(1)}s</td><td>{f.spread_points.toFixed(0)}pt</td>
            <td>{f.webhook_p99_latency_s == null ? "—" : `${f.webhook_p99_latency_s.toFixed(1)}s`}</td>
            <td>{f.supervisor_heartbeat_age_s.toFixed(0)}s</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
