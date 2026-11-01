# H-89 post-FOMC continuation: confirm result (2026-11-01) — FAIL, retired

Pre-registration `prereg-h89-fomc-2026-11-01.md`; code `scripts/research/h89_fomc_confirm.py` frozen at commit
`a5f413b0` (pre-registration and code committed together before the run). One look at 2018-01-01..2025-09-30;
no 2025-10+ bar or event loaded (asserted in code). Output (statistics only, not committed): `~/tmp/h89/result.json`.
Process note: the first invocation computed the result but crashed writing the output (missing directory) before
printing anything; the identical deterministic run was repeated once to see it. No parameter was changed.

## Result (net $/oz per trade: bid/ask fills + $0.08 commission + $0.30 slippage margin)
| | Value | Rule | Leg |
|---|---|---|---|
| n | 62 (0 skipped; 2025 has 6 meetings) | | |
| mean net | **-$1.23** | > 0 | fail |
| t | **-0.82** | >= 2 | fail |
| years positive | **3 of 8** (2018, 2021, 2024) | >= 5 | fail |
| share net > 0 | **50.0%** | >= 55% | fail |

Informative only: median net +$0.45; mean net with the top 5 events removed -$2.74. Per-year mean net (n):
2018 +0.33 (8), 2019 -0.21 (8), 2020 -1.67 (8), 2021 +2.95 (8), 2022 -4.82 (8), 2023 -1.70 (8), 2024 +1.81 (8),
2025 -8.26 (6).

For comparison, the same frozen script on 2010-2017 (explore, already seen): n 64, +$3.00, t 3.12, 7/8 years, 70%.

## Verdict
**FAIL on all three legs. H-89 is retired, final** (pre-registered rule). The 2010-2017 drift does not persist into
the press-conference-every-meeting era. The release-minute cost arm (decision doc line 2) loses its purpose as a
gate for H-89; whether it still runs as a pure cost measurement is the research director's call. Do not re-test
post-FOMC continuation on another window, entry minute, horizon or event subset without a new mechanism or data
source. The evaluator confirms the verdict against the pre-registered rule.

## Multiple-testing count
- Explore look recounted: K 30 (14 event types x 2 directions + 5m signal window + 60m hold), not 15.
- This confirm: 1 registry trial, K +1. Ledger ~2,971 (before the memo) + 30 + 1 = **~3,002**; survivors 0.
- Registry: one `preregistered` + one `evaluated` row (family `h89_fomc_continuation`, budget quarter 2026Q4) in the
  local sidecar `~/tmp/h89/research_registry.h89.jsonl`. **Owed:** union into the release registry via
  `registry_sync` (the Mac publish path of PR #243 once merged; the brain upload has the open 403 item).
