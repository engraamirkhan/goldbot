# Costs are binding: what they really are, what they are in R, and what follows (strategy-researcher, 2026-10-18)

**Brief (research director).** Several signals have a real gross edge, yet every one is net negative under the cost
priors. (1) Find what the real costs are and write the exact steps to build `costs-v1` from recorded broker ticks.
(2) Quantify cost in R by timeframe, stop, session and hour at measured ATR, and find the configurations under
0.03 R. (3) Propose cost-aware trials, outcome-blind. Pre-registrations: `preregistration-costs-2026-10-18.md`.

**Scope and attestation.** Inputs:
- Code at `a327699`.
- Registry snapshot `research-v1/registry.jsonl`, 62 rows, read 2026-10-18 about 07:10 UTC.
- Dukascopy 1m bars from release `data-v1`.

Only **cost and volatility** statistics were computed: bar spreads and ATR, no returns and no signals. The 2026 rows
are holdout-period descriptives of the same kind that `atr-measured-2026-10-18.md` already reports. I read trials
#1-#44 as published, and the configurations of #45-#47 but not their results. No outcome of any configuration
proposed here has been computed or seen. This file costs **0 trials and adds 0 to K**. The scratch scripts
(`spread_hour.py`, `cost_share.py`, `cost_r.py`) are not committed. They are described in enough detail to rerun.

## Answer in five lines

1. **No broker cost has been measured yet.** Every trial so far charges the priors: the Dukascopy bar spread, plus
   0.37 $/oz for slippage and commission, plus a −60 USD/lot/night long swap. Release `costs-v1` does not exist.
   Two code paths would stop it from ever being published for the owner's **GBP** account (section 1.3).
2. **The cost in R in our research window belongs to a regime that is gone.** For a 1h 1.5 × ATR stop, the median
   cost was 0.17-0.18 R in 2010-2019, about 0.07 R in 2025, and is about 0.035 R today. Over 2010-2025 only 0.6% of
   1h bars, and essentially no 15m bars, were as cheap in R as today's market (section 2.2). So "net negative in
   2010-2025" mostly measures past costs.
3. **Under 0.03 R** at today's ATR, with market orders, needs a stop of about **$17-36/oz**. That is 1h ≥ 1.0-2.0 ×
   ATR, or 4h ≥ 0.5-1.0 × ATR, depending on the cost scenario. No 15m stop that the £1,000 account can size gets
   there. At the largest sizable stop ($7.98) the target needs a total cost of **≤ 0.24 $/oz**. Only a Raw spread
   near $0.10, plus limit entries and take-profits (almost no slippage), would reach that (section 2.1).
4. **Lower costs alone cannot make any known signal pass.** At K = 53 a pass needs a per-trade t of about 3.94.
   Take H-04 (gross +0.043 R, t 3.09, n 4,025) and tsmom #36 (gross +0.084 R, t 2.43, n 1,691) at **zero cost**:
   they would need +0.055 R and +0.136 R respectively, and their gross edge is below both. #16 (+0.061 R, t 2.63)
   would need +0.091 R. **Costs decide the sign. Deflation decides the pass.** (Section 3.)
5. **Recommendation.**
   - Spend **0 trials** on measurement now: build `costs-v1`, fix the GBP blockers, and record a broker spread
     profile by hour.
   - Spend **at most 1 trial** now on the cheapest 1h tsmom configuration (CB-T1). Hold 1 more as conditional
     (CB-T2, limit entry).
   - Do **not** re-test H-04: it cannot pass at any cost.
   - The decisive test of the current regime at today's costs is the **one-time holdout**, and it needs G2 first
     (due 2026-11-06).

## 1. What the real costs are, and how to build `costs-v1`

### 1.1 What research charges today (all priors)

