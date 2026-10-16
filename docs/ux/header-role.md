# Header: the signed-in role, never the email (BACKLOG 64)

One line: **the header says who is signed in by role ("Owner", "Approver", "Viewer"), never by address; the email is
shown only on the Users tab (owner) and on the signed-in user's own Account tab.**

The owner shares phone screenshots of the dashboard (to the program, to a broker, in chat). Today every one of them
carries the owner's sign-in email in the header, the one string that identifies the account holder and is also the
first half of a sign-in. The reader of a screenshot needs to know *what this person may do* (approve, or only look),
not *who they are*. The role answers that; the email adds nothing and leaks an identity, against the same rule the
API already follows for `halted_by`, `mode_by` and `decided_channel` (channel, never who: A15, BACKLOG 52).

## Data

| Shown | Source |
|---|---|
| Role word in the header | `currentUser().role` (`Me.role`: `owner` \| `approver` \| `viewer`), set at sign-in from `LoginResponse.role` |
| Email on the Account tab | `currentUser().email` (`Me.email`), already in the session record; nothing new is fetched |
| Email on the Users tab | `GET /api/users` (`UserRow.email`), owner only, unchanged |

No API change. `LoginResponse.email` and `Me.email` stay: the Account tab needs them, and the session record in
`sessionStorage` (`goldbot_me`) is not on screen. The header never reads `email`.

## Copy

| Role (`Me.role`) | Header text (visible) | Accessible name |
|---|---|---|
| `owner` | **Owner** | "Signed in as Owner" |
| `approver` | **Approver** | "Signed in as Approver" |
| `viewer` | **Viewer** | "Signed in as Viewer" |
| no session record (token present, `goldbot_me` missing) | **Signed in** | "Signed in" |

- The prefix "Signed in as " is in an `.sr-only` span inside the label, so screen readers say the full phrase and
  the phone shows one short word. The role words come from one map (`web/src/lib/roles.ts`, `roleLabel(role)`),
  reused by the Account tab and the Users tab's role column, so the same role is spelled the same way everywhere.
- Capitalised word, no dot separator, no avatar, no initials (initials are part of the email).
- No `title`, `aria-label`, `data-*` attribute or `document.title` carries the email either: hover text and the
  accessibility tree are part of what gets shared (screen readers, browser "share page", tab titles).

## Layout

Phone (360 px; the header already wraps below 720 px):

```
┌────────────────────────────────────────────┐
│ goldbot                     Owner  Sign out │  row 1: title left; role + Sign out pushed right
│ [Overview][Approvals][News][Agents][He…  →  │  row 2: tabs, full width, scroll sideways (unchanged)
├────────────────────────────────────────────┤
│ safety strip / screen content …            │
```

- The role is 13 px `--muted` text, as today's `.who`; it needs no colour and carries no state, so it is never red
  or green (red is only for things that stop trading).
