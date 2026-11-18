# 0089: Demo-only: window_0207, N-3 and session entry caps become pre-registered arm parameters

Status: Proposed (research-director, 2026-11-18). Needs quant-reviewer and trading-safety-reviewer sign-off. Applies
to **demo accounts only**. For real money every refusal stays global. Takes effect only after the red-box week ends
(2026-11-22 22:00 UTC) and after it is implemented through the normal lane. The red-box arms keep window_0207, N-3 and
the session budgets for their whole week.

## Context
- **The owner's directive (2026-11-18, firm):** the system designs, improves and retires strategies itself, with no
  rule restrictions from the owner.
- **Three engine-wide refusals apply to every gold exploration arm on demo.** None of them was tested as part of the
  arms it refuses:
  - **window_0207:** gold entries with decision time in [02:00, 07:00) UTC are refused
    (`goldbot/engine/entry_refusals.py`, `prereg-amendment-2026-11-07-kill-only.md` section 3). Its own disclosure
    says the window was chosen after looking at the demo data: -£394.7 on 634 fills, the worst of about 24 hourly
    cells, with roughly £100-150 of that being selection. Its only reopening path is section 3.3.
  - **N-3:** the HAR low-range-day abstention (`goldbot/engine/refusals_novel.py`, `prereg-2026-11-07-n3-har.md`,
    K +1). It is a conditional-gross claim, refused at fail-closed availability.
  - **The session entry budgets:** the account's daily entry budget is split into asia, london, newyork and
    late_asia shares that roll forward (`goldbot/engine/exploration_budget.py`,
    `prereg-amendment-2026-11-09-session-roll-forward.md`). A signal past its segment's share is refused as
    `session_entries`.
- **Global refusals cannot be learned from.** Each one applies one fixed configuration to every arm, so the search
  can never test whether a family does better with or without it. The red-box pre-registration (rule 10) left open
  "whether the owner's 'every' should override window_0207 or N-3 ... a separate decision for the research
  director".
- **The why-fail audit** (`docs/research/why-strategies-fail-2026-11-15.md`, section 1) measured the ICM cost by
  session: Asia $0.111/oz, London $0.063, New York $0.080. Asia costs most, which is part of why the 02-07 window
  lost. Costs differ by session, so an arm can only be judged with or without a window at the right cost cell.

## Decision
1. **Three arm parameters, inside the arm hash, for gold arms on demo:**
   - `entry_filters.window_0207: on | off`
   - `entry_filters.n3_har: on | off`
   - `entry_filters.session_budget: shared | day_only`
   The values are a closed enum. The search may not choose free time windows or thresholds. Any other window, side
   or hour cut is a new card (kill-only amendment section 9).
   - **`window_0207: off` is a new card under kill-only section 9.** Its history screen must meet section 9's
     criteria at the upper CI of the Asia v3 cost cell:
     - gross excess > 0 against the matched control;
     - cost per R < 0.06;
     - gross >= 2x cost.
   - This ADR amends kill-only section 9 for demo only. If the section 3.3 read reverts the block, the parameter is
     moot.
2. **The default is today's behaviour.** A row without `entry_filters` behaves as on / on / shared. No existing
   arm's hash, id or K row changes. Each non-default value is a different arm hash and its own K row, so every
   variant the search tries is counted.
3. **The twins inherit the arm's filters.** An arm's replay twins (ADR 0086) use exactly its `entry_filters`, so the
   comparison stays matched. The refused-candidate shadow log keeps recording, so every "on" arm still has the
   counterfactual for "off".
4. **`day_only` leaves the session split only.** It stays inside the account's `max_per_day`, `max_per_hour`,
   `scalp_day_cap` and the thinning of paired controls. Every capacity rail stays global. An arm on `day_only` is
   left out of the session-share computation, so it cannot strand or take another segment's share. Each
   `day_only` arm is capped at floor(max_per_day / active edge arms) fills a day, fixed at registration (quant
   ruling 2026-11-18).
5. **Real money: global and unchanged.** On any account that is not demo, `entry_refusals` applies window_0207,
   N-3 and the session budgets as today.
   - **Demo means ALL of:** accounts.yaml `mode: demo`, no live gate in `phase_file`, the terminal's own
     `trade_mode == "demo"`, and `probe_only`.
   - One shared predicate checks all four. Missing or unreadable means not demo.
   - It is checked at load, on every re-read, at the signal, and again by the RiskGate on every order
     (`exploration_not_demo`, `exploration_terminal_not_demo`). A loader check is never sufficient alone.
   - A non-demo arm row carrying `entry_filters` is refused at load, not silently ignored, and the engine also
     ignores the field.
   - A real-money history qualification is run only on the on/on/shared variant, under its own hash. Demo evidence
     from an arm with filters off never carries over to real money.
   - A test pins this for every refusal.
6. **These never become arm parameters:** the RiskGate, the per-trade cap, the £220 day, the weekly 6 %, the
   rollover, Friday-close and tier-1 blackout filters, the stop band, F1_PIT on its cost arms, `learning_icm_only`,
   and every exit. They stay global on every account.
7. **Pricing.** An arm that trades 02:00-07:00 is charged the Asia cell of the v3 cost of record (ADR 0084), at
   its upper CI, in every screen and read.

## Consequences
- The search can learn whether a family needs a filter, instead of every arm inheriting a filter chosen from 10 days
  of demo data.
- The kill-only amendment gains a second reopening path, for demo only, by explicit variant. It goes through
  section 9's new-card criteria (Decision 1). The quant-reviewer ruled this on 2026-11-18.
- Off-variants will trade the hours that lost before. That demo loss is bounded by the arm's loss cap, the
  account's day and the entry budget.
- Implementation, each step through review:
  - `entry_refusals` and `exploration_budget` read the arm's `entry_filters` only when the account is demo;
  - the arm schema and `arm_hash` gain the field, absent meaning the default;
  - boundary tests run on both account modes.

## Risks and guards
1. **Overfitting by filter mining.** Time windows are the classic data-snooping knob. *Guard:* the closed enum
   (two values each, not free windows), K counted per variant, a history screen at measured session cost, the
   forward confirm under alpha (ADR 0086/0088), and no hour, side or window variants without a new card.
2. **The gap between live fills and the replay in thin hours.** Asia fills slip more, and demo may flatter them.
   *Guard:* the Asia cost cell at its upper CI. The ADR 0086 parity monitor runs per session for off-variants. On
   real money the filters stay on whatever demo shows.
3. **Budget starvation.** A `day_only` arm could use up the day early. *Guard:* it stays inside `max_per_day` and
   `max_per_hour`. Whether it also needs its own share cap is an open question below.
4. **N-3 off or a missing HAR row.** `n3_har: off` drops both N-3 refusals (`har_unavailable` and `har_low_range`)
   for that arm only. *Guard:* an arm with N-3 on still fails closed on a missing or stale HAR row, and the HAR
   job's health alert is unchanged.

## Review rulings (2026-11-18)
- quant-reviewer:
  - `window_0207: off` reopens only as a kill-only section 9 card, for demo only.
  - Off-variant controls are twins only.
  - A `day_only` arm is capped at floor(max_per_day / active edge arms), fixed at registration.
  - Real-money qualification runs only on the on/on/shared variant.
- trading-safety-reviewer:
  - Demo is the four-part shared predicate.
  - A non-demo row carrying `entry_filters` is refused at load, and the engine also ignores the field.
  - Tests are listed in ADR 0087's "Required tests" (items 7 and 8).
