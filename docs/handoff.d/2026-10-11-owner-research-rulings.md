---
section: next
---
- Owner research rulings recorded (2026-10-11; the owner approved the research director's recommendations).
  - `research.screen_min_events_daily: 150` is committed: the daily-signal floor is retire-only.
  - Code check, code unchanged: a daily screen with >= 150 events can still read `pass` (`research/screen.py`
    `screen_verdict`). That pass reaches the model stage in `scripts/research_pass.py` and H-01's `continue`, which
    permits model research only. Promotion still needs `research/gates.py` (>= 1,500 candidates), so the floor can
    retire but never promote. If the stricter reading (no pass below 1,000) is wanted, that is a quant and
    implementer change.
  - Also decided: budget 20 unchanged; no consensus feed; no other instruments; K_eff default confirmed; `costs-v1`
    published by 2026-12-11 if the demo terminal runs, otherwise "priors accepted" on 2026-12-14; owner history
    exported in full, analysed locally, summary only.
  - H-06 reading-rule pins G1-G4 written. The Q1 reservation is planned at 8 unless H-06's count is >= 150 (the
    quant estimate is about 80-130).
  - Q2 draft: P-1 resolved; P-2 adopted (a literal first breach: a closed session, no ATR or a blackout means no
    entry; `trend_day` follows it); P-5 adopted (cost source pinned at the freeze, the same for gates and labels).
  - Recorded in `docs/research/preregistration-2027Q1.md`, `preregistration-2027Q2-draft.md`, `hypotheses.md`,
    `research-plan.md` and `scorecard-2026-10.md`.
