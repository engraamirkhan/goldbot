// Top movers on Home (multi-instrument UI spec 2026-11-02, section 1): per account, the instruments with the largest
// |day P&L| net in the account currency, "re-rank at most every 60 s in 3 fixed-height slots". Between re-ranks the
// slots keep their instruments (values refresh in place), so a 5 s poll never shuffles the line under the owner's
// eye; an instrument that left the list is replaced at once, never shown with a stale figure.
import type { components } from "./schema";

export type SymbolDay = components["schemas"]["SymbolDay"];
export const MOVER_SLOTS = 3;
export const RERANK_MS = 60_000;

export type MoverRank = { at: number; order: string[] };

const mag = (d: SymbolDay) => (d.day_pnl == null ? -1 : Math.abs(d.day_pnl));

/** The ranking to show at `now`: the previous one while it is younger than RERANK_MS and every instrument in it is
 *  still listed, else a fresh one by |day P&L| (unknown last), ties by symbol. */
export function rankMovers(rows: readonly SymbolDay[], now: number, prev?: MoverRank | null): MoverRank {
  const listed = new Set(rows.map((r) => r.symbol));
  if (prev && now - prev.at < RERANK_MS && prev.order.every((s) => listed.has(s))
      && prev.order.length === Math.min(MOVER_SLOTS, rows.length)) {
    return prev;
  }
  const order = [...rows].sort((a, b) => mag(b) - mag(a) || a.symbol.localeCompare(b.symbol)).slice(0, MOVER_SLOTS).map((r) => r.symbol);
  return { at: now, order };
}

/** A single-instrument account shows no movers (spec): at most one instrument listed and no list of more. */
export const showsMovers = (rows: readonly SymbolDay[], instrumentsN: number | null | undefined) =>
  rows.length > 1 || (instrumentsN ?? 0) > 1;
