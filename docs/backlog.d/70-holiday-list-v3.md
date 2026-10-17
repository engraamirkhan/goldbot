---
number: 70
rank: 16
title: Ship the cme-metals-v3 holiday list (BACKLOG 45 decision)
stage: Operational risk (evidence) · RUN
size: S
status: 'ready (decided 2026-10-16 by program director + product owner on BACKLOG 45) · accountable: implementer · due 2026-10-30'
---
- **70. Ship the cme-metals-v3 holiday list (implementer; quant review).** Decided 2026-10-16 by program director +
  product owner (owner delegated) on BACKLOG 45: ADOPT cme-metals-v3 (evidence:
  `docs/research/holiday-list-measurement-2026-10.md`, draft feda07a, quant ruling CHANGES with the v3 list SAFE TO
  ADOPT, applied by the research director 2026-10-16). Value: the list RiskGate reads becomes a measured list
  instead of a proxy, and stops blocking a day that trades normally (2 Jan). The window stays off: this item changes
  the list, not `risk.holidays.enabled` (switching it on is the owner's, after 46). Depends on: 45's final
  (2026-10-30) for the final sha in the `source:` line. Files: `config/holidays_cme_metals.yaml`,
  `tests/test_entry_windows.py` (hand-reviewed set and its comment); nothing in `goldbot/`.
  Acceptance:
  - `version: cme-metals-v3`; `valid_from` and `valid_through` unchanged (2026-01-01 .. 2028-12-31).
  - 2026-01-02 is removed (the only 2 Jan in the list; the next weekday 2 Jan is 2029-01-02). No other date is
    added or removed: every other v2 date stays.
  - `source:` reads `"measured (BACKLOG 45, Dukascopy proxy, feda07a + <final sha>); unresolved days marked"`, with
    the sha of the 2026-10-30 final; the header comment no longer says "a proxy, not measured" and points to the note.
  - The labels of Juneteenth, Easter Monday, the day after Thanksgiving and 26 Dec carry "(unresolved, BACKLOG 45)".
  - `REVIEWED_DAYS` in `tests/test_entry_windows.py` loses 2026-01-02 and its comment cites BACKLOG 45; the test
    asserts version `cme-metals-v3` and "measured" in the source, and keeps the Columbus/Veterans and
    day-after-Thanksgiving/Easter Monday checks. The holiday tests pass; the window's default stays off (existing
    test).
  - Reviews: quant-reviewer (the list matches the note's recommendation table) and code-reviewer, recorded at the
    merge (E1).
