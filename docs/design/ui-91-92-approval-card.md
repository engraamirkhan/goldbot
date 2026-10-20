# Spec 103 + 92 (backlog item 103, formerly 91): approval card says demo vs real money and why it asks; Settings entry mode per account (ADR 0036)

Author: ui-ux-designer staff; signed off with the corrections below by the Principal Product Designer, 2026-10-19. Status: ready for implementer, except section 6's demo-back-to-auto control (open, trading-safety ruling). Today's shots: ~/tmp/ui-shots/{phone,desktop}-{light,dark}/ (04/03-home-default = card, 22/21-settings-entry-mode-and-halt).
Code read: web/src/screens/ProposalCard.tsx (:46 bar scales by WINDOW_S, :217 "within {WINDOW_S} s"), web/src/lib/approval.ts:8, web/src/screens/AutoMode.tsx (:92 `owner_mode ?? "propose"`), goldbot/api/schema.py (Proposal :71, AccountSummary.mode :49, AutoModeView :871, ForcedPropose :863), goldbot/api/explain.py:539, goldbot/engine/auto_entry.py, goldbot/telegram/approvals.py:172,270.

## Principal sign-off (2026-10-19): corrections applied in this file
1. State 3 said "within 90 s": that broke principle 4. All expired copy uses `windowWords(window_s)`.
2. `terminal_not_demo` (an account configured demo, connected to a terminal that reports real money): an approval would
   place a real-money order. Approving it with demo habits is the worst mistake this card can invite. Ruling (the
   conservative option, pending trading-safety confirmation): the badge reads `REAL MONEY (terminal)`, the card uses the
   real-money rule, and Approve is disabled. In its place: "Not allowed here: this account is set up as demo but its
   terminal is a real-money account. Fix the account setup first." Reject stays.
3. Flag 2 (the bottom nav covering Approve) is a full-page screenshot artefact: the fixed nav is drawn at the first
   viewport. It is not a bug. The real above-the-fold problem is backlog 101 (Trades), checked with the `fold-*.png` shots
   and an e2e bounding-box assertion. The elementFromPoint assertion stays, as a cheap regression guard.
4. The Settings rows' "ask (90 s)" is `asks you ({windowWords})`, from the account's window. No literal 90.
5. Badge copy is cleaned up (section 2): an inline SVG shield (aria-hidden) plus text. No emoji on the dashboard.
6. Unknown account type (state 11): solid rule, amber `ACCOUNT TYPE UNKNOWN` badge, Approve label "Approve entry
   (account type unknown)".
7. Section 6's switch back to automatic stays OPEN until trading-safety rules on it. Build the per-account rows
   read-only first; the control comes later.

## 0. Principles (decisions made here)
1. The first thing the owner reads on a card is the money class. Real money = loud, labelled, with a hard visual edge; demo = quiet, labelled. Never colour alone: the badge is TEXT plus a glyph, and the card carries a 4 px left rule whose PATTERN differs (solid = real money, dashed = demo).
2. A demo card is an exception (ADR 0036: demo enters automatically). So it must say why it is asking, in one line, in words, and the card never offers the habit "just tap". It still has Approve/Reject (the engine really is waiting).
3. Real money NEVER shows an "asking because" line (asking is the rule, not an exception). Showing a reason there would imply it could be otherwise. API: ask_reason null on live.
4. All timing comes from the proposal's own window_s. No literal 90 anywhere in UI copy (grep gate: `rg -n "\b90 ?s" web/src --glob '!*.test.*'` returns nothing outside comments).
5. Layout: card height must not change between pending and expiring, and a badge or reason line must not appear later (account_mode, ask_reason arrive with the proposal). Reserve the reason line row on demo cards only; real-money cards have no such row (static per card, so no shift).
6. Unknown is a state, not a default: if account_mode is missing (older engine) show badge "ACCOUNT TYPE UNKNOWN" (amber, text) and treat the card as real money for caution: Approve button label unchanged, but the header badge is amber. Never default to DEMO.

