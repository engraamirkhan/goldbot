---
number: 98
rank: 34
title: 'Limits: useful empty state and the combined cap in money, readable'
stage: 'UI (limits) · PROTECT'
size: S
status: 'proposed, severity MEDIUM · accountable: ui-ux-designer (spec) then implementer · reviews: code-reviewer'
---
- **98. Limits: useful empty state and the combined cap in money, readable.** Value: with no engine the Limits screen shows a bare "No engines reporting yet." under a duplicated heading (page "Limits", h2 "Risk limits"; `web/src/screens/Limits.tsx:194-197`), the error state is a raw red line and a raw button (`:186-190`), and the combined cap that binds first is one small grey sentence in percent only (`:157-167`). The owner's limit is £50 a day (ADR 0004): it should read in money. Acceptance: one heading; the combined cap is a card at the top: "All accounts together: £x of £y used today, £a of £b this week" (percent on hover), "binds first" as a label; empty state uses the design-system EmptyState ("No engine is reporting. Limits show once an engine reports; the configured defaults are …"); error uses ErrorState with "Engines fail closed on this" in body text. API: `LimitsView.supervisor` gains money amounts if not present (`daily_cap_amount`, `weekly_cap_amount`, `daily_used_amount`, `weekly_used_amount`, `currency`). Vitest for empty/error/loaded; shots at 390 and 1440. Design note: `docs/design/ui-97-98-research-limits.md` (98; also `day_resets_utc`/`week_resets_utc` and a strategy display name on the API).
