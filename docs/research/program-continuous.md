# Continuous research program: every style, every generation (research director, 2026-10-17)

**Owner directive, 2026-10-17 (verbatim):** "from now on expert agents for trading strategies should work, scalping,
intra day, swing trades, all sorts to make this system profitable to the greatest length recursively".

This file turns that directive into a standing program. It runs every week from today, across every trading style,
and each generation's survivors seed the next. It does not replace the quarter files. Trials are still defined only
in `preregistration-<quarter>.md`, the agenda is still ranked in `hypotheses.md` (A3), and the plan by timeframe is
still `research-plan.md` §5. This file says **who works on what, how often, on what compute, and how the loop
turns**, so the lanes are never idle and never undisciplined.

**What this program does not promise.** No profit is forecast here. The record so far is 20 trials and 0 passes
(`scorecard-2026-10.md`). The most likely result of the next generations is more retirements, recorded honestly. Being
"profitable to the greatest length" means finding every edge that survives costs and deflation. It does not mean
finding one at any price.

## 1. The discipline (non-negotiable)

These rules are the program. A lane that breaks one has produced nothing, whatever its result shows.

1. **Pre-registration before any read.** A hypothesis, its configuration, its sample and its reading rule are
   committed to a `preregistration-*.md` file (and written to the registry as `preregistered`) before any outcome of
   that configuration is computed. "Outcome" means anything conditioned on future returns: R, hit rate, expectancy,
   AUC, label balance under a signal, or an equity curve. A rule changed after a look becomes exploratory and cannot
   justify promotion.
2. **ADR 0022 bars.** Every trade t is mean / day-clustered se (CR1, G − 1 df). A breakdown row needs the Bonferroni
   bar `max(2.0, t⁻¹_{G−1}(1 − 0.025/m))`. The overall cell needs `max(2.0, t⁻¹_{G−1}(0.975))`. A row is a lead, not a
   licence: changing a strategy because of a row is a new registry trial.
3. **K is honest and never resets.** `registry.n_trials_effective` counts every registry row ever recorded, plus each
   discovery's K_eff (H-02 at `survivor_unit: group`, about 10; H-14 about 12). The owner-confirmed conservative
   default adds every discovery's K_eff to every later trial. Gate 1 recomputes the deflated Sharpe on the K of the
   day it is read, so run order cannot lower the bar.
4. **The budget is 20 trials a quarter and is never loosened to reach a result.** The state on 2026-10-17:
   - Q4 2026: **20/20 spent**. No trial runs before 2027-01-01. `check_budget` refuses a 21st.
   - Q1 2027: 8 reserved (4 queued rows + 4 contingent H-02 survivor slots) and **12 open** under the E-5 cap. The
     12 are allocated in `preregistration-2027Q1.md` "Q1 open-budget round": 9 conditional rows (O-1..O-10 without
     O-9) and **3 free reserve**.
   - Reserved slots are never spent early. A row that misses its date returns its trial to the reserve. It is never
     re-pointed at a new idea without a written note before its pre-registration.
5. **Holdout.** 2025-10-01 .. 2026-09-30 is loaded only by a pre-registered holdout scoring (O-10 in Q1), only after
   G2 closes, and once per configuration.
6. **The safety rails are never a research variable.** The rails are: RiskGate is the only path to an order, the 1%
   per-trade cap, the £50 daily loss limit, the owner confirms entries, and exits are never gated. A candidate that
   needs one of them changed is rejected at intake.

### 1a. What runs without budget, and what does not

Lanes stay busy between trials with work that reads no outcome of an unregistered idea. When in doubt, the test is:
**if the number would change your opinion of a candidate rule's profitability, computing it is a read.**

| Allowed with no trial (0 budget) | Not allowed without a pre-registered trial |
|---|---|
| Literature and practitioner sweeps (SoA refresh, survey updates) | Backtesting, screening or "quick checks" of any candidate rule on 2010-2025 |
| Event and candidate **counts** (`count-owed-events`), coverage, power in days | Hit rates, mean R, AUC or label balance of a candidate signal |
| Feature and label **engineering** on the frozen folds: code, lookahead check (must report 0 columns), point-in-time joins (`asof_join` on `available_utc`), NaN and constant-column checks, feature-to-feature correlation | Feature-to-label correlation or importance outside a pre-registered discovery |
| Cost-in-R distributions (spread / ATR, cost per stop) by session and day type: they use no forward return | Selecting a session, threshold or hold length because its past R looked better |
| Pre-registered 0-trial diagnostics, read only by their rule: the H-13 and H-19 overlays, H-A, H-B, H-C (O-9) | Re-reading a retired family's data for a "variant" (R-01..R-09) |
| Shadow and live book analysis (forward data after the shadow start; new out-of-sample by construction) | Using the holdout for anything but its pre-registered scoring |
| Specialist code, unit tests, parity tests between labels and live exits | Any paid data or paid compute (owner-only) |

