# Sprint-3000 pre-registration amendment C1 (with W2)

Written 2026-11-02, after the catalogue (`config/research/sprint3000_catalogue.yaml`, sha256 `8a766201856237d4d96511f44bd053c7980fcfbf95582affe0c2d39ff2614c1e`) and **before any explore run**. No sprint-3000 spec has been scored on any real window. Engine tests and timing used synthetic bars and a separate 20-spec fixture catalogue; those fixture specs count in no K. This amendment **supersedes section 7 of `docs/research/sprint3000/prereg.md`** (ruling f).

The frozen thresholds are in `config/research/sprint3000_rules.yaml`, sha256 **`76630aa991a039f751ea92c6d12ad1561adc9bc4db8c7c54adac4978cc9be1ac`**. Every screen report records this sha256, and a later window refuses a report scored under any other sha256 or by any other engine (`engine_sha256`).

## Rulings (quant review of PR #286)

a. **Overshoot.** Overshoot is charged only on stops filled at the stop price. A stop that gapped is filled at the minute's open, so it pays no overshoot on top; the open fill already carries the slippage. This was accepted by the quant reviewer. Every result reports `gap_share`, the share of trades whose stop filled at a gap open, and `reopen_fill_share`, the share of fills within 5 minutes of a reopen after a gap of at least 30 minutes.

b. **learn_old (2003-01-01..2005-12-31).** learn_old is a REPORT-ONLY side window for sprint-3000. It is never a gate and is never used to trim passers. Its results carry verdict `REPORT-ONLY` and `passed: false`, the null-control VOID rule does not apply to it, and no confirm or test_old run accepts a learn_old report as its passer source.

c. **Explore warm-up.** The explore window reads from 2009-01-01 for indicator warm-up only. No trade decides before 2010-01-01; the explore entry window is unchanged. On 2026-11-02 there is no 2009 Dukascopy file on disk (`raw/` begins at 2010). Until that file exists, explore warms up inside 2010: the long indicators (vol_regime's 250-day rank, the 1d EMA 100/200 trend filters, compression's 250-bar rank) cannot fire on early-2010 decisions. **That loss of power is accepted in advance.** If the 2009 file is added later, it is used for warm-up only, by the same rule. Each report's `data_first_utc` records which case applied.

d. **Ensembles.** Null controls (grade D: inside_bar_break, session_range_break, opening_range_break, level_reject, and every `mode: fade` entry) never join a family ensemble. They form `ENS-NULL-<family>` by the same membership rule, as a control. At confirm the null ensembles are read as their own BH family, so a control never moves a candidate's threshold, and they are not counted in the candidates' m. If any null single or null ensemble passes confirm or test_old, the report sets `audit_required`, and the next window refuses that report until the engine has been audited.

e. **Level specs.** The 379 specs that use the ADDENDUM A level types (level_break, level_reject, near_level, room_to_level) form a **separate later batch**, run after the KEYLEVELS lane registers those types. That batch has its own ensembles and its own BH family. In this batch's reports they show as `not_evaluable`; they are never dropped and never mixed into this batch's m.

f. **Supersession.** This amendment, with W2 and C1 below, supersedes `prereg.md` section 7 (its pass rules and reading rule). Section 7 rule 1, the null-control rule, is kept as amended in (b) and (d): VOID on explore only, and an audit at confirm or test_old.

## One read per window (quant review H2, H3, M1)

- Confirm and test_old write a marker to the read ledger (`state/sprint3000_reads.jsonl`) before any of their bars is read. A second run of the same window on the same catalogue is refused unless its plan hash is identical, which covers a crashed run's exact repeat.
- Those windows refuse `--ids` and `--limit`, and refuse to run from an engine with uncommitted changes.
- A report that requires an audit, or that was scored under other rules or by another engine, is refused.
- Run from the main checkout, not a worktree, so the ledger outlives the worktree.

## Trials and K (quant review H1)

- `python scripts/sprint3000_screen.py --preregister` writes one PRE-REGISTRATION row per spec (3,000), plus a candidate and a null ensemble per family, into `state/research_registry.jsonl` before the first score. Pre-registrations are not trials and take no budget slot.
- At run time, K is read from the registry: `n_trials_effective` plus every planned sprint-3000 row not yet run, so every spec counts whether it fires or not. The rules file's `k_program` (6,233) is used only on a host with no registry, and the report says so (`k_source`).
- **Trial budget: owner decision, pending.** The coordinator relayed the owner's chat directive of 2026-11-02 as: "now find ranked 3000 and test those". The quarterly trial budget (`research.trial_budget_quarter`, at most 500 by its validator) and any override of that cap are owner-only settings. This amendment and its PR do not change them, and do not record the budget as approved. The owner records that decision in `config/settings.yaml` (or an ADR) before the result rows are written as trials.

---

## Verbatim: amendment C1 (`c1-amendment.md`)

````text
# Amendment C1 (adopted 2026-11-02 on the independent audit; supersedes the cost and control parts of W2; frozen before any re-score)

