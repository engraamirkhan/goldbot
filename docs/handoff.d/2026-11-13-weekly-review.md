- 2026-11-13 (branch feat/weekly-review; R141; BACKLOG 219-222): new Saturday job `weekly_review` (13:00 UTC, after
  research_director) writes one ranked action list a week to aux.db `review_actions` (migration 30). It covers each
  family against its matched control (from the ranking's figures; no control reads "unpaired"), loss drivers from
  attribution.json, cost drift against the cost of record, reads due at their registered size, MFE/MAE stop-exit
  diagnostics and the Monday roll-up's proposals. Each roll-up is marked read with `mark_weekly_consumed`, R140's
  helper copied with the same text. The next research_director run consumes the actions once into
  `research_plan.json` `review_queue`; budgets are unchanged. Unpaired families and cost drift open one `review`
  issue a week (outbox without the token). The Sunday digest gains "Changed this week" (counts only). The only
  automatic action is a registered stop the daily review found breached but not yet recorded (tighten only). Nothing
  on the order path changed.
