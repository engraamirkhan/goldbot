- 2026-11-13 (branch feat/n3-har-abstention; R148; BACKLOG 145, 247): N-3 enable checks, and N-3 stays off. New
  `har_abstention.dry_run` (the row `har_days` would write, never written: a dry-run `ok` row would register N-3) and
  `measure_overlap` (prereg §2: median ln(ICM/Duka) of daily TR, W, w; pass |ln w| <= 0.02 on >= 20 days), both
  behind the read-only `scripts/har_check.py overlap|dry-run` for the brain. Data: the Mac's 2026 Duka year has 0 gap
  days (refreshed 11-07); replaying the job on it over every 2026 weekday gives 92 `ok` nights from 2026-07-02 on and
  fails the 128 before on 2025 holes (2025-02-26..28, 03-26..28, 05-12..14; with three 2024 holes, BACKLOG 247). On
  those 92 nights the forecast marked 6 low days (6.5 %, not 20 %; none since 2026-09-29); 24 of 73 20-day windows
  hold 0 low days and are void under the prereg. A calibration failure; those nights lie in the sealed holdout, so no fix
  may be tuned on them (BACKLOG 145 note). With the in-scope learning arms mostly retired (A5) the read cannot
  reach the GBP 10/week kill line: the data-engineer recommends not writing the K row and leaving `enabled: false`;
  fix-or-drop (re-point at ML review improvement A) is the research director's call. Owed on the brain:
  `scripts/local_bars.sh push`, then `scripts/har_check.py dry-run` and `overlap`.
