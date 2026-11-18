# Pre-registration amendment: discovered arms on demo (standing, 2026-11-18)

Research director, 2026-11-18. This amendment is written and committed before any discovered arm is registered
under it, and before any fill, candidate or label of such an arm exists.

It implements the quant-reviewer's ruling of 2026-11-18 on discovered arms. Requirements (1)-(6) are quoted from that
ruling and are not reworded. It is a **standing** amendment: it covers every arm that `discovery_nightly` (ADR 0090)
offers through `candidate_arms` and that `autodisc-register` registers, from its effective date to its sunset
(section 7). It registers no arm itself.

It adds a demo **learning-class** evidence route (ADR 0061 amendment 2026-11-02): never a qualification, never in a
family ranking, never real money. It closes the gap that `docs/research/arm-lifecycle-2026-11-18.md` section 3 names:
"a hot-loaded discovered arm loads and is held by ADR 0061 (unqualified: no entry)".

**Effective** when all of these hold:

- this file is on main;
- the code checks in section 6 pass on main;
- ADR 0090 has the quant-reviewer's and the sre's GO;
- the time is on or after 2026-11-22 22:00 UTC (the end of the red-box week).

Until then no discovered arm enters.

## 1. Requirement (1): written first, with its own reading rule

> It is written and committed before any discovered arm is registered under it, and has its own reading rule.

- `Learning.prereg` of every arm registered under this amendment names this file, and `learning_refusal` checks that
  the file exists.
- Its reading rule is section 5. No other read applies to these arms' fills.
- A registration whose arm row carries a `since` earlier than this file's commit date is refused.

## 2. Requirement (2): K for every screened configuration; K row with the arm row; budget refusal

> K is booked for every screened configuration, not only those registered. The generator's screen counts as trials.
> Each registered arm gets its own K row in the same transaction as the arm row. Registration is refused over
> `research.trial_budget_quarter`.

### 2.1 What is booked for the screen

- **Screened configuration**: a spec whose outcome was read on the explore window. That covers:
  - every spec the search evaluated after the ADR 0085 admission;
  - every plateau neighbour evaluated;
  - each night's distinct spec hashes, which are the search ledger's `K_search`.
- `sync_discovery_k` books them as the night's `k_contrib` in the registry's discovery sidecar, after each timeframe
  and again before any confirm read. They count in `n_trials_discovery`, the K a discovered candidate's DSR is read at.
- The G0 null reruns are booked as `K_null` and reported. They are the null distribution, not configurations under
  test.
- **Admission refusals are not trials** (quant ruling 2026-11-18, Q2). They are counted and reported with every
  night (`n_refused_power`) but not booked as K. This holds because the power gate is outcome-blind: it reads the
  entry mask and side only, never a label. Test T5 pins that: admission must be identical with the labels permuted or
  NaN. If T5 ever fails, refused draws are no longer outcome-blind, and from then on they count as trials.
- The quarter's discovery K is bounded by `research.discovery.k_cap_quarter`
  (`docs/research/discovery-k-cap-2026-11-18.md`).

### 2.2 What is booked for each registration

- **One learning K row for the arm**: `learning_k_config(arm_id, arm_config_hash, prereg)`, family `learning_class`,
  `config_hash` = `learning_k_id`.
- **Its dedicated replay-only control is not a trial** (quant ruling 2026-11-18, Q3). It is recorded in the registry
  with a `NOT_TRIAL` status and linked to the arm's K row (the arm id and the arm's `k_id`). It adds no K and uses
  no budget slot. That is **+1 K a registration**.
  - The control's parameters are frozen at registration: its seed, its signal rate, `per_trade_bracket` and
    `PER_TRADE_SAME`. They are recorded in the `NOT_TRIAL` row by hash.
  - **Any change to the control's parameters after registration voids the arm.** The arm's entries are refused, its
    reads are void from the change onward, and it retires. Its K row stays counted.
- **Same transaction.** Under the registry lock, the arm's K row, the control's `NOT_TRIAL` row and the aux.db
  `arm_registry` row are all written, or none of them:
  - registry rows first;
  - then the arm row, which names the `k_id`;
  - if the arm row fails, a void marker is written against the registry rows. The K stays counted and is never
    deleted.
- **No arm row without its K row.** `learning_refusal` refuses an arm whose `k_id` does not resolve in the registry,
  and the registration refuses before writing the arm row.
