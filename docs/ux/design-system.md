# goldbot design system (v2, 2026-10-18)

Status: proposed, for architect review. Applies to every screen in `web/` and is the bar for new UI.
Goal: a calm, professional trading app the owner reads in 3 seconds on a phone. One accent, neutral greys,
semantic colour only where it means something. No new UI library: plain CSS variables + ~12 React components.

## 1. Tokens (`web/src/styles/tokens.css`, CSS custom properties)

### Colour (light / dark). Every text pair is WCAG AA (>= 4.5:1; large text >= 3:1)
| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #f6f7f9 | #0f1115 | page |
| `--surface` | #ffffff | #171a21 | cards, nav |
| `--surface-2` | #f0f2f5 | #1f232c | inset rows, table head, skeleton |
| `--border` | #e2e5ea | #2a2f3a | hairlines (1 px) |
| `--text` | #14171c | #e8eaee | primary text |
| `--text-2` | #5b6270 | #a3aab8 | labels, secondary (AA on bg and surface) |
| `--accent` | #1d5fd1 | #6ea2ff | links, active nav, focus, primary button (white text on light, #0f1115 on dark) |
| `--ok` / `--ok-bg` | #12784a / #e6f4ec | #4cc38a / #12301f | allowed, healthy, profit |
| `--warn` / `--warn-bg` | #8a5a00 / #fdf3dc | #f2c46d / #33270d | needs attention, does not stop trading |
| `--bad` / `--bad-bg` | #b42318 / #fdecea | #ff8a80 / #3a1614 | ONLY things that stop trading (halts, cap hit, refused) |
| `--long` / `--short` | #12784a / #1d4fa8 | #4cc38a / #8fb3ff | trade direction; short is blue, never red (red = stop) |

Gold (`#b8860b`) is retired as the accent: white on it is 3.0:1 (fails AA) and it reads as "warning" next to amber.
Brand gold stays only in the wordmark dot.

### Type: system stack, one scale
`font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`; numbers use
`font-variant-numeric: tabular-nums` everywhere money, prices, percent or time appear (class `.num`).
| Token | Size/line | Weight | Use |
|---|---|---|---|
| `--fs-display` | 32/40 | 650 | the one hero number per screen (today's P&L) |
| `--fs-title` | 20/28 | 650 | page title |
| `--fs-heading` | 16/24 | 600 | card title |
| `--fs-body` | 15/22 | 400 | body (16 in inputs: no iOS zoom) |
| `--fs-small` | 13/18 | 400 | labels, captions, timestamps |
No uppercase-tracked micro labels except metric-tile labels (13 px, `--text-2`, sentence case).

### Space, radius, elevation, motion
8-pt grid: `--s1 4px, --s2 8px, --s3 12px, --s4 16px, --s5 24px, --s6 32px, --s7 48px`.
Card padding 16 (phone) / 24 (desktop); gap between cards 16; page gutter 16 / 32; max content width 1120.
Radius: `--r-sm 6px` (badges, inputs), `--r-md 10px` (buttons), `--r-lg 14px` (cards). One shadow, light only:
`0 1px 2px rgb(0 0 0 / .06)`; dark uses borders only. Motion 150 ms ease; none under `prefers-reduced-motion`.
Tap targets >= 44 px; focus ring `2px solid var(--accent)` + 2 px offset on every interactive element.

## 2. Components (`web/src/ui/`)
| Component | Contract | Notes |
|---|---|---|
| `AppShell` | header (wordmark, status pill, role, Sign out) + nav + `<main>` | Phone: bottom tab bar (5 items, 56 px, icons + labels) ; desktop: left rail 220 px. Hash routes `#/home`, `#/trades`... so Telegram can deep-link |
| `StatusPill` | `tone: ok/warn/bad/idle`, text | header, always visible: "Trading normally" / "Entries paused: news until 14:30" / "Halted". Dot + word, never colour alone |
| `PageHeader` | title, optional "as of hh:mm" (UTC in title attr), actions | |
| `Card` | `title?`, `aside?`, children, `tone?` (left 3 px bar only for warn/bad) | replaces 9 ad-hoc card classes (`acct`, `pcard`, `mode-card`, `entry-row`, `hyp-card`...) |
| `MetricTile` | label, value (`.num`), unit, sub (delta/time), tone | grid of 2 (phone) / 4 (desktop) |
| `Meter` | value, max, label, tone, aria meter | one bar style for budgets, limits, milestones, research budget |
| `Badge` | tone, children | one pill style (replaces `badge`, `pbadge`, `entry-badge`, `tag`, `state`) |
| `Button` | `primary / secondary / danger / ghost`, size md (44) / lg (56) | primary = accent; Approve = `--ok` lg; Halt = danger outline |
| `Segmented` (`role=tablist`) | sub-tabs inside a page | e.g. Trades: Waiting / Recent / Results |
| `DataTable` | columns, rows, `mobile="cards"` | collapses to label/value cards < 720 px |
| `EmptyState` | icon, one sentence, optional action | "No trade waiting. You'll get a Telegram ping." |
| `ErrorState` | what failed, what still works, Retry | "Couldn't reach goldbot. Exits keep running on the server." |
| `Skeleton` | same box size as content | no layout shift |
| `TimeText` | iso -> local "14:02" with `title` "13:02 UTC" | every time on screen |
| `Money` | amount, currency, signed? | "£12.40", "−£8.20"; minus sign U+2212 |

## 3. Rules
1. One accent. Red only stops trading; amber needs attention; green is "fine"; grey is idle.
2. Every number: unit + time. Money in account currency; price in USD/oz with 2 dp; R and % labelled.
3. Plain English. No internal codes on screen (`size_down` -> "Smaller sizes", `session_open-g0-abc` -> "Session open
   strategy"; ids live in a "Details" disclosure). Our own indicator names only.
4. One primary action per screen. Owner-only controls (halt, auto mode, users, recovery) live in Settings, except
   Halt, which is reachable from the Home status card in one tap and confirms in place.
5. Fixed-height live regions (timer, status pill, tiles) so updates never shift layout.
6. Empty, loading, error states use the shared components, worded "what / what still works / what to do".
