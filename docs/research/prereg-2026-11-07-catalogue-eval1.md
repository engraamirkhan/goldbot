# Pre-registration (DRAFT): the catalogue screen under EVAL-1, 2026-11-07

Status: WITHDRAWN (quant ruling (c), 2026-11-07); never registered.

Previous status: **DRAFT, not registrable.** Nothing in this file has been run and no registry row exists for it. It becomes
binding only when the quant-reviewer signs section 12 and the blockers in section 1 are cleared in writing.
Owner of the text: research director. Signs: quant-reviewer. Runs: strategy-researcher. Verdict: evaluator.

Reading rule: `docs/research/prereg-2026-11-07-eval1-reading-rule.md` (binding; this card adds nothing to its gates).
Amendment: EVAL-1, `docs/research/evaluation-audit-2026-11-07.md` section (b), branch `research/eval-audit-1107`
(commit 95a03b92), with the quant changes C1-C6 and the harness rulings C7-C10 (`eval1_signoff_1107`).
Closed predecessor: WS-3, `docs/research/prereg-2026-11-07-catalogue-screen.md` (0/79 at explore; section 8: no
re-run under that card). Unit source: `docs/research/catalogue-units-2026-11-07.json` (specs sha256 87975f06...).

## 1. Blockers the signature must clear first (written by the research director)

This card asks to bring 72 of the WS-3 units back as new K rows. Three written rules stand against it. They are
stated here so that the signature is an explicit decision, not an oversight.

1. **EVAL-1 section 7:** "The 79 units of WS-3 are not re-gated under EVAL-1 and do not go to CONFIRM (rule 3:
   closed line)." EVAL-1 (c) adds: "Re-opening this book as a 'new' line would be renaming a closed line (rule 3).
   It stays closed." The reading rule repeats it in its header. This card cannot be registered while that text
   stands. Clearing it needs an amendment to EVAL-1 section 7, signed by its signatories, that names the new
   mechanism evidence (rule 3).
2. **WS-3 section 8:** "The detectors may return only as point-in-time features under a new card counted in K."
   This card brings them back as strategies, not as features.
3. **The explore window has been seen under the new gate.** EVAL-1 (c) already published the EVAL-1 explore book
   numbers for the best units: S06-V1 net book t 1.80, S17-V2 0.68, S07-V1 0.69, and nothing passes E1. The gate
   was also written after the 0/79 was known. An EVAL-1 explore read on 2010-17 is therefore not
   pre-registered before the data was seen. What has not been seen is CONFIRM 2018-01..2025-09. The read-once
   ledger (`state/catalogue_reads.jsonl`) holds only one entry, `explore` at 09:38 UTC (head 7f075e36), and no
   `confirm`. I re-derived that on the Mac copy. The run code also refuses a second explore read
   (`run._claim`), so this card needs a code change to read explore at all.

What is new since WS-3: harness defects D1-D4, C7 and C10 are fixed, and measured costs replace the 3x fallback
(`measured_costs_1107`, version measured-2026-11-07-v1). The fixes are measurement repairs, and the WS-3 audit
showed that every defect pushed results up, so they cannot revive a unit. Measured costs can lower the cost, which
is the (A)/(B) sensitivity that EVAL-1 (c) already showed and still failed DSR at K = 79. That is not new mechanism
evidence in the sense of rule 3.

**Research-director recommendation: do not register this card as it stands.** If the quant still wants a test, the
honest option is the CONFIRM-only variant in section 11 (Q1, option b). It uses the one window nobody has read, at a
result-blind unit list, and counts in K.

## 2. Cost-aware card (research rule 2)

| item | value |
|---|---|
| mechanism | none new. The units are the WS-3 catalogue rules (breakout, fake-breakout, pattern, calendar and premia). The best WS-3 explore books are long-index event and seasonal premia (S06, S07, S17), which E9 was written to filter |
| expected gross edge | best EVAL-1 explore book in (c): S06-V1, +0.059 R/trade net at 313 trades in 8 years. No unit passes E1 at current costs |
| measured cost per trade | measured-2026-11-07-v1: gold round trip 0.40 bp (ICM, 18 days); indices 0.30-0.93 bp plus swap markup of 5-6 %/yr per side; FX 0.75-1.64 bp. Spread samples are 3-5 days for every instrument except gold |
| trades per day | best units: about 0.15-0.3 per day, pooled |
| expected net £/day at demo size (0.5 % risk per R on £10k, £50 per R) | about £0-0.5 per account before the selection discount; about £0 after it |
| gross edge >= 2 x cost? | not shown for any unit. Under rule 2 this line would not be started |

