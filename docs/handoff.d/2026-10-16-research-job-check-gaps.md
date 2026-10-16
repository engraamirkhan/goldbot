- **Research job check on the coverage triage (research director, 2026-10-16; nothing dispatched, no outcome
  read).** The Q1 readiness list (`docs/research/preregistration-2027Q1.md`) now carries the data gaps G1-G5 as
  item 5, each with one accountable role and a date (G1/G2 repair 11-06, G3 recount 11-13, G4 files final 12-14,
  G5 Saturday-retry cap 10-27). **H-01 is AT RISK:** its lower bound of 152 against the retire floor of 150 was
  counted on data-v1 with 21 research-window weekdays missing (9 of H-04's 10 sample-rule drops are those gap days).
  The planned 11-01 script recount and the 11-13 recount on repaired data are merged into **one count-owed-events
  run on `main` after G1 closes, by 11-13**; < 150 withdraws H-01 by its rule and the 2027Q1 reservation goes 8 -> 7.
  The evaluator adds a G3 check row by 11-14. W1-5 (H-13 cost filter, strategy-researcher) was not started and is
  scheduled to start Mon 10-19 (due 10-30). The performance analyst starts BACKLOG 45 (holiday measurement) now: it
  is not blocked; draft 10-23, final 10-30.
  - Next (data-engineer, Tue 10-27 / Fri 11-06): `check_coverage.py` and the no-shrink merge, then the refetches (G1,
    G2, G5). No `full_refresh` until then.
  - Next (research-director, by Fri 11-13): dispatch the merged recount once G1 is verified; paste it under H-01/H-04.
