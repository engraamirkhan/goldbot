# H-82 screen read: wider targets / time-only exits on the scalp arms' own entries, 2010-2017 (bet 2b, "H-B")

Trade management researcher, 2026-10-30. Rule: `prereg-2026-10-30-hb-exits.md` (committed `64b5cd2b` before any
history outcome was computed; hypotheses row `0b3922d5`). Scripts: `docs/research/candidates/hb_exits/hb_exits_screen.py`
(pairs) and `hb_exits_read.py` (the pre-registered read). Data: local Dukascopy 1m, 2010-01-15..2017-12-31 only (the
loader asserts no 2018+ bar); outputs in `~/tmp/hb_exits/` (pairs parquet, `read.json`, `parity.json`), nothing in the
repo. Run time 11 s (pairs) + 6 s (read).

## Verdict

**FAIL on all five variants. The wider-target / time-exit line closes for these entries (research-plan line 2, H-B
part). Demo exits stay as they are.** The demo's +0.18 R gain from dropping the target (+0.032 R at 1 R vs +0.213 R
with no target) is direction drift in one falling week. It is not an exit effect: on 337,341 history entries, every
variant changes the arm's net R by less than 0.01 R relative to its random control.

## Integrity checks

- **Entries match the demo**: the live rule code (`arm_rules.candidates`) on the hashed rows of
  `config/exploration_arms.yaml`, with the spread gate, the W window and the send delay. `limit_open(max_open)` is
  applied under the current exit. No tier-1 blackout, because the calendar files are not local. Arms and controls are
  treated the same way.
- **Exit walk = engine labeller**: the vectorised walk was compared with `triple_barrier_next_open_1m` (mid,
  `scripts/run_arm_backtest.py`'s convention) on 4 arms x 5 variants x 300 entries. 0 of 6,000 differ by more than
  1e-6 R (max 2.2e-16).
- **Pairs**: S5 110,706, Z4 30,079, Z6 27,851, Z7 168,705. Pairs dropped for having no valid control: 606 (0.2%).

## Primary read: DiD of net R vs the arm's current exit, minus the same change on random entries (±60 min, same day)

Bootstrap by trading day (10,000 resamples). Overshoot $0. At $0.70 per stop exit every figure moves by less than
0.0002 R.

| Variant | Pooled DiD (R) | 90% CI | Bonferroni-5 LB | Families (S5 / Z4 / Z6 / Z7) | Years > 0 | Verdict |
|---|---|---|---|---|---|---|
| 1 R | -0.0008 | -0.0014..-0.0002 | -0.0016 | -0.0031 / base / +0.0001 / +0.0005 | 2 of 8 | FAIL |
| 1.5 R | -0.0017 | -0.0037..+0.0003 | -0.0041 | base / -0.0017 / base / base | 2 of 8 | FAIL |
| 2 R | +0.0002 | -0.0004..+0.0008 | -0.0006 | +0.0025 / -0.0008 / -0.0011 / +0.0001 | 3 of 8 | FAIL |
| 3 R | +0.0005 | -0.0004..+0.0013 | -0.0007 | +0.0049 / -0.0015 / -0.0015 / -0.0001 | 4 of 8 | FAIL |
| none (time exit) | +0.0009 | -0.0002..+0.0019 | -0.0004 | +0.0061 / -0.0017 / -0.0010 / +0.0001 | 4 of 8 | FAIL |

The bar was +0.05 R. The best point estimate is 1/55 of it. The intervals are tight (±0.001 R), so this is a clean
null and not a lack of power.

## Secondary and diagnostic reads (not in the verdict)

- **Plan line 2's own wording ("beats 1 R by >= 0.05 R over the control")**: none vs 1 R gives +0.0015 R (90% CI
  +0.0003..+0.0027), 3 R vs 1 R gives +0.0011 R, and 2 R vs 1 R gives +0.0008 R. All are about 30-60 times below the
  bar. The only positive family is S5 (+0.009 R for none vs 1 R).
- **Side-matched control** (same control times, but with the arm's side; not point in time): -0.0018..+0.0027 R by
  family. Taking the arm's side on a nearby random entry gains nothing from wider exits either, so the entries carry no
  direction that persists inside the hold.
- **Why the variants hardly differ**: these fixed-$ brackets rarely reach a target. Median MFE is 0.18-0.30 R. The share
  of arm entries reaching +1 R is S5A 12.7%, S5B 8.5%, Z4D 10.1%, Z4P 8.6%, Z6D 4.4%, Z7D/Z7P 4.2%/4.1%; reaching +3 R
  is 0.2-1.1%. Stop exits are 4-12%. Most trades end at the time exit, so the target only matters in the right tail,
  and in that tail the arm's entries behave like random ones.
- **Absolute gross R per arm**: all within ±0.010 R at every variant. For example, Z7D at none is +0.009 with its
  control at +0.002, and Z6D is -0.009 with its control at +0.002. Net of the measured 0.05 R, every arm loses
  0.04-0.06 R a trade at every exit, and 0.11-0.13 R at the signal-time 0.116 R. **No exit makes any of these entries
  pay on 2010-2017.** This is descriptive only. It gives no entry credit, and the H-81/H-61 demo reads stand.

## What this means

1. The demo's exit pattern (0.5 R -0.070 ... none +0.213) is a short book in a falling tape. History reproduces none of
   it, on random entries or on the arms' own entries. This is consistent with the performance-analyst's side split
   (shorts +0.216 R, longs -0.164 R).
2. No arm row changes, and no exit-path or label change is proposed. There is nothing for the quant-reviewer or the
   trading-safety-reviewer.
3. K: +5 (one per variant), as pre-registered. 0 registry trials.

## Limits (stated, not used to rescue the result)

- **Volatility regime**: in 2010-2017 a $3-$4.50 stop is wide relative to a 30-minute range, so few trades reach a
  target. Today's gold is about 3 times the price. A volatility-scaled replication, with stops set at today's stop/ATR
  ratio, would be the only extension with a plausible different answer. It would be a **new** hypothesis needing its
  own pre-registration and K. I do not recommend it ahead of H-80 R2 and bet 3, because the demo's MFE in R (median
  0.19-0.27) matches history's (0.18-0.30), which points to the same geometry.
- **No tier-1 blackout**: it applies equally to arm and control, and event minutes are a small share of the
  337k pairs.
- **Trailing runners** are not tested here. They are H-80's question (R2 on 2018-2025-09).
