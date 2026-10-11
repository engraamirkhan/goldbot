// Money and loss-budget helpers for the live account view (ADR 0004). Amounts are in the broker account's currency,
// which comes from the engine (AccountSummary.currency); nothing here assumes pounds or dollars.

/** An amount in the account currency: "£1,000.00"; a plain number (plus the label) when the currency is unknown. */
export function cash(x: number, currency: string | null | undefined): string {
  if (currency && /^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat("en-GB", { style: "currency", currency, currencyDisplay: "narrowSymbol" }).format(x);
    } catch { /* an unknown ISO code: fall through to the plain form */ }
  }
  const n = x.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${n} ${currency}` : n;
}

/** A change: "+£12.00", "−£10.00" (true minus), "£0.00". */
export function signedCash(x: number, currency: string | null | undefined): string {
  const body = cash(Math.abs(x), currency);
  return x > 0 ? `+${body}` : x < 0 ? `−${body}` : body;
}

export type Tone = "ok" | "warn" | "bad" | "idle";
type Budget = { limit: number; used: number; remaining: number };

/** Red only when the budget is spent (the risk check stops new entries); amber with under a quarter left. */
export function budgetTone(b: Budget | null | undefined): Tone {
  if (!b) return "idle";
  if (b.remaining <= 0) return "bad";
  return b.limit > 0 && b.remaining / b.limit < 0.25 ? "warn" : "ok";
}
