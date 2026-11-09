# 0076: EVAL-2: how goldbot judges a strategy

Status: accepted, **signed by the quant-reviewer 2026-11-09** (research director for PD + PO, 2026-11-09). Decided by PD + PO +
quant-reviewer; the owner is informed. Method text: `docs/research/eval2-method-2026-11-09.md` (v6, signed by the
quant-reviewer 2026-11-09). Frozen thresholds, function signatures and the gamma sequences:
`docs/research/prereg-2026-11-09-eval2.md`.

## Context
Months of screens produced no strategy that beats the random control net of costs out of sample (Sprint-200 0
passers; sprint-3000 0 of 328 at confirm; the time lane 0 of 33). Evaluation Audit 2 and the 2026-11-09 review found
the screen itself was mis-specified in ways that make both errors likely: a pooled 1.3 R sd that understates the MDE
of noisy cells by 1.2-1.7x; demo controls that share only 21 (broker, day, side) cells with the signals they are
meant to control; a replay that charges a 2 x $0.15 slippage prior the fills do not pay (+0.078 R of a +0.084 R
fill-vs-replay gap); 241 non-gold labels replayed on gold bars; a 2018-25 confirm window already read 14 times; a
holdout partly spent; a never-built mSPRT cited as the live kill rule; and Ruling A (150 events, daily, retire on
sign), which fails a power test.

## Decision
- EVAL-2 is adopted as the program's evaluation method for every family registered after this ADR. Its ladder is
  L0 (harness-sane, card admission with a power floor) -> L1 (exit-agnostic +/-1 ATR probe, family NO SIGNAL retire)
  -> L2 (EB hierarchical model on explore, carry at most 3 cells per family) -> L3 (confirm, read once per family:
  TEST-OLD primary, 2018-25 corroboration, BatchPRDS at q 0.10) -> L4 (forward demo e-processes: conformance and a
  futility kill, not discovery) -> L5 (owner).
- The unit of evidence is instance **excess R**: net R at the deciding cost minus the mean of 20 replay-only random
  twins matched by instrument, timeframe, side, hour +/-1 h and (when pre-registered) regime level, drawn after the
  signal's exit. The 20 twins are Rule 3's "matched control".
- One BatchPRDS sequence per window (Zrnic, Jiang, Ramdas and Jordan, AISTATS 2020, Algorithm 1), gamma fixed in the
  prereg: TEST-OLD starts at index 1 (gamma_1 = 0.5); on 2018-25 the 14 earlier reads (P1-P9, F1-F5) are batches 1-14
  with 0 rejections credited, so EVAL-2's first 2018-25 read is batch 15. Pure alpha-spending, no rejection credit
  (prereg §3.1).
- The deciding cost is max(today's ICM R, bp-engine R) + overshoot x (stop-exit + stop-entry share) + swap
  (strict-interior rollover rule, #343) + the positive S5 fill-vs-replay gap until S5 passes; gates read at 1.5x.
  The 2010-17 $-constant stress line is reported (COST-SENSITIVE tag), never gated.
- Self-tests S0-S6 must all pass before any verdict is trusted; a fast S0/S2/S3/S6 runs in CI (`selftest` job).
- Replay v2 (measured per-leg slippage, never credited; stop overshoot as its own term; non-gold v1 labels void) is a
  versioned relabel; `label_sim_r` is never overwritten.
- Random-control arms shrink by share to <= 10 % of demo capacity and are kept as the fill-vs-replay calibration
  stream; the never-trained meta-labeller leaves the decision path; the Thompson bandit is replaced by equal shares
  until the first L2 carry, then posterior allocation with a 20 % uniform share and logged propensity.
- Arms registered after this ADR are killed by the L4 e-processes; existing arms keep `arm_stops.unit_stop` (H-61,
  W1, W2/Z futility). The unbuilt mSPRT of `prereg-amendment-2026-11-07-kill-only.md` section 5 is superseded
  (prereg section 9).
- DSR at the real K (registry `n_trials_effective` + snooping K, ADR 0078: 3,471 + 17,862 = 21,333, sensitivity 20,151) and the Harvey-Liu haircut are reported,
  never gated, up to L4. At L5 `research/gates.py` applies as coded.

## Owner-only items (unchanged by this ADR)
- Going live with real money, L5 confirmation, sizing, per-trade risk, the daily loss limit, spending, and any
  exemption from `research/gates.py`.
- **The DSR K basis at L5 is an owner item for L5 time.** The quant's recommendation (K = hypotheses ever tested on
  2018-25, about 400, t about 3.1) is offered then with options; nothing is asked now.
- No lane changes RiskGate, the per-trade cap, the daily loss limit, the real-money confirmation path or the phase
  gate; Lane 5B carries a test asserting it.

## Consequences
- Honest money expectation: at batch 1's explore edges, after out-of-sample decay and confirm power, about
  £1-3/day on a £10k demo and £0.1-0.3/day at £1k real. The catalogue is not where £50/day comes from; the owner is
  told so plainly.
- Most batch-1 decisions will come from the forward holdout (about 12 months per family): 2018-25 is spent (batch 15,
  alpha about 0.00016) and TEST-OLD intraday is thin (2008-09 only, ruling 2026-11-09).
- Any arm re-read on replay v2 is a new read under its own prereg; replay v2 lifts every exploration arm's replay net
  by up to +0.078 R, so no keep/kill call may rest on a v1 read after v2 lands.
- No code lane (1-5B) starts before this ADR, ADRs 0077/0078 and the prereg are merged with the quant's signature.
- Owner information line for #34: "EVAL-2 adopted: one test per strategy against 20 matched random twins, net of
  measured costs, confirmed on 2006-09 data first; real money and sizing stay yours."
