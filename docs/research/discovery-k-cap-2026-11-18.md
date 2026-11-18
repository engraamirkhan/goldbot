# Decision: the discovery K cap (`research.discovery.k_cap_quarter`), 2026Q4 and 2027Q1

Research director, 2026-11-18. A method decision taken under the owner's delegation of 2026-11-18 (section 5). The
owner can revoke it with one line (section 6). Not changed here: no K row, no budget in code, no settings file. The
lane that enables `discovery_nightly` (ADR 0090, branch `feat/discovery-nightly`) writes these values.

## 1. The values

| Quarter | `k_cap_quarter` | Condition |
|---|---|---|
| 2026Q4 | **15,000** | Effective on the first enabled night. The job is enabled only after the quant-reviewer's and the sre's GO (ADR 0090), and not before ADR 0088 takes effect (2026-11-22 22:00 UTC). |
| 2027Q1 | **30,000** if the Q4 continuation rule passes, **0** otherwise | Rule below. 0 means the job refuses every night and the line is closed. |

**Q4 continuation rule (pre-registered here, read on 2026-12-31 after the last Q4 night).**

- The line continues into 2027Q1 only if Q4 produced at least one explore survivor that passed the registration bar
  of `prereg-amendment-2026-11-18-discovered-arms.md` section 2.4. That bar includes explore gross mean per trade
  of at least 2 x the charged cost of record.
- Zero such survivors **closes the line** (research-director rule 3). It is not re-run, widened, renamed or
  re-parameterised as a "new" line. Raising `population` or `generations`, or adding timeframes, is a widening.
- Reopening needs new mechanism evidence, a new card, and the K of this line counted in full.

**Mechanics.** The setting is a single integer today. `quarter_k_used` is per quarter, so a value set in Q4 would
carry into Q1 without anyone deciding it. The enabling lane should make it a per-quarter map, like
`trial_budget_overrides`: `{"2026Q4": 15000, "2027Q1": 0}`, where a quarter not listed is 0. The 2027Q1 entry moves
to 30,000 only on a written Q4 read that cites the rule above. Until the map exists, the 2027-01-01 change is a
manual settings edit, and the director's Q4 read owns it.

## 2. Why 15,000: what K costs, and what binds

**K the job writes per night.**

- The bound comes from code: `discovery.planned_k`, timeframes x (population x (generations + 1) + max_candidates
  x 24). At the defaults that is 3 x (32 x 9 + 20 x 24) = **2,304**. The budget check needs `k_used + 2,304 <= cap`
  before every night.
- **What a night actually adds** is the distinct specs whose outcomes it reads (`k_contrib`). The power gate
  (ADR 0085) refuses about 92 % of G2 specs at 1h and 95 % at 4h at sampling time. Refused specs are never
  ledgered.
  - The GP then selects toward admitted specs, so later generations admit more.
  - Without the gate, the last real run (2026-10-31, `autodisc-v2`) ledgered 288 per timeframe: 864 a night, with no
    neighbours because there were no candidates.
  - Planning figure: **300-450 a night**, with 864 a ceiling at today's settings (neighbours only appear with
    candidates).
- Q4 from 2026-11-23 has 39 nights:
  - at 350 a night, K is about 13,700, plus the 2,304 headroom check: inside 15,000;
  - at 864 a night the cap stops the job after about 15 nights. That is the intended brake: a night that ledgers
    everything means the power gate is not doing its job.

**Deflated-Sharpe penalty.** I re-derived it with the expected maximum of K standard normals,
(1 - gamma) Phi^-1(1 - 1/K) + gamma Phi^-1(1 - 1/(K e)), + 1.645. Trade counts are for a net edge delta at sd 1.2 R,
n = (t x 1.2 / delta)^2.

| K a discovered candidate is read at | t hurdle | trades at 0.10 R | trades at 0.20 R |
|---|---|---|---|
| 21,333 (brain registry, why-fail audit) | 5.69 (matches ADR 0088) | 4,657 | 1,164 |
| + 864 prior grammar-v1 seed = 22,197 | 5.70 | 4,673 | 1,168 |
| + Q4 cap 15,000 = 37,197 | 5.82 | 4,870 | 1,218 |
| + Q1 cap 30,000 = 67,197 | 5.95 | 5,095 | 1,274 |

- The penalty grows as the square root of ln K. The full Q4 cap raises the hurdle by 0.12 t and the trades needed by
  4 %. Q1's cap adds another 0.13 t and 5 %.
- That bar was already out of reach for any 1h-1d rule (ADR 0088). K is not what makes discovered specs fail it:
  the sample is.
- The DSR at `n_trials_discovery` is a **reported** figure for research. Discovered specs are promoted through the
  ADR 0085 fresh-data confirm, Holm at m = 3 per batch, where the search size does not enter alpha. The search's
  selection is priced by the G0 null, which reruns the whole search 20 times with the same priors.
- So the penalty does not justify a small cap. Nor does it justify an open one. The real-money phase gate reads K
  (section 4).

**Power.** The scarce resource is the confirm read, not K.

- ADR 0085 allows 4 confirm batches a ledger year at m = 3, so at most **12 discovered specs a year can ever be
  confirmed**.