| Component | Value charged | Source | Measured? |
|---|---|---|---|
| Spread | Dukascopy bar spread on every label (entry at ask, exit at bid) | `data-v1` bars, `goldbot/labels/triple_barrier.py` | Measured, but on **Dukascopy**, not on IC Markets |
| Slippage | 0.15 $/oz a side (0.30 a round trip) | `config/settings.yaml` `costs.slippage_prior_usd` | Prior ("conservative $0.15 until 50 fills") |
| Commission | 3.5 USD/lot a side = 0.07 $/oz a round trip | `costs.commission_per_lot_side_usd.icm` | Prior |
| Swap | long −60 USD/lot/night = −0.60 $/oz; short 0; ×3 on Wednesday | `costs.swap_*` | Prior |
| Total beyond the spread | **0.37 $/oz**, printed as `cost_source: "PRIORS ONLY..."` on rows #21-#44 | `execution.costs.settings_extra_cost_usd` | — |

The batch's cost-stress figure, "0.71 $/oz measured Raw" (`batch-2026-10-18.md` rule 2.6), is **not a
measurement**. It is 0.34 + 0.37: the 0.34 is the 2010-2026 normal-day mean Dukascopy spread
(`holiday-list-measurement-2026-10.md` line 32) and the 0.37 is the prior above. Its label in the batch file should
read "prior + historical Dukascopy spread". It does not change rule 2.6, which stays as written.

**Dukascopy spread has risen with the price** (median 1m close spread, $/oz, this study):

| Year | 2010 | 2015 | 2019 | 2022 | 2024 | 2025 | 2026 (to 10-05, descriptive) |
|---|---|---|---|---|---|---|---|
| Median spread | 0.51 | 0.31 | 0.29 | 0.37 | 0.38 | 0.58 | **0.70** |
| ATR15 median | 1.63 | 1.27 | 1.17 | 2.15 | 2.74 | 4.97 | 9.64 |
| Spread / ATR15 | 0.31 | 0.24 | 0.25 | 0.17 | 0.14 | 0.12 | **0.07** |

So the 0.34 in "0.71" is already stale. Today's research-basis round trip is about **0.70 + 0.37 = 1.07 $/oz**.
Spread grows much less than ATR, which is why the cost in R falls (section 2).

