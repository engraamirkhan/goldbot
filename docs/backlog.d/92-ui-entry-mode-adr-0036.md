---
number: 92
rank: 28
title: Settings entry-mode card shows per-account entry mode under ADR 0036
stage: 'UI (settings) · PROTECT'
size: S
status: 'proposed, severity HIGH · accountable: ui-ux-designer (spec) then implementer · reviews: code-reviewer, trading-safety-reviewer'
---
- **92. Settings entry-mode card shows per-account entry mode under ADR 0036.** Value: Settings says "Entry mode: Propose-and-approve (engine setting)" with "auto mode offered after 100 decided proposals" and an "Enable auto mode" form (`web/src/screens/AutoMode.tsx:11`, `:92` defaults a null owner_mode to propose, `:96-106`, `:65-76`). Since ADR 0036 demo accounts enter automatically by config and real-money accounts always ask (`goldbot/engine/auto_entry.py:50-52` refuses auto on live), so the card misstates the safety state on both sides. Acceptance: the card leads with one row per account from `engine_modes` and the account mode: "icm-demo (demo): enters automatically" / "<live> (real money): you confirm every entry", plus forced-propose rows as today; with no engine reporting it says "No engine reporting: entry mode unknown" (amber), never a default of propose. The A10 evidence and "Enable auto mode" form show only if a non-demo account could use it (today: never) and otherwise move under a collapsed "History: auto-mode evidence"; the copy never implies live can go automatic. Vitest for demo-only, demo+live, no engines; e2e shot at 390 and 1440.
