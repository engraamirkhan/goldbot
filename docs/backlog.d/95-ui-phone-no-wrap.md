---
number: 95
title: 'Phone: direction badge and number-unit pairs never wrap'
stage: 'UI (layout) · UX'
size: S
status: 'done (triage 2026-11-13): PR #158 (e3e76c0f, 5633857a)'
---
- **95. Phone: direction badge and number-unit pairs never wrap.** Value: at 390 px the LONG/SHORT badge wraps with the arrow above the word (`web/src/styles.css:104-105`: `.card-head` flex with no `flex: none`/`white-space: nowrap` on `.dir`), and "+0.56 R" breaks between number and unit in the Learning card (`web/src/screens/HomeCards.tsx:164`; `signedR` uses a normal space, `web/src/lib/tradesOverview.ts`). Acceptance: `.dir` never wraps or shrinks; every number-unit formatter (R, %, pt, lots, money) joins with a no-break space (U+00A0) in one shared helper in `web/src/ui/format.ts`; the lots/symbol line may wrap as a unit. Vitest asserts U+00A0 in formatters; e2e at 360 px asserts the badge height equals one line.
