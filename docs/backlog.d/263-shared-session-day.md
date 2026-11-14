---
number: 263
rank: 88
title: One session_day() for read-due and futility stop
stage: Research integrity · read rules
size: S
status: 'proposed 2026-11-14 (quant re-review of fix/weekly-read-due) · accountable: implementer, evaluator · reviews: quant-reviewer'
---
- **263. One session_day() for read-due and futility stop.** Value: two registered rules count "session days" in different ways, so the same fill can land on different days. goldbot/research/arm_stops.py (lines ~70 and ~195) counts the futility stop's `min_days` over the UTC calendar days of fills. The weekly review's `read_due` and the strategy ranking (fix/weekly-read-due, R156) use the fill's entry opportunity day, which rolls at 22:00 UTC. A fill entered at 22:30 UTC therefore counts on day D+1 for the read and on day D for the stop. Acceptance: (1) one shared `session_day()` helper (entry day, 22:00 UTC roll) used by both arm_stops and the ranking/read-due path; (2) a test that a fill entered at 22:30 UTC counts on the same session day in both; (3) the evaluator rules whether W1 inherits H-61's amended >= 20 session-day minimum (arms-expansion-2026-10-26.md 2.1(6)), and the ruling is recorded in config/arm_reads.yaml (`min_session_days` for the W1 arms, or a comment saying none applies).
