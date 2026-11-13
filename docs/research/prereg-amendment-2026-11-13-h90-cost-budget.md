# Amendment 2026-11-13: H-90 population cut on the cost budget (Z1P, Z2P, Z3D stopped)

Status: **in force 2026-11-13** (research director). Amends `prereg-2026-11-12-london-signal-arms.md` sections 2, 6
and 7, as its section 6 requires for any change to the frozen arms. Evidence: `cost-budget-audit-2026-11-13.md`.
Nothing in the engine, RiskGate, caps, sizing or the daily limits changes. No owner-only decision is taken.

## 1. Timing: before any confirmatory fill

H-90's F leg starts on the later of 2026-11-13 00:00 UTC and the day after the quant sign-off and the K rows both
exist (section 6). On 2026-11-13 neither exists (`hypotheses.md` H-90: "quant sign-off pending; K rows not yet
written"). So no F fill has counted yet, and this cut is made before any confirming data is read. Leg H has not run.

## 2. What is stopped and why

| arm | 2026-10-26..11-13 fills (n, net R, GBP) | cost budget (audit section 3.1) | ruling |
|---|---|---|---|
| Z2P run_fade 1m | 74, -0.090 R, -£14.94 | gross -0.017 R: FAIL; excess vs LCZ2P -0.28 R | stop |
| Z3D round_break 5m | 193, +0.018 R, +£12.15 | cost/gross 0.73: FAIL; excess vs LCZ3D +0.03 R (t 0.16) | stop |
| Z1P tick_surge 1m | 43, +0.152 R, +£14.80 | cost/gross 0.42 at the ADR 0064 charge: FAIL; below LCZ1P by 0.09 R | stop |

Fill counts, net R and GBP were re-derived by the research director from the brain's `aux.db` `exploration_trades`
(read-only, evidence exclusions removed, cut-off 2026-11-13 06:41 UTC); they match the audit exactly.

Reason: the program's cost-budget rule (cost per trade <= gross edge / 3, gross > 0) is failed on the forward point
estimate and, more strongly, on history: H-82 bounded the same entry code at +/-0.011 R gross per arm on 2010-17,
against a bracket cost of 0.05-0.07 R. Even an H-90 PASS would only give London-first admission to arms that cannot
carry their cost, so keeping them buys no money outcome. The money at stake is small either way (+£12 net over 14 days
for the three together); the daily admission slots they hold are the real cost.

Their dedicated controls LCZ1P, LCZ2P and LCZ3D stop with them (precedent: `learning-2026-11-02.md`, "stops with its
learning arm"). Rows, ids and K stay (`exploration_arms.yaml` `retired_day` / `retired_rule`); open positions keep
every exit.

## 3. Effect on H-90

- **Population for F:** Z5P, Z9D, Z10D and GS1 (GS1 to 2026-11-27). After 11-27 three arms remain, so the
  "fewer than 3 arms" clause is not triggered.
- **Leg H (H-learn, H-seen): unchanged.** It replays the six frozen Z families' entry code at zero fills and is
  kill-only. Keeping its population as registered keeps the kill as strict as it was; nothing is loosened.
- **Power.** Z1P, Z2P and Z3D made 81 of the 117 London fills of the six Z arms in the discovery window (69 %). F's
  London rate drops from about 11 to about 3.5 a day: about 80 London fills by 2026-12-17 instead of about 260. The
  E_L detectable at 80 % power rises from about 0.30 R to about 0.54 R. Power if the full discovery gap holds
  (+0.21 R) falls from 51 % to about 20 %.
- **Reading.** The 400-fill trigger and the 150-fill futility look will not be reached. F is read once on
  2026-12-17 with what exists; the futility rule (one-sided 90 % upper bound of E_L < +0.05 R -> FAIL) is applied at
  that read. Every PASS / LEAD / FAIL bar in section 7 is unchanged. A PASS was already unlikely; it is now near
  impossible, and the expected verdict is FAIL or LEAD. Leave-one-arm-out runs over the remaining arms.
- **K: unchanged** (+3 as registered). Retiring arms reads nothing new.

## 4. Not changed

Caps, `exploration.daily` segments of the remaining arms, the cost table version, the session definition, the
statistic, the controls of Z5P/Z9D/Z10D/GS1, the holdout and the live gate.
