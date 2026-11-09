# 0077: Ruling A withdrawn: daily units use the 300-event futility floor

Status: accepted, signed by the quant-reviewer 2026-11-09 (research director for PD + PO, 2026-11-09). Decided by PD + PO +
quant-reviewer; the owner is informed. Part of EVAL-2 (ADR 0076; method item 19 in section 8).

## Context
Ruling A (owner, 2026-10-11) let rules whose signal is on daily bars retire at 150 events on sign or t
(`config/settings.yaml` `screen_min_events_daily: 150`, read by `goldbot/research/screen.py` `min_events_for`; the
setting is declared in `goldbot/config.py`). Re-derived 2026-11-09: at sd 1.3 R the SE at n 150 is
1.3 / sqrt(150) = 0.106 R, so a sign-based retire at 150 throws away a true +0.05 R edge about a third of the time.
The experts' futility ruling of 2026-11-09 (evaluator YES with conditions, on the quant's recommendation) replaced
retire-on-sign between 300 and 999 events with a powered futility test, and referred Ruling A to EVAL-2 for the same
power test. It fails it.

## Decision
"`screen_min_events_daily: null`; daily units use `screen_retire_floor` 300 with the futility rule."

The futility rule (ruling 2026-11-09, all units, daily included). Between `screen_retire_floor` (300) and
`screen_min_events` (1,000) a screen retires a unit **only** if:
- (a) the one-sided 90 % upper bound of the statistic named in the card before the read (gross mean R, or the
  difference against the control/twins), with day-clustered SE, is < +0.03 R (`screen_retire_ub_r`);
- (b) the clustered SE is <= 0.055 R (`screen_retire_max_se_r`; <= 5 % chance of retiring a true +0.05 R edge);
- (c) both were pre-registered before the read, and the read is counted in K.

Otherwise the result is "inconclusive (event floor)" and the unit goes to the forward read only. Never retire on
sign or net alone. A pass still needs >= 1,000 events plus P4; promotion still needs `research/gates.py`. Below 300
is "insufficient".

Settings (Lane 3 is the sole writer of `goldbot/config.py` and `config/settings.yaml`):
`research.screen_min_events_daily: null`, `research.screen_retire_floor: 300`, `research.screen_retire_ub_r: 0.03`,
`research.screen_retire_max_se_r: 0.055`. The futility branch lands test-first in `screen.py` `screen_verdict`
(signature frozen in `docs/research/prereg-2026-11-09-eval2.md` section 4.7).

## Consequences
- Daily units: tightening (150-299 become 'insufficient'; retire at ≥ 300 only by the powered futility test).
  Non-daily units: a new powered retire path at 300-999 (previously 'inconclusive'), with ≤ 5 % chance of retiring
  a true +0.05 R edge. No pass path changes.
- SE <= 0.055 needs n about 560 at sd 1.3 R and about 800 at sd 1.56 R, so most 300-559-event reads will be
  "inconclusive (event floor)" and route forward. That is the honest answer, not a defect.
- Units already retired under Ruling A stay retired (no re-run); reopening one needs new mechanism evidence, a new
  card and a K row (rule 3).
- The KoG card constants are updated to these keys before its R1 read.
- No owner-only item is touched. Owner information line for #34: "The 150-trade daily shortcut is withdrawn: a daily
  strategy is now dropped only when 300+ trades show it is clearly worthless."