**IC Markets Raw: what is known without measuring**
- Commission: USD 3.50 a lot a side (7.00 a round trip) is published for Raw Spread accounts
  (https://www.icmarkets.com/trading-accounts/overview, read 2026-10-18). That metals pay the same rate comes only
  from a third-party copy of a spec sheet, so it is *(unverified)*.
- A GBP account is probably charged in GBP. The amount is *(unverified)*, and our code does not measure it
  (section 1.3).
- Raw XAUUSD spread: a single 2026 broker-review snapshot reports "0.03". Its method, time and units are unknown, so
  it is *(unverified, not usable)*.
- No peer-reviewed or audited source gives a retail CFD gold spread. **Only our own ticks can settle it.**

Cost scenarios used below, in $/oz round trip, all marked as scenarios:

| Scenario | Spread | Commission | Slippage | Total |
|---|---|---|---|---|
| A, research basis today | Dukascopy 0.70 | prior | prior | **1.07** |
| B, batch stress | historical 0.34 | prior | prior | 0.71 |
| C, Raw central | 0.15 | 0.07 | 0.30 | **0.52** |
| D, Raw with limit entry and limit take-profit | 0.15 | 0.07 | about 0.09 (stop exits only) | 0.31 |
| E, Raw optimistic | 0.05 | 0.07 | 0.10 | 0.22 |

### 1.2 What exists in code

- **Engine tick sample.** `Engine.flush_ticks` (`goldbot/engine/runner.py:3204`) appends the engine's `last_tick`
  polls to the store table `ticks` (source = account). This is a time sample, a few reads a second, only while the
  engine runs.
- **Broker tick record.** `goldbot/data/ticks.py` (BACKLOG 79, scheduler job `tick_record`, 21:10/22:10 UTC inside
  New York's daily break). It holds the terminal's complete `copy_ticks` history, one Parquet per server date, in
  `state/ticks/<account>/<date>.parquet`, with `index.json` (rows, London/NY ticks, median spread, sha256).
  - It is read with `load_day("icm-demo", "YYYY-MM-DD")`.
  - Size is about 0.6-1.7 MB a day. **About 140 MB is 240 retained days per account, not one day** (module
    docstring; the brief's "140 MB/account/day" is off by about 240×).
  - It is backed up with `state/`.
- **Nightly cost table.** `nightly_costs` (`goldbot/ops/jobs.py:180`, weekdays 23:10). For each account it reads 30
  days of `ticks` and 180 days of `fills`, plus `state/broker_terms_<account>.json`, and writes
  `state/costs_<account>.json`. That is a `CostTable`:
  - spread median and p90 by **session** (three cells),
  - slippage by session and order type,
  - commission and swap.
- **Publishing.** `publish_costs` uploads `PublishedCostTable` (costs only, no identifiers; `fields` marks each
  component as measured or prior) as `costs_measured.json` on release `costs-v1`. This happens only when
  `costs.publish_release` is true and a github-token is in the keyring. `research.yml` then downloads it, and
  `research_costs` replaces the 0.37 prior with the table's slippage and commission. **The labels still pay the
  Dukascopy bar spread**, even with `costs-v1`: the broker spread is reported but never charged.

### 1.3 Blockers found (ranked by impact)

| # | Blocker | Evidence | Effect | Fix, owner of the fix |
|---|---|---|---|---|
| K1 | **Swap is never measured on a GBP account** when the symbol's swap mode is points (1) or deposit currency (4) | `mt5_adapter.swap_usd_per_lot`: modes 1 and 4 convert only when `account_currency == "USD"`; otherwise it returns None ("settings prior applies") | `publishable()` refuses while swap is None ("swap not measured by the terminal yet"), so **`costs-v1` is never published** for `icm-demo` (GBP) | Convert the GBP tick value or swap to USD at the terminal's GBPUSD bid, and note the rate used. Implementer, then trading-safety-reviewer and quant-reviewer. About half a day with tests |
| K2 | **Commission is never measured on a GBP account** | `measure_broker_terms`: `if account_currency.upper() == "USD": ... else: "deposit currency GBP: commission from settings"` | Commission stays at the 3.5 USD prior for ever. `fields.commission.source = prior` | Convert deal commission at the deal-time GBPUSD rate. Same owners as K1, same change |
| K3 | **Publishing needs ≥ 50 fills** (`research.min_fills_for_slippage`) | `publishable()` checks `n_fills < min_fills` | No strategy has passed, so the demo engine may place no orders, and `costs-v1` waits indefinitely, even though the spread is measured from day 1 | Publish once spread and swap are measured, with slippage marked `fields.slippage.source = "prior"`. The schema was built to say this. Implementer and quant-reviewer. It is a research-data rule, not a gate threshold (PD and PO decide). Demo fills also **understate** live slippage, so demo slippage should never lower the prior (`max(measured, prior)`) until live fills exist |
| K4 | The cost table has 3 session cells, not hours. Research needs the rollover (21-23 UTC) and New York open hours | `spread_table` groups by `session_label` | The hour economics in section 2.3 cannot be checked against the broker | A small aggregate job, CB-0 step 6 below |
| K5 | Labels charge the Dukascopy spread even after `costs-v1` | `research_costs` returns only the extra cost beyond the spread | If the IC Markets Raw spread is 0.10-0.20 against Dukascopy's 0.60-0.70, research **overstates** today's cost by about 0.5 $/oz. That is conservative, so it is no safety risk, but it is the wrong number to decide with | Do not change the labels yet. After ≥ 20 broker trading days, report the broker/Dukascopy spread ratio by hour (CB-0 step 7). Any change to the label cost model is a later, separately reviewed decision (quant-reviewer, research director), never applied to already-read trials |
| K6 | The owner ruling "`costs-v1` by 2026-12-11, else priors accepted 2026-12-14" (`hypotheses.md`, Next generation) **is at risk**: K1 and K3 alone stop publication whatever the terminal does | — | — | Program director: put K1-K3 on the board this week. Research director: tell the owner that the 12-11 date depends on K1-K3, not only on the terminal |

### 1.4 Exact steps to build `costs-v1` (who, where, when)

Everything runs on the **brain VM**, where the scheduler, the engine and `state/` live, through the MT5 bridge.
**Raw ticks never leave the brain.** The only things published are the `PublishedCostTable`, a few hundred bytes,
and an hourly aggregate of about 5 KB. Neither carries an identifier.

| Step | What | Who | Where | When |
|---|---|---|---|---|
| 0 | Prerequisites: demo MT5 login done, engine and scheduler running, `account_class: raw` declared (already in `accounts.yaml`) | owner (login), program director (deploy) | brain + MT5 VM | in progress (deploy is at the login step) |
| 1 | Fix K1 + K2 (GBP conversion of swap and commission) and K3 (publish with slippage as a prior); tests first | implementer → trading-safety-reviewer, quant-reviewer, code-reviewer → release-manager | repo | **by 2026-10-30** (owed within 14 days, so it goes on the board now) |
| 2 | Day 1 after login: check `state/broker_terms_icm-demo.json`. It should show measured swap long/short and the triple weekday, plus a note naming the conversion rate | sre (job check), strategy-researcher (reads the values) | brain | first weekday after login |
| 3 | Commission: one deliberate **demo** round trip of 0.01 lot in the London session, entered through the normal approval flow. The owner confirms the entry (demo, no money at risk) and the exit is automatic. `BrokerTerms` then has `commission_per_lot_round_trip_usd` from the deal | owner clicks; program director arranges | brain, demo account | after step 1 is deployed |
| 4 | Spread: `nightly_costs` builds `costs_icm-demo.json` from the engine sample (30-day window). Read the table only after **≥ 10 trading days** with all three sessions, at least one US data release day (NFP, CPI) and the 26-30 Oct US/EU clock-gap week, so the medians are not a single quiet week's | strategy-researcher (read), sre (job health) | brain | about 2026-11-06 if login lands by 2026-10-22 |
| 5 | Publish: set `costs.publish_release: true` in the brain's local settings. The owner enters the github-token into the keyring if it is not already there; Claude never sees it. The next `nightly_costs` uploads `costs_measured.json` to release `costs-v1`, and `research.yml` picks it up | owner (token), sre (setting), release-manager (checks the asset) | brain → GitHub release | after steps 1-4 |
| 6 | Hourly profile (fixes K4): a script, for example `scripts/research/broker_spread_profile.py`, run on the brain. It reads `state/ticks/icm-demo/*.parquet` through `ticks.load_day` (sha256 checked) and writes per UTC hour × weekday: median, p75 and p90 spread, ticks per minute and days counted. It also covers the 21-23 UTC rollover separately and the 12:30/14:00 UTC data minutes. The ~5 KB aggregate goes as `spread_profile.json` on `costs-v1`, or under `docs/research/` | data-engineer (script + tests), quant-reviewer | brain | after ≥ 20 recorded trading days (about 2026-11-20) |
| 7 | Basis check (feeds K5): the same hours on Dukascopy (`data-v1` refreshed to cover the dates) against the broker. Output: the broker/Dukascopy spread ratio by hour, with a 95% interval across days. Reported only; no label change without a separate decision | strategy-researcher | brain + Actions (data-v1) | with step 6 |
| 8 | Slippage: measured only from real fills. Demo slippage is reported but never lowers the 0.15 prior (K3). Live slippage needs the paper → tiny-live gate (owner) | — | — | open-ended |

## 2. Cost in R at measured volatility

R = stop distance in $/oz = k × ATR(timeframe). ATR is the last-90-day median from `atr-measured-2026-10-18.md`:
15m 8.01, 1h 17.73, 4h 36.61, 1d 87.63. Swap is extra, for each night a position is held.

### 2.1 Round-trip cost in R by timeframe and stop multiple, today

| Timeframe | Scenario | 0.75 × | 1.0 × | 1.5 × | 2.0 × | 3.0 × |
|---|---|---|---|---|---|---|
| 15m | A 1.07 | 0.178 | 0.134 | 0.089 | 0.067 | 0.045 |
| 15m | C 0.52 | 0.087 | 0.065 | 0.043 | 0.032 | **0.022** |
| 15m | D 0.31 | 0.052 | 0.039 | **0.026** | **0.019** | **0.013** |
| 1h | A 1.07 | 0.080 | 0.060 | 0.040 | **0.030** | **0.020** |
| 1h | C 0.52 | 0.039 | **0.029** | **0.020** | **0.015** | **0.010** |
| 4h | A 1.07 | 0.039 | **0.029** | **0.019** | **0.015** | **0.010** |
| 4h | C 0.52 | **0.019** | **0.014** | **0.009** | **0.007** | **0.005** |
| 1d | A 1.07 | **0.016** | **0.012** | **0.008** | **0.006** | **0.004** |

Bold means under 0.03 R.

Long swap at the −60 USD/lot prior, in R per night:

| Stop | 15m 1.5 × | 1h 1.5 × | 1h 2.0 × | 4h 1.5 × | 1d 1.5 × |
|---|---|---|---|---|---|
| Swap per night | 0.050 | 0.023 | 0.017 | 0.011 | 0.005 |

**Stop needed for cost < 0.03 R:**

| Scenario | Stop in $ | 15m | 1h | 4h | 1d |
|---|---|---|---|---|---|
| A | 35.7 | 4.5 × ATR | 2.0 × ATR | 1.0 × ATR | 0.4 × ATR |
| C | 17.3 | 2.2 × | 1.0 × | 0.5 × | 0.2 × |
| D | 10.3 | 1.3 × | 0.6 × | 0.3 × | — |

**The sizing conflict, in one line.** 0.01 lot fits £5-6 of risk only while the stop is ≤ $7.98
(`small-account-playbook-2026-10-18.md`). At that stop the cost is:

| Scenario | A | B | C | D | E |
|---|---|---|---|---|---|
| Cost at a $7.98 stop | 0.134 R | 0.089 R | 0.065 R | 0.039 R | 0.028 R |

So, at £1,000:
- **cost < 0.03 R and a placeable 0.01 lot happen together only in scenario E**: a Raw spread of about $0.05-0.10
  plus limit execution;
- every other configuration under 0.03 R is shadow-only;
- this is an account-size fact. Changing it is an owner decision (deposit, risk per trade). It is not a research
  lever.

### 2.2 The research window is the wrong cost regime

This is the share of decision bars whose point-in-time round trip, (bar spread + 0.37) / (k × ATR14 of the bar), is
at or below a threshold. Research window only, bars before 2025-10-01. Cost and volatility only.

| Timeframe, stop | Median cost in R: 2010-2019 | 2020-2023 | 2024 | 2025 (to Sep) | today (scenario A) | Share of 2010-2025 bars ≤ today's cost |
|---|---|---|---|---|---|---|
| 15m, 1.0 × ATR15 | 0.37-0.58 | 0.32-0.37 | 0.28 | 0.22 | 0.13 | ≤ 0.12 R: **0.8%** |
| 1h, 1.5 × ATR1h | 0.12-0.18 | 0.10-0.12 | 0.086 | 0.072 | 0.040 | ≤ 0.04 R: **0.6%**; ≤ 0.10 R: 25% |
| 4h, 1.5 × ATR4h | 0.06-0.09 | 0.05-0.06 | 0.043 | 0.036 | 0.019 | ≤ 0.02 R: **0.3%**; ≤ 0.04 R: 11% |

Consequences:
- **(a)** The net figures of rows #16 and #36 are averages over years when the cost was 3-5 times today's.
  - Row #36's own decomposition: gross +0.084 R; swap −0.048 R; spread, slippage and commission −0.140 R; net
    −0.104 R.
  - The same trade at today's ATR, under scenario A, would pay about 0.04 R plus swap.
  - This is an **observation about costs, not a re-read of #36's verdict**. #36 stays failed (section 3 explains why
    it would fail anyway).
- **(b)** A cost filter set at today's levels would starve the P4 screen.
  - Under "trade only when cost ≤ 0.06 R", a 1h family keeps 3.9% of bars. For a #36-like rule that is about 70
    events, far below 1,000.
  - So "cost-filtered entry" **cannot be a research-window trial**. It is a live/shadow rule (H-13) and a reported
    quantity (CB-D1).
- **(c)** The only data at today's cost in R is the **holdout** (2025-10..2026-09), which is all high-volatility,
  plus forward shadow and demo.
  - The holdout is scored once per configuration, and only after G2.
  - Spending that one look well matters more than any cost tweak.

### 2.3 By session and hour (15m, 1.0 × ATR15)

Medians by UTC hour, Dukascopy, 2026 to 10-05, descriptive:

| UTC hour | Spread | ATR15 | Cost in R, A | Cost in R, C (Raw, assuming the broker spread keeps Dukascopy's hourly shape) |
|---|---|---|---|---|
| 00-03 (Asia) | 0.75-0.77 | 9.3-10.3 | 0.11-0.12 | 0.05-0.06 |
| 05-06 (late Asia) | 0.67-0.75 | 8.7-8.9 | 0.12-0.13 | 0.06 |
| 07-11 (London) | 0.60 | 8.5-8.9 | 0.11 | 0.06 |
| 13-16 (London/NY overlap) | 0.64-0.66 | 10.1-12.1 | **0.085-0.10** | **0.04-0.05** |
| 20 | 0.70 | 9.0 | 0.12 | 0.06 |
| 21-23 (rollover) | 0.76-0.82 | 7.5-11.7 | 0.10-0.15 | 0.05-0.07 |

The cheapest hours in R (13-16 UTC) save about 0.03-0.05 R against the worst at a 15m ATR stop. At a 1h stop the
saving is under 0.005 R. Two further limits:
- The cheapest-in-R hours are the **least sizable**: 1.0 × ATR15 fits 0.01 lot on only 6-17% of 14-16 UTC bars
  (`small-account-playbook-2026-10-18.md`).
- At a fixed sizable stop ($6.65), the hour changes the cost only through the spread, by ±0.1 $/oz, which is
  ±0.015 R.

**Trading only tight-spread hours is worth about 0.01-0.05 R. That is a filter feature, not a strategy** (CB
"not proposed" list).

## 3. Why cheaper execution is necessary but not sufficient

The gate-1 bar at K = 53 is DSR ≥ 0.95, which is about a per-trade t of **3.94** (`batch-2026-10-18.md` rule 2.3,
Bailey and López de Prado 2014). Each signal's SD in R is implied by its own gross t: SD = mean × √n / t.

| Signal (registry or brief) | Gross R | Gross t | n | Implied SD (R) | Net mean needed for t 3.94 | Passes at **zero** cost? |
|---|---|---|---|---|---|---|
| H-04 `asia_drift` (brief: +0.043 R, t 3.09, 4,025)\* | +0.043 | 3.09 | 4,025 | 0.88 | **+0.055** | no |
| tsmom 1h #36 (min_score 1) | +0.084 | 2.43 | 1,691 | 1.42 | **+0.136** | no |
| tsmom 1h #16 (defaults) | +0.061 | 2.63 | 3,764 | 1.42 | **+0.091** | no |
| tsmom 4h #17 | +0.062 | 2.37 | 2,275 | 1.25 | **+0.103** | no |

\* Discrepancy: the research-v1 snapshot has H-04 as row **#44, "failed", with no rows** ("cell timed out ... before
the walk-forward started", issue #96). The brief's +0.043 R / t 3.09 is in no registry row I could find. The
evaluator should say which run produced it before any H-04 decision. The conclusion holds either way: t 3.09 < 3.94
even at zero cost.

**Implications for the research director (ranked):**
1. **Trials spent on cost tweaks of signals with gross t < 3.94 cannot produce a PASS.** At best they produce a
   LEAD (shadow), and each one raises K for everyone (K 53 → 54 moves the bar by about +0.01 t). So spend very few.
2. A pass needs a signal whose **gross** t is about 4.2 or more (net t ≥ 3.94 after about 0.03 R of cost). For
   n ≈ 4,000 and SD ≈ 1 R that is about +0.07 R gross. So:
   - more independent events per signal: 15m frequency, or pooled across sessions;
   - or a much larger edge per event: event-conditional setups, opportunity days (H-16/H-17).
   This is the scalping/opportunity agenda, not cost engineering.
3. Cost work still pays twice:
   - it moves the **net sign** of every future candidate by +0.05 to +0.15 R (section 2.2);
   - it decides whether a holdout read at today's costs can be positive at all.
   So do it first, at 0 trials (CB-0).
4. **The one-time holdout is the current-regime test.**
   - A LEAD scored on the holdout at the pipeline's normal costs (Dukascopy spread 0.6-0.7 + 0.37) is a direct test
     of "the edge survives today's costs".
   - Whether LEADs, and not only PASSes, may spend their one holdout look is a holdout-policy question:
     **owner-only, flagged** in section 5.

## 4. Literature on the cost levers (for the review; nothing here is gold-specific)

| Claim | Source | Sample, instrument | Net of costs? | Robust? |
|---|---|---|---|---|
| Trend-following survives costs at multi-week horizons | Hurst, Ooi and Pedersen 2017, *J. Portfolio Management* 44(1) | 1880-2016, 67 futures and forwards incl. gold | Net of estimated costs and fees | Robust (replicates Moskowitz, Ooi and Pedersen 2012, *JFE* 104(2), 1965-2009, 58 futures, gross) |
| High-turnover anomalies mostly die after costs; low-turnover ones survive | Novy-Marx and Velikov 2016, *Rev. Financial Studies* 29(1) | US equities 1963-2013 | Net (effective-spread estimates) | Robust in equities. The direction (amortise cost over longer holds) carries over |
| Real institutional costs are lower than academic estimates | Frazzini, Israel and Moskowitz 2018, SSRN working paper 3229719 | AQR live executions 1998-2016, 21 equity markets | Measured costs | Single source (practitioner). Why we must **measure**, not assume |
| Limit orders save the spread, but their fills are adversely selected; backtests that assume fills overstate returns | Harris and Hasbrouck 1996, *JFQA* 31(2) (NYSE SuperDOT, 1990-91); Linnainmaa 2010, *J. Finance* 65(4) (Finnish equities, 1995-2002) | Equities | Execution-cost studies | Robust direction. Size of the effect in XAUUSD CFDs is **unknown**, so CB-D2 measures it on our ticks |
| Deflated Sharpe: the bar rises with the number of trials | Bailey and López de Prado 2014, *J. Portfolio Management* 40(5) | Method | n/a | Standard |
| Gold-specific: retail CFD cost levels, Raw versus Dukascopy basis | **none found** (broker marketing only, *unverified*) | — | — | **Measure it** (CB-0) |

## 5. Decisions and flags

**Owner-only (flagged with recommendations, not assumed):**
- **Budget.** 2026Q4's 53 slots are allocated in full: batch 32 + H-02 + #1-#20. CB-T1 needs 1 slot. The research
  director has two routes:
  - (a) take it from the 2027Q1 free reserve (3 slots), which is the director's call within the budget and needs no
    owner;
  - (b) ask the owner to raise `trial_budget_overrides."2026Q4"` from 53 to 54.
  **Recommendation: (a).** No owner action is needed, and the batch shows nothing is lost by waiting for a slot.
- **Holdout policy.** May a LEAD that cannot pass gate 3 spend its one holdout look to test today's-cost economics?
  **Recommendation: yes, but only after G2, at most 2 LEADs a quarter, chosen by the forward-cost quantity of CB-D1
  written before the look, and counted in K.** It changes how the holdout is used, so the owner decides.
- **Commission measurement trade (CB-0 step 3).** One 0.01-lot demo round trip. It is demo with no money at risk,
  but the owner clicks the entry.
- **github-token on the brain** for `costs.publish_release`. The owner enters it into the keyring.
- **Account size and risk per trade** (section 2.1): only these make a sub-0.03 R configuration placeable.
  Research does not recommend loosening any risk limit.

**PD + PO (routine):** K1-K3 on the board this week; K5 label cost stays Dukascopy until step 7 reports.

**Raised to the research director within 24 h:** K6, the 12-11 `costs-v1` date at risk; the H-04 figure
discrepancy (section 3); the "0.71 measured" label (section 1.1).

**What would change my mind:**
- If `costs-v1` measures a Raw round trip ≤ 0.25 $/oz, scenario E is real. Then 15m sizable stops cost about
  0.03 R, and CB-T2 (limit entry) moves up the ranking.
- If CB-T1 reads gross ≥ +0.12 R with t ≥ 3.5, the "more events or bigger edge" conclusion softens for 1h.

Accountable: strategy-researcher (this file, CB pre-registrations, hypotheses rows). Next check: weekly readiness walk
Tue 2026-10-20 (K1-K3 on the board? login done?).
