- 2026-11-12 (branch fix/history-reread-off-tick; backlog 157, 159; R127): the engine's store reads after the warm
  start no longer run on the tick thread. The history re-read (while `history_read` / `history_short` refuses entries)
  and the release reload (while a window has a hole) each run on a thread of their own, one of each at a time, and
  the tick thread merges a finished read at its next bar close; a hung store delays no tick and no exit. The refusal
  holds until a read has finished, merged and been judged (`history_short` re-judged as before); a read that never
  returns keeps entries refused and logs one error after 120 s ("has not returned"). Release minutes are merged only
  before the cut-over current at the merge. Behaviour change: a re-read or reload is merged at the first close after it returns, usually one close after it
  starts (it used to merge at the close that started it). Key zones: an unreadable store cache now warns once per exception type
  with its traceback, then at most hourly with the count, and logs "cache readable again" once on recovery. Owner
  step when the hung-read error fires: check the data root's disk and the store lock; the engine keeps trading
  exits. Tests: tests/test_engine_history_read.py (backlog 157 section), tests/test_engine_key_zones.py (backlog 159).
