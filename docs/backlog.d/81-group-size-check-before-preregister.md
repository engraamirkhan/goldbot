---
number: 81
rank: 18
title: Discovery group-size check runs before the pre-registration row is written; context columns counted
stage: Research (discovery pipeline) · LEARN
size: S
status: 'ready (quant finding on the scalping candidates, 2026-10-17) · accountable: implementer · reviews: quant-reviewer, code-reviewer · due 2026-11-13 (before any O-4 count)'
---
- **81. Discovery group-size check runs before the pre-registration row is written; context columns counted.** Value: `check_group_sizes` (goldbot/research/discovery.py ~527) runs AFTER `reg.preregister` (~838-846), so a refused run leaves a pre-registered row behind; and at 15m the h1/h4/d1 context columns (~852-854) join their base group (~257-259), putting `trend` (~72) and `volatility` (~49) over the 39-column cap so O-4 would be refused. Acceptance: the size check runs before any registry row is written (test: a refused config writes no row and spends no trial); context columns are counted in the check with the documented split rule (e.g. `<family>_ctx`); a test asserts O-4's pinned group map has no group over 39 with context included; K_eff restated in the pre-registration by the research-director.