- **Budget.** Registration needs `used + reserved + 1 <= quarter_budget(quarter)` (`research.trial_budget_quarter`
  and its overrides) at the moment of writing. Otherwise it is refused, and the refusal is recorded with the
  quarter's numbers. This refusal stands until the owner changes `research.trial_budget_quarter`.

### 2.3 Concurrency

At most **5 discovered arms are running at once** (role learning, prereg = this file, not retired). A sixth
registration is refused until one retires. The aggregate loss budget of the line is therefore 5 x the per-arm loss
cap, at most £300 (section 3).

### 2.4 Registration bar (cost-aware, research-director rule 2)

A `candidate_arms` row is registered only if its explore record shows **all** of the following. Otherwise
`pending()` keeps it, refused, with the reason:

- (a) it is an explore survivor of `search_and_judge` (beats the G0 null, passes the explore checks);
- (b) explore **gross** mean per trade >= 2 x the charged cost of record. ADR 0084 v3: icm's dearest `charged_usd`
  cell, 0.90 $/oz, so gross >= 1.80 $/oz per round trip;
- (c) explore net mean per trade > 0 at the cost of record;
- (d) it runs live through `arm_rules_spec` and `Arm`, on a live timeframe (1h, 4h or 1d), gold only;
  **1d is held back** (`arm_rules_spec.LEARNING_TFS` is 1h and 4h): a 1d entry is sent 90 min after its
  decision close, which breaks the 60 s latency guard of section 4. Allowing 1d needs a written amendment (guard =
  the scheduled send + 60 s, the control replayed from the same instant);
- (e) it has a per-trade bracket under section 4;
- (f) its kill rule is readable by `until` (quant ruling 2026-11-18, Q7). These kill-only learning arms have **no
  forward power gate**. Registration is refused only when the kill rule cannot be read. That means either of these,
  both from the spec's explore signal rate:
  - expected replayed candidates by `until` (explore rate x 42 days) < MIN_N 50;
  - expected signal days by `until` < MIN_DAYS 10.

  Forward power against the twin is computed and reported with the registration. It is labelled "reported, not
  evidence" and never refuses or admits an arm.

Cost at demo size, for the record:

- 0.01 lot = 1 oz, so 0.90 $/oz is about **£0.68 a round trip**.
- At 1h-1d stops of $10-30, that is 0.03-0.09 R.
- Expected net at demo size, if a survivor kept its explore edge of about 0.1 R at 1-2 trades a day: about **£1-3 a
  day an arm, £5-15 a day for 5 arms**.
- This route is for learning which discovered rules survive costs on live fills. It is not the £50-a-day north-star,
  and no one should read it as such.

## 3. Requirement (3): the learning block

> Each arm carries a learning block: `since` = registration, a fixed `until`, `loss_cap_gbp`, minimum lot, the
> 300-fill read, demo only. Its fills are never qualify or promote evidence.

| Field | Value | Notes |
|---|---|---|
| `since` | the UTC day the arm row and K row are written | Only fills sent on or after it count, in the 300 and in the loss cap (quant ruling 2026-11-02 (1)). |
| `until` | `min(since + 42 days, 2027-03-31)` | Fixed at registration and written in the row, never extended. 42 <= LEARNING_MAX_DAYS 45. A re-registration of the same spec hash is refused (section 5.4). |
| `loss_cap_gbp` | **£60** | Fires when cumulative net realised P&L since `since`, after spread, commission and swap, is <= -£60 (quant ruling 2026-11-18, Q5). A close with no readable P&L counts as a loss at its stop plus its round trip. At the cap: no new entry, and every exit of an open position is kept. |
| `control` | the arm's dedicated replay-only control row (section 4) | Named in the control as `params.control: "learning:<arm id>"`. |
| `k_id` | `learning_k_id(arm_id, arm_config_hash, prereg)` | Section 2.2. |
| `prereg` | `docs/research/prereg-amendment-2026-11-18-discovered-arms.md` | |
| size | **0.01 lot** (the minimum) | Never resized, never grown. No size, growth or add field on the row (arm-lifecycle section 3, row checks). |
| per-trade cap | the style's ceiling through the RiskGate, unchanged | A candidate whose loss at the stop plus the round trip exceeds it is refused and recorded, never resized. |
| 300-fill read | at the 300th admissible fill since `since` | Section 5.3. The arm retires after it. |
| accounts | demo only | `risk.gate.is_demo` (accounts.yaml `mode: demo`, no live gate in the phase file, terminal trade_mode "demo", probe_only), checked at load, on every re-read and at the signal. `spec_rule` arms are refused for a live-class account (ADR 0058). |