Audit finding: "0 of 3,330" is mostly a process artefact. Power for a real +0.10R net edge under the old rules: 1%.
Top flaws: (1) costs charged in TODAY's dollars against 2010-17 dollar stops (gold $1,160-1,670 then vs ~$4,000 now; 1h ATR in
bps is stable 15-26 bps every year) + the 90% upper bound + long swap -60 / short 0 => net understated ~0.05-0.10R/trade at 1h-4h;
(2) confirm deflated at program K (needs t>=3.6 OOS); (3) same-side "leak-free" twin + 0.05R hurdle rejects pure directional
(TSMOM-type) edges by construction; (4) no ensemble stage; (5) horizon/venue mix and a single regime split.
Demo check: realised spread median $0.08-0.10/oz + commission $0.080 matches cor RT $0.166 (screens charged $0.23, +39%).

## C1 cost model (all research screens, all windows)
cost_R = (RT_point_bps x P_entry + OS_point_bps x P_entry x [stop or trail exit] - nights x swap_bps(side) x P_entry) / stop_usd
- RT_point_bps, OS_point_bps: the cost of record's ICM POINT estimates (not the upper bound), converted at reference price 4,000.
- swap_bps(side): measured broker terms (state/broker_terms_icm-demo.json today: long -52.2, short +36.0 USD/lot/night,
  i.e. ~-1.3 / +0.9 bps a night). Shorts are CREDITED.
- No double count: if fills already gap to the minute open, the overshoot term is the measured residual only.
- Dollar upper bound kept as a stress column (reported, not gated).
Test: same ATR-multiple stop in 2012 and 2025 -> same cost_R within 5%; an overnight short has a positive swap.

## Control (gate)
Null = random-SIDE twin at the same minute with the same stop/exit, or drift-adjusted zero (subtract side x in-window drift x
hold); pass needs excess >= 0. Same-side twins are an "entry-timing" diagnostic only. Test: planted 0.2R side edge with random
timing passes.

## Process
- Explore (LEARN 2010-17; +2003-05 for new trials): day-summed net R > 0, t >= 2.0, >= 5/8 years positive, drift-adjusted gross > 0,
  random-side control excess >= 0, n >= 100 (60 for 4h/1d); long and short legs reported separately.
- Ensemble stage: per family an equal-weight composite of its explore passers + members with gross t >= 2, frozen before confirm,
  one trial per family.
- Confirm (2018-01..2025-09, once): BH-FDR q=0.10 over all families + singles sent, net > 0, >= 5/8 years, n >= 100; ex-2025 and
  drift-adjusted gross reported. NO DSR term at confirm (DSR at K_eff reported on explore).
- TEST-OLD 2006-02..2009 once: net > 0, t >= 1.28.
- Demo: every qualifier at 0.01 lot within 48 h, as a cost/fill check; pre-registered kill at 100 trades (realised cost <= 1.5x
  C1 model, net R >= -0.10); read as performance only after 300 trades.
- Re-scoring past EXPLORE results under C1 with the same frozen rules adds no trials (declared). Windows already read stay read:
  S200-TREND-03's confirm is used, so it goes to TEST-OLD only.
- Live gate (DSR >= 0.95 at real K) stays owner-only; recommendation: K_eff on pooled explore+confirm evidence.
````

## Verbatim: amendment W2 (`windows-amendment.md`)

````text
# Amendment W2 (supersedes W1; adopted 2026-11-02 on the statistics expert's memo; frozen before any pre-2010 bar or sprint3000 result is read)

## Windows
| window | years | use |
|---|---|---|
| LEARN (explore) | 2010-01-01..2017-12-31 | learn/screen |
| LEARN-OLD | 2003-01-01..2005-12-31 | extra learning data (new trials only) |
| embargo | 2006-01 | not used |
| TEST-OLD | 2006-02-01..2009-12-31 (incl. 2008) | held-out test, read ONCE, only for confirm-passers |
| CONFIRM | 2018-01-01..2025-09-30 | one test |
| HOLDOUT | 2025-10-01 onward | sealed |
A trade belongs to the window of its ENTRY; warm-up may read earlier bars.

## Costs (all windows, new runs)
Cost of record cor-2026-10-31.2 converted to price-relative terms: round trip and overshoot in basis points of the entry
price (dollar values / reference price 4,000), swap as % a year of notional (cor swap / 4,000 x 365). In-flight lanes keep
their frozen dollar costs and also report the bps version.

## Pass rules (sprint3000 and all new trials; in-flight lanes report both theirs and these)
- Explore: n>=100, net>0, t>=2.0 on DAY-SUMMED R, net minus leak-free control >= 0. Also report a DRIFT control (same side,
  same time of day, random other day within +/-20 trading days) — reported, not gated.
- Confirm: m = the number of explore passers, fixed before the run. Pass = BH-FDR at q=0.10 on the one-sided day-block-
  bootstrap p of confirm net R, AND net>0, n>=100, >=5 of 8 years positive. Report DSR at program K and at K_eff, the
  result EXCLUDING 2025, and the drift-adjusted gross.
- TEST-OLD (qualification): n>=100 (>=60 for 4h/1d), net>0, t>=1.28, net minus leak-free control >= 0.
- Qualified (deploys to demo) = explore + confirm + TEST-OLD. If TEST-OLD data is missing: "old-test pending", no deploy.
- The live-money gate (DESIGN DSR>=0.95 at the real trial count) is unchanged; restating it is the owner's call.
- "DSR > 0" in earlier briefs meant DSR >= 0.5.
````
