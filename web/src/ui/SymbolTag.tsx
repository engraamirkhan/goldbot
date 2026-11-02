// An instrument's name on screen (UI spec 2026-11-02): the ticker, the owner's word for it muted beside it (hidden by
// `compact`, the phone line), and both with the unit in the tooltip. Presentation only: the words come from
// lib/instruments (the server's list once loaded, the registry's snapshot before).
import { labelOf, unitOf } from "../lib/instruments";

export function SymbolTag({ symbol, compact = false }: { symbol: string; compact?: boolean }) {
  const label = labelOf(symbol);
  const unit = unitOf(symbol);
  const title = unit ? `${label} (${symbol}), prices in ${unit}` : `${label} (${symbol})`;
  return (
    <span className="symbol-tag" title={title}>
      <span className="symbol-tag-ticker">{symbol}</span>
      {!compact && label !== symbol ? <span className="symbol-tag-name muted small"> {label}</span> : null}
    </span>
  );
}