## 3. Units

Rule (decided on the spec and the code path, before any return under this card):

- Start from the 90 registered WS-3 units (79 real, 11 null) in `catalogue-units-2026-11-07.json`.
- **Sparse (`goldbot/research/catalogue/rw_guard.json`, 9 units): 0 excluded.** None of the 9 (C08-V0, C08-V2,
  C08-V3, C09-V1, C09-V3, C10-V3, F10-V3, S22-V1, S24-V2) is among the 90. All 9 were in WS-3's
  `dropped_infeasible`. I checked this against the file. The guard's `failed` list is empty.
- **Missing 1m bars (quant C10, `dispatcher.unit_trades`): 18 excluded whole.** These units place stop or limit
  entries (sim kinds 1/2) on an instrument other than XAUUSD, and only XAUUSD has 1m bars (`data.FINE_INSTRUMENTS`).
  The list comes from the detector code and the specs:
  S20-V0, S20-V1, S20-V2, S20-V3 (NR7 OCO stop); S21-V0, S21-V1, S21-V2, S21-V3 (stretch OCO stop); S22-V0, S22-V3
  (squeeze OCO stop); S23-V1 (soup plus-one stop); B04-V0, B04-V3 (round-number buy stop); B05-V1 (retest limit);
  F05-V0, F05-V2, F05-V3 (80-20 stop); C09-V2 (ABCD limit at D).
  The run's `excluded_no_1m` list must equal this list. If it differs, the run stops before any result is written.
- **Screened: 72 units = 61 real + 11 null.** The 61 real units are:
  S02-V0 S02-V2 S02-V3 S04-V0 S04-V1 S06-V0 S06-V1 S06-V2 S07-V0 S07-V1 S08-V0 S08-V1 S08-V2 S11-V0 S11-V1
  S11-V2 S15-V0 S15-V1 S15-V2 S16-V0 S16-V1 S17-V0 S17-V1 S17-V2 S22-V2 S23-V0 S23-V2 S27-V0 S27-V1 S27-V2
  S30-V0 S30-V1 B04-V1 B07-V0 B07-V1 B07-V3 F02-V1 F03-V0 F03-V1 F03-V2 F03-V3 F07-V0 F07-V1 F07-V2 F07-V3
  F08-V0 F08-V1 F08-V2 F09-V0 F09-V1 F09-V2 F09-V3 C02-V0 C03-V0 C03-V1 C04-V0 C04-V1 C04-V2 C05-V0 C05-V2
  C10-V1.
  The nulls are the WS-3 eleven: NULL-RAND-5m, -15m, -30m, -1h, -4h, -1d, -1w, NULL-LONGCLOSE-US500, -IDX, -GOLD,
  and NULL-XS-RANDOM. They are counted in K and never promoted. The void rule is the same as WS-3 section 8 /
  EVAL-1 section 4, with k recomputed on the feasible nulls at the event-count pass, before signing.
- **Do the excluded units count in K?** Their WS-3 rows stay in K permanently. Under this card they get **no new
  row**, because no trial runs on them. This follows the WS-3 section 7 precedent ("not run, no K"). They are
  listed in the run output. Question 11.4 asks the quant whether to register them anyway, which is more conservative.

## 4. K and the trial budget (ADR 0073: 2026Q4 cap 3,500, owner)

I re-derived these on the Mac registry copy (`state/research_registry.jsonl`, read only):

