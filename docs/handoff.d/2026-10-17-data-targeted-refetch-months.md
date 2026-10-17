- **Targeted refetch for G1/G2: `data-dukascopy` input `months` (data-engineer, 2026-10-17; not dispatched, not
  pushed; security review of the workflow change pending).** One manual dispatch now refetches exactly a list of
  months, e.g. `gh workflow run data-dukascopy.yml --ref main -f months=2024-01,2024-06`. The list is validated
  strictly (comma-separated `YYYY-MM`, ASCII digits, 2010-01 up to the current month, at most 24) by
  `scripts/dukascopy_months.py` in the `plan` job (sparse checkout, `contents: read`). It reaches the shell only
  through `env:`, and `build-year` receives only the plan job's normalised list. `full_refresh` or `retry_flagged`
  together with `months` is refused. `scripts/dukascopy_year.py --months` downloads only that year's listed months,
  flagged or not, so a listed month gets the `retry_flagged` treatment. It merges each month through
  `merge_never_shrink` (a day is replaced only by more bars, or by an unflagged copy of an imputed day) and keeps
  every other month as published. Without a published year file it refuses before downloading. Tests:
  `tests/test_dukascopy_year.py`, 51 passed (the parser, the CLI, `main()` refetching only listed months and never
  shrinking a day, imputed-day repair, both refusals, and the workflow's plan step executed with good, bad and
  conflicting inputs, with no `${{ }}` in any `run:`). actionlint 1.7.12 and shellcheck are clean. RUNBOOK section 6
  covers the dispatch: "Missing days in data-v1: targeted refetch".
  - Month list for G1/G2 from the 2026-10-16 coverage triage: research window 2020-05, 2021-11, 2022-01, 2023-11,
    2024-01, 2024-06, 2024-07, 2025-02, 2025-03, 2025-05; holdout 2026-04, 2026-06..09 (lost days) and 2026-02,
    2026-03, 2026-05 (bid-imputed). G1 ("0 unexpected weekday gaps in 2010-01..2025-09") needs all ten
    research-window months, not only the three 2024 ones.
  - Actions minutes for the full 18-month list (7 year jobs, two at a time): about 40 minutes if Dukascopy answers
    (10-06 healthy years: 10-16 min per 12-month job). About 105 minutes if 2026 throttles as it did on 10-06 (89 min
    for 10 months). Worst case about 1,100 minutes, if every month throttles as on 10-10 (259 min for 4 months), with
    the 2026 job stopped at its 340-minute timeout and publishing nothing. Data volume: 7 year files re-uploaded with
    `--clobber`, tens of MB. The repo is public, and standard GitHub-hosted runners are not billed for public repos.
    The owner should confirm the "2,000 free minutes" premise in G5 on the billing page.
  - Next (program director): security review of the workflow diff, then the PR, then the dispatch after the PR is on
    `main`, by Fri 2026-11-06. Then the data-engineer reads the day-by-day coverage issues for 2020-2026 and reports
    which days remain.
