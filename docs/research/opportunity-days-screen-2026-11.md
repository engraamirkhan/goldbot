# H-16 opportunity-score screen on 2010-2017 (A3): result

Run 2026-10-30 by the opportunity-researcher. Frozen rule, code and inputs:
`prereg-2026-10-30-h16-screen.md`, committed **before any output** at `1d25461d` (code `h16.py` code hash
`d5fa78b00239`, `day_type` feature version `f-52c853166b`, driver `scripts/research/h16_screen.py`). Bars
2010-01-01..2017-12-29 only (Dukascopy 1m -> 15m mid); every input cut at 2018-01-01; no 2018+ data and no holdout
was loaded. Not a registry trial; nothing was recorded in the registry. Outputs (ratios only, no prices) stay outside
the repo (`~/tmp/h16-screen/result.json`).

## Frozen rule (restated)

Pass iff pooled OOS rho(full, y) - rho(HAR, y) >= 0.05 **and** the full score's top-quintile mean W/A >= 1.5 x its
bottom quintile's. Fail: GVZ, tier-1 events and compression/gap are retired as range inputs, the day factor uses HAR
alone, and the formal O-1 trial on 2018-01..2025-09 is cancelled. Floor 1,200 evaluation days.

## Read

| Statistic | Value | Bar | Holds |
|---|---|---|---|
| Evaluation days (first OOS month 2012-03; 0 dropped) | 1,506 | >= 1,200 | yes |
| rho(full, y) | 0.286 | | |
| rho(HAR, y) | 0.085 | | |
| **rho gain** | **+0.201** (block-bootstrap 95% [0.145, 0.260]) | >= 0.05 | **yes** |
| **Top / bottom quintile mean W/A (full)** | 1.074 / 0.748 = **1.437** | >= 1.5 | **no** |

**Verdict by the frozen rule: FAIL** (second leg: 1.437 < 1.5).

Reported, not in the rule:
- Drop-one: GVZ/YZ20 +0.098, tier-1 count +0.071, gap +0.005; prior range, NR4, NR7, inside day each about 0
  (-0.004..+0.000). The gain is GVZ and events; compression adds nothing (H-20 practitioner claim, no support here).
- rho gain by year: +0.23, +0.16, +0.15, +0.20, +0.17, +0.40 (2012..2017), positive in all 6.
- HAR-only quintile ratio 1.165: neither model separates days by 1.5x in ATR-relative terms.
- Capture (realised best quintile in the score's top two quintiles): full 55%, HAR 39% (chance 40%). Poorest
  quintile in the score's bottom two: full 59%, HAR 48%.
- Window range in round-trip-cost multiples, median by full-score quintile: 17x / 14x / 15x / 16x / 18x at $0.85/oz
  (58x..63x at $0.25). Every quintile's median day moves well over the cost; the range dimension alone does not
  identify days to stand aside on cost grounds, and the USD profile is U-shaped because the target is ATR-relative.

## Consequences (by the rule; no re-read, no rule change: quant condition 2)

1. GVZ, tier-1 events and compression/gap are **retired as H-16 range inputs**; the day factor (BACKLOG 30b) uses
   HAR alone. The formal O-1 trial on 2018-01-01..2025-09-30 is **cancelled**; its sample floor (stated in the
   pre-registration: >= 90% of the counted `expected_eval_days`, never below 1,700) is moot.
2. H-17 is unaffected (it conditions on the HAR baseline only) and may proceed to its own screen.
3. **K +1** (screen, counted, not a registry trial). The trial budget and the holdout are untouched.

## Researcher's note for the research director (not a verdict change)

The rule's two legs measured different things and only the incremental one was about the inputs. GVZ and events
raised rank information by 0.20 rho in every year, but the conjunctive ATR-relative quintile bar, which HAR itself
misses by more (1.17), retired them. The retirement stands. Any return of GVZ or event counts as day-selection inputs
needs a **new pre-registration with a new id**, on a target tied to money (for example net R of a fixed random
bracket by score quintile, or USD range in cost multiples), not a re-read of this one. Recorded in `hypotheses.md`
H-16 status.
