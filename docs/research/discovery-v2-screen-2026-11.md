# Discovery grammar v2: explore runs on 1h, 4h and 1d (result; research-director bet 1, week 2026-11-02)

Principal quantitative strategist, run 2026-10-31 on the owner's Mac. Rule frozen in
`prereg-2026-11-02-discovery-v2.md`, committed at **`513f088c`** on top of the code commit **`2ccbc8e5`**, before any v2
output existed. The driver `scripts/research/autodisc_v2.py` ran unchanged from that commit, once per timeframe, in the
order 1h, 4h, 1d. Inputs: `raw/xauusd_1m_dukascopy_2010..2017.parquet`, FRED first releases (`macro_fred.parquet`) and
calendar-v1 (`us_calendar.parquet`), each cut at 2018-01-01 on available_utc. All three are local only and none is
committed. Outputs, also not committed: `~/tmp/autodisc-v2/` holds the ledger, the reports `autodisc/survivors-v2-*.md`,
the JSON summaries and the logs. The ledger's hash chain verifies (33,268 entries, head
`3e527ebfb45b...`). No registry trial was used, the holdout is untouched, and no 2018+ row was loaded.

## 1. Verdict

**FAIL: 0 explore survivors on all three timeframes.** Under the pre-registered rule (A4 item 5), automated
generation **freezes on 2026-11-20** until the Q1 state-of-the-art refresh. There is no v2.x, and the nightly job
stays off. H-85 is retired as R-18. Its gates stay available to hand-written pre-registrations, which pay their own K.

| Timeframe | Specs (K) | Floor passers | Null bar (95th pct of best-of-search) | Real best fitness (null pct) | SPA p | PBO | Candidates | Survivors |
|---|---|---|---|---|---|---|---|---|
| 1h | 288 | 113 | 1.430 | 0.985 (0.70) | 0.044 | 0.35 | 0 | 0 |
| 4h | 288 | 96 | 0.903 | 0.325 (0.20) | 0.479 | 0.62 | 0 | 0 |
| 1d | 288 | 17 | 0.881 | -0.123 (0.20) | 0.845 | 0.51 | 0 | 0 |

- **Nothing beat its own search null.** In every timeframe the best real fitness (the min-fold t of excess net R)
  was below the null bar, and the batch gate failed as well:
  - 1h: SPA passed (0.044 <= 0.10), but PBO 0.35 > 0.30;
  - 4h and 1d: both SPA and PBO failed.
- **No spec reached the judging stage.** None had to be checked against the shift null, the plateau test or the
  DSR, and "beat the null but failed an explore check" is 0 everywhere.
- **The net-R floor did not decide anything.** It never had to remove a spec that had beaten the null.
- **The search was no better than its null.** v1's 1h best was 0.841 against a bar of 1.337, and its 4h best was
  0.692 against 1.019. v2 moved the 1h best up and the 4h best down, and neither reached the null bar.

## 2. What the ledger says (recorded results, re-read; no new evaluation)

Own net mean R of the floor passers at the cost of record:

| Timeframe | Median | Max | Count >= +0.05 R | Count > 0 |
|---|---|---|---|---|
| 1h | -0.172 | +0.322 | 8 of 115 | 11 |
| 4h | -0.158 | +0.148 | 3 of 98 | 6 |
| 1d | -0.241 | +0.007 | 0 of 21 | 1 |

The cost of record is the measured cells: about 0.17 R a trade at 1h, 0.09 R at 4h and 0.04 R at 1d, plus the long
swap prior.

- **Typical cost against typical edge.** The median spec loses about the round trip, so gross is close to zero.
  That is the same picture as every hand-written family.
- **The closest miss (a recorded fact, not a lead).** It is the 1h spec `8c57acaa1349`:
  - Rule: Asia session; GVZ in its top tercile; side against the 4h return; time exit at 48 bars.
  - Result: n 557, net +0.322 R, excess +0.451 R, +0.231 R at 1.5x cost.
  - Fitness 0.867 against a bar of 1.430: the fold t's are uneven, so the mean comes from part of the sample.
  - It was picked as the best of 288 searched specs. With 5,331 null specs behind the bar, its in-sample mean is
    what selection produces.
  - Under the frozen rule it does not advance. Re-testing it as a hand-written rule would need new evidence and a
    new pre-registration, and it would pay the DSR at K 2,968.
- **The other top-fitness specs have negative own net R.**
  - 1h: -0.09 R (`level_break` in Asia, 2R target).
  - 4h: -0.16 R (long in Asia on a Hurst/GVZ gate).
  - They beat drift (excess +0.10 to +0.13 R), not zero. This is the cost-crushed-null pattern A4 named, and the
    net-R floor would have stopped it.
- **The 1d timeframe is thin.** It has 2,490 bars, a 10-bar time barrier and one position at a time, so only 21
  specs reach 100 events. No 1d spec could reach the 200 events that the confirm DSR needs.

## 3. Multiple-testing count

- **K_v2 is 1,704 as declared before the run.** The ledger's actual count is 864 (288 a timeframe): no candidate
  was judged, so no grid neighbours were evaluated.
- **Program K goes from 1,264 to 2,968**, at the declared figure (conservative). The null specs, 15,770 cumulative,
  are K_null only.
- **Local registry sidecar** (`~/tmp/autodisc-v2/research_registry.discovery.jsonl`): v1 prior row 864, plus three v2
  batch rows of 288 each.
- **Owed by the release-manager:** the brain registry's discovery sidecar has no v2 rows. This is the open
  `registry_sync` 403 item.

## 4. Consequences and what would change the verdict

- **Generation freezes on 2026-11-20 (A4).** Discovery returns to the Q1 state-of-the-art refresh. The 2027Q1
  allocation of 6 discovery-confirm slots (ADR 0058) should be released to hand-written hypotheses. That is a
  research-director/PD decision, flagged here and not assumed.
- **Retired as a search configuration** (R-18): bounded G2 search over the v1 vocabulary plus these 13 gates, the
  r4h side and these 5 exits, on 1h/4h/1d.
- **Not retired:** the gates as features. They stay in the code base (`grammar_v2.py`, PIT-tested) for any
  pre-registered hypothesis that pays its own K.
- **What would justify another generation after the Q1 refresh.** A new information source not in v1/v2: order
  flow or positioning (COT), ETF flows, or the TradingView author calls if T-1 passes. More search over the same
  bar-derived and macro inputs would not justify one.
- **Owner-only matters:** none changed. No budget, sizing, instrument or live decision is taken or implied.
