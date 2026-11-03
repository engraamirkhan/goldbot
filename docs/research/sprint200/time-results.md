# Sprint-200, lane TIME: explore result, 0 of 33 pass (no confirm read, no survivors)

Principal regime and opportunity-day researcher, 2026-11-02.
- Pre-registration: `time-prereg.md`, commit `2fc0a5cf`, pushed before any data was read.
- Amendment A1 (leak-free control): commit `65a3ec7f`, also before any data was read.
- Code: `scripts/sprint200/time_screen.py`, commit `35b8d8ed` plus A1.
- One explore run on 2010-01-01..2017-12-31 bars only. The 2018+ bars were not loaded and the holdout was never
  opened.
- Costs: cost of record `cor-2026-10-31.2`, icm, ADR 0064 charges, plus swap per rollover.

To re-derive (about 7 s, deterministic, seed 20261102):
`uv run python scripts/sprint200/time_screen.py explore --workers 2 --out ~/tmp/s200time`.
Self-test (synthetic bars and calendars only): `uv run python scripts/sprint200/time_selftest.py`.

## Result

Columns:
- n trades;
- gross and net mean R;
- net R with the 0.70 stop overshoot (stress line, not a gate);
- t of the day-summed net R;
- the pre-registered same-session control. It is reported only, because it looks ahead (A1);
- the leak-free control, which is the gate;
- net minus the leak-free control;
- years with a positive net mean.

Pass = n >= 100, net > 0, t >= 2.0, and net minus the leak-free control >= 0.05 R. The verdict column lists every leg
that failed.

| ID | n | gross R | net R | net @0.70 | t | ctl (same-session) | ctl (leak-free) | net - ctl LF | yrs+ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| S200-TIME-01 | 600 | +0.011 | -0.053 | -0.094 | -1.13 | -0.053 | -0.084 | +0.030 | 2/8 | FAIL (net, t, ctl) |
| S200-TIME-02 | 638 | +0.116 | +0.060 | +0.036 | +1.33 | +0.120 | -0.068 | +0.128 | 3/8 | FAIL (t) |
| S200-TIME-03 | 1478 | +0.020 | -0.029 | -0.035 | -2.12 | +0.020 | -0.063 | +0.033 | 3/8 | FAIL (net, t, ctl) |
| S200-TIME-04 | 627 | +0.037 | -0.008 | -0.010 | -0.51 | -0.046 | -0.062 | +0.055 | 3/8 | FAIL (net, t) |
| S200-TIME-05 | 706 | +0.064 | +0.011 | -0.006 | +0.41 | -0.152 | -0.053 | +0.065 | 5/8 | FAIL (t) |
| S200-TIME-06 | 187 | +0.027 | -0.025 | -0.038 | -0.71 | -0.253 | -0.082 | +0.056 | 4/8 | FAIL (net, t) |
| S200-TIME-07 | 615 | +0.060 | +0.008 | -0.002 | +0.33 | -0.102 | -0.061 | +0.069 | 3/8 | FAIL (t) |
| S200-TIME-08 | 167 | +0.046 | -0.017 | -0.040 | -0.31 | +0.139 | +0.085 | -0.102 | 3/8 | FAIL (net, t, ctl) |
| S200-TIME-09 | 151 | -0.104 | -0.160 | -0.200 | -2.56 | -0.099 | -0.135 | -0.026 | 1/8 | FAIL (net, t, ctl) |
| S200-TIME-10 | 309 | +0.063 | +0.001 | -0.056 | +0.02 | +0.761 | +0.033 | -0.032 | 4/8 | FAIL (t, ctl) |
| S200-TIME-11 | 136 | +0.078 | +0.017 | -0.018 | +0.18 | -0.084 | -0.057 | +0.074 | 4/8 | FAIL (t) |
| S200-TIME-12 | 189 | -0.126 | -0.176 | -0.209 | -2.41 | -0.077 | -0.092 | -0.083 | 3/8 | FAIL (net, t, ctl) |
| S200-TIME-13 | 783 | +0.001 | -0.050 | -0.074 | -1.72 | -0.038 | -0.065 | +0.014 | 2/8 | FAIL (net, t, ctl) |
| S200-TIME-14 | 81 | +0.040 | -0.006 | -0.024 | -0.07 | +0.015 | +0.005 | -0.011 | 4/8 | FAIL (n, net, t, ctl) |
| S200-TIME-15 | 201 | +0.006 | -0.049 | -0.074 | -0.81 | -0.157 | -0.122 | +0.073 | 3/8 | FAIL (net, t) |
| S200-TIME-16 | 216 | -0.068 | -0.126 | -0.185 | -1.41 | -0.110 | -0.135 | +0.009 | 2/8 | FAIL (net, t, ctl) |
| S200-TIME-17 | 190 | +0.061 | +0.000 | -0.032 | +0.00 | -0.041 | -0.029 | +0.029 | 4/8 | FAIL (t, ctl) |
| S200-TIME-18 | 190 | +0.031 | -0.025 | -0.045 | -0.43 | -0.048 | -0.083 | +0.059 | 3/8 | FAIL (net, t) |
| S200-TIME-19 | 127 | -0.031 | -0.079 | -0.129 | -0.72 | -0.048 | -0.047 | -0.032 | 2/8 | FAIL (net, t, ctl) |
| S200-TIME-20 | 110 | -0.032 | -0.113 | -0.165 | -1.35 | -0.118 | -0.139 | +0.026 | 4/8 | FAIL (net, t, ctl) |
| S200-TIME-21 | 160 | -0.069 | -0.127 | -0.149 | -2.47 | -0.237 | -0.096 | -0.031 | 1/8 | FAIL (net, t, ctl) |
| S200-TIME-22 | 240 | -0.042 | -0.111 | -0.158 | -1.59 | -0.056 | -0.087 | -0.023 | 3/8 | FAIL (net, t, ctl) |
| S200-TIME-23 | 144 | +0.098 | +0.029 | -0.020 | +0.25 | -0.207 | -0.155 | +0.184 | 4/8 | FAIL (t) |
| S200-TIME-24 | 154 | +0.007 | -0.042 | -0.086 | -0.42 | -0.095 | -0.132 | +0.090 | 3/8 | FAIL (net, t) |
| S200-TIME-25 | 97 | -0.038 | -0.094 | -0.113 | -1.32 | -0.198 | -0.110 | +0.016 | 3/8 | FAIL (n, net, t, ctl) |
| S200-TIME-26 | 223 | -0.106 | -0.144 | -0.155 | -2.89 | +0.670 | -0.112 | -0.033 | 2/8 | FAIL (net, t, ctl) |
| S200-TIME-27 | 85 | -0.150 | -0.216 | -0.246 | -2.41 | -0.054 | -0.108 | -0.108 | 3/8 | FAIL (n, net, t, ctl) |
| S200-TIME-28 | 431 | +0.024 | -0.029 | -0.048 | -0.91 | -0.317 | -0.053 | +0.024 | 4/8 | FAIL (net, t, ctl) |
| S200-TIME-29 | 930 | -0.023 | -0.075 | -0.087 | -3.56 | +0.278 | -0.125 | +0.050 | 0/8 | FAIL (net, t) |
| S200-TIME-30 | 150 | +0.019 | -0.050 | -0.072 | -0.84 | +0.267 | -0.045 | -0.005 | 4/8 | FAIL (net, t, ctl) |
| S200-TIME-31 | 127 | +0.136 | +0.098 | +0.073 | +0.88 | +0.109 | +0.078 | +0.020 | 5/8 | FAIL (t, ctl) |
| S200-TIME-32 | 191 | +0.126 | +0.062 | +0.018 | +0.61 | -0.149 | -0.127 | +0.189 | 5/8 | FAIL (t) |
| S200-TIME-33 | 209 | -0.028 | -0.090 | -0.101 | -2.80 | -0.218 | -0.108 | +0.018 | 2/8 | FAIL (net, t, ctl) |