- Width: the longest label, "Approver", is ~60 px at 13 px; "goldbot" + "Approver" + "Sign out" fit row 1 at 360 px
  with 16 px gutters (today's email often forced row 1 to wrap or squeeze). Nothing truncates; no ellipsis.
- "Sign out" gets `min-height: 44px` and 8 px horizontal padding below 720 px (it is on this line and is a tap
  target; today it is text-height).
- No layout shift: the label is fixed for the session (the role is set at sign-in), and a missing record renders
  "Signed in" in the same slot rather than collapsing it.

Desktop: unchanged layout, one row: `goldbot  [tabs …]            Owner  Sign out`.

Light and dark: `--muted` on `--bg` is 5.0:1 (light, #6b6b6b on #f7f7f5) and 6.0:1 (dark, #9a9a9a on #121212), so
WCAG AA holds for 13 px text. Focus order stays: tabs, then Sign out (the label is not focusable).

## Account tab: "Your sign-in" (new, first section)

The only place a non-owner can see the address they signed in with. Reached deliberately, so it is not in every
screenshot.

```
Your sign-in
Role     Owner
Email    owner@example.com
```

A `<dl>` with two rows, above "Change password"; email in `overflow-wrap: anywhere` so a long address wraps inside
360 px. No session record: "Email  not known in this tab: sign out and in again to see it" (muted).

## Where the email may still appear, and why

| Place | Why it stays |
|---|---|
| Users tab table, invite field, role select's `aria-label` ("Role of …"), confirmations | The owner manages users by address; the tab is owner-only and visited on purpose |
| Account tab, "Your sign-in" | The user's own address, so they can confirm which account they are in (e.g. the owner checking a viewer test login) |
| Sign-in screens (`Login.tsx`): Email field, invite "accept" pre-fill, forgot / email-link forms | Signed out; it is the user's own input, not chrome |
| Authenticator enrolment link (`otpauth://…` `href`) | The authenticator app labels the entry with it; shown once at set-up, never as text |
| API error text on Users actions ("no such user: …") | The owner's own action on the owner's own screen |

It never appears in persistent chrome: the header, the tab bar, the safety strip, any footer or side bar added later,
the page title, toasts or banners shown on other tabs. This is a rule for all future screens (recorded below).

## States

| State | Header |
|---|---|
| Loading (signed in, screen data loading) | Role shown at once: it comes from the session, not a request |
| Signed in, record present | "Owner" / "Approver" / "Viewer" |
| Record missing (session storage cleared in another tab) | "Signed in"; Account tab says the email is not known in this tab |
| Error (API unreachable) | Unchanged: the role is local; the screens show their own errors |
| Signed out | No header (sign-in screen) |

## Web components that show the email today

| File | What | Change |
|---|---|---|
| `web/src/App.tsx:46` | Header `<span className="muted who">{currentUser()?.email} · {currentUser()?.role}</span>` | Replace with `roleLabel` + `.sr-only` prefix; never read `email` |
| `web/src/screens/Users.tsx:34,46,50,53` | Invite field, "Email" column, row cells, `aria-label="Role of {email}"` | Keep (owner manages users); role column uses `roleLabel` |
| `web/src/screens/Login.tsx:76,79,144` | Email input, pre-filled on invite accept | Keep (signed out, own input) |
| `web/src/screens/Login.tsx:118` | `otpauth` URI in the "Open in authenticator app" `href` | Keep (enrolment, once) |
| `web/src/screens/Account.tsx` | Shows no email today | Add "Your sign-in" section |
| `web/src/lib/api.ts` | `Me` in `sessionStorage`, request bodies | Keep (not rendered) |
| `web/e2e/dashboard.spec.ts:52,213`, `web/e2e/auth.spec.ts:107,298` | Assert `` `${email} · owner` `` / `· viewer` in the header | Update to the role assertions below |

Nothing else under `web/src` renders an email (grep `email` on 16 Oct 2026: SafetyStrip, Overview, Approvals,
AutoMode, EntryStatus and the other screens have none; `halted_by`, `mode_by` and decisions carry the channel only).

## Acceptance tests (the implementer writes these first)

Unit (vitest):
1. `web/src/App.test.tsx` (new): with a token and `setUser({ email: "owner@example.com", role: "owner" })`, render
   `App`; `getByRole("banner")` has text containing "Owner" and "Signed in as Owner" (accessible text), and its
   `textContent` and `innerHTML` contain none of `owner@example.com`, `example.com`, `@`. Check `innerHTML` so
   attributes (`title`, `aria-label`) are covered too. (The local part "owner" equals the role word, so the
   "any part" check is the domain and the "@", not the local part.)
2. Same for `approver` ("Approver") and `viewer` ("Viewer") with `v@x.io`: neither `v@x.io` nor `x.io` nor `@` in the
   banner.
3. Token present, no session record: banner shows "Signed in", renders without throwing.
4. `document.title` contains no `@` after render.
5. `Account.test.tsx`: "Your sign-in" lists "Owner" and the email for the owner; for a viewer, "Viewer" and their own
   email; with no record, the "not known in this tab" line.
6. `lib/roles.test.ts`: `roleLabel` maps the three roles exactly as in Copy.

e2e (Playwright):
7. `dashboard.spec.ts`, owner signs in on Overview: `page.getByRole("banner")` contains "Owner", and
   `not.toContainText(OWNER.email)`, `not.toContainText("example.com")`, `not.toContainText("@")`; the safety strip
   (its existing locator in `dashboard.spec.ts`) contains no "@" either.
8. Same file, viewer sign-in (line 213): banner says "Viewer", no "@".
9. Account tab (owner): "Your sign-in" shows `OWNER.email`.
10. `auth.spec.ts` lines 107 and 298: replace the email assertion with the banner role check; at 360 px also assert
    the header's row 1 does not wrap the role under the title (`Owner` and `goldbot` share a `boundingBox().y`) and
    `scrollWidth <= 360` (already there).
11. Tap target: at 360 px, "Sign out" `boundingBox().height >= 44`.

Gates: `npm run lint && npm run typecheck && npm test && npm run build`, then `npm run test:e2e`. No API change, so no
contract regeneration.

## Rule for every future screen

Persistent chrome (header, tab bar, safety strip, any footer or side bar) never renders an identity: no email, login,
Telegram id, MT5 login, host. It may show a role, a channel ("dashboard", "telegram") or an account's config name.
Identities appear only on the screen whose job is to manage them. Reviewers check this with a grep of the chrome
components for `email`.

Review: the ui-ux-designer reviews the implementation against this spec (screenshots at 360 px light and dark,
desktop) and records the verdict at the merge with the code-reviewer's (E1).
