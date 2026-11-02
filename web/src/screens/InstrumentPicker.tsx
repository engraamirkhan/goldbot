// The key-level chart's instrument choice (multi-instrument UI spec 2026-11-02, section 5): a native <select> (a phone
// opens its own wheel; screen readers know it), options grouped account -> group in the fixed group order. The value
// is an instrument key; the URL keeps it (?sym=). Gold alone when no account lists more.
import { byGroup, GOLD, GROUP_WORDS, labelOf, useInstruments } from "../lib/instruments";

export function InstrumentPicker({ value, onChange }: { value: string; onChange: (symbol: string) => void }) {
  const q = useInstruments();
  const list = q.data ?? [];
  const accounts = [...new Set(list.map((i) => i.account))];
  const known = list.some((i) => i.symbol === value);
  return (
    <label className="small">Instrument{" "}
      <select aria-label="Instrument" value={value} onChange={(e) => onChange(e.target.value)}>
        {!known && <option value={value}>{value === GOLD ? "XAUUSD · Gold" : `${value} · ${labelOf(value)}`}</option>}
        {accounts.map((acct) => byGroup(list.filter((i) => i.account === acct)).map(([g, xs]) => (
          <optgroup key={`${acct}-${g}`} label={`${acct} · ${GROUP_WORDS[g]}`}>
            {xs.map((i) => <option key={`${acct}-${i.symbol}`} value={i.symbol}>{i.symbol} · {i.label}</option>)}
          </optgroup>
        )))}
      </select>
    </label>
  );
}
