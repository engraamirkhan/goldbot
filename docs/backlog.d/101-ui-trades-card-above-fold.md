---
number: 101
rank: 37
title: 'Phone Trades: the waiting card and Approve above the fold'
stage: 'UI (approvals) · PROTECT'
size: S
status: 'proposed, severity MEDIUM · accountable: ui-ux-designer (spec) then implementer · reviews: code-reviewer'
---
- **101. Phone Trades: the waiting card and Approve above the fold.** Value (found in the fold shot, `scripts/ui_snapshots.mjs` `00-fold-trades.png` at 390x844): on Trades the seven safety tiles (`web/src/screens/SafetyStrip.tsx`) and the No-engines banner push the waiting card to y≈620, so direction and risk are barely visible and Approve needs a scroll inside a 90-second window. Acceptance: on phone with a waiting proposal, the card's header, risk line and Approve sit above the fold at 390x844 and 360x740; the safety tiles collapse to one summary line ("All clear" / "2 things block entries"), red only for a stop, that expands on tap; on desktop unchanged. e2e asserts Approve's bounding box is inside the viewport at 360x740 with two waiting proposals and the idle strip.
