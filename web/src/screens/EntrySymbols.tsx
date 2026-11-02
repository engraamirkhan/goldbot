// Per-symbol entry state on a multi-instrument account (UI spec 2026-11-02, section 3): "15 of 19 can enter", then one
// line per reason naming its symbols (a closed market with its reopen time, a stale feed in amber, a spent group cap
// in red, "terms not loaded: entries refused, exits still close" in amber), and "Show all 19" for the full list. The
// tone is a word and a class, never colour alone. The account-wide headline above still decides the account.
import { useState } from "react";
import type { components } from "../lib/schema";
import { stateTone } from "../lib/instruments";
import { whenText } from "../lib/entry";

type EntrySymbol = components["schemas"]["EntrySymbol"];
type State = EntrySymbol["state"];

const REASON_WORDS: Record<Exclude<State, "can_enter">, string> = {
  market_closed: "Market closed", stale: "Price feed stale", group_cap: "Group cap spent",
  terms_missing: "Terms not loaded: entries refused, exits still close", disabled: "Switched off",
};
const REASON_ORDER: Exclude<State, "can_enter">[] = ["group_cap", "terms_missing", "stale", "market_closed", "disabled"];

export function EntrySymbols({ symbols, now }: { symbols: readonly EntrySymbol[]; now: number }) {
  const [all, setAll] = useState(false);
  if (symbols.length === 0) return null;
  const can = symbols.filter((s) => s.state === "can_enter");
  return (
    <div className="entry-symbols">
      <p className="entry-count"><strong>{can.length} of {symbols.length}</strong> can enter</p>
      <ul className="entry-reasons">
        {REASON_ORDER.map((r) => {
          const xs = symbols.filter((s) => s.state === r);
          if (xs.length === 0) return null;
          const reopen = r === "market_closed" ? xs.map((s) => s.clears_at).filter((x): x is string => !!x).sort()[0] : undefined;
          const when = reopen ? whenText(reopen, "opens", now) : null;
          return (
            <li key={r} className={`entry-reason ${stateTone(r)}`}>
              {REASON_WORDS[r]}: {xs.map((s) => s.symbol).join(", ")}
              {when && <> · <time dateTime={reopen} title={when.utc}>{when.text}</time></>}
            </li>
          );
        })}
      </ul>
      <button type="button" className="btn ghost" aria-expanded={all} onClick={() => setAll(!all)}>
        {all ? "Show less" : `Show all ${symbols.length}`}
      </button>
      {all && (
        <ul className="entry-all small">
          {symbols.map((s) => <li key={s.symbol} className={stateTone(s.state)}><strong>{s.symbol}</strong> {s.text}</li>)}
        </ul>
      )}
    </div>
  );
}
