// Formatting shared by the design-system components (docs/ux/design-system.md).
const SYMBOL: Record<string, string> = { GBP: "£", USD: "$", EUR: "€" };

/** "£12.40", "−£8.20" (U+2212), "+£3.00" when signed. */
export function money(x: number, ccy?: string | null, signed = false): string {
  const sym = ccy ? SYMBOL[ccy] ?? `${ccy} ` : "";
  const body = `${sym}${Math.abs(x).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (x < 0) return `−${body}`;
  return signed && x > 0 ? `+${body}` : body;
}

/** No-break space: a number and its unit are one word and never wrap apart (backlog 95). */
export const NBSP = "\u00A0";

/** "22 pt", "5 %": the one place a number meets its unit. */
export function withUnit(n: number | string, unit: string): string {
  return `${n}${NBSP}${unit}`;
}

/** "+1.50 R", "−0.40 R" (U+2212); a dash when there is no R. */
export function signedR(r: number | null | undefined): string {
  if (r == null) return "—";
  return withUnit(`${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(2)}`, "R");
}

export const points = (n: number) => withUnit(n, "pt");
export const lots = (n: number) => withUnit(n, n === 1 ? "lot" : "lots");
export const seconds = (n: number) => withUnit(n, "s");