| item | value |
|---|---|
| registry K quoted for WS-3 (`k_registry_after`) | 3,263 |
| Q4 rows by the enumerator's own count (`run.q4_used`: preregistration plus result rows) | 3,353 = 3,173 + 90 WS-3 preregistration rows + 90 WS-3 explore result rows |
| this card | 72 units |
| reserve for WS-5/6/7 (`RESERVED_OTHER_WS`) | 12 |
| **K before / after, one row per unit (caller's convention)** | 3,263 -> 3,335 |
| **K before / after, enumerator's row count** | 3,353 -> 3,425 at enumerate; 3,497 once the 72 result rows are written |
| `assert_budget` at enumerate: 3,353 + 72 + 12 | 3,437 <= 3,500: passes, headroom 63 |
| conservative (2 rows per unit, ADR 0073 "pre-registration rows plus registry K"): 3,353 + 144 + 12 | 3,509 > 3,500: **over by 9** |

DSR and promotion use the larger K, 3,425 (code: `k_eff_confirm` = max(explore K, `n_trials_effective`)).

**Pre-stated cut rule (written now, blind to results).** If the budget the quant signs (Q3) does not fit, drop real
units in ascending order of their WS-3 `config_hash`. That hash is a hash of the spec, fixed at WS-3 enumerate
before any return, so the order carries no result. Drop the fewest units that make it fit. Nulls are never cut. The
order for the first 8 is F03-V3, S23-V0, C03-V0, S30-V0, S02-V2, C04-V0, S16-V0, S11-V0. Under the conservative
reading the first 5 are cut, giving 67 units = 56 real + 11 null, K 3,353 -> 3,420 (rows 3,487 + 12 = 3,499). Raising
the cap is owner-only and is not proposed.

## 5. Windows (W2, frozen 11-02)

| window | span | use |
|---|---|---|
| explore (learn) | decisions 2010-01-01..2017-12-31 | E1-E9 and the twin kill (reading rule section 2). Already seen: see section 1, item 3 |
| learn_pre | 2003-05..2005 | E7 same sign where n >= 60 |
| TEST-OLD | 2006-02..2009 (incl. 2008) | CONFIRM passers only; E9 beta > 0.3 must be net >= 0 |
| CONFIRM | 2018-01..2025-09, read once, BH q = 0.10 over the m explore passers | never read by any catalogue card (ledger checked) |
| holdout | 2025-10-01..2026-09-30 | sealed. The loader raises `HoldoutSealed`; no bar at or after 2025-10-01 is loaded |

## 6. Cost model and the freeze rule

- Cost model: **measured costs**, version measured-2026-11-07-v1 (sha256 f31bd09507e6), as built into the sim by the
  costs builder on `fix/catalogue-eval1-gates`. Cost commit: `<to be filled: the costs builder's final commit>`.
  Financing follows C2 (benchmark differential plus a markup >= 0, never a credit; triple swap on the broker's
  weekday). Where no measurement exists, the conservative fallback stays. BTCUSD swap is MISSING: if a BTCUSD trade
  occurs, the run uses the fallback and says so. 1.5x stress is reported at explore and gated at CONFIRM.
- **Freeze rule:** `harness_commit` = the costs builder's final commit on this branch. It is recorded in this file
  and in the new units file **before `run.py enumerate`**. Enumerate refuses if `catalogue/` differs from it
  (`run.code_matches`). Any later change to `catalogue/`, `specs.yaml` or the cost table voids the card. It is not
  patched.
- Sizing (C4): one rule, fixed fractional 0.5 % of equity per 1 R (`RISK_PER_TRADE`). The CONFIRM drawdown bound
  (<= 8 %) is read at this sizing, and no later projection may exceed it.
- NW lag (C3): L = max(5, ceil(1.5 x median holding calendar days)) per unit. It is computed at enumerate from the
  WS-3 event specs (hold caps), written into the units file, and fixed.

## 7. Decision rule (decided before any return)

- **0 explore passers, or 0 CONFIRM passers:** the line closes the same day. It is not re-run, widened, renamed or
  re-costed. These 61 detectors then cannot return as strategies under any later card. They may return only as
  point-in-time features (WS-3 section 8).
- **Void:** the null rule or any NULL-LONGCLOSE sentinel passing explore voids the run, and nothing continues.
- **A unit passes explore:** it goes to CONFIRM once (BH q = 0.10 on the one-sided block-bootstrap p of the daily net
  book; >= 5/7 years net positive; net > 0 at 1.5x; max drawdown <= 8 %), then TEST-OLD. Promotion to
  `live-candidates.md` needs DSR >= 0.95 at registry K_eff on the CONFIRM net series.
- **A unit passes CONFIRM and TEST-OLD:** WS-5 gives it one demo learning arm and one matched random control on the
  same account (matched by day, session and side), routed by the allocation rule. Kill: mSPRT futility, or
  net-negative against the control at 300 fills or 45 days, whichever comes first. Demo reads only kill; they never
  promote. Lot-fit failures are listed, never resized. Real money remains owner-only.
- **A unit passes CONFIRM and fails TEST-OLD:** it is retired with both numbers.

## 8. Budget, owner, dates

One run on the Mac at 8 workers, after the costs commit and the signature. Budget: the units in section 3 (72, or 67
under the cut) and no second run. Owner: strategy-researcher. The evaluator re-derives the verdict independently,
on the day of the run. Kill date: the run day.

## 9. Open point from the quant: a sharper E9 sentinel walk (proposal; seeds fixed here)

Problem (quant G2): `tests/test_catalogue_random_walk.py` uses drift 0.0005 per day on 1d walks with a daily vol of
1 %. That is a daily SR of 0.05, or a gross t of about 2.2 over about 2,000 decision days, before costs and before
the 1-bar hold's round trip. The buy-every-close sentinel therefore never passes E1. So "it fails E9" says little:
E9 is only exercised on units that are already dead.

Proposal (test-only synthetic units: no market data, no registry rows, not in K):

| item | value |
|---|---|
| drift | log 0.002 per daily bar (daily SR about 0.2, gross t about 9 over 8 years). Sensitivity row: 0.001 (t about 4.5) |
| sentinel S-hold | long at every 20th daily close, held 20 bars, exits on time only, no stop inside 3 ATR. Always in the market, so its gross t is about 9 and E1 passes |
| partial-exposure P | long at a seeded random close with p = 0.05, held 20 bars (about 60 % of days in the market, no timing skill) |
| positive control Q | as P, but entered only when the realised next-20-bar drift-free return is > 0 with prob 0.6 (seeded look-ahead, test only). It has timing skill, so E9 must let it pass |
| seeds | `E9S_SEEDS = tuple(range(100, 120))` (20 walks, disjoint from G2's 0-19); keys `_seed(uid, seed, "e9s", inst)`; bars 2,400; years 2011-2018; same cost model as the run |
| assertions | (1) power: S-hold passes E1 on >= 18/20 walks, otherwise the test is uninformative and fails. (2) S-hold passes E9 on 0/20. (3) P: share passing both E1 and E9 <= 5 % (at most 1/20). (4) Q passes E9 on >= 10/20, so E9 is not a blanket kill |

The quant decides drift, hold and thresholds. Once signed, the values are frozen and are not tuned after the first run.

## 10. Data rules

These are unchanged from WS-3 section 4 and the harness fixes. The fill bar of a gold stop or limit entry is resolved
from 1m bars (C10). A fill is never dropped (C7). A trade booked to both OCO legs is side 0 (C8). A missing twin is
NaN (D4 ruling). Every join is an as-of join on `available_utc`. `n_events` and trade n are reported separately.

## 11. Open questions for the quant's signature

1. **Can this card exist at all (section 1)?** (a) Amend EVAL-1 section 7 and WS-3 section 8, naming new mechanism
   evidence, then run as written. (b) **Recommended if anything:** CONFIRM-only. No explore re-read. A result-blind
   list (for example the 61 real units, BH over all 61, or a pre-named subset with stated reasons) reads CONFIRM
   once at the EVAL-1 CONFIRM gates. Power is lower at m = 61, but it is the only unread window. (c) Do not run.
   The line stays closed, and the WS-3 detectors return only as features.
2. Are 3-5-day spread samples a "pinned measurement" that may lower a cost (EVAL-1 section 1)? Or does the fallback
   stay for those instruments until 20 days are measured?
3. Budget reading: one row per unit (72 fit, headroom 63) or two (cut to 67 by the hash rule)?
4. Do the 18 C10-excluded units get registry rows under this card (more conservative K), or are they "not run, no
   K" as in WS-3 section 7?
5. E9 sentinel (section 9): drift 0.002 or 0.001; a 20-bar hold; seeds 100-119; the power assertion and positive
   control Q.
6. The read-once ledger: is a new window key per card (for example `eval1:explore`) acceptable, or does the explore
   window stay read once program-wide?

## 12. Signature

- Quant-reviewer: ______ (decision on 11.1-11.6)
- Harness / cost commit recorded before enumerate: ______
- Registry rows written: none yet. Holdout untouched. This draft adds 0 trials.
