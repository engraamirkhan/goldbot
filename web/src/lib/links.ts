// Invite and reset links carry their one-time token in the URL fragment (#invite=… / #reset=…): a browser never
// sends the fragment to a server, so it stays out of access logs, proxies and Referer headers (the API also sends
// Referrer-Policy: no-referrer). The token is read once and removed from the address bar and the history entry.

export type LinkTokens = { invite: string; reset: string };

let taken: LinkTokens | null = null;

export function linkUrl(kind: "invite" | "reset", token: string, origin = location.origin): string {
  return `${origin}/#${kind}=${encodeURIComponent(token)}`;
}

/** The tokens in the current URL's fragment, read on the first call and cleared from the URL; later calls return the same values. */
export function takeLinkTokens(): LinkTokens {
  if (taken === null) {
    const h = new URLSearchParams(location.hash.replace(/^#/, ""));
    taken = { invite: h.get("invite") ?? "", reset: h.get("reset") ?? "" };
    if (taken.invite || taken.reset) history.replaceState(null, "", location.pathname + location.search);
  }
  return taken;
}
