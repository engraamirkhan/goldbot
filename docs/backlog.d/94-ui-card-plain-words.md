---
number: 94
rank: 30
title: Approval card and trade views in plain words, not p, R, pt or indicator codes
stage: 'UI (approvals, trades) · UX'
size: S
status: 'proposed, severity MEDIUM · accountable: ui-ux-designer (spec) then implementer · reviews: code-reviewer'
---
- **94. Approval card and trade views in plain words, not p, R, pt or indicator codes.** Value: the owner is a trader, not a developer; the card shows "p 0.62", "+0.31 R", "22 pt", "Volatility (ATR 14) 0.40" and "Smallest trade size: above the normal 0.5%, within your 1% cap" (`ProposalCard.tsx:62-63`, `:81-83`, `:90`; `web/src/lib/features.ts` keeps indicator codes in labels and shows raw values). Acceptance: "Win chance 62%"; "Expected +0.31 R (≈ +£2.54)" with R explained on hover/tap as "1 R = the risk if the stop is hit"; spread in dollars per ounce with points on hover; feature lines as words with direction ("Volatility: high for the hour", "Trend: weak"), code and raw value on hover/tap only; min-lot line reads "Smallest possible size: risks 0.82% (normal 0.5%, cap 1%)". The same words in Trades/Activity and the Telegram card. Vitest on the formatter table; copy reviewed by ui-ux before build.
