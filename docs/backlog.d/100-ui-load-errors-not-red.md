---
number: 100
rank: 36
title: Load errors that do not stop trading are amber, not red
stage: 'UI (errors) · UX'
size: S
status: 'proposed, severity LOW · accountable: ui-ux-designer (spec) then implementer · reviews: code-reviewer'
---
- **100. Load errors that do not stop trading are amber, not red.** Value (found in the shots): with the API unreachable, Home leads with red "Could not load waiting trades. Telegram approvals still work." (`web/src/screens/Home.tsx:46`, class `err`), while the cards below use amber ErrorState. Red is reserved for things that stop trading; this one does not. Acceptance: the waiting-trades load error uses ErrorState (amber, Try again, "Telegram approvals still work"); a lint/test check lists every `className="err"` use and each is either a trading-stopping state or converted. Vitest for Home with the proposals query failing.
