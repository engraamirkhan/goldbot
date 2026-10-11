# Approval card and safety strip

The owner makes one decision: approve or reject an entry within 90 s, usually on a phone. Everything after entry is
automatic. This spec covers the dashboard's Approvals tab (`web/src/screens/Approvals.tsx`, `ProposalCard.tsx`,
`SafetyStrip.tsx`) and the Telegram proposal text (`goldbot/telegram/approvals.py`, `Proposal.text`).

## Data

| Shown | Source |
|---|---|
| Pending proposals | `GET /api/proposals`, polled every 2 s and refreshed on `proposal`/`decision` WebSocket events |
| Decided in the last 10 min | `GET /api/proposals/recent` (`DecidedProposal.status`: submitted, approved, rejected, expired, refused) |
| Risk | `Proposal.risk_amount` in `Proposal.currency` (the broker account's): the RiskGate's `GateDecision.risk_amount`, lost if the stop is hit; `risk_fraction` = risk / equity, as the gate sized it. Older engines: only `risk_usd` (USD), shown labelled "(USD)" without a share |
| Minimum-lot exception | `min_lot_exception`, `risk_normal` (risk per trade), `risk_cap` (the cap that applied, halved in size-down) |
| Stop and target in money | stop: −risk; target: `reward_amount` = risk × target distance / stop distance (before costs) |
| Loss budget if this loses | `loss_left`, `loss_left_period`: `account_view.proposal_budget`, every loss cap less this trade's risk, least left wins; computed live on each poll, pending cards only. Null when the engine file or the account-currency risk is missing |
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
┃ RISK £8.20 (0.82% of equity) if the stop is    ┃   account currency, rounded UP to the cent; share of
┃ hit, before costs                              ┃   equity as the gate sized it
┃ ┌ Smallest trade size: above the normal  ┐ ┃   blue info note, only with the minimum-lot exception
┃ └ 0.5%, within your 1% cap.              ┘ ┃
┃ LOSS BUDGET £11.80 left today if this loses┃   live, rounded DOWN; amber when nothing is left;
┃ (open trades aside)                        ┃   muted "unknown"
┃ Entry      Stop        Target              ┃
┃ 2400.25    2396.00     2406.50             ┃
┃            −4.25       +6.25               ┃   distance from entry in price
┃            −£8.20      +£12.30             ┃   the same in the account currency
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
| Minimum lot | sized by the minimum-lot exception | Blue note: "Smallest trade size: above the normal 0.5%, within your 1% cap." Information, not a warning: the gate allowed it within the owner's cap |
| Budget left | pending, engine reporting | "LOSS BUDGET £11.80 left today if this loses (open trades aside)" ("this week" when the weekly cap binds). Open positions' stop risk is not reserved, so the card says so |
| Budget spent | the trade's stop would use the rest | Amber: "No loss budget left today if this loses (open trades aside): new entries stop" |
| Budget unknown | no engine file, or an older engine (USD risk only) | Muted: "Loss budget left today: unknown" |
| Older engine | only `risk_usd` | "RISK $20.00 (USD) if the stop is hit, before costs", no share of equity, no budget |
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
- Every number has its unit: the account currency (£, $, €, else the ISO code), %, pt, R, lots, price. Times are
  local; UTC is in the hover title. Risk is never shown in dollars unlabelled: a USD figure from an older engine says
  "(USD)".
- The share is "of equity" (what the RiskGate sizes on), not "of balance": with a position open they differ.
- Risk is rounded up and the budget left rounded down to the cent: the card never shows less risk or more room than
  the RiskGate computed. The normal risk in the min-lot note is the gate's effective target at this conviction
  (halved and capped in the size-down stage), so the note never reads "above 0.5%, within 0.5%".

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
🟢 LONG 0.01 lots XAUUSD
risk £8.20 (0.82% of equity) if the stop is hit, before costs
smallest trade size: above the normal 0.5%, within your 1% cap
£11.80 left today if this loses (open trades aside)
entry 2400.25  stop 2396.25 (−£8.20)  target 2406.25 (+£12.30)
p=0.62  EV=+0.31R  spread 22pt
why: atr_14 +0.40, adx_14 -0.20
icm-demo · session_open-g0-abc
⏱ approve within 90s or it expires · exits are automatic
```

The min-lot line appears only with the exception. The budget line is computed when the message is sent (the
Telegram service reads engine_<account>.json and supervisor.json via `account_view.proposal_budget_from_state`);
"loss budget left today: unknown" when it cannot be read. An older engine's line reads "risk $20.00 (USD) if the
stop is hit, before costs"; with no risk at all, "risk: not reported by this engine version". Buttons stay as they are: Approve,
then the five reason codes.

Live updates: the budget line is one line on desktop and at 360 px except the spent wording, which can wrap once; it
changes only when the budget crosses zero, so polling does not shift the card.
