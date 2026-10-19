import { useState } from "react";
import type { DecidedProposal, ReasonCode } from "../lib/api";
import {
  type AnyProposal, type CardState, EXPIRING_S, REASONS, WINDOW_S, budgetLine, cardMoney, cardState, countdown, isDecided,
  localTime, pct, plain, reasonLabel, signed, viaChannel,
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
  const money = cardMoney(p);
  const budget = budgetLine(p);
  return (
    <article className={`card ${p.side} ${state}`} aria-label={`${title}, ${stateName(p, state)}`}>
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
        {money
          ? <strong>{money.risk}</strong>
          : <strong title="This engine version does not report the risk">—</strong>}
        {money?.usd && <span className="muted"> (USD)</span>}
        {money && !money.usd && p.risk_fraction != null && <span> ({pct(p.risk_fraction)} of equity)</span>}
        <span className="muted"> if the stop is hit, before costs</span>
      </p>
      {p.min_lot_exception && (
        <p className="info-note">
          {`Smallest trade size: above the normal ${p.risk_normal != null ? pct(p.risk_normal) : "risk"}`}
          {p.risk_cap != null ? `, within your ${pct(p.risk_cap)} cap` : ""}.
        </p>
      )}
      {open && (
        <p className={`budget ${budget.tone}`}><span className="k">Loss budget</span> <span>{budget.text}</span></p>
      )}
      <dl className="levels">
        <div><dt>Entry</dt><dd>{p.entry.toFixed(2)}</dd></div>
        <div>
          <dt>Stop</dt>
          <dd>{p.stop.toFixed(2)}<small>{signed(p.stop - p.entry)}</small>{money && <small>−{money.risk}</small>}</dd>
        </div>
        <div>
          <dt>Target</dt>
          <dd>{p.target.toFixed(2)}<small>{signed(p.target - p.entry)}</small>{money?.reward && <small>+{money.reward}</small>}</dd>
        </div>
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
        <span>{p.account_id}</span>
        <span>expires <time dateTime={p.expires_at} title={exp.utc}>{exp.text}</time></span>
        {p.tradingview_url && <a href={p.tradingview_url} target="_blank" rel="noreferrer">Chart</a>}
      </footer>
    </article>
  );
}

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const STATE_NAME: Record<CardState, string> = {
  pending: "waiting for your decision", expiring: "expiring", expired: "expired", submitted: "approval sent",
  approved: "approved", rejected: "rejected", refused: "not placed",   // by the RiskGate, the MT5 box or the broker
  not_confirmed: "sent, not confirmed by the broker",                  // docs/ux/order-not-confirmed.md
};
// docs/ux/order-not-confirmed.md: a resolved order the broker didn't confirm at first is named by what was found
const NOT_CONFIRMED_NAME: Record<string, string> = {
  opened: "opened", opened_late: "opened", opened_closed: "opened and closed", no_trade: "not placed",
  still_unknown: "still not confirmed",
};
const stateName = (p: AnyProposal, state: CardState) =>
  (state === "not_confirmed" && isDecided(p) && NOT_CONFIRMED_NAME[p.not_confirmed?.outcome ?? ""]) || STATE_NAME[state];
// the API's tone to the note's class: amber while unknown (not a halt), red only for "Not placed"
const TONE_CLASS: Record<string, string> = { warn: "warn", ok: "ok", neutral: "muted-box", bad: "bad" };

// the API's words for the RiskGate's codes (`refusal_text`, BACKLOG 72); an older API's codes in plain words
const refusedWhy = (d: DecidedProposal | null) =>
  (d?.refusal_text?.length ? d.refusal_text : (d?.refusal ?? []).map(plain)).join(", ") || "no reason given";

function Outcome({ p, state }: { p: AnyProposal; state: CardState }) {
  if (state === "pending" || state === "expiring") return null;
  const d = isDecided(p) ? p : null;
  const by = viaChannel(d?.decided_channel);
  const tail = by ? <span className="muted"> · via {by}</span> : null;
  switch (state) {
    case "not_confirmed": {
      // BACKLOG 72: the API words what the engine's look-up at the broker found (one table with Telegram)
      const nc = d?.not_confirmed;
      if (!nc) return null;
      return (
        <p className={`outcome ${TONE_CLASS[nc.tone] ?? "warn"}`} role="status">
          <strong>{nc.heading}</strong> {nc.text}{tail}
        </p>
      );
    }
    case "submitted":
      return <p className="outcome info" role="status"><strong>Approval sent.</strong> The engine re-checks risk and places the order on its next tick.{tail}</p>;
    case "approved":
      return <p className="outcome ok" role="status"><strong>Approved, order sent.</strong> Stop, target and exit are automatic.{tail}</p>;
    case "rejected":
      return <p className="outcome muted-box" role="status"><strong>Rejected</strong> · {reasonLabel(d?.reason_code)}{tail}</p>;
    case "refused":
      {
        // docs/ux/order-not-placed.md: the RiskGate allowed it; the MT5 box, the broker or the engine refused it. The API words it
        // (`not_placed`, one table with the Telegram card and notice); the codes are never shown
        const np = d?.not_placed;
        if (np) {
          // BACKLOG 85: `not_sent`, the approval failed before the order existed: nobody refused it
          const heading = np.not_sent ? "Could not be sent"
            : np.by === "mt5_box" ? "Refused by the MT5 box"
            : np.by === "engine" ? "Refused by the engine" : "Refused by the broker";   // engine: a switched terminal account
          return (
            <p className="outcome bad" role="status">
              {np.reason != null
                // nothing_sent false: the paper broker's duplicate, whose first send went out (trading-safety LOW)
                ? <><strong>{heading}.</strong> {capitalise(np.reason)}{np.nothing_sent ? "; nothing was sent." : "."}</>
                : <><strong>{heading}</strong> (code {np.code}); nothing was sent.</>}
              {tail}
            </p>
          );
        }
        // an older API without `not_placed`: the broker's code, never the RiskGate's words
        const broker = (d?.refusal ?? []).map((r) => /^broker_refused:(\d+)/.exec(r)).find((m) => m != null);
        if (broker) {
          return (
            <p className="outcome bad" role="status">
              <strong>Refused by the broker</strong> (code {broker[1]}); nothing was sent.{tail}
            </p>
          );
        }
        // BACKLOG 66, from an older API: the MT5 box's own ceiling (6× its equity) refused it (a prefix match)
        const bridge = (d?.refusal ?? []).find((r) => r.startsWith("bridge_notional_ceiling"));
        if (bridge) {
          return (
            <p className="outcome bad" role="status">
              <strong>Refused by the MT5 box.</strong>{" "}
              {bridge === "bridge_notional_ceiling_unreadable"
                ? "It couldn't read the account figures it checks, so it refused to be safe; nothing was sent."
                : "Its safety limit stops open gold worth more than 6× equity, and this trade would have gone over it; nothing was sent."}
              {tail}
            </p>
          );
        }
      }
      return (
        <p className="outcome bad" role="status">
          <strong>Refused at approval.</strong> The RiskGate blocked the order ({refusedWhy(d)}); nothing was sent.{tail}
        </p>
      );
    case "expired":
      return <p className="outcome muted-box" role="status"><strong>Expired.</strong> Nobody approved within {WINDOW_S} s; no order.</p>;
  }
}