Why £60:

- At 0.01 lot a 1h-4h stop of $10-30 loses about £7.5-23.
- A zero-edge arm pays about £0.68 a trade in cost, about £40-70 over its likely 60-100 fills in 42 days.
- £60 is about 3-8 full stops beyond cost drift. That is enough to see a bad arm stopped by loss before the
  e-process has the sample (section 5). It is small enough that 5 arms at once can lose at most £300 of the £10,000
  demo account (3 %).
- This is a demo learning cap inside the RiskGate's daily and weekly limits, which are unchanged. It is not a
  qualification threshold.

**Never evidence.** These fills, candidates and labels are:

- never qualification or promotion evidence (`qualification_refusal`, `research_promotion_refusal`,
  `phase_gate_refusal`);
- never in a family ranking or `docs/research/live-candidates.md`;
- never a forward read under ADR 0086 or ADR 0088.

A discovered spec reaches promotion only through its own deliberate ADR 0085 confirm batch on fresh history, under
its own pre-registration. That route does not use these fills.

## 4. Requirement (4): the per-trade bracket and the twin

> Each arm has a per-trade bracket under PER_TRADE_KEY. The spec_rule arm writes stop, target and time barrier to the
> candidate row at decision_utc, before any outcome. The twin is the control's candidates in the same cell, replayed
> with that bracket. control_match_refusal passes only when both carry the key.

- **The key.** Arm and control both carry
  `params.per_trade_bracket: "spec_exit:<spec_hash[:12]>"` (`arms.PER_TRADE_KEY`). They carry equal
  `PER_TRADE_SAME` params:
  - `hold_bars`, the spec's time exit in bars of the arm's timeframe;
  - `min_stop_usd` 1.00;
  - `max_stop_usd`, the largest stop whose loss at 0.01 lot fits the per-trade cap;
  - `end_utc` = `until` 22:00 UTC.

  The construction is one shared function, `arm_rules_spec.bracket(spec, bars, t)`, used for live, replay and the
  twin. It evaluates the spec's exit template (stop, target, time barrier) on the bars closed at the decision.
- **Written at the decision.** The arm writes `stop_dist`, `target_dist` and `max_hold_s` in the candidate's
  `exploration_obs` INSERT at its `decision_ns`, before the entry.
  - **Recompute check** (quant ruling 2026-11-18, Q6). This is what decides whether the bracket was fixed before
    any outcome. The stored `stop_dist`, `target_dist` and `max_hold_s` must equal
    `arm_rules_spec.bracket(spec, bars closed <= decision_ns, t)`, recomputed from the bars, within 1e-9. Otherwise
    the candidate is void.
    - The function reads no bar after `decision_ns`. If any bar after `decision_ns` is perturbed or removed, the
      bracket must not change (test T6).
  - **Latency guard.** All three fields must be set in that INSERT, with `recorded_ns <= decision_ns + 60 s`, and
    `recorded_ns < entry_ns` when the candidate is taken.
  - A bracket that fails either check is refused: a field set later by UPDATE, one recorded after the bound, or one
    that does not match the recompute. The candidate is void for the read and is not sent.
  - Any refused bracket on an arm holds its entries and is journalled (fail closed).
  - A stop outside [`min_stop_usd`, `max_stop_usd`] is skipped and recorded (`stop_lt_floor` / `stop_gt_cap`), never
    resized.
- **The control.** A dedicated replay-only control row per arm, role control, same timeframe, same key and
  `PER_TRADE_SAME`:
  - It draws random entries at the arm's bar closes: a seeded Bernoulli at the arm's explore signal rate, frozen at
    registration, with side 50/50. The seed is (arm id, bar close).
  - Each control candidate gets the same bracket construction evaluated at its own decision bar, is recorded skipped
    `replay_only`, and is labelled by `exploration_data.label_candidates`.
  - **No control places an order.** The common filters drop arm and control candidates alike.
