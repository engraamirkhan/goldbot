// Hash routes for the 7 destinations (docs/ux/dashboard-v2.md section 2): "#/trades/waiting". A Telegram card or a
// bookmark can open a screen directly; an unknown hash falls back to Home. The sign-in link tokens (#signin=, #reset=,
// #invite=) are taken out of the URL before the app reads it (lib/links.ts), so they never reach this.
import { useEffect, useState } from "react";

// "chart" is off for now (owner 2026-10-25: focus on the trading system); #/chart opens Home. Screens kept for later.
export const SECTIONS = ["home", "trades", "limits", "research", "feeds", "settings"] as const;
export type Section = (typeof SECTIONS)[number];
export type Route = { section: Section; tab: string | null };

export function parseRoute(hash: string): Route {
  const [s, t] = hash.replace(/^#\/?/, "").split("/");
  const section = (SECTIONS as readonly string[]).includes(s ?? "") ? (s as Section) : "home";
  return { section, tab: section === s && t ? t : null };
}

export const routeHash = (section: Section, tab?: string | null) => `#/${section}${tab ? `/${tab}` : ""}`;

export function useRoute(): [Route, (section: Section, tab?: string | null) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (section: Section, tab?: string | null) => {
    const h = routeHash(section, tab);
    if (window.location.hash !== h) history.pushState(null, "", h);
    setRoute({ section, tab: tab ?? null });
    window.scrollTo?.(0, 0);
  };
  return [route, go];
}
