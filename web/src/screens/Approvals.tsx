import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, hasRole, type ReasonCode } from "../lib/api";
import { secondsLeft, useNow } from "../lib/useNow";

const REASONS: ReasonCode[] = ["news", "cost", "discretion", "duplicate", "other"];

export function Approvals() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["proposals"], queryFn: api.proposals, refetchInterval: 2000 });
  const m = useMutation({
    mutationFn: (v: { id: string; action: "approve" | "reject"; reason?: ReasonCode }) => api.decide(v.id, v.action, v.reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["proposals"] }),
  });
  const now = useNow();
  const items = q.data ?? [];
  if (items.length === 0) return <p className="muted">No proposals waiting. Entries you approve here or on Telegram are managed automatically afterwards.</p>;
  return (
    <section className="cards">
      {items.map((p) => {
        const left = secondsLeft(p.expires_at, now);
        return (
          <article key={p.proposal_id} className={`card ${p.side}`}>
            <header><strong>{p.side.toUpperCase()} {p.lots.toFixed(2)} lots</strong><span>{p.account_id}</span><span className="muted">{left}s left</span></header>
            <dl>
              <dt>Entry</dt><dd>{p.entry.toFixed(2)}</dd><dt>Stop</dt><dd>{p.stop.toFixed(2)}</dd><dt>Target</dt><dd>{p.target.toFixed(2)}</dd>
              <dt>p</dt><dd>{p.p.toFixed(2)}</dd><dt>EV</dt><dd>{p.ev_r.toFixed(2)}R</dd><dt>Spread</dt><dd>{p.spread_points.toFixed(0)}pt</dd>
            </dl>
            <p className="muted">{p.agent_id} · {p.top_features.slice(0, 3).map(([n, v]) => `${n} ${v >= 0 ? "+" : ""}${v.toFixed(2)}`).join(", ")}</p>
            {hasRole("approver") ? <div className="actions">
              <button className="ok" onClick={() => m.mutate({ id: p.proposal_id, action: "approve" })}>Approve</button>
              {REASONS.map((r) => (
                <button key={r} onClick={() => m.mutate({ id: p.proposal_id, action: "reject", reason: r })}>Reject: {r}</button>
              ))}
            </div> : <p className="muted">Viewer role: you can watch, not approve.</p>}
            {p.tradingview_url && <a href={p.tradingview_url} target="_blank" rel="noreferrer">Open chart in TradingView</a>}
          </article>
        );
      })}
    </section>
  );
}