- **The twin.** For each replayed arm candidate, taken or refused, the twin is the mean r2 replay label of the
  control's candidates in the same (UTC hour, side) cell, decided on or before the candidate's session day (point in
  time). Replay is compared with replay, with no fill cost on either side (arm-lifecycle section 1).
- **Matching.** `control_match_refusal(arm, control)` takes the per-trade route only when both carry
  `per_trade_bracket` (`_per_trade_refusal`). The engine and `load_arms` refuse a discovered arm whose control does
  not match.

## 5. The reading rule (kill-only, HV Rule 3)

This is the arm-lifecycle read of `docs/research/arm-lifecycle-2026-11-18.md` (branch `feat/arm-lifecycle-auto`,
R159). It applies to these arms with the values below, and to nothing else. **It retires; it never promotes,
qualifies or sizes.**

### 5.1 Nightly, 06:25 UTC, per (broker, arm)

RETIRE on the first of these:

1. **Loss cap.** Cumulative net realised P&L since `since`, after spread, commission and swap, is <= -£60.
2. **Last day.** The day is after `until`. No candidate is decided at or after `end_utc`.
3. **300 admissible fills.** Read once (5.3), then retire.
4. **Worse than its twin.**
   - The excess of the arm's replayed candidates over the twin, on the frozen day terms, reaches E_RETIRE = 40 on the
     worse stream (H0: excess >= 0; `eval2.forward.e_edge`, anytime-valid).
   - This needs at least MIN_N 50 replayed candidates over at least MIN_DAYS 10 session days, at most 10 %
     unmatched, and a validated twin (replay-parity monitor PASS).
   - Under the null this retires at most 1 arm in 40 over its whole life, whatever the number of looks.
5. **Bracket integrity.** A refused bracket (section 4) on 3 or more candidates, or on any taken candidate.

Otherwise the arm is on HOLD, with the reason: control unvalidated, insufficient, not readable (> 10 % unmatched),
or no evidence. "Better than the twin" is journalled and labelled "reported, not evidence". It changes nothing.
Retirement uses the engine's own path (`exploration_arm_status`). It is permanent for the broker; every exit of an
open position is kept.

### 5.2 Not read

- The mSPRT futility rule of `prereg-amendment-2026-11-07-kill-only.md` rule 4 compares arm fills with control fills.
  These controls have no fills, so it does not apply here. 5.1 (4) is the evidence kill.
- Expected power from the lifecycle doc: a 0.8 R deficit retires in about 30 days. A 0.3 R deficit needs about 100
  days or more, beyond `until`, so the loss cap and `until` decide those arms.

### 5.3 The 300-fill read, descriptive only

At the 300th admissible fill since `since`, these are reported once, per arm:

- net GBP and n;
- mean `label_fill_r`;
- the live-minus-replay gap;
- the excess over the twin;
- the cost per trade.

No p-value and no DSR from this read is evidence. The arm then retires. At 1h-1d frequencies few arms will reach
300 fills before `until`; they retire at `until` with the same report.

### 5.4 After retirement

- The spec hash is never registered again under this amendment, by any arm id: no re-run.
- What was learnt goes to the search only as a grammar-node prior (ADR 0090 section 3, `exp(2 x sum R / (n + 50))`
  clipped to [0.5, 2.0]). The G0 null reruns with the same priors, so the steering is priced.
- A spec that merits more becomes a new pre-registration with its own K and a fresh-data confirm.

## 6. Requirement (5) and requirement (6)

> (5) The holdout 2025-10..2026-09 is never touched.

- The search reads only the explore window, through `cost_of_record.load_research_bars`. That refuses any window past
  2018-01-01 before a file is opened, and opens only the explore years.
- The registration bar (2.4) reads only explore records.
- Demo fills and priors date from 2026-10 onward and are labelled from live bars, never from the 2025-10-01 ..
  2026-09-30 holdout files.
- No step of this amendment opens a confirm or holdout read. The stage journal is read, never opened (ADR 0090
  section 7).

> (6) The required tests: registration without a K row is refused; registration over budget is refused; booked K
> equals the screened configurations; a bracket written after decision_utc is refused.

These tests must pass on main before this amendment is effective: T1-T4 are the ruling's required four, and T5-T9 were
added by the quant-reviewer's CHANGES of 2026-11-18. Suggested homes: `tests/test_autodisc_register.py`,
`tests/test_discovery_k.py`, `tests/test_spec_bracket.py`, `tests/test_phase_gate_pin.py`.

