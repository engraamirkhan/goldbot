# 0086: Demo arms may confirm on a pre-registered forward read against a replay twin (amends 0061)

Status: Proposed (research-director, 2026-11-18). Needs quant-reviewer and trading-safety-reviewer sign-off.
Amends ADR 0061 for **demo accounts only**. Takes effect only after the red-box week ends (2026-11-22 22:00 UTC) and
after it is implemented through the normal lane (failing tests first, reviews, release). Nothing here touches the
red-box week (`prereg-amendment-2026-11-15-redbox-week.md`).

## Context
- **The owner's directive (2026-11-18, firm):** "no input from me on strategies; the system designs, improves,
  executes, analyses, feeds back, finds new and retires strategies itself; no rule restrictions from my side." The
  money rails are not part of the directive: the RiskGate is the only path to an order, the live unlock is the
  owner's typed phrase, real-money entries are confirmed by the owner, and the public repo holds no secrets.
- **ADR 0061** lets a demo strategy arm enter only with a history `qualification`: a screen (reference 2010-2017)
  and a confirm (2018-01..2025-09), both passing at measured cost. The only other routes are the bounded,
  never-evidence learning class (amendment 2026-11-02) and the closed `owner_experiment` list.
- **The why-fail audit** (`docs/research/why-strategies-fail-2026-11-15.md`, branch `research/why-fail-1115`, base
  9d1abbc7) shows that the history confirm cannot pass a real slow edge. Take a true net edge of +0.10 R a trade
  (sd 1.2 R). The 2018-25 confirm at batch 15 passes it 6 % of the time at 15m, 1 % at 1h and about 0 % at 4h
  and 1d (section 3). ADR 0085 rebuilt the confirm as a Holm alpha-spending read on fresh data. Even so, the 2018-25
  confirm window is one fixed span, and every family that reads it spends it.
- **The audit also shows the replay is a fair instrument.** On 3,653 demo fills, the replay agrees with the broker on
  the barrier 94.5 % of the time. The trade-level correlation is 0.913 and the gross bias is -0.012 R (section 1).
  Broker-traded random controls were the largest P&L item on demo: -£572 over 3 weeks, against -£51 for all learning
  arms (section 4; re-derived for this ADR from the audit's per-account table: -282.80 - 83.90 - 205.34 = -572.04
  and +77.38 - 52.64 - 75.37 = -50.63). A demo read against one control at 300 fills has 27 % power. Against 20
  replay twins it has 41 % (section 3).
- So a forward demo read is fresh, out-of-sample data that nobody has seen. Read against replay twins, it is the
  cheapest confirm the program has. Today it can only count as "learning", never as a confirm.

## Decision
1. **A demo-only confirm route, `forward`.** A demo strategy arm's `qualification` may name a forward read as its
   `confirm`, in the form `forward:<registry id>`. Its `screen` is still a history screen that ends before
   2018-01-01, at measured cost, as ADR 0061 requires. Only the confirm changes. The route is valid only on demo
   accounts.
   - **Demo means ALL of:** accounts.yaml `mode: demo`, no live gate in `phase_file`, the terminal's own
     `trade_mode == "demo"`, and `probe_only`.
   - One shared predicate checks all four. Missing or unreadable means not demo.
   - It is checked at load, on every re-read, at the signal, and again by the RiskGate on every order
     (`exploration_not_demo`, `exploration_terminal_not_demo`). A loader check is never sufficient alone.
   - Every forward arm's order goes through `purpose=exploration` (`goldbot/risk/gate.py:487-492`). There is no
     lighter order path.
2. **What the forward read must pre-register,** hash-chained in the registry before its first fill. The arm is
   frozen by its arm hash. The read names:
   - **The replay twins.** At least 20 seeded twins per arm, replay-only (no order is ever sent). Each twin uses
     the arm's timeframe, bracket construction and every entry filter the arm uses (ADR 0089). Twins are matched
     on (session day, session, side) cells and scored by `exploration_data.label_candidates`.
     - **Seed:** seed = hash(arm_hash, session_day, session, side).
     - **Decision times:** uniform over the cell's admissible times. They never depend on prices.
     - **The floor is 20 twins** (quant ruling 2026-11-18).
   - **The statistics.**
     - **The excess:** the matched excess per candidate, in R. It is the arm's **replayed** candidates (taken and
       refused) minus the twins' replayed candidates, on the same research bars. Both sides are replays, so the
       excess is cost-neutral and blind to fills.
     - **The net:** net R on the arm's replayed candidates, taken and refused. Spread comes only from the bid/ask
       labels. Slippage, commission, and swap for every rollover held through come from cost-of-record v3
       (ADR 0084) at the upper CI.
   - **The test.**
     - **Form:** a betting e-process (GRAPA/ONS) on day-level means. The bound comes from the bracket. Gaps beyond
       the stop are clipped, and the clip rate is reported. It is not mSPRT, because R is skewed.
     - **Both e-processes must pass.** The excess and the net each run as an e-process, and both must reach
       1/alpha_i. This is an intersection-union test, so alpha is not split. alpha_i is the spend drawn from the
       single ADR 0085/0088 ledger when the arm is registered.
     - **Stopping:** Ville's inequality makes the rejection valid at any stopping time, so a continuous read is not
       optional stopping (ADR 0087).
     - **Futility:** the boundary is binding. It need not be anytime-valid, and it is used in the power figure. The
       read also has an end date.
   - **The floors.** MIN_FRESH_N_EFF 200 clustered observations (ADR 0085) and at least 20 session days.
   - **The kill rule** and the arm's loss cap (as the learning class's: 20 x its per-trade cap).
