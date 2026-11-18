# 0087: Machine-registered demo arms: no weekly cap, no PR per arm (amends 0045)

Status: Proposed (research-director, 2026-11-18). Needs quant-reviewer and trading-safety-reviewer sign-off.
Amends ADR 0045 for **machine-registered demo arms only**. Takes effect only after the red-box week ends
(2026-11-22 22:00 UTC) and after it is implemented through the normal lane. Nothing here touches the red-box week.

## Context
- **The owner's directive (2026-11-18, firm):** the system designs, registers, runs and retires strategies itself.
  The money rails (RiskGate, live unlock, owner-confirmed real-money entries, no secrets in the repo) are unchanged.
- **ADR 0045, tier 2** allows at most 4 reviewed variant proposals a week, each going through pre-registration,
  quant and trading-safety review and a PR before it trades. This is enforced in code as `WEEKLY_MAX = 4` in
  `goldbot/ops/autodisc_register.py:64` (checks at :198 and :352). `autodisc_register` writes
  `config/spec_arms.yaml` and `config/accounts.yaml`, and "nothing runs until those files are merged and the
  engine restarts". A machine search that finds 10 passers needs 3 weeks of calendar time and 10 PRs.
- **Tier 3 ("never daily")** bans daily per-arm looks, because a look that can stop an arm is optional stopping.
  That is true for fixed-n tests. It is not true for a pre-registered e-process (test martingale). By Ville's
  inequality, P(sup_t E_t >= 1/alpha) <= alpha, so a read at every fill, stopped at any time, keeps its level. The
  kill-only amendment of 2026-11-07 already runs an mSPRT on this basis.
- **The why-fail audit** (`docs/research/why-strategies-fail-2026-11-15.md`, section 4) warns the other way: "volume
  made it worse". K went from about 1,258 to 21,333 in two weeks, which moved the deflated-Sharpe t from 4.96 to
  5.69. Tests with no power were the main thing feeding K. Removing the cap therefore needs a better guard than a
  count.

