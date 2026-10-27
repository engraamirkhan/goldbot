// Hash routes for the destinations (docs/ux/dashboard-v2.md section 2): "#/trades/now". A Telegram card or a
// bookmark can open a screen directly; an unknown hash falls back to Home. The sign-in link tokens (#signin=, #reset=,
// #invite=) are taken out of the URL before the app reads it (lib/links.ts), so they never reach this.
// Trades (docs/design/trades-ux-2026-10-27.md §3): "#/trades/list?acct=scalp&fam=box_break" keeps the List's filters in
// the URL, "#/trades/list/<trade_id>" opens one trade; the old tabs (open, activity, summary) redirect.
import { useEffect, useState } from "react";

// "chart" is off for now (owner 2026-10-25: focus on the trading system); #/chart opens Home. Screens kept for later.
export const SECTIONS = ["home", "trades", "limits", "research", "feeds", "settings"] as const;
export type Section = (typeof SECTIONS)[number];
/** `sub`: a third path part (one trade's id); `query`: the part after "?" (filters), without the "?". */
export type Route = { section: Section; tab: string | null; sub?: string | null; query?: string };

/** Old tab names that moved (trades redesign 2026-10-27): bookmarks and Telegram links keep working. */
export const TAB_ALIAS: Partial<Record<Section, Record<string, string>>> = {
  trades: { open: "now", activity: "list", summary: "today" },
};

export function parseRoute(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const q = raw.indexOf("?");
  const path = q >= 0 ? raw.slice(0, q) : raw;
  const query = q >= 0 ? raw.slice(q + 1) : "";
  const [s, t, ...rest] = path.split("/");
  const section = (SECTIONS as readonly string[]).includes(s ?? "") ? (s as Section) : "home";
  const known = section === s;
  const tab0 = known && t ? t : null;
  const tab = tab0 ? TAB_ALIAS[section]?.[tab0] ?? tab0 : null;
  const sub = known && rest.length ? decodeURIComponent(rest.join("/")) : null;
  return { section, tab, sub, query: known ? query : "" };
}

export const routeHash = (section: Section, tab?: string | null, sub?: string | null, query?: string) =>
  `#/${section}${tab ? `/${tab}` : ""}${tab && sub ? `/${encodeURIComponent(sub)}` : ""}${query ? `?${query}` : ""}`;

export type Go = (section: Section, tab?: string | null, sub?: string | null, query?: string, opts?: { replace?: boolean; keepScroll?: boolean }) => void;

export function useRoute(): [Route, Go] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    window.addEventListener("popstate", on);
    return () => { window.removeEventListener("hashchange", on); window.removeEventListener("popstate", on); };
  }, []);
  const go: Go = (section, tab, sub, query, opts) => {
    const h = routeHash(section, tab, sub, query);
    if (window.location.hash !== h) {
      if (opts?.replace) history.replaceState(null, "", h); else history.pushState(null, "", h);
    }
    setRoute({ section, tab: tab ?? null, sub: sub ?? null, query: query ?? "" });
    if (!opts?.keepScroll) window.scrollTo?.(0, 0);
  };
  return [route, go];
}