## 1. API fields (Item 103)
| Field | Where | Status | Notes |
|---|---|---|---|
| Proposal.account_mode `demo\|live\|paper` | api Proposal + engine Proposal record + Telegram | NEW | engine knows `cfg.mode`; fall back (api only) to AccountSummary.mode join by account_id (existing) if absent |
| Proposal.window_s int | api Proposal | NEW in API, EXISTS in engine record (approvals.py:190, runner.py:195) | expose it; UI default only when null from older API: treat as unknown -> bar uses (expires_at - created) as the window (both exist today), so no 90 fallback is needed. Prefer: window = window_s ?? (expires_at - created) |
| Proposal.ask_reason `{kind, text} \| null` | api Proposal + engine record | NEW | null on live and paper-by-design; on demo the kind plus plain text, see section 2 |
| Proposal.created, expires_at | exist | existing | |
| DecidedProposal.status, decided_channel, reason_code, refusal(_text), not_placed, not_confirmed | exist | existing | outcome copy unchanged except Expired |
| AccountSummary.mode, account_class, stage | exist | existing | |
ask_reason.kind values: `rearm_lock | kill_switch | risk_unreadable` (exist in ForcedPropose), plus TWO that ForcedPropose does not model today and the brief requires: `owner_propose` (owner set /mode propose, control.json approval_mode=propose, runner.py:1964) and `terminal_not_demo` (demo account but the terminal reports real/contest, auto_entry.refusal, tests/test_demo_auto_entries.py), and fallback `config_propose` (accounts.yaml approval_mode: propose on a demo account). FLAG: extend ForcedPropose.kind (or a new AskKind) with these; the Settings panel needs the same set (section 5).

## 2. Exact copy
Badge (header, first element, before direction):
- live: `REAL MONEY` with an inline SVG shield (aria-hidden); the text is the signal.
- demo: `DEMO` + sub-text `no real money`
- paper: `PAPER` + `simulated`
- unknown: `ACCOUNT TYPE UNKNOWN`
aria-label of the card: "{Real money|Demo|Paper} account {id}: Long 0.05 lots XAUUSD, waiting for your decision".

"Asking because" line (demo only), one sentence, plain words, with the date where one exists (local time, UTC on hover):
- rearm_lock: `Asking because: this account was just re-armed; it asks for every entry until {Tue 17 Nov 14:05} (30 days).`
- kill_switch: `Asking because: the 12% drawdown stop fired; entries ask you until the account is re-armed.`
- owner_propose: `Asking because: you set entry mode to Propose in Settings. Demo normally enters on its own.`
- terminal_not_demo: `Asking because: this demo account is connected to a real-money terminal, so it is not allowed to enter on its own. Check the terminal before approving.` (amber warn note, see state 6: this is a safety fault, escalates tone)
- risk_unreadable: `Asking because: the risk file could not be read, so automatic entry is off until it is fixed.`
- config_propose: `Asking because: this account is set to ask in its configuration.`
Link on the reason: `Entry mode ›` to Settings > Entry mode.