## Reading

1. **No strategy reaches t >= 2 with a positive net.** The best nets are all inside noise:

   | ID | Rule | Net R | t | n |
   |---|---|---|---|---|
   | 31 | Monday fade of a big prior week | +0.098 | 0.88 | 127 |
   | 32 | Month-start NY long | +0.062 | 0.61 | 191 |
   | 02 | 10:00 NY burst continuation | +0.060 | 1.33 | 638 |

   None is re-tuned or re-run (pre-registration section 2).
2. **Eight strategies are significantly negative** (t <= -2): 03, 09, 12, 21, 26, 27, 29 and 33. Taking the other
   side of a failed rule is a new hypothesis, not a pass. They are recorded here so nobody re-tests them in the
   forward direction. Strategy 29 (NY post-settle continuation) is positive in 0 of 8 years, at net t -3.56. Its
   gross is only -0.023 R, so most of the loss is cost, not a reversal edge.
3. **Costs run 0.04-0.08 R a trade** at these 1.5-2 ATRh stops; the median stop was about $5.5 in 2010-2017. Every
   gross mean is within 0.15 R of zero. In none of these conditional forms do the clock or the calendar give gold a
   direction strong enough to pay the round trip. The 2026-10-26 time-structure programme reached the same
   conclusion.
4. **The A1 correction mattered:**
   - The same-session control reads +0.76 R for 10 and +0.67 R for 26, because draws before the signal take on the
     day's move. Leak-free, the same controls read +0.03 R and -0.11 R.
   - With the old control, every momentum rule would have failed the control test and fades would have cleared it
     too easily.
   - With either control nothing passes, because no rule reaches t >= 2.
5. **Three calendar-subset rules failed on n < 100,** as pre-registered: 14, 25 and 27.

## Multiple testing and what stops

**K +33:** 33 explore reads.
- A1 corrects the null; it is not a variant.
- There is no confirm read, because no strategy passed explore.

Retired for this lane, in the forms tested here:

| Family | Strategies |
|---|---|
| Conditional NY data-slot continuation | 01, 02 |
| Settle-to-settle and Gao-filtered intraday momentum | 03, 04 |
| Conditional fix and settle fades | 05-07 |
| Narrow-Asia London break | 08 |
| Session-handoff continuation and reversal | 09-13 |
| Weekend-gap fade and continuation | 14, 16 |
| Friday and Monday week fades | 15, 31 |
| Month-end WM-fix flow and post-fix rebound | 17, 18 |
| Quarter-turn and month-start intraday longs | 19, 32 |
| Expiry-week pin and fade | 20, 21 |
| Pre- and post-FND | 22, 23 |
| Pre-holiday long and thin-day fade | 24, 25 |
| Strong-US-day overnight continuation | 26 |
| First Tokyo hour | 27 |
| London lunch fade | 28 |
| NY afternoon continuation | 29 |
| Frankfurt open | 30 |
| Asian dip buy | 33 |

Time of day and calendar are still useful as **inputs for range and opportunity** (time-structure 2026-10-26, section
1.1). They are not useful as a source of direction.