- The search only has to produce a handful of strong, power-admitted explore survivors a quarter. A K budget that
  lets the job run every night of the quarter at its expected rate is enough. More K buys more candidates for the
  same 3 slots a batch, plus a slightly higher reported DSR. It buys no power.

**The brain window (01:00-03:10 UTC, 2 vCPUs, one core for the child at nice 15, `budget_s` 5,400 s).**

- I re-derived timing from the 2026-10-31 sidecar timestamps on the Mac: 288 specs plus 20 G0 null reruns, about
  5,600 evaluations a timeframe, took **161 s at 4h and 107 s at 1d** on one process.
- The brain's Ampere core runs niced beside the engines. Taking it as 3 to 5 times slower gives about 5-13 minutes a
  timeframe, **15-40 minutes a night**, inside the 90-minute budget.
- So compute does not bind at today's settings; the K cap does, by design. The first enabled night must record wall
  time per timeframe (the sre's check). If a night takes over 60 minutes, settings are not raised to use the rest:
  the window is a ceiling, not a target.

## 3. Inside or alongside the registry cap: alongside, with its K inside every DSR

- **Alongside the trial budget.**
  - `research.trial_budget_quarter` counts pre-registered trials, each a registry row that may take a confirm slot:
    3,500 in 2026Q4 (ADR 0073), about 3,429 used per the brain (not re-derived; the Mac copy of 2026-11-07 reads
    3,194).
  - Discovery K counts screened specs. It sits in the registry's sidecar and is never a trial or a budget slot
    (ADR 0058 F3, `TrialRegistry.record_discovery_batch`).
  - Putting 15,000 screened specs inside a 3,500-trial cap would exhaust it in one night. It would also mix two units
    that answer different questions.
- **Inside the program K.** Every screened spec is in `n_trials_discovery` = `n_trials_effective` + every batch's
  `k_contrib`. That is the K a discovered candidate's DSR is read at. Nothing about this cap lowers K.
- **Registrations go inside the registry cap.** Each discovered arm registered for demo books its K rows against
  `research.trial_budget_quarter`, and registration is refused over it (amendment section 2.2).
  - That leaves about 71 rows in 2026Q4.
  - 2027Q1 has the default 20, with 8 reserved, so 12 are open, and it is listed in `open_budget_preregistered_only`.
  - At 1 row an arm (the control is recorded NOT_TRIAL, quant ruling Q3), that is at most 12 discovered arms in
    2027Q1, and at most 5 at once, until the owner changes `research.trial_budget_quarter`. That is the honest
    constraint, and I keep it.
- **One interaction to fix in the enabling lane.** `budget_line` also checks 3 registry "units" a night against the
  trial headroom (a tightening; sidecar batches use no row). When registrations take the last open rows, the search
  stops too. In 2027Q1 that happens at 12 - 3 = 9 rows used. I accept it: no rows left means nothing found tonight
  could be registered anyway.

## 4. What this cap does not touch

- The 3,500 registry cap and its 5,000 ceiling (ADR 0073). Only ADR 0088 lifts them, and 0088 is Proposed and not
  merged.
- The holdout.
- The phase gate's thresholds. Its K for discovered specs is set by the quant-reviewer's ruling of 2026-11-18
  (Q1): "The phase gate reads `n_trials_discovery` for any spec whose origin is discovery. Test:
  test_phase_gate_reads_discovery_k_for_discovered_specs." Every other spec stays at `n_trials_effective`. That
  ruling makes the gate stricter for discovered specs. It does not loosen the gate for any spec.
- RiskGate, sizing, any risk limit, live unlock.

## 5. Basis, stated so the owner can see it

- **The quant-reviewer ruled that the budget is never raised without the owner.** That ruling was made for the
  trial budget, `research.trial_budget_quarter` and its overrides.
- `k_cap_quarter` is a new setting. It defaults to 0 as "not yet set", not as a level the owner chose. Even so,
  moving it from 0 to 15,000 lets the system spend K it could not spend yesterday, so I treat it as a budget decision
  under that ruling and record it here.
- **The owner's words, 2026-11-18, verbatim** (recorded as acceptance in ADR 0088's Status): "the system should be
  autonomous on finding new strategies, retire strategies etc, and there is no rules restrictions from my side, the
  system should be able to become the system i want no matter how it achieves it".
- I read that as delegating **method** to the research director, which includes how much search K the discovery line
  may spend. I do not read it as delegating money, sizing, risk limits or going live. Those stay owner-only.
- The delegation is relied on only within the guards that stay fixed: K counted in full, the holdout sealed, the
  phase gate pinned, demo only.
- **Owner-only, unchanged by this decision** (quant-reviewer, 2026-11-18): the trial budget
  (`research.trial_budget_quarter` and its overrides), the gate thresholds and the risk limits. This decision sets
  only the discovery search's K cap.
- **Posted to the owner.** The quant-reviewer posts this K-cap decision on issue #34, so the owner sees it where
  the revocation below would be made.

## 6. Revocation

- One line from the owner, on issue #34 or in chat, revokes this decision. `k_cap_quarter` is then set to 0 and
  every night refuses.
- Trials and K already spent stay counted, and arms already registered keep running under their own kill rules
  until they retire.
- A lower number from the owner replaces mine the same day.
