---
number: 216
rank: 76
title: Research director reads and marks the weekly review proposals
stage: 'research ops (follow-up of R140, 2026-11-13)'
size: S
status: 'done (2026-11-14, fix/ops-backlog-1114): consumer is the Mac research director (the brain research_director job is off without an Anthropic key): goldbot/research/weekly_inbox.py (list read-only; mark with the decision on stdin; an unreadable roll-up never marked) and scripts/weekly_proposals.sh over ssh as the goldbot user; mark_weekly_consumed records the decision; tests/test_weekly_inbox.py proves one mark and the loop check passing'
---
- **216. Research director reads and marks the weekly review proposals.** Value: the Monday roll-up (`state/aux/daily_review/weekly-<sunday>.json`) proposes at most four variants, but nothing reads them ("which nothing runs"), so `feedback_loop` FAILs on every roll-up with proposals 8 days after it is written. Acceptance, tests first: the weekly research_director job reads every unread roll-up with proposals, carries each into its plan as a candidate (pre-registration rules unchanged; a proposal is never run without one), and calls `daily_review.mark_weekly_consumed(state_dir, name, by="research_director", now=slot)`; a test proves an unread roll-up is marked once and the loop check then passes; an unreadable roll-up is not marked.