| # | Test | Asserts |
|---|---|---|
| T1 | `test_registration_without_a_k_row_is_refused` | `autodisc-register` with the K write failing, or the `k_id` not resolving, writes no `arm_registry` row; `learning_refusal` refuses the arm; the engine gives it no entry. |
| T2 | `test_registration_over_the_quarter_budget_is_refused` | with `used + reserved + 1 > quarter_budget(q)`, nothing is written and the refusal carries the quarter's numbers. Also: a 6th concurrent arm is refused (2.3), and an arm whose kill rule is unreadable by `until` is refused (2.4 (f)). |
| T3 | `test_booked_k_equals_the_screened_configurations` | after a night, and after a night killed mid-timeframe and re-synced, the summed `k_contrib` of the night's batches equals the ledger's distinct evaluated spec hashes (search + neighbours), and `n_trials_discovery` rises by exactly that. Admission refusals are reported as `n_refused_power` and are not in it. |
| T4 | `test_a_bracket_written_after_decision_utc_is_refused` | a candidate is refused when any of these holds: its `stop_dist`/`target_dist`/`max_hold_s` was set by UPDATE; its `recorded_ns > decision_ns + 60 s`; or the stored bracket differs by more than 1e-9 from `arm_rules_spec.bracket(spec, bars closed <= decision_ns, t)` recomputed. Refused means not sent, void in the lifecycle read, entries held, journalled. A bracket in the INSERT at the decision that matches the recompute passes. |
| T5 | `test_power_admission_is_outcome_blind` | the ADR 0085 admission verdict of every spec is identical with the labels as they are, permuted, or all NaN. |
| T6 | `test_bracket_is_unchanged_by_bars_after_decision` | perturbing or removing every bar after `decision_ns` leaves `arm_rules_spec.bracket(spec, bars, t)` unchanged (truncation test). |
| T7 | `test_phase_gate_reads_discovery_k_for_discovered_specs` | `phase_gate_refusal` reads `n_trials_discovery` for a spec whose origin is discovery, and `n_trials_effective` for every other spec. |
| T8 | `test_prior_search_seed_is_outside_quarter_k_but_inside_n_trials_discovery` | the `PRIOR_SEARCHES` seed rows (grammar v1, K 864) are excluded from `quarter_k_used` in every quarter, and included in `n_trials_discovery`. |
| T9 | `test_a_change_to_the_control_after_registration_voids_the_arm` | the control is recorded `NOT_TRIAL`, linked to the arm, and adds no K. Any change to its frozen parameters after registration refuses the arm's entries, voids its reads from the change onward and retires it. Its K row stays. |

Where they live: T5 `test_power_admission_is_outcome_blind` and T8 are in
`tests/research/autodisc/test_discovery_nightly.py` on branch `feat/discovery-nightly` (the admission code is on that
branch); T3 is covered by `test_sync_tops_the_registry_up_to_the_ledger_k_search_once` and
`test_the_nightly_job_records_its_batch_k_contrib_only_for_a_real_search` in
`tests/research/autodisc/test_registry_link.py`, with `n_refused_power` on `feat/discovery-nightly`. Until every
piece is on one branch, `autodisc_register.LIFECYCLE_READY` is False and every registration is refused ("amendment
not effective: lifecycle voids, forward power, T5 pending (section 6)").

Also required, from the same ruling: `control_match_refusal` passes a discovered arm and its control only when both
carry `per_trade_bracket` with equal `PER_TRADE_SAME` params, and refuses when one lacks it.

## 7. Sunset and the line's own kill

- **No registration under this amendment after 2027-03-31.** Arms registered before then run to their `until` (at
  most 2027-03-31).
- Renewal needs a new written amendment that reports this one's results: arms registered, retired and why, net GBP
  per arm against its twin, and the K spent.
- The discovery line's continuation rule is in `docs/research/discovery-k-cap-2026-11-18.md` section 1. Zero
  registrable survivors in 2026Q4 closes it, and this amendment then registers nothing.

## 8. Not changed

- RiskGate, the per-trade cap, the daily and weekly limits, sizing, the phase gate, live unlock.
- The holdout.
- `research.trial_budget_quarter` and its overrides.
- Any existing arm, the red-box week arms.
- ADR 0085 and ADR 0088 semantics.

No K row is written by this document.