## Decision
1. **A hot-loaded registry.** Machine-registered demo arms are written to an append-only, hash-chained table in the
   brain's `aux.db` (working name `arm_registry`), not to config files.
   - **The row:** the frozen arm row (`rule: spec_rule`, validated by `arm_rules_spec.check`), its pre-registration
     (hash-chained, with the ADR 0086 forward read: twins, statistic, e-process, alpha_i, floors, kill rule, end
     date), its K row id, its account and its pot.
   - **The engine** re-reads the table on its existing minute cadence (as it already does for owner-experiment
     expiry) and validates each row again. A malformed row refuses only its own arm and never stops the load
     (ADR 0061's fail-closed rule).
   - **No PR per arm and no engine restart.** Code is different. A new rule function, DSL leaf or engine behaviour
     still goes through a PR and review. Only specs the existing DSL can express hot-load.
   - **What a row may not set.** A row that sets any of these is refused:
     - membership of `growth_arms`;
     - a lot above the minimum;
     - the roles `owner_experiment`, `cost_measurement` or `learning_probe`;
     - a per-trade cap above the style ceiling;
     - any exit or bracket-widening parameter;
     - an account outside the configured demo `probe_only` list.
   - **Size is part of the arm.** Size and resize are in the arm hash, so a resize is a new arm, never an in-place
     edit.
   - **Every order** of a hot-loaded arm goes through `purpose=exploration` (`goldbot/risk/gate.py:487-492`). There
     is no lighter order path.
   - **The sealed holdout.** Machine-registered arms never read 2025-10..2026-09.
2. **`WEEKLY_MAX` no longer applies to these arms.** In its place, three guards apply:
   - **ADR 0085's power-first start rule** applies to every machine card. A card with power below 0.5 at
     delta 0.10 R, or with expected n_eff below 200 at its gate, is refused before registration and costs no K.
     Power-first is computed at the arm's alpha_i and at the e-process's sample size up to its end date.
   - **K counts arms only.** Each arm is a K row. Twin sets are controls, so they are `NOT_TRIALS` and not K
     (quant ruling 2026-11-18).
   - **Every registration spends alpha** from the single ledger (ADR 0088). Each additional arm gets a smaller
     alpha_i, so volume is priced continuously, not capped. No per-family concurrency cap is needed for honesty
     (quant ruling 2026-11-18).
   - **Every capacity rail binds as today:** demo-only, probe_only, exploring accounts; the per-trade cap inside
     the style's caps; the pots; the ADR 0057 room floor; the account's daily entry budget; the £220 day; the weekly
     6 %; the RiskGate.
   All of `register`'s other checks stay.
3. **Pre-registered sequential stops are tier 1 (automatic), not looks.** The machine may evaluate a registered
   arm's e-process at every fill. It retires the arm at the futility boundary, loss cap or end date, and marks it
   demo-qualified at the rejection boundary (ADR 0086), all automatically. Nothing else is decided from per-arm
   results daily: thresholds, priors, caps and bandit settings stay "never daily".
4. **Blinding for people is kept.** The daily review and every human-facing report still show no per-arm R, rank
   or sign while a read is open (`docs/ux/daily-learning-report.md`). Results are shown once a read ends (pass,
   futility, cap or end), in the weekly report. Human-proposed variants keep ADR 0045 tier 2 unchanged: 4 a week,
   reviewed, by PR.
5. **Real money is untouched.** No hot-loaded row can name an account that is not demo.
   - **Demo means ALL of:** accounts.yaml `mode: demo`, no live gate in `phase_file`, the terminal's own
     `trade_mode == "demo"`, and `probe_only`.
   - One shared predicate checks all four. Missing or unreadable means not demo.
   - It is checked at load, on every re-read, at the signal, and again by the RiskGate on every order
     (`exploration_not_demo`, `exploration_terminal_not_demo`). A loader check is never sufficient alone.
   - A real-money arm still comes only through config, a PR, review and the phase gate.
6. **Public record.** Arm rows live on the brain, not in the public repo. A weekly aggregate (arms registered,
   retired, passed; K; alpha spent) is committed under `docs/research/`. It contains no account identifiers, fills or
   market data.

## Consequences
- The search-to-demo loop runs without a human step. Throughput is bounded by power, alpha and the accounts'
  capacity, not by a weekly count or by PR latency.
- Config files stop being the full arm inventory. `aux.db` holds the machine arms, so it must be in the backup
  scope (ADR 0002) and in the health line's arm counts. The arm-inventory tests must read both sources.
- ADR 0061's statement that `ops.autodisc_register` "must write a qualification before a discovered arm can trade"
  is met by the ADR 0086 forward route, or by the learning class with its own bounds.
- Implementation, each step through review:
  - the registry table and migration;
  - the engine loader and minute re-read;
  - `autodisc_register` writes to the table, with `WEEKLY_MAX` scoped to the human tier only;
  - the e-process evaluator in the daily job;
  - the weekly aggregate export.

## Risks and guards
1. **A flood of correlated arms (overfitting by multiplicity).** *Guard:* the power-first rule, alpha spent per
   registration from the one FWER ledger (valid under arbitrary dependence), K counted per arm, failed hashes never
   re-registering, and the account's entry budget capping total fills.
2. **Optional stopping in disguise.** For example, re-registering a stopped arm under a new hash with a tweak, or
   choosing the e-process after seeing data. *Guard:* the e-process, alpha_i and boundaries are inside the
   hash-chained pre-registration before the first fill. A spec with the same multiset of DSL leaves, the same
   timeframe and the same instruments as a retired spec is a re-run and is refused (quant ruling 2026-11-18;
   research-director hard rule 3).
3. **A bad row, or a broken registry, reaching the engine.** *Guard:*
   - A bad row refuses only its arm.
   - **Hash-chain break or partial write.** Readers see only committed WAL transactions. If the chain breaks or a
     write is partial or uncommitted, every hot-loaded arm refuses new entries until the chain verifies, and an
     alert is raised.
   - **DB lock or read error.** The engine keeps the last good snapshot for at most 3 cadences, then refuses new
     hot-loaded entries. The limit is never extended.
   - **Restart mid-load.** The engine starts with zero hot-loaded arms until a full validated load.
   - **Open positions.** A retire, a resize or a lost snapshot with open positions blocks new entries only. SL, TP,
     time, weekend, the kill switch and reconciliation run from the position's own persisted brackets, and never
     look up the row.
   - **Overrides.** The kill switch, halts, propose-only and the drift and account-class rules override every
     hot-loaded arm.
4. **The gap between live fills and the replay.** *Guard:* ADR 0086's parity monitor voids a read whose own fills
   drift from the replay.
5. **Lost audit trail.** *Guard:* the table is append-only and hash-chained, backed up, and summarised weekly in
   the repo.

## Required tests (trading-safety-reviewer, 2026-11-18)
Each is written failing first, before the loader ships.
1. The demo predicate over all 16 combinations of its four inputs: only all-four-true is demo.
2. A live flip mid-arm (the phase file gains a live gate or the terminal reports real): the next signal is refused,
   and so is the next order at the RiskGate.
3. A forward hash placed in a real-money config is refused at load and at the gate.
4. Each forbidden field (Decision 1, "What a row may not set") refuses its row.
5. The loader under a DB lock, a truncated row, a broken hash chain and a restart mid-load, each with the behaviour
   in Risks 3.
6. Retire or resize with an open position: new entries are blocked, and SL, TP, time, weekend, kill switch and
   reconciliation still act from the persisted brackets.
7. `entry_filters` on a non-demo account: refused at load and ignored by the engine (ADR 0089).
8. `day_only` shares: the per-arm cap holds, and no other segment's share is taken (ADR 0089).
9. `phase_gate_refusal` rejects any verdict with confirm `forward:*` or ledger `adr0088:*`.

## Review rulings (2026-11-18)
- quant-reviewer:
  - No per-family concurrency cap is needed for honesty.
  - A re-run is the same multiset of DSL leaves, timeframe and instruments.
  - Power-first is computed at alpha_i.
  - Twin sets are NOT_TRIALS.
  - The holdout is never read.
- trading-safety-reviewer:
  - The four-part demo predicate.
  - The forbidden fields.
  - The loader failure modes in Risks 3.
  - `purpose=exploration` only.
  - The required tests above.
