// Instruments on the dashboard (multi-instrument demo, UI spec 2026-11-02; GET /api/instruments). The spec's rule:
// "Every price via fmtPrice(symbol, x) with the instrument's digits + unit", and no "USD/oz" off gold. The server
// says each instrument's digits and unit; TERMS below is the registry's snapshot (config/instruments.yaml) so a price
// renders right before /api/instruments answers, offline in tests, and for a row of an account the list no longer
// names. Groups are the RiskGate's correlation groups, always in GROUP_ORDER.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { components } from "./schema";
import { NBSP } from "../ui/format";

export type Instrument = components["schemas"]["InstrumentView"];
export type InstrumentGroup = Instrument["group"];
export type InstrumentState = Instrument["state"];

export const GROUP_ORDER: readonly InstrumentGroup[] = ["gold", "metals", "equity", "fx", "energy", "rates", "ags", "crypto"];
export const GROUP_WORDS: Record<InstrumentGroup, string> = {
  gold: "Gold", metals: "Metals", equity: "Equity", fx: "FX", energy: "Energy", rates: "Rates", ags: "Ags",
  crypto: "Crypto",
};
export const GOLD = "XAUUSD";

type Terms = { digits: number; unit: string; label: string; group: InstrumentGroup };
const T = (digits: number, unit: string, label: string, group: InstrumentGroup): Terms => ({ digits, unit, label, group });
/** The registry's snapshot: digits, unit, the owner's word and group per instrument key. */
export const TERMS: Record<string, Terms> = {
  XAUUSD: T(2, "USD/oz", "Gold", "gold"), XAUEUR: T(2, "EUR/oz", "Gold in euros", "gold"),
  XAUAUD: T(2, "AUD/oz", "Gold in Australian dollars", "gold"),
  XAGUSD: T(3, "USD/oz", "Silver", "metals"), XPTUSD: T(2, "USD/oz", "Platinum", "metals"), COPPER: T(4, "USD/lb", "Copper", "metals"),
  US500: T(2, "pts", "S&P 500", "equity"), USTEC: T(2, "pts", "Nasdaq 100", "equity"), DE40: T(2, "pts", "DAX 40", "equity"),
  UK100: T(2, "pts", "FTSE 100", "equity"), JP225: T(2, "pts", "Nikkei 225", "equity"),
  EURUSD: T(5, "", "Euro / dollar", "fx"), USDJPY: T(3, "", "Dollar / yen", "fx"), GBPUSD: T(5, "", "Pound / dollar", "fx"),
  AUDUSD: T(5, "", "Aussie / dollar", "fx"), USDCAD: T(5, "", "Dollar / Canadian", "fx"), USDCHF: T(5, "", "Dollar / franc", "fx"),
  NZDUSD: T(5, "", "Kiwi / dollar", "fx"),
  XTIUSD: T(3, "USD/bbl", "WTI crude", "energy"), XBRUSD: T(3, "USD/bbl", "Brent crude", "energy"),
  XNGUSD: T(4, "USD/MMBtu", "Natural gas", "energy"),
  UST10Y: T(2, "", "US 10-year note", "rates"), BUND10Y: T(2, "", "German 10-year Bund", "rates"),
  WHEAT: T(3, "", "Wheat", "ags"), SOYBEAN: T(3, "", "Soybeans", "ags"), SUGAR: T(5, "", "Sugar", "ags"), COFFEE: T(4, "", "Coffee", "ags"),
  // owner decision 2026-11-04: the 10 added instruments
  GBPJPY: T(3, "", "Pound / yen", "fx"), EURGBP: T(5, "", "Euro / pound", "fx"), EURJPY: T(3, "", "Euro / yen", "fx"),
  AUDJPY: T(3, "", "Aussie / yen", "fx"), EURAUD: T(5, "", "Euro / Aussie", "fx"), GBPAUD: T(5, "", "Pound / Aussie", "fx"),
  BTCUSD: T(2, "USD", "Bitcoin", "crypto"), XAUJPY: T(0, "JPY/oz", "Gold in yen", "gold"),
  USINDEX: T(3, "pts", "US dollar index", "fx"), US30: T(2, "pts", "Dow Jones 30", "equity"),
};

const live = new Map<string, Terms>();
/** What the server says wins over the snapshot (useInstruments calls it with every answer). */
export function registerInstruments(list: readonly Instrument[]): void {
  for (const i of list) live.set(i.symbol, { digits: i.digits, unit: i.unit, label: i.label, group: i.group });
}

/** The terms of `symbol`: the server's, else the snapshot's; an unknown symbol gets 2 digits, no unit, its own name. */
export function termsOf(symbol: string | null | undefined): Terms {
  const s = symbol || GOLD;
  return live.get(s) ?? TERMS[s] ?? { digits: 2, unit: "", label: s, group: "gold" };
}

export const labelOf = (symbol: string | null | undefined) => termsOf(symbol).label;
export const groupOf = (symbol: string | null | undefined) => termsOf(symbol).group;

/** "4,012.40", "150.123", "1.08452": the instrument's digits; with `unit`, "4,012.40 USD/oz" (a no-break space; no
 * unit for an FX rate). A dash when there is no price. */
export function fmtPrice(symbol: string | null | undefined, x: number | null | undefined, opts: { unit?: boolean } = {}): string {
  if (x == null || !Number.isFinite(x)) return "—";
  const { digits, unit } = termsOf(symbol);
  const n = x.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return opts.unit && unit ? `${n}${NBSP}${unit}` : n;
}

/** The unit alone ("USD/oz", "pts"), "" for an FX rate: for a line that lists several prices of one instrument. */
export const unitOf = (symbol: string | null | undefined) => termsOf(symbol).unit;

export const INSTRUMENTS_POLL = 60_000;

/** GET /api/instruments, registered for fmtPrice; polled once a minute (states change, terms rarely). */
export function useInstruments() {
  return useQuery({
    queryKey: ["instruments"],
    queryFn: async () => { const list = await api.instruments(); registerInstruments(list); return list; },
    refetchInterval: INSTRUMENTS_POLL,
    staleTime: INSTRUMENTS_POLL,
  });
}

/** The instruments of one account, in group order then the server's order. */
export function byGroup(list: readonly Instrument[]): [InstrumentGroup, Instrument[]][] {
  return GROUP_ORDER.map((g) => [g, list.filter((i) => i.group === g)] as [InstrumentGroup, Instrument[]]).filter(([, xs]) => xs.length > 0);
}

/** UI spec tones: grey = closed/disabled, amber = stale/unknown terms or swap, red = a spent group cap only. */
export function stateTone(s: InstrumentState): "muted" | "warn" | "bad" | "ok" {
  if (s === "market_closed" || s === "disabled") return "muted";
  if (s === "stale" || s === "terms_missing" || s === "swap_unknown" || s === "unknown") return "warn";
  if (s === "group_cap") return "bad";
  return "ok";
}