3. **A pass** writes `verdict: pass` with `confirm: forward:<id>` and `cost_basis: measured`. The arm is then
   **demo-qualified**. It may run past the learning class's 45-day and 300-fill bounds, it is ranked with the
   families, the bandit may allocate it, and it is listed in `docs/research/live-candidates.md` as "demo-qualified,
   not history-confirmed". A futility stop, the loss cap or the end date retires the arm. Its hash never
   re-registers, as today.
   - **Demo-qualified changes no sizing, cap or growth status.** The bandit allocates only within the existing
     min-lot caps.
   - `qualification_refusal` refuses `forward:` on any non-demo account.
   - `phase_gate_refusal` and the real-money live-candidate route reject any verdict whose confirm is `forward:*`
     or whose ledger is `adr0088:*`.
4. **Real money is unchanged.** The forward route qualifies nothing for real money. Real money still needs ADR 0061's
   history screen and history confirm, then the owner's phase gate (DSR at the full registry K, pinned by ADR 0085
   section 4), the live unlock phrase and owner confirmation of each entry. A forward pass is a reason to run that
   history qualification. It is never a substitute for it.
5. **No reuse of seen data.** A forward read counts only candidates decided after its registration row's timestamp
   and after this ADR's effective date. The red-box week's fills (RB15/RB1H/RB4H and their controls) and every
   learning-class fill stay learning evidence. They can never be a forward confirm. ADR 0061's learning-class rule
   still applies: a confirm citing a learning K row is refused.

## Consequences
- Demo can confirm strategies on its own, with fresh data, without anyone looking at per-arm results. A demo strategy
  arm no longer needs a 2018-25 read, which every family has already spent.
- **Power is honest, and it is low for slow arms.** An oracle e-process needs about n = 2 sd^2 ln(1/alpha) / delta^2
  trades. At delta 0.10 R and sd 1.2, the first forward read of a year-1 month (alpha 2.6e-4, ADR 0088) needs about
  2,380 trades, and the tenth (alpha 4.7e-6) about 3,530. Real betting e-processes need more, and the net
  e-process must pass as well. Forward confirm is practical at 5m-15m, for pooled families, or for edges around
  0.2 R (about 590-880 trades). A 1h-1d arm still relies on history and on ADR 0085.
- The engine gains an evidence class, `forward_confirm`. The perf report and the daily review show it apart from
  learning and from qualified arms until its read ends.
- Twins replace broker-traded controls for these reads, which removes the control leak the audit measured.
- Implementation, each step through review:
  - `research.arms.qualification_refusal` accepts `forward:` only on demo;
  - the registry gets a forward-read row type with the ledger stamp;
  - a twin generator is built and scored by `label_candidates`;
  - the e-process and its parity monitor are built (risk 2 below).

## Risks and guards
1. **Overfitting through selection among many forward arms.** If many arms run, some will pass by luck.
   *Guard:* every pass spends alpha from the one global FWER ledger (ADR 0085, extended by ADR 0088). The arm's
   parameters are frozen by hash before data. A failed hash never re-registers. A history screen is still required
   before the forward read. K is counted and reported for every arm. Twin sets are controls: they are
   `NOT_TRIALS` and do not count in K (quant ruling 2026-11-18).
2. **Gap between live fills and the replay.** The statistic is computed on replays, but the money comes from broker
   fills. Demo also gives about $0.08 of positive target slippage that real money may not give (audit section 1).
   *Guard:* a parity monitor on the arm's own fills checks barrier agreement against the replay and the gross bias
   per fill. If barrier agreement falls below 90 %, or the gross bias exceeds $0.05/oz over the trailing 300 fills,
   the read is **voided**, not passed. These are the audit's plan-line-1 bounds. The net e-process charges costs
   from cost-of-record v3 at the upper CI, not from demo's favourable fills. Real money keeps the history route
   and the phase gate, and the first real fills re-check target slippage.
3. **One regime.** A forward window of a few weeks covers one regime. *Guard:* at least 20 session days, the
   clustered n_eff floor of 200 and the end date. A demo-qualified arm is re-read whenever its rolling excess
   breaches the futility boundary, which is a kill-only rule.
4. **Twin miscalibration.** If the twins are mis-specified, the excess is biased. *Guard:* twins inherit the arm's
   bracket, timeframe and filters. The twin generator is reviewed by quant before first use. The twins'
   replayed gross is reported against the broker-traded cost probes kept by the cost protocol.

## Review rulings (2026-11-18)
- quant-reviewer:
  - The twin floor is 20.
  - The test is a betting e-process (GRAPA/ONS) on day-level means, not mSPRT.
  - The futility boundary is binding and need not be anytime-valid.
  - The net and the excess pass together (intersection-union).
  - Twin sets are NOT_TRIALS.
- trading-safety-reviewer:
  - Demo is the four-part shared predicate, checked at load, at re-read, at the signal and in the RiskGate.
  - `forward:*` and `adr0088:*` are rejected by `phase_gate_refusal` and the real-money live-candidate route.
  - Demo-qualified changes no sizing, cap or growth status.
