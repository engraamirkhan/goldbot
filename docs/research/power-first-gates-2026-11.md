# Power-first gates: re-derived power table and the L5 trial-count basis (2026-11-15)

Evaluator, base `9d1abbc7`. This memo proposes and decides nothing. Gate thresholds and the promotion path are
owner-only decisions, taken through the expert panel (quant-reviewer, research-director, CTO). It reads no returns,
writes no registry row and does not touch the holdout. **K +0.** The scripts are local and written independently of
`power.py`: `~/tmp/power-gates/rederive.py` (the hurdle found by root-finding on the `metrics.deflated_sharpe` formula,
power by noncentral t, plus a two-point-R Monte Carlo) and `~/tmp/power-gates/options.py`.

## 1. Re-derivation of the gate audit (why-strategies-fail-2026-11-15, section 3)

Setup: true net edge 0.10 R a trade, sd 1.2 R, one-sided tests, typical cell frequencies (5m 600, 15m 100, 1h 37,
4h 12, 1d 6 trades a year).

| timeframe | explore (8 y, t >= 2) | TEST-OLD (z 2.128) | confirm b15 (z 3.882) | L5 DSR K 21,333 (t 5.694) | demo vs 1 control | demo vs 20 twins |
|---|---|---|---|---|---|---|
| 5m | 1.000 | 0.775 | 0.964 | 0.992 | 0.266 | 0.407 |
| 15m | 0.638 | 0.169 | 0.058 | 0.008 | 0.266 | 0.407 |
| 1h | 0.284 | 0.077 | 0.006 | 0.000 | 0.266 | 0.407 |
| 4h | 0.117 | 0.041 | 0.001 | 0.000 | 0.266 | 0.407 |
| 1d | 0.076 | 0.031 (0.041 on 1d's real 3.92 y window) | 0.000 | 0.000 | 0.266 | 0.407 |

The deflated-Sharpe hurdle in t (DSR >= 0.95, `var_sr_trials` = 1/n, normal returns, n 2,000):

| K | 60 | 100 | 300 | 1,258 | 3,471 | 21,333 |
|---|---|---|---|---|---|---|
| t needed | 3.99 | 4.18 | 4.55 | 4.97 | 5.25 | 5.69 |

**Agreement.** Every cell in the audit's tables agrees within 0.02. That covers the power by timeframe, the hurdle by
K, trades for 80 % power (1,163 / 1,270 / 3,214 / 5,334 / 6,151 against the audit's 1,161 / 1,268 / 3,208 / 5,320 /
6,134; the audit uses z 0.84, not 0.8416), 12.6 and 66.7 years for an annual SR of 0.8, and the demo ranking errors
(15.4 % and 7.9 %).

**Differences and additions. None reverses the audit. Two make it stronger.**
1. *L5 excludes more than 1d.* At cell frequency over 15.75 years, `MIN_CANDIDATES` 1,500 fails at 1h (583), 4h
   (189) and 1d (94). `MIN_TRADES_FOR_DSR` 200 fails at 4h and 1d. `MIN_TRADES_PER_YEAR` 10 means 1d can never
   score a positive year. 15m passes only just (1,575). These counts fail no matter what the edge is. Only pooling
   lifts them: a rule that fires more often, or a family counted across instruments.
2. *The DSR at registry K is far stricter than a 5 % family-wise test.* Take 21,333 independent nulls. The hurdle
   t 5.69 gives a per-null false-pass rate of 6.4e-9 and an FWER of 1.3e-4. A Bonferroni 5 % FWER over the same K
   needs only z 4.58. Because the trials are correlated variants, the effective K is smaller, so the real FWER is
   lower still.
3. The hurdle hardly moves with the shape of R. On a two-point R (+1.41 / -1, skew 0.17, kurtosis 1.03) it is 5.67
   at K 21,333. A two-point Monte Carlo puts 1h explore at 0.265, against 0.284 for the normal approximation. Real
   trades are clustered, which lowers effective n. **Every power figure here is therefore an upper bound.**
4. Wording: about 6,100 trades gives **80 % power** at the L5 hurdle. A true 0.10 R edge reaches an *expected* t of
   5.69 at 4,669 trades.
5. The TEST-OLD read condition already in force (power >= 0.3, else INSUFFICIENT) means that at 1h-1d TEST-OLD is
   never read at all. The chain stops there and does not reach a fail.

## 2. Power-first rule (proposed)

Before a card starts, it states for **every gate it must pass**:
- the expected trade count, from outcome-blind signal counts;
- the smallest edge worth trading net of deciding cost: the larger of 0.10 R and the edge that ADR 0004's £50/day
  needs at the card's frequency;
- the sd;
- the gate's exact threshold, at the batch index and alpha it will actually receive;
- the resulting power.

**Rule.**
- Power must be >= 0.5 at each gate.
- The chain's joint power (simulated when gates share data) must be >= 0.25.
- A card that fails either test is recorded as "NOT STARTED (power)". It reads no data and costs no K.
- The rule generalises EVAL-2's existing 0.3 TEST-OLD floor. The floor moves to registration and applies to every
  gate.
- It lowers no bar. It stops tests that cannot pass, which are the main thing feeding K today.

## 3. L5's K basis: options

The FDR figures are computed over the same volume as today's registry: 21,333 independent nulls, and a pooled card
with n 2,000 at 0.10 R. π1 is the share of tested configs with a real edge.

| option | t needed | E[false passes] / FWER | power at n 2,000 | FDR at π1 0.5 % / 2 % | keeps ADR 0003's intent? |
|---|---|---|---|---|---|
| A. Registry K, global (today) | 5.69 | 0.000 / 1.3e-4 | 0.02 | ~0 / ~0 | yes, but nothing can pass |
| B. Per-family K (100; 300) | 4.18; 4.54 | 0.32 / 0.27; 0.06 / 0.06 | 0.33; 0.21 | 0.9 % / 0.2 %; 0.3 % / 0.1 % | **no**: cross-family selection is uncharged, and splitting a family resets its K |
| C. K reset per pre-registered program (60), same data | 3.99 | 0.70 / 0.51, growing linearly with every program | 0.40 | 1.6 % / 0.4 % at this volume, no bound | **no**: no ceiling on false passes; the same 2010-25 data is reused by every program |
| D. Pre-registered alpha-spending on fresh data | z(1 - α_j/m_j) | FWER <= q over all promotions ever, under any dependence | batch 1 (q 0.05, m 3): z 2.39, 0.80 at 1,924 trades | <= q | **yes**: every selecting look is charged once, globally, never reset |

**Option D in detail.**
- Selection happens on learn: the program's pre-registered configs, then explore/L2.
- The <= 3 carried configs are confirmed only on data that **no selection has touched**. That means TEST-OLD for
  families never read there, the sealed holdout once, or a frozen-card forward replay.
- The confirm runs BH at α_j over the m_j configs read in batch j. α_j comes from one global, never-reset ledger,
  using pure alpha-spending with Σα_j <= q. This is the quant ruling of 2026-11-09 extended to promotion, and the
  code already exists (`eval2/confirm.py` BatchPRDS).
- The registry-K DSR on the reused 2010-25 out-of-fold trades is still computed and reported. **It stops being a
  pass/fail item.**
- CPCV/PBO stay veto-only (ADRs 0003, 0083).

## 4. Recommendation

**Option D with q = 0.05, the false-promotion rate.** Spend schedule: a fixed number of batches per year, set in
advance, with equal shares of that year's budget q_y, and Σ q_y <= q over the years. EVAL-2's s^-2 decay leaves
batch 3 at 0.26 power with 1,200 trades.

**Why.**
- It is the only option with a hard bound: **FWER, and so FDR, <= 0.05 over all promotions ever, valid under
  arbitrary dependence.**
- It keeps ADR 0003's intent (no uncharged look, nothing rescued).
- It moves the bar from t 5.69 (6,151 trades) to about z 2.4-3.0 (1,900-2,700 trades).

**What it does not do. Read this before anyone treats D as a rescue.**
- Fresh data is scarce. A 1h family pooled over 10 instruments has about 740 TEST-OLD trades and 370 holdout trades.
  At 0.10 R, power is 0.29 and 0.11, so the power-first rule correctly refuses to start it.
- At 0.20 R the same card has power 0.96 and 0.65, so it qualifies.
- No K basis makes a single-cell 0.10 R edge at 1h-1d provable within a decade.

**One explicit loosening.** The registry-K DSR ceases to be pass/fail. That trade is made only in exchange for
confirmation on fresh data at a stricter family-wise level. It changes a promotion gate and needs the owner's
ruling, with the panel's recommendation, before any code changes. The count minimums (1,500 / 60 / 200 / 10 a year)
are design thresholds. The panel should replace them with each card's own power floor, counting pooled family trades;
that too is owner-only.

**What would change this:** a quant ruling that TEST-OLD or the forward replay is not independent of learn (regime
overlap). D would then need the holdout alone, which has too little power for anything slower than 15m.
