// Home's top movers per account (multi-instrument UI spec 2026-11-02, section 1): three fixed-height slots, each an
// instrument with its day P&L net in the account currency, signed and with ▲/▼ (never colour alone), re-ranked at
// most every 60 s (lib/movers); then "N of M instruments traded · all →" to the Today tab for this account. Money is
// never summed across accounts: each account has its own line.
import { useState } from "react";
import { type MoverRank, MOVER_SLOTS, rankMovers, showsMovers, type SymbolDay } from "../lib/movers";
import { routeHash } from "../lib/route";
import { useNow } from "../lib/useNow";
import { money } from "../ui/format";
import { SymbolTag } from "../ui/SymbolTag";

export function TopMovers({ account, rows, instrumentsN, currency, now }: {
  account: string; rows: readonly SymbolDay[]; instrumentsN: number | null | undefined; currency: string | null; now?: number;
}) {
  const tick = useNow(5_000);
  const [rank, setRank] = useState<MoverRank | null>(null);
  const next = rankMovers(rows, now ?? tick, rank);
  if (next !== rank) setRank(next);            // the derived-state pattern: kept between renders, re-ranked by rule
  if (!showsMovers(rows, instrumentsN)) return null;
  const by = new Map(rows.map((r) => [r.symbol, r]));
  const slots = Array.from({ length: MOVER_SLOTS }, (_, i) => by.get(next.order[i] ?? ""));
  const traded = rows.filter((r) => r.closed_n + r.open_n > 0).length;
  return (
    <div className="movers" role="group" aria-label={`Top movers ${account}`}>
      <ol className="movers-slots">
        {slots.map((r, i) => <li key={i} className="mover-slot">{r ? <Mover r={r} currency={currency} /> : <span className="muted small">—</span>}</li>)}
      </ol>
      <a className="small home-link" href={routeHash("trades", "today", null, `acct=${encodeURIComponent(account)}`)}>
        {traded} of {instrumentsN ?? rows.length} instruments traded · all →
      </a>
    </div>
  );
}

function Mover({ r, currency }: { r: SymbolDay; currency: string | null }) {
  const v = r.day_pnl;
  const arrow = v == null || v === 0 ? "" : v > 0 ? "▲" : "▼";
  return (
    <span className="mover num">
      <SymbolTag symbol={r.symbol} compact /> {arrow && <span aria-hidden="true">{arrow}</span>}{" "}
      <span className={v == null ? "muted" : ""}>{v == null ? "not known" : money(v, currency, true)}</span>
    </span>
  );
}
