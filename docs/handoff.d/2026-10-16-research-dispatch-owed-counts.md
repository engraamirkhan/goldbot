- **Q1 readiness corrected and the owed counts run (research director, 2026-10-16).** `macro-v1` has been published
  since 2026-10-13 (scheduled `data-macro` run 38394945106); the docs that said it did not exist are corrected.
  count-owed-events was dispatched on `main` (run 38931951849, success, commit `eafc6ce`). It needs no `calendar-v1`:
  the calendar rows come back "not counted". Q1 counts: H-04 4,025 entries (10 dropped by the 7h rule); H-01 2,154
  signals, lower bound 152 (>= 150, stays queued); **H-06 lower bound 107 < 150, withdrawn by its pre-registered
  rule**, so the 2027Q1 reservation becomes 8. The readiness list and the freeze checklist in
  `docs/research/preregistration-2027Q1.md` now give each item one accountable role, a date and a status.
  - Next (implementer, 2026-10-23): H-06 `withdrawn:` and `reservation: 8` in `preregistration-2027Q1.yaml` with
    `research.reserved_trials_quarter` 2027Q1 = 8 in `config/settings.yaml`, in one change; quant-reviewer checks.
  - Next (strategy-researcher, 2026-11-01): explain H-01's 152 vs 154 and H-04's upper bound 4,088 vs 4,087.
  - Blocked on the owner (chaser: program-director, by 2026-10-18): the `FRED_API_KEY` repo secret is not set, so
    `data-us-calendar` has never run and `calendar-v1` does not exist. Q2 needs it; Q1 does not. The Sunday 06:17 UTC
    schedule fails on 2026-10-18 without it.
  - Not dispatched: the first `preregister-quarter` dry run (read-only); this session's permission policy refused it.
    New date 2026-10-20 (research-director).
