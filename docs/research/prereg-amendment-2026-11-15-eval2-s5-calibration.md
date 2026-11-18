# Amendment 2026-11-15: EVAL-2 section 5, the S5 calibration stream without S0

Status: **in force with the release that makes S0 replay-only** (prereg-amendment-2026-11-15-replay-only-controls.md).
Amends eval2-method-2026-11-09.md section 5 ("Controls ... They serve as the fill-vs-replay calibration stream") and
the S5 read of prereg-2026-11-09-eval2.md section 5. Neither original is edited.

## The change

1. **S0 leaves the broker stream.** It sends no order (`control_mode: replay_only`), so it has no fill and no
   fill-vs-replay pair. Its candidates keep their r2 labels and remain replay twins.
2. **The S5 calibration stream is** the gold fills of IEX1H, VEX15 and VEX1H (the execution controls,
   prereg-amendment-2026-11-10-execution-controls.md), the cost probes, and the learning and strategy arms' gold fills.
   Every pair is read by `exploration_data.fill_replay_pairs` / `eval2.forward.s5_read`, unchanged.
3. S5's pass rule (at least 50 pairs over at least 10 session days, |mean fill - replay v2| <= 0.02 R, lower one-sided
   95 % day-clustered bound >= -0.02 R), its bands, the vantage entry-leg rule and `s5_passed` are unchanged.
4. **Added:** a scope the replay-parity monitor fires on (section 3 of the companion amendment) is not validated for
   S5, whatever S5 read: its gap is charged in the deciding cost and its L4 forward reads stop counting, until a dated
   amendment re-validates it.

## What does not change

K, thresholds, windows, the deciding-cost pin and the forward e-processes. GS1C stays broker-traded as GS1's control,
not as a calibration source.

## Bias statement

No S5 read has passed (`s5_passed` is empty), so removing S0's fills cannot move any verdict. The stream loses volume
in the 1m-5m band, which is informational only (method C4).
