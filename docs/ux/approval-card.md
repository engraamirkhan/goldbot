# Approval card and safety strip

The owner makes one decision: approve or reject an entry within 90 s, usually on a phone. Everything after entry is
automatic. This spec covers the dashboard's Approvals tab (`web/src/screens/Approvals.tsx`, `ProposalCard.tsx`,
`SafetyStrip.tsx`) and the Telegram proposal text (`goldbot/telegram/approvals.py`, `Proposal.text`).

## Data

| Shown | Source |
|---|---|
| Pending proposals | `GET /api/proposals`, polled every 2 s and refreshed on `proposal`/`decision` WebSocket events |
| Decided in the last 10 min | `GET /api/proposals/recent` (`DecidedProposal.status`: submitted, approved, rejected, expired, refused) |
| $ risk | `Proposal.risk_usd`: lots × stop distance × 100 oz, set by the engine from the RiskGate sizing. Null from older engines |
| Owner, supervisor and drift halts, news blackout | `GET /api/status`: `halted`, `supervisor_halt`, `drift_halt` (+ reasons), `blackout` |
| Drawdown stage, account class, mode | `GET /api/accounts` |
| Refused at approval | `Proposal.gate_refusal`, written by the engine when the RiskGate re-check at approval refuses the order |

Feature values are the current values of the model's three most important features, not contributions. The card
labels them in plain words (`web/src/lib/features.ts`) and keeps the raw name in the hover title.

## Layout

Phone (360 px, 16 px gutters, single column):

```
┌ New entries allowed ───────────────────────┐   summary: green, or red with the blockers listed
│ Owner halt ● off   │ Supervisor ● ok       │   2-column chip grid
│ Drift halt ● off   │ Drawdown ● normal 1.2%│
│ News ● clear       │ Account ● raw · demo  │
└────────────────────────────────────────────┘
┃ [▲ LONG] 0.05 lots XAUUSD            1:12  ┃   direction badge, size, countdown (tabular digits)
┃ ███████████████████████░░░░░░░░░░░░░░░░░░  ┃   progress bar, 90 s window
┃ RISK $21.25 if the stop is hit             ┃
┃ Entry      Stop        Target              ┃
┃ 2400.25    2396.00     2406.50             ┃
┃            −4.25       +6.25               ┃   distance from entry in price
┃ Win prob.  Expected    Spread now          ┃
┃ p 0.62     +0.31 R     22 pt               ┃
┃ WHY  • Volatility (ATR 14) 0.40            ┃
┃      • Trend strength (ADX 14) −0.20       ┃
┃ [            Approve  (56 px)            ] ┃
┃ [            Reject…  (48 px)            ] ┃
┃ icm-demo · session_open-g0-abc · expires 14:32:05 · Chart
```

Reject opens the reason codes in place of the two buttons (2-column grid, 48 px): News, Cost, Discretion,
Duplicate, Other, Cancel. They are the backend's `REASON_CODES`; one tap sends the rejection.

Desktop (≥ 720 px): the strip is one row of six chips; cards sit in a grid of 360 px minimum columns; Approve and
Reject share a row (2 : 1); reasons are three per row. Pending cards are sorted most urgent first; decided cards
follow under "Decided in the last 10 minutes", with no buttons.

## States

| State | When | Card |
|---|---|---|
| Pending | > 15 s left | Countdown m:ss, accent progress bar, Approve + Reject… |
| Expiring | ≤ 15 s left | Amber border, timer and bar; screen readers hear "15 seconds left to decide" once |
| Sending | decision in flight | Buttons disabled, Approve reads "Sending…" |
| Error | decision refused by the API | Message on the card (`role=alert`), e.g. "already decided", "proposal expired" |
| Submitted | decision recorded, engine not yet applied | Blue note: "Approval sent. The engine re-checks risk and places the order on its next tick." |
| Approved | engine sent the order | Green note: "Approved, order sent. Stop, target and exit are automatic." |
| Rejected | with reason | Neutral note: "Rejected · Cost" |
| Refused at approval | approved, RiskGate refused | Red note: "Refused at approval. The RiskGate blocked the order (owner halt); nothing was sent." |
| Expired | clock reached 0, or engine archived it | Neutral note: "Expired. Nobody approved within 90 s; no order." No buttons, no timer |
| Blocked | a halt or blackout is active | Amber note above the buttons: "New entries are blocked (…): the RiskGate will refuse this at approval." Approve stays available; the gate decides |

Every decided note ends with the channel ("via Telegram", "via dashboard (email)") when known.

Screen states: loading ("Loading proposals…"), error ("Could not load proposals (…). Telegram approvals still
work."), empty ("No proposals waiting. Entries you approve here or on Telegram are managed automatically
afterwards."), viewer role ("Viewer role: you can watch, not approve." in place of the buttons).

Strip states: loading (grey chips, "Checking safety state…"), unavailable (amber "Safety state unavailable. Check
Overview before approving."), no engines (amber "No engines reporting"), blocked (red summary listing owner halt,
supervisor halt, drift halt, news blackout or news shock, drawdown halt, account class unknown on demo/live).

## Colour and copy rules

- Red only for what stops trading: owner, supervisor, drift and drawdown halts, an active blackout, an unknown
  account class on demo/live, and a refusal at approval. Size-down is amber. Short is blue, not red.
- State is never colour alone: chips carry words (off/ON/normal/halted), the timer carries digits, notes carry text.
- Every number has its unit: $, pt, R, lots, price. Times are local; UTC is in the hover title.

## Accessibility

- Contrast ≥ 4.5 : 1 for all text in light and dark (tokens in `styles.css`; checked: lowest is muted text on the
  green tint, 4.6 : 1). White on the Approve fill is 5.3 : 1.
- Tap targets: Approve 56 px tall, Reject and reasons 48 px, chips 44 px.
- Focus: 3 px `:focus-visible` outline in both themes; no focus is stolen when cards arrive.
- The countdown is `role=timer` with "N seconds left to decide"; outcomes are `role=status`; errors `role=alert`.
- No layout shift on ticks: tabular digits, fixed-width timer, the bar animates with `transform` (off under
  `prefers-reduced-motion`).

## Telegram

The message carries the card's essentials in the card's order:

```
🟢 LONG 0.05 lots XAUUSD  ·  risk $21
entry 2400.25  stop 2396.00  target 2406.50
p=0.62  EV=+0.31R  spread 22pt
why: atr_14 +0.40, adx_14 -0.20
icm-demo · session_open-g0-abc
⏱ approve within 90s or it expires · exits are automatic
```

Buttons stay as they are: Approve, then the five reason codes.
