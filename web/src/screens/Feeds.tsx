import { useQuery } from "@tanstack/react-query";
import { api, type JobRow } from "../lib/api";

const fmt = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");

function jobState(j: JobRow): { label: string; bad: boolean } {
  if (j.heartbeat_age_s > 300) return { label: "scheduler silent", bad: true };
  if (j.last_ok === false) return { label: "failed", bad: true };
  if (j.last_ok === true) return { label: "ok", bad: false };
  return { label: "not run yet", bad: false };
}

export function Feeds() {
  const q = useQuery({ queryKey: ["feeds"], queryFn: api.feeds, refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, refetchInterval: 30000 });
  const rows = q.data ?? [];
  return (
    <section>
      {rows.length === 0 ? <p className="muted">No feeds reporting yet.</p> : (
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
      )}
      <h2>Scheduled jobs</h2>
      {(jobs.data ?? []).length === 0 ? <p className="muted">The scheduler has not reported yet.</p> : (
        <table>
          <thead><tr><th>Job</th><th>State</th><th>Last slot</th><th>Finished</th><th>Next</th><th>Runs / failures</th><th>Last error</th></tr></thead>
          <tbody>
            {(jobs.data ?? []).map((j) => {
              const s = jobState(j);
              return (
                <tr key={j.name} className={s.bad ? "bad" : ""}>
                  <td>{j.name}</td><td>{s.label}</td><td>{fmt(j.last_slot)}</td><td>{fmt(j.last_finished)}</td>
                  <td>{fmt(j.next_slot)}</td><td>{j.runs} / {j.failures}</td><td className="mono">{j.last_error ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
