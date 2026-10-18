// Formatting shared by the design-system components (docs/ux/design-system.md).
const SYMBOL: Record<string, string> = { GBP: "£", USD: "$", EUR: "€" };

/** "£12.40", "−£8.20" (U+2212), "+£3.00" when signed. */
export function money(x: number, ccy?: string | null, signed = false): string {
  const sym = ccy ? SYMBOL[ccy] ?? `${ccy} ` : "";
  const body = `${sym}${Math.abs(x).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (x < 0) return `−${body}`;
  return signed && x > 0 ? `+${body}` : body;
}
