- **Replica rebuild storm fixed; scalp sends off the quarter-hour (2026-10-27, R79).** Root cause of the 61 "bar_index
  discontinuity" / "revised" rebuilds (~60 CPU-s each at the quarter-hour closes): XTV1 (icm-demo) and VXTV2
  (vantage-demo) share one replica parameter block and state/aux.db, so both engines wrote one feed with their own
  broker's bars and each found the other's snapshot at every close (reproduced: 16 rebuilds in 8 closes). Each host
  now has its own feed (`<account>-xauusd-s2010-...`; the old shared feed's rows are orphaned, harmless derived data).
  Also: a corrected last bar resumes from the previous kept snapshot (exact), the store's bar count is a logged note
  not a rebuild trigger, every update keeps its last 16 snapshots, the replica is fed complete window bars, and a feed
  rebuilds at most once an hour per process. 1m/5m exploration orders on :00/:15/:30/:45 go out 3-7 s after the close
  (by arm id); history uses the same rule (ARM-SPEC section 0 dated note; no history number moves). Deploy: expect one
  rebuild per host feed on restart.
