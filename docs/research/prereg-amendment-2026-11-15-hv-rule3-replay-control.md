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
   amendment section 3). A read whose scope (broker x TF band, or pooled) has fired is reported "control unvalidated"
   and is never a verdict until a dated amendment re-validates the scope.
4. S0 fills recorded before this amendment stay where they are. A read that started on fills keeps them; the control
   source is stated beside the read.

**Implementation status (2026-11-15).** This amendment registers the rule. The strategy ranking's matched read does
not read S0's replay labels yet, nor the parity revocations (`replay_parity.revoked`); that wiring is a follow-up
item. Until it lands, no read against S0 is a verdict.

## What does not change

K, every threshold and every window. No arm is added, removed or re-registered. The other matched controls (the LC*
twins, GS1C, the LBC-<key> twins) are unchanged and stay broker-traded.

## Bias statement

The replay label is gross of the fill's own slippage on the control side. Tying its validity to the parity monitor
(gap CI within +-0.02 R, barrier agreement at least 90 %) bounds that bias at the S5 tolerance.
