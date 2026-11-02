# Rethink lane OWNDATA: what our own evidence says (performance analyst, 2026-11-02)

Read: explore-window (2010-2017) per-trade outputs of sprint-200 trend/patterns/time (frozen scripts, exact parity),
published explore aggregates of every other lane, the brain demo learning ledger (read-only), the owner's two MT5 summary
reports. No confirm or holdout row opened by this lane.

## Numbers first
1. The cost convention decides the verdict: under frozen dollar costs 1 of 101 sprint-200 trend/patterns/time rules passes
   explore; under price-relative (bps) costs with day-summed t, 8 pass (TREND-03, -04, -16, -20, -24, -31, TIME-02,
   PATTERNS-19). Adopted ruling: amendment C1 (bps point costs, measured swap both sides).
2. The gross trend premium is real at book level: equal-risk book of all 33 frozen trend rules (no selection), weekly t:
   gross 4.30 (8/8 years), real minus leak-free twin 3.62, net at bps 2.71 (7/8), net at dollar cost -0.49 (2/8).
   Mean pairwise weekly correlation of the 13 gross-positive rules 0.13.
3. Shorts net more than longs (long swap + 2011-15 bear); 13-rule long book gross t 4.52 but net with swap t 0.55.
4. Patterns and time books: gross t 2.61 / 2.11, bps net ~0. Random 1m-mid brackets earn +0.03-0.05R gross: part of
   every gross figure may be measurement bias (H-OD3).
5. Pocket mining is noise: 22 of 2,106 slices net>0 with t>=2 (1.0% vs 2.3% null). Kept at 2% prior.
6. Demo: 2,132 learning closes over 11 trading days (exploration 1,024, control 453, cost probes 655); 1m 762, 5m 641,
   15m 62, 1h 11, 4h 1 — demo cannot inform 1h-4h candidates. Exploration +0.074R vs controls +0.012R, no family
   significant on day clusters. Cost probes -0.053R (measured spread + commission).
7. Owner book: profit factor 0.98 / 0.99, costs ~55% of the net loss; nothing codifiable; 0 candidates.
8. Contamination: confirm 2018-2025 is not clean for anything built on sprint-200 trend rules (TREND-03 and MLNET read it).
   The only clean test for those is TEST-OLD 2006-02..2009.

## Candidates
110 in `config/research/candidates/owndata.yaml` (RT-OWNDATA-001..110), all data-mined (fresh prereg, K+1 each):
books 8, single rules near W2 at bps 18, horizon rescue 8, one side 22, Friday long 1, inversions 16, slice pockets 11,
other near-misses 21, demo pockets 5. Expected confirm passes ~1-2 effective (correlated trend family).

## Hypotheses
- H-OD1: equal-risk book of the 33 frozen trend rules, bps costs, net weekly t >= 1.28 and >= twin on TEST-OLD.
- H-OD2: long side gross weekly t >= 2 on TEST-OLD and swap >= 50% of its cost (evidence for a venue decision).
- H-OD3: leak-free random brackets per lane geometry have gross within +/-0.01R of 0 on 2010-2017 (measurement null).