Footer (all states): `{account_id} · {Real money|Demo} · window {window_s} s · expires 21:31:39` (time has UTC title).
Timer aria-label: `{n} seconds left to decide` (exists).
Approve button copy: real money `Approve real-money entry`; demo `Approve demo entry`. (Label carries the class, so a thumb on the bottom nav-adjacent button still reads it.) Reject unchanged: `Reject…` then reason codes (existing).
Expired (all classes): `Expired. Nobody approved within {window_s} s; no order.` ; with window 3600 -> `within 60 min` (format: <=120 s in s, else minutes; helper `windowWords(window_s)`, also used by Telegram text).
Real-money extra line under header badge on pending/expiring only: none. (Decision: no extra "are you sure": one tap, 90 s, is the owner's contract; friction would cost fills. Badge + button label are the guard. Would change my mind: any evidence of a mis-approval.)

## 3. Layout sketch, phone 360 px (pending, real money)
```
+--------------------------------------+
|#  [ REAL MONEY ]            1:12      |  badge left, timer right (tabular nums)
|#  ▲ LONG  0.05 lots XAUUSD            |
|#  [=========-----------------]        |  bar = left / window_s
|#  Risk £8.20 (0.82% of equity) if the stop is hit, before costs
|#  Loss budget left today £41.80 ...
|#  Entry 2400.25 | Stop 2396.25 -4.00 -£8.20 | Target 2406.25 +6.00 +£12.30
|#  Win p 0.62 | Expected +0.31 R | Spread 22 pt
|#  Why: Volatility (ATR 14) 0.40 ...
|#  [ Approve real-money entry ]  [Reject…]   >=44 px
|#  icm-live · Real money · window 90 s · expires 21:31:39
+--------------------------------------+
```
# = solid 4 px rule (real money). Demo variant: dashed rule, badge `DEMO · no real money`, and directly under the header row:
```
|:  [ DEMO · no real money ]     1:12     |
|:  Asking because: you set entry mode to Propose in Settings.  Entry mode ›
|:  ▲ LONG 0.05 lots XAUUSD ...
```
The reason line sits between header and direction row (above the fold, before the risk number), wrap allowed (max 3 lines at 360 px; longest copy is terminal_not_demo ~150 chars: acceptable, it is meant to be read).
Desktop 1440: card max-width 560 px in the Home column (as today); badge and timer on one row; reason line one row; footer one row. No new columns.
Sticky decide bar: today the bottom nav overlaps the Approve button in the phone shot (04-home-default: nav covers the card's lower part, footer cut). Flag: card needs `scroll-margin-bottom` = nav height + 16 px, and Approve must not sit under the nav (separate defect, also in 103's e2e at 390 px: assert Approve is not covered via elementFromPoint).

## 4. Card states (each at 360 and 1440; light and dark use tokens only, contrast AA >= 4.5:1 text, 3:1 rule/bar)
| # | State | Class | Differences from base card |
|---|---|---|---|
| 1 | Real-money pending | live | solid rule, REAL MONEY badge (filled accent bg, white text, check AA), bar accent, Approve filled green, label "Approve real-money entry" |
| 2 | Real-money expiring (left <= 15 s, EXPIRING_S) | live | timer turns bold + amber bg + text `0:09 left` (not colour alone: the word "left" and bold weight); bar amber; sr-only aria-live assertive "15 seconds left to decide" once. 15 s stays absolute but cap at 25% of window: `min(15, 0.25*window_s)` so a 20 s window is not "expiring" from the start |
| 3 | Real-money expired | live | buttons removed, replaced by `Expired. Nobody approved within {windowWords(window_s)}; no order.` (muted box, role=status); badge stays; footer unchanged; card remains 1 more refresh then moves to Recent |
| 4 | Demo pending/expiring (ask_reason present) | demo | dashed rule, DEMO badge outline style (not filled: quieter than real), reason line, "Approve demo entry". Expiring as state 2 |
| 5 | Demo expired | demo | `Expired. Nobody approved within {window}; no order.` + second line `Demo accounts normally enter on their own; the next signal will be handled as set in Settings.` (only when ask_reason.kind is owner_propose; otherwise omit) |
| 6 | Configured demo, terminal real (terminal_not_demo) | fault | treated as real money (sign-off 2): badge `REAL MONEY (terminal)`, solid rule, Approve disabled with the reason in words, Reject enabled; red (it stops the entry) |
| 7 | Approved (submitted/approved) | both | existing outcome copy: `Approved, order sent. Stop, target and exit are automatic. · via Telegram`; badge kept; no reason line (not actionable any more) |
| 8 | Rejected | both | `Rejected · {Reason label} · via {channel}`; badge kept |
| 9 | Refused / not placed / not confirmed | both | existing copy; badge kept (tells the owner whether real money was NOT spent) |
| 10 | Loading / empty / error | n/a | unchanged (not touched by this item); account badge absent until the proposal exists |
| 11 | Account type unknown (older engine) | ? | amber `ACCOUNT TYPE UNKNOWN`, solid rule (cautious), no reason line, Approve "Approve entry (account type unknown)" |
| 12 | Viewer role | any | unchanged `Viewer role: you can watch, not approve.` plus badge |

Colour tokens (use design-system tokens; do not hardcode): real money badge = `--accent-strong` bg / `--on-accent` text; demo badge = transparent bg, 1.5 px `--text-muted` border, `--text` text; amber = `--warn`. Light and dark both: verify each pair with the contrast check in docs/ux/design-system.md.

## 5. Telegram card (same essentials)
Add as first line, before direction:
```
🔒 REAL MONEY · icm-live            (live)
DEMO · no real money · icm-demo      (demo)
```
(emoji only allowed in the Telegram channel where the existing card already uses them; the words are the signal.)
Demo adds line 2: `Asking because: you set entry mode to Propose.` (same copy table as section 2, shortened without links).
Last line: `⏱ approve within {window_words} or it expires · exits are automatic` (already uses window_s; keep, use windowWords).
Buttons: `Approve` stays; real money labelled `Approve real money`, demo `Approve demo` (callback ids unchanged).

## 6. Item 92: Settings > Entry mode and halt (redesign)
Problem today (shot 22): header says "Entry mode: Propose-and-approve (engine setting)" and "auto mode offered", with an Enable form, while no engine reports. Wrong on both sides: demo enters automatically; real money never can.

API (existing vs new):
- Per-account rows: engine_modes {account: effective mode} EXISTS; account type from `AccountSummary.mode` (existing, via /accounts join). Better NEW: `AutoModeView.accounts: [{account_id, account_mode, effective_mode, configured_mode, ask_reason|null, reporting: bool}]` so the page needs no join and the reason is the SAME source as the card (ask_reason kinds, section 1). Until then join client-side.
- forced_propose EXISTS (extend kinds as flagged).
- can_enable / eligible EXIST. Change rule: `can_enable` and the A10 evidence show only if some account has account_mode != demo that could use auto: backend returns false for live always (auto_entry.py:50-52); so the form never shows today.

Panel (phone 360, desktop identical single column, max 640 px):
```
Entry mode
Who confirms entries, per account.

icm-demo     [DEMO]        Enters automatically        ● auto
             Every signal that passes the RiskGate is placed without asking. Exits are always automatic.
icm-live     [REAL MONEY]  You confirm every entry     ● asks you ({windowWords})
             You get a card (here and in Telegram); no tap within {windowWords} means no trade. Exits are always automatic.

(demo forced to ask)  amber note:
icm-demo     [DEMO]        Asking for now            ◐ ask
             Because: this account was just re-armed; asks until Tue 17 Nov 14:05.

(no engine reporting)  amber:
No engine reporting: entry mode unknown.
Entries are not placed while no engine runs.

▸ History: auto-mode evidence   (collapsed <details>; the Evidence table and A10 text)
```
Row anatomy: account id, class badge (same component as card), mode in words, status chip with text (`auto`, `asks you`, `unknown`) and glyph (not colour alone). Real-money rows have NO control to change them: no toggle, no link, one line "Real-money accounts always ask: this cannot be changed here." under the list (once).
Owner control for demo: a single `Switch demo to ask me` / `Switch demo back to automatic` button (existing POST /mode propose|auto, owner only; reverting to auto needs no code ONLY if it restores the ADR 0036 default; if backend still requires TOTP for auto, keep the code field behind the button). FLAG for trading-safety-reviewer: today /mode auto requires TOTP and 100-proposal evidence; under ADR 0036 demo auto is by config, so "switch back to automatic" must be a defined path (clear control.json approval_mode) rather than the evidence-gated enable. Needs a decision, not made here.
Header chip beside the title: none ("auto mode offered" removed).
Copy never says live can become automatic; the old sentence "Auto mode places every entry ... without asking you" is only inside the collapsed history and rewritten to "Demo accounts enter automatically (ADR 0036)".
States: loading (`Loading entry mode…`, exists), error (exists), no engines (amber above), one demo, demo+live, demo forced to ask, viewer role (rows visible, `Only the owner can change the mode.`), stale (engine report older than 2 min: row chip `last seen 14:02`, amber).
1440 px: same stack; rows become a 3-column grid (account+badge | mode words | status chip), description spans below.

## 7. Tests the implementer should add (all from the backlog, plus)
- Vitest: badge for live/demo/paper/unknown; window 300 s with 150 s left = 50% bar; expiring threshold min(15, 25% window); expired copy "60 min" for 3600; ask_reason rendered only for demo; no reason line on live even if (wrongly) supplied (ignore); Approve label class; Settings: demo-only, demo+live, no engines (never "Propose"), forced row, collapsed history.
- e2e at 390 and 1440 light/dark: badge visible, Approve not under nav (elementFromPoint), settings rows.
- Python: Proposal.ask_reason mapping table (6 kinds), live always null; Telegram text golden for both classes.
- Grep gate for stray `90` copy.
- Vitest + Python: terminal_not_demo renders REAL MONEY (terminal), Approve disabled, Reject enabled; the API refuses an approval for it (trading-safety confirms).
- API regen: `python scripts/export_openapi.py && npm --prefix web run gen:api`.

## 8. Flags (outside this scope)
1. ForcedPropose does not cover owner_propose / terminal_not_demo / config_propose (section 1): shared enum needed.
2. Bottom nav overlaps the card's Approve/footer in the phone shot (separate defect).
3. Switch-demo-back-to-auto path vs TOTP/evidence gate (section 6): decision for PD/trading-safety.
4. e2e seed: window_s=3600 on one card, 90 on the other; mark one demo forced (per backlog).
5. Home banner "No engine reporting" while a proposal card is shown: seed artefact, but if real, a card with no engine is unactionable; consider a note.