An idea dropped on outcome-blind grounds (no mechanism, too few events, cost in R too high) adds nothing to K. An
idea dropped after anyone looked at its outcome is recorded in the ledger as a spent look, and the look counts
toward K. The way to keep K small is to not look.

## 2. Lane structure

One strategy-researcher per style. Each writes **candidate files only**:
`docs/research/candidates/<style>-<YYYY-MM-DD>.md`. Candidate files hold no trial and no outcome. The research
director turns accepted candidates into H-ids in `hypotheses.md` and into pre-registration drafts. Subagents do not
spawn subagents; a new lane is opened through the program-director.

| Lane | Role | Scope | Starts | First file |
|---|---|---|---|---|
| **Scalping (15m)** | strategy-researcher | 15m, M1-tradeable now (stop ≤ ~0.32% of price). H-14 discovery and its survivor groups, H-17 trend day with runner, H-18 event continuation, the 15m forms of H-05, H-08 and H-10 (news split), stand-aside on quiet days via H-16 and H-19 | 2026-10-17 | `candidates/scalping-2026-10-17.md` |
| **Intraday (1h/4h)** | strategy-researcher | 1h (M2) and 4h (M3). Session families: H-04 Asia drift, H-24 West-hours drift, H-02 survivors (H-03, H-05, H-07, H-08, H-10 clusters), the tsmom 4h continuity trial, 1h versions of 15m survivors | 2026-10-17 | `candidates/intraday-2026-10-17.md` |
| **Swing (1d/1w)** | strategy-researcher | 1d (M4) and 1w as context only. H-01 slow TSMOM and its exit and hold variants, H-03 VRP, H-07 equity-stress haven, H-15, macro and positioning conditioning (H-06 is withdrawn for Q1 on its event floor) | 2026-10-17 | `candidates/swing-2026-10-17.md` |
| **Events and calendar** | strategy-researcher (4th lane) | H-10 news split, H-18 tier 1/2 events, H-11 (blocked: no consensus feed) | **when `calendar-v1` is published** (owner's `FRED_API_KEY`); until then the scalping lane holds these | `candidates/events-<date>.md` |
| **Holiday and session costs** | performance-analyst, with the research director | H-A (forward broker check), H-B (cost in R per family after each verdict), H-C (O-9), BACKLOG 45 final and BACKLOG 70 | running | `holiday-list-measurement-2026-10.md` |
| **Shadow and live book** | performance-analyst | Net R by timeframe, family, session and day type, costs, opportunity days captured | **once shadow or live trades exist** (BACKLOG 1, demo terminal) | monthly report |
| **Verdicts** | evaluator (read-only, independent) | One verdict per trial against its pre-registered rule, recorded (`TrialRegistry.record_verdict`) before any follow-up is written | per trial | registry and the trial's issue |
| **Rules and statistics** | quant-reviewer (read-only) | Rules every pin, amendment and K_eff unit before a count or run | per draft | review notes |

**What each candidate file must contain** (one block per candidate, `C-<style>-<nnn>`):
- the mechanism: who loses money to the trade and why that persists, with a citation or "practitioner only";
- timeframe, entry window by session (Asia, London, New York), hold, and the milestone at which it is tradeable;
- the candidate-count estimate and how it will be counted (`count-owed-events`), with the power stated in days;
- cost in R at the stop it needs (15m yardstick about 0.27 R per round trip, up to 0.67 R at the cost-gate floor),
  and the gross edge it would need to be net positive;
- what it adds to K (1 per trial; a discovery's K_eff by its survivor unit);
- the features, labels and data it needs, each tagged "exists", "engineering" or "blocked on <item>";
- a draft reading rule and kill rule, for the director to tighten;
- overlap with retired families R-01..R-09 (a candidate that is a retired family in new clothes is rejected);
- an **outcome-blind attestation**: "no outcome of this configuration has been computed or seen".

## 3. Cadence

| When | What | Who |
|---|---|---|
| **Daily** (weekdays) | One work unit per lane (literature, engineering, counts). Check `gh release list` and `gh run list` for dependency changes (G1/G2, `calendar-v1`, `costs-v1`) and dispatch non-trial jobs whose inputs now exist (`data-macro`, `data-us-calendar`, `count-owed-events`, `preregister-quarter` dry run) | each lane; research director |
| **Daily** (once trades exist) | Ingest shadow and live trades; flag rule breaches and cost surprises (no strategy is changed on a daily reading) | performance-analyst |
| **Twice a week** (Tue, Fri) | Walk the readiness list and freeze checklist. Every row due within 14 days is on track, dispatched, or re-dated with the program-director told within 24 h | research director |
| **Weekly** (Fri) | Each lane commits a dated candidate file (or an addendum to the latest one). The director accepts, merges or rejects each candidate with a reason, and ranks accepted ones into `hypotheses.md` A3. Research rows of the weekly scorecard | lanes; research director |
| **Monthly** (first week) | `scorecard-YYYY-MM.md`, consistent with the performance-analyst's numbers. Candidate cull: a candidate unchanged for 8 weeks with no path to a slot is parked with its reason | research director; performance-analyst |
| **Per trial** | Run only by its pre-registered rule. Evaluator verdict recorded before any follow-up is written. Ledger (`hypotheses.md` C) and this file's §5 updated the same day | runner; evaluator; research director |
| **Quarterly** | SoA refresh (`state-of-the-art.md`). Generation step (§4). Next-quarter pre-registration: draft 10 weeks ahead, quant review, freeze before the quarter, queued rows written from 14 days before it. Trial plan fitted to 20 with reserved slots intact | research director; strategy-researchers; quant-reviewer |

Quarter dates in force: Q1 2027 freeze commit 2026-12-14, queued rows 2026-12-18..23, no Q1 run before
2027-01-01, Q1 cut-off 2027-03-26. Q2 2027 draft exists (`preregistration-2027Q2-draft.md`) and freezes by
2027-03-31.

## 4. The generation loop

```
candidates (lanes, outcome-blind) -> H-id ranked (A3) -> pre-registration (quarter file, quant-ruled, frozen)
  -> registry row -> run -> evaluator verdict by the rule -> survivor | inconclusive | retired
       survivor     -> founder in the population; seeds neighbours for the next quarter
       inconclusive -> may be re-run once, only with more power (more events), written before the data
       retired      -> settings `research.retired_families` + hypotheses.md B, with the reason
```

- **Generation 0** (Q4 2026): 20 trials, 0 survivors. Retired: R-01..R-07; R-08 and R-09 were retired from the
  literature without a trial.
- **Generation 1** (Q1 2027): the queued rows (H-02, H-01, H-04, C4H), up to 4 H-02 survivor slots, and the open
  round (H-16, H-17 V0/V1, H-14 with ≤ 2 survivors, the H-04 follow-ups, a holdout scoring).
- **Generation 2** (Q2 2027): only the Q1 rows that did not run, conditional rows (H-18 if ruled), and neighbours of
  Generation 1 survivors.

**How survivors seed the next generation.** A survivor is a trial whose recorded verdict is "continue" under its own
rule, or a gate-1 pass. It enters the population as a founder. Its neighbours are written in the next quarter's file
before any of their data is read, from a fixed menu (`hypotheses.md` "Next generation"):
- hold length ±50%;
- one execution-timeframe step (15m → 1h when M2 is within about three quarters; 1d signal on 4h execution);
- a side split (long-only or short-only);
- the cost filter (H-13) on or off;
- the H-16 day filter on or off;
- a runner exit (V1-style) against the fixed barrier.

At most **3 neighbours per founder per quarter**, and never more than the free reserve allows. Each neighbour is one
trial and raises K for everyone. Survivors also cross styles: a 15m survivor group becomes a 1h candidate in the
intraday lane, and a swing survivor's signal becomes a context feature for the shorter lanes (features cost no trial
until a discovery screens them).

**How families are retired.**
- **Retire** when a screen at or above its event floor reads net mean R ≤ 0 under its rule, or when its model trial
  fails its kill rule. Record the family, registry rows, date and reason in `config/settings.yaml`
  `research.retired_families` and in `hypotheses.md` B on the same day (`tests/test_director.py` checks they agree).
- **Inconclusive** (below the event floor, or underpowered) is not a pass and not a retirement. One re-run is allowed,
  only with a pre-registered power gain. After two inconclusive quarters the family is parked.
- **Reinstatement** needs out-of-sample attribution after the retirement date (shadow or live), or new peer-reviewed
  evidence named in the SoA refresh. Retired families share one exploration trial a quarter (`research/director.py`).

**How K_eff is carried.** K never resets at a quarter boundary. Each generation inherits the whole ledger:

| Point | K = `n_trials_effective` (projected) | Gate-1 t needed (approx.) | Net R needed at 1,000 trades (SD 1.4 R) |
|---|---|---|---|
| Today | 20 | about 3.5 | about 0.16 R |
| End of Q1 2027 (40 rows + H-02 ~10 + H-14 ~12) | about 60 | about 4.0 | about 0.18 R |
| End of Q2 2027 (about 20 more rows) | about 80-100 | about 4.2 | about 0.19 R |
| Two years at the full budget | about 200 | about 4.4 | about 0.20 R |

These use the same Bailey and López de Prado approximation as `preregistration-2027Q1.md` (it ignores skew and
kurtosis). The recursion has a price: every trial makes every later pass harder. So the loop spends trials on
mechanisms with a reason and many events, not on parameter neighbours of failures. That trade-off is deliberate, and
it is the reason the budget is not raised.

## 5. Compute and data

**Compute: the Mac and GitHub Actions only.** Paid compute, paid data and paid plans are owner-only decisions.
- Mac: at most 12 worker processes across all lanes (`lane-test-rules`). Each research lane gets **at most 2
  workers**: for engineering, tests (`--testmon` while iterating, full gates once) and counts on local Parquet.
  Lanes never run gates one after another, and never edit files while gates run.
- CI (public repo, standard runners): data pulls (`data-dukascopy`, `data-macro`, `data-us-calendar`,
  `data-positioning`), `count-owed-events`, `preregister-quarter` dry runs and the trial runs themselves (`research`).
  One `research` trial workflow at a time. Market-data hosts are unreachable from the sandboxes, so every pull is a
  workflow.
- A trial is dispatched only by its pre-registered rule. A lane never dispatches a trial.

**Data dependencies** (status checked against `gh release list` and `gh run list` on 2026-10-17):

| Dependency | Needed for | Accountable | Date | Status 2026-10-17 |
|---|---|---|---|---|
| G1: data-v1 2020-2025 targeted refetch, then the G3 merged recount and the G4 digests pinned | every Q1 row | data-engineer (G1, G4), research director (G3) | 2026-11-06 / 2026-11-13 / 2026-12-14 | a `data-dukascopy` run on `main` succeeded 2026-10-17 07:48 UTC; the data-engineer confirms whether it closes G1. Not closed until confirmed |
| G2: holdout repair (42 missing weekdays, bid-imputed months) | O-10 and every holdout scoring | data-engineer | 2026-11-06 | open |
| BACKLOG 70: cme-metals-v3 holiday list; BACKLOG 45 final with the instance list (sha256) | H-B, O-9, the holiday lane | implementer (70), performance-analyst (45) | 2026-10-30 | 70 ready to build; 45 draft ruled |
| `calendar-v1` (US tier 1/2 calendar, 2010 on) | O-1 H-16, O-2/O-3 H-17, O-4 H-14, the events lane | **owner adds `FRED_API_KEY`**; data-engineer dispatches `data-us-calendar` | key by 2026-11-30 (escalated 2026-10-17) | release absent; blocked on the owner |
| `costs-v1` or "priors accepted" | the P-5 cost pin on every row | owner / research director | 2026-12-11 / 2026-12-14 | release absent; fallback to priors likely |
| Broker tick recorder (`copy_ticks`) on the demo | H-A | sre with data-engineer | 2026-11-25 | no BACKLOG item yet (with the program-director) |
| `macro-v1`, `positioning-v1` | swing lane, H-02 macro clusters | data-engineer | present | published (macro 2026-10-13, positioning 2026-10-17) |
| Shadow or live trades | performance-analyst lane | program-director (BACKLOG 1) | late | none yet |

## 6. The honest metric of progress

The north-star (ADR 0004 §6) is **£50 or more on opportunity days, less or nothing otherwise**. At £1,000 a £50 day
is about 5 R at 1% risk (about 6.1 R at the 15m minimum lot). As a monthly average, £50 a day needs roughly
£21k-£53k of equity at a sustained 2-5% a month. That is a ladder goal, not a near-term research target.

Progress is reported every month on three levels. A level is never claimed before the one below it is real:

1. **Evidence ladder, per style:** candidates → pre-registered → screened (verdict) → model trial → gate-1 pass →
   paper → tiny-live. Today every style is at "pre-registered" or below, with 0 gate-1 passes.
2. **Shadow and live R** (once trades exist): net R per opportunity day and per other day, by style, with the
   ADR 0022 t. Converted to £ at the current R (£10 at 1% of £1,000).
3. **North-star KPIs** (ADR 0004): monthly average £/day and %, share of opportunity days captured, the best-day
   histogram (days ≥ £50), and max drawdown.

**Distance to £50/day** is stated as two numbers in every scorecard: (a) the R per opportunity day the live book makes
against the about 5 R needed, and (b) equity against the next milestone (M1 £825 reached on equity, M2 £1,650,
M3 £3,270, M4 £7,900). Today both read: no trades, so £0/day, and 5 R short on every opportunity day. Counts of
candidates, documents or trials are activity, not progress, and are never reported as progress.

## 7. Q4 2026 remainder: what the lanes do now with zero trials

No trial can run until 2027-01-01. Each lane's job until then is to make sure **every Q1 slot it owns is ready to
run on its date, and that a ranked, outcome-blind pipeline exists for Q2**.

| Lane | By 2026-10-23 (first candidate file) | By 2026-11-13 | By 2026-12-11 (before the Q1 freeze) |
|---|---|---|---|
| **Scalping** | `candidates/scalping-2026-10-17.md`: H-14 group map review; 15m mechanisms outside H-14's groups (for Q2 and later); H-18 event floor options for the owner ruling | Draft the Q2 pins P-3, P-4, P-6, P-7 and P-8 for the quant reviewer (due 2026-10-25); the E-2 15m candidate spec with the implementer; the count method for H-14 candidates and the cost-in-R quartiles (published before O-4's row is written) | H-14 `DiscoveryConfig` and group-map hash pinned as a dated amendment; H-17 `trend_day` (E-3) spec reviewed; nothing counted on outcomes |
| **Intraday** | `candidates/intraday-2026-10-17.md`: session-family candidates (Asia, London open, New York), H-24 spec readiness, 1h transfer candidates for 15m survivors | H-04 and C4H rows checked against the G3 recount on repaired data; the O-8 (H-04 model research) config drafted, to be written as an amendment only if H-04 continues | Queued rows for H-02, H-04 and C4H ready for the 2026-12-18..23 write (`preregister-quarter` dry run clean) |
| **Swing** | `candidates/swing-2026-10-17.md`: H-01 exit and hold neighbours, written now for use only if H-01 continues; macro and positioning conditioning candidates (H-03, H-07, H-15) against ruling A's floor of 150 | H-01 recount on repaired data (G3: stays at ≥ 150 or is withdrawn by its rule); SoA sweep of 2025-2026 literature on gold trend and macro | H-01's queued row ready; candidates that cannot reach 150 events are parked with that reason |
| **Holiday** (performance-analyst) | — | BACKLOG 45 final (2026-10-30) and its G1 addendum (2026-11-13) | Instance list frozen with its sha256 before 2027-01-01 |
| **Research director** | Rank the three candidate files into `hypotheses.md` A3; reference them from the quarter files | Dispatch `count-owed-events` after G1 closes; paste the result into `preregistration-2027Q1.md` | Freeze commit 2026-12-14; reserved slots intact (8); the registry and the docs agree |

**Escalations already open** (program-director, within 24 h): the owner's `FRED_API_KEY` by 2026-11-30, the tick
recorder item, the demo terminal (BACKLOG 1), and the `costs-v1` / priors decision by 2026-12-11.

## 8. What would change this program

- **A gate-1 pass in any style:** that style's lane gets first claim on the next quarter's free reserve for
  neighbours (within §4's cap), and the performance-analyst lane opens on its paper trades.
- **Every 15m trial through Q3 2027 null:** the scalping lane narrows to H-16 selection and exits, and the
  director writes options to the owner (deposit plan to reach M2, or accept a lower north-star cadence) through the
  program-director. The budget is not raised.
- **A discipline breach** (an outcome read before registration, a reserved slot spent early, a holdout touched): the
  affected result is exploratory, the breach goes in the ledger, and the lane's next candidate file explains it.
