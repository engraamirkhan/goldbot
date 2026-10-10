import { useState } from "react";
import type { ReasonCode } from "../lib/api";
import {
  type AnyProposal, type CardState, EXPIRING_S, REASONS, WINDOW_S, cardState, countdown, isDecided, localTime, money,
  plain, reasonLabel, signed, via,
} from "../lib/approval";
import { featureValue, plainFeature } from "../lib/features";
import { secondsLeft } from "../lib/useNow";

type Props = {
  p: AnyProposal;
  now: number;
  canDecide: boolean;
  blockers: string[];          // why the RiskGate would refuse an entry right now (empty: allowed)
  busy?: boolean;              // a decision for this card is in flight
  error?: string | null;
  onApprove?: () => void;
  onReject?: (reason: ReasonCode) => void;
};

const OPEN: CardState[] = ["pending", "expiring"];

/** One proposal: what the trade is, why, how long is left, and one tap to approve. Spec: docs/ux/approval-card.md. */
export function ProposalCard({ p, now, canDecide, blockers, busy = false, error, onApprove, onReject }: Props) {
  const [rejecting, setRejecting] = useState(false);
  const state = cardState(p, now);
  const open = OPEN.includes(state);
  const left = isDecided(p) ? 0 : secondsLeft(p.expires_at, now);
  const long = p.side === "long";
  const exp = localTime(p.expires_at);
  const title = `${long ? "Long" : "Short"} ${p.lots.toFixed(2)} lots XAUUSD`;
  return (
    <article className={`card ${p.side} ${state}`} aria-label={`${title}, ${STATE_NAME[state]}`}>
      <header className="card-head">
        <span className={`dir ${p.side}`}><span aria-hidden="true">{long ? "▲" : "▼"}</span> {long ? "LONG" : "SHORT"}</span>
        <strong className="size">{p.lots.toFixed(2)} lots <span className="muted">XAUUSD</span></strong>
        {open && (
          <span className="timer" role="timer" aria-label={`${left} seconds left to decide`}>
            {countdown(left)}
          </span>
        )}
      </header>
      {open && (
        <div className="bar" aria-hidden="true"><span style={{ transform: `scaleX(${Math.min(1, left / WINDOW_S)})` }} /></div>
      )}
      <p className="sr-only" aria-live="assertive">{state === "expiring" ? `${EXPIRING_S} seconds left to decide` : state === "expired" ? "Expired" : ""}</p>
      <Outcome p={p} state={state} />

      <p className="risk">
        <span className="k">Risk</span>{" "}
        {p.risk_usd != null
          ? <strong>{money(p.risk_usd)}</strong>
          : <strong title="This engine version does not report $ risk">—</strong>}
        <span className="muted"> if the stop is hit</span>
      </p>
      <dl className="levels">
        <div><dt>Entry</dt><dd>{p.entry.toFixed(2)}</dd></div>
        <div><dt>Stop</dt><dd>{p.stop.toFixed(2)}<small>{signed(p.stop - p.entry)}</small></dd></div>
        <div><dt>Target</dt><dd>{p.target.toFixed(2)}<small>{signed(p.target - p.entry)}</small></dd></div>
      </dl>
      <dl className="metrics">
        <div><dt>Win prob.</dt><dd>p {p.p.toFixed(2)}</dd></div>
        <div><dt>Expected</dt><dd>{signed(p.ev_r)} R</dd></div>
        <div><dt>Spread now</dt><dd>{p.spread_points.toFixed(0)} pt</dd></div>
      </dl>
      {p.top_features.length > 0 && (
        <div className="why">
          <span className="k">Why</span>
          <ul>
            {p.top_features.slice(0, 3).map(([n, v]) => (
              <li key={n} title={n}>{plainFeature(n)} <span className="muted">{featureValue(v)}</span></li>
            ))}
          </ul>
        </div>
      )}

      {open && blockers.length > 0 && (
        <p className="warn-note">New entries are blocked ({blockers.join(", ")}): the RiskGate will refuse this at approval.</p>
      )}
      {open && (canDecide ? (
        rejecting ? (
          <div className="reasons" role="group" aria-label="Reason for rejecting">
            {REASONS.map((r) => (
              <button key={r.code} type="button" disabled={busy} title={r.hint}
                      onClick={() => { setRejecting(false); onReject?.(r.code); }}>{r.label}</button>
            ))}
            <button type="button" className="cancel" onClick={() => setRejecting(false)}>Cancel</button>
          </div>
        ) : (
          <div className="decide">
            <button type="button" className="approve" disabled={busy} onClick={onApprove}>{busy ? "Sending…" : "Approve"}</button>
            <button type="button" className="reject" disabled={busy} onClick={() => setRejecting(true)}>Reject…</button>
          </div>
        )
      ) : <p className="muted">Viewer role: you can watch, not approve.</p>)}
      {error && <p className="err" role="alert">{error}</p>}

      <footer className="card-foot muted">
        <span>{p.account_id}</span><span>{p.agent_id}</span>
        <span>expires <time dateTime={p.expires_at} title={exp.utc}>{exp.text}</time></span>
        {p.tradingview_url && <a href={p.tradingview_url} target="_blank" rel="noreferrer">Chart</a>}
      </footer>
    </article>
  );
}

const STATE_NAME: Record<CardState, string> = {
  pending: "waiting for your decision", expiring: "expiring", expired: "expired", submitted: "approval sent",
  approved: "approved", rejected: "rejected", refused: "refused by the RiskGate",
};

function Outcome({ p, state }: { p: AnyProposal; state: CardState }) {
  if (state === "pending" || state === "expiring") return null;
  const d = isDecided(p) ? p : null;
  const by = via(d?.decided_by);
  const tail = by ? <span className="muted"> · via {by}</span> : null;
  switch (state) {
    case "submitted":
      return <p className="outcome info" role="status"><strong>Approval sent.</strong> The engine re-checks risk and places the order on its next tick.{tail}</p>;
    case "approved":
      return <p className="outcome ok" role="status"><strong>Approved, order sent.</strong> Stop, target and exit are automatic.{tail}</p>;
    case "rejected":
      return <p className="outcome muted-box" role="status"><strong>Rejected</strong> · {reasonLabel(d?.reason_code)}{tail}</p>;
    case "refused":
      return (
        <p className="outcome bad" role="status">
          <strong>Refused at approval.</strong> The RiskGate blocked the order ({(d?.refusal ?? []).map(plain).join(", ") || "no reason given"}); nothing was sent.{tail}
        </p>
      );
    case "expired":
      return <p className="outcome muted-box" role="status"><strong>Expired.</strong> Nobody approved within {WINDOW_S} s; no order.</p>;
  }
}
