// Invite, reset and emailed sign-in links carry their one-time token in the URL fragment (#invite=… / #reset=… /
// #signin=…): a browser never sends the fragment to a server, so it stays out of access logs, proxies and Referer
// headers (the API also sends Referrer-Policy: no-referrer). The token is read once and removed from the address bar
// and the history entry. Sign-in links are built by the server (goldbot/api/mailer.py) and only read here.

export type LinkTokens = { invite: string; reset: string; signin: string };

let taken: LinkTokens | null = null;

export function linkUrl(kind: "invite" | "reset", token: string, origin = location.origin): string {
  return `${origin}/#${kind}=${encodeURIComponent(token)}`;
}

/** The tokens in the current URL's fragment, read on the first call and cleared from the URL; later calls return the same values. */
export function takeLinkTokens(): LinkTokens {
  if (taken === null) {
    const h = new URLSearchParams(location.hash.replace(/^#/, ""));
    taken = { invite: h.get("invite") ?? "", reset: h.get("reset") ?? "", signin: h.get("signin") ?? "" };
    if (taken.invite || taken.reset || taken.signin) history.replaceState(null, "", location.pathname + location.search);
  }
  return taken;
}

/** Forget the tokens read from the URL. They are one-time links: once a session starts or ends they are spent, and a
 * Login that mounts again in the same page (after sign-out) must not offer them a second time (issue #89). */
export function clearLinkTokens(): void {
  taken = null;                               // the next read looks at the URL again, whose fragment was already removed
}
