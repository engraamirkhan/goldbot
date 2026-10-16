- **Coverage triage of the data issues #2-#32 (data-engineer, 2026-10-16; roadmap W1-6a; read-only, no GitHub
  action taken).** Checked every open `data-coverage` issue against the published `data-v1` files (downloaded
  2026-10-16), day by day against the session calendar (a weekday with under 700 1m bars that is not a closure),
  and against the run logs. **The issues' "12/12 months complete" hides real gaps:** a month passes at 15,000 bars
  (`scripts/dukascopy_year.py:39`, `:81`), so a month that lost three whole days still says `ok`. **Root cause:** the
  full-refresh dispatch of 2026-10-06 (run 37448714319, every month re-downloaded although the previous file was
  present) replaced each month with whatever the throttled download returned. The published bars are kept only when
  a download returns nothing (`:222`), so a short month that still clears 15,000 replaced a fuller published one. Every
  day listed below was present in the 10-02 / 10-03 files (runs 37012050707, 37112099323): 2025 went from 351,024 to
  340,070 bars and 2026 from 258,197 to 210,608. The data can be recovered by a refetch. Expected closures seen in
  every year: 1 Jan, Good Friday, 25 Dec (plus the 26th or the observed day), and US-holiday early closes (5 h holes).
  | Issue | Year | Class | Evidence (published data-v1 file) | Proposal |
  |---|---|---|---|---|
  | #9, #5, #17 | 2010, 2011, 2016 | closures only | no weekday gap beyond 1 Jan, Good Friday, Christmas/Boxing Day | close |
  | #7 | 2012 | closures + artefact | 2012-04-06 (Good Friday, NFP) 517 bars is the short CME session; Sunday 09-30 reopen missing (see artefact below) | close |
  | #13, #22, #20 | 2013, 2017, 2018 | closures + artefact | Sunday reopen missing on 2013-06-30, 2017-04-30, 2018-09-30 (artefact) | close |
  | #15, #11, #19 | 2014, 2015, 2019 | closures + small fetch loss | about 2-4 h each: 2014-08-24 22:00 to 08-25 02:00, 2015-06-30 21:00-00:45, Sunday reopen 2019-03-10 (the 2019-06-30 one is the artefact) | close |
  | #24 | 2020 | **real gap** | 2020-05-26..28 (Tue-Thu) 0 bars; May 28,666 (10-02) to 24,535 | keep |
  | #25 | 2021 | **real gap** | 2021-11-05 (Fri, NFP) 0 bars; Nov 29,544 to 28,226 | keep |
  | #27 | 2022 | **real gap** | 2022-01-31 (Mon) 0 bars; Jan 28,727 to 27,350 | keep |
  | #30 | 2023 | **real gap** | 2023-11-08 (Wed) 0 bars; Nov 29,640 to 28,263; also the Sunday 04-30 reopen | keep |
  | #29 | 2024 | **real gap** | 0 bars on 01-10..12, 06-07 (NFP), 07-01, 07-16; Jan 28,818 to 26,064, Jun 27,380 to 26,123, Jul 31,511 to 28,757 | keep |
  | #2 | 2025 | **real gap** (research window) | 0 bars on 02-26..28, 03-26..28, 05-12..14; Feb 27,377 to 23,248, Mar 28,123 to 24,227, May 30,014 to 25,767. Q4 (holdout) clean apart from the Sunday 11-30 reopen | keep |
  | #32 | 2026 | **real gap** (holdout) | 42 non-holiday weekdays at 0 bars in Apr, Jun-Sep (16.3% of the holdout's 258 trading weekdays); Feb, Mar and May bid-imputed on every bar (85,645 bars); file ends 2026-10-05 23:58. The 10-10 scheduled run (38042847605, 4 h 19 min) downloaded 0 bars and still reported "10/10 complete". Rolling issue: the workflow edits it every Saturday | keep (rolling) |

  Duplicates: none among the open issues. The 2026 failure issues #33 and #35 overlap #32 and are already closed.
  **Research window total (2010-01..2025-09): 21 whole weekdays missing (2020-2025), two of them NFP Fridays.**
  **Artefact (code):** `week_chunks` skips a chunk that has no weekday (`scripts/dukascopy_year.py:55-60`). A 30-day
  month that starts on a Saturday therefore loses its last Sunday's 22:00-24:00 reopen bars (2012-09, 2013-06,
  2017-04, 2018-09, 2019-06, 2023-04, 2024-06, 2025-11). Low impact (about 2 h of thin trading each). The fix goes in
  the W1-6 script lane. **Doc correction:** `docs/RUNBOOK.md` section 2.5 still said `fetch_data_release.py --macro`
  prints `macro 0` "until the data-macro workflow has run once". It is now corrected: `macro-v1` has been published
  since 2026-10-13 (run 38394945106; 36,192 rows across 7 series, VIXCLS and SP500 included). DESIGN.md, the readiness
  list and the freeze checklist were already correct. The HANDOFF status line "every month 2010-01 .. 2026-10 is
  complete on data-v1 (no partial or imputed months)" (from 2026-10-05, in `_template.md`) is no longer true; this
  bullet replaces it. Dated records (the 2026-10-11 research fragment, scorecard-2026-10 section 9) are left as
  written.
  - Freeze gap G1 (data-engineer, Fri 2026-11-06): repair data-v1 2020-2025 to 0 unexpected weekday gaps in
    2010-01..2025-09. This needs a coverage check by day against the session calendar (new `scripts/check_coverage.py`,
    W1-6, code by Tue 10-27) and a merge that never replaces a published day with fewer bars. Then targeted refetches
    of the 15 affected months, about 1 h of Actions minutes. **Until that merge lands, no `full_refresh` dispatch of
    `data-dukascopy`.**
  - Freeze gap G2 (data-engineer, Fri 2026-11-06): repair the holdout (2026: 42 days, plus Feb/Mar/May
    bid-imputed) in the same lane, before any holdout scoring.
  - Freeze gap G3 (research-director, Fri 2026-11-13): dispatch count-owed-events on `main` again on the repaired
    data. H-01's lower bound is 152 against a floor of 150, which leaves no room for 21 missing days. Re-read H-04's
    4,025 entries in the same run.
  - Freeze gap G4 (data-engineer, Mon 2026-12-14): the research-window files (2010-2025) are final at the freeze
    commit. The rows written 12-18..12-23 pin their sha256 digests (`goldbot/research/preregistration.py:19`,
    BACKLOG 50), so any rewrite after that refuses every Q1 run. The 2026 file is not pinned.
  - Freeze gap G5 (data-engineer, Tue 2026-10-27): each Saturday run re-downloads the three bid-imputed 2026 months
    for about 4 h (they count as never complete, `:75`, `:81`). That is about 1,000 of the 2,000 free minutes a month,
    and the December pre-registration and research runs need those minutes. Cap or skip the retry in the W1-6 script
    lane.
  - Not freeze (data-engineer, Sat 10-17): the 2026 file has stopped at 2026-10-05, which affects the engines'
    warm start. Read the 03:17 UTC scheduled run. If it again gets 0 bars, run one targeted 2026 dispatch (not a
    full refresh) and tell the program-director the same day.
  - Proposed on GitHub (not done here; the owner or program-director acts): close #5 #7 #9 #11 #13 #15 #17 #19 #20
    #22 with a comment pointing to this bullet. Keep #2 #24 #25 #27 #29 #30 until G1 verifies, and #32 until G2.
