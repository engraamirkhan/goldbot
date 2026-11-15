# Amendment 2026-11-15: HV Rule 3, S0's matched control becomes its replay labels

Status: **in force with the release that makes S0 replay-only** (prereg-amendment-2026-11-15-replay-only-controls.md).
Amends the matched-control read of prereg-2026-10-25-high-volume.md ("Rule 3's matched control", as
eval2-method-2026-11-09.md section 5 names it) for the arms read against the borrowed S0 (the H-81 strata registered
against S0; quant ruling 2026-11-06 (d)). The original is not edited.

## The change

1. **S0's matched control is its replay labels.** An arm whose read names S0 as its control is read against S0's r2
   replay labels (`exploration_replay`, replay_version r2) in the same (session, UTC hour) cells, instead of S0's
   broker fills. The candidates are the ones S0 records as `replay_only` (blocked, mu 0, labelled like every other
   candidate).
2. Each read's rule, threshold, window, sample size and cell-coverage test are unchanged. Every read reports beside
   its verdict the share of the arm's fills in cells with no S0 label (above 10 %: "not readable", as before).
3. **Validity:** the replay labels stand in for fills only while the replay-parity monitor holds (companion
   amendment section 3). A read whose broker x TF band scope has fired is reported "control unvalidated" and is
   never a verdict until a dated amendment re-validates the scope. (A pooled fire alerts only.)
4. S0 fills recorded before this amendment stay where they are, but an arm fill after 2026-11-15 is never compared
   with a pre-2026-11-15 S0 fill: from this date an arm's matched control is S0's replay labels only. A read that
   started before it states both control sources and the date they changed.

**Implementation status (2026-11-15).** This amendment registers the rule. The strategy ranking's matched read does
not read S0's replay labels yet, nor the parity revocations (`replay_parity.revoked`); that wiring is a follow-up
item. Until it lands, no read against S0 is a verdict, and the ranking says so on every row read against S0
(`control_note`: "control: S0 replay (unwired) - not a verdict", `api.strategy_ranking.S0_REPLAY_NOTE`).

## What does not change

K, every threshold and every window. No arm is added, removed or re-registered. The other matched controls (the LC*
twins, GS1C, the LBC-<key> twins) are unchanged and stay broker-traded.

## Bias statement

The replay label carries the r2 cost model, not the fill's own slippage, on the control side. The parity monitor
bounds that difference against the r2 `sim_r` (the S5 quantity, fill_r - sim_r), not against the gross `mid_r`. The
bound is what the monitor can detect: about 0.06 R within a quarter (companion amendment section 3, detection
table), not the 0.02 R tolerance. Barrier agreement must also be at least 90 %.

Parity is measured on other arms' fills (the execution controls, the probes, the learning and strategy arms), not on
S0's own, because S0 sends none. Those fills are mostly on wider stops and slower timeframes, where an absolute
slippage error is a smaller share of R. A 1m S0 at a $3 stop pays the same dollars on a smaller R, so its true error
is likely larger. The monitor therefore probably understates S0's replay error in R, while each scope's read stays
conservative for the fills it measures. Until a 1m-5m scope has read on its own fills, S0-matched reads say which
scope validated them. The quant review of 0fb8fbff argued the opposite direction: edge arms' entries suffer
adverse selection that a random entry does not, which would make the monitor overstate S0's error. Both effects
exist. At a $3 stop the stop-size effect is the larger, so this amendment does not claim the bound is conservative.
