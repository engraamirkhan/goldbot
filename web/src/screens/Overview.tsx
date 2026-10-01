import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;

export function Overview() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  return (
    <section>
      <div className="tiles">
        <div className="tile"><span>Mode</span><strong>{status.data?.mode ?? "…"}</strong></div>
        <div className={`tile ${status.data?.halted ? "bad" : ""}`}><span>Halted</span><strong>{status.data ? (status.data.halted ? "yes" : "no") : "…"}</strong></div>
        <div className="tile"><span>Pending approvals</span><strong>{status.data?.pending ?? "…"}</strong></div>
      </div>
      <table>
        <thead><tr><th>Account</th><th>Mode</th><th>Class</th><th>Equity</th><th>Today</th><th>Week</th><th>Drawdown</th><th>Stage</th><th>Open</th></tr></thead>
        <tbody>
          {(accounts.data ?? []).map((a) => (
            <tr key={a.account_id} className={a.stage !== "normal" ? "bad" : ""}>
              <td>{a.account_id}</td><td>{a.mode}</td><td>{a.account_class}</td>
              <td>{a.equity.toFixed(2)}</td><td>{pct(a.day_pnl_pct)}</td><td>{pct(a.week_pnl_pct)}</td>
              <td>{pct(a.drawdown_pct)}</td><td>{a.stage}</td><td>{a.open_positions}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {accounts.data?.length === 0 && <p className="muted">No engines reporting yet.</p>}
    </section>
  );
}
