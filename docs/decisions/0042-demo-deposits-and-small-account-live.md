# 0042: Demo limits after the top-up, deposits, and a small-account live policy

Status: decided (CFO, 2026-10-25) for part 1 (demo limits and deposits), under the owner's delegation of demo limit
decisions to the expert panel (2026-10-18: "experts decide, report"). Part 2 (small-account live) is a **policy and a
recommendation**: every item in it that touches sizing, a risk limit, a broker account or going live is OWNER-ONLY and
goes to the owner decision queue through the program director. Implementation needs trading-safety review (list at the
end). Amends ADR 0032 (icm-demo and icm-probe ceilings), ADR 0037 amendment 4 (icm-probe budget) and ADR 0018 (the
high-water mark on a balance operation). ADR 0004's £50 real-money daily loss limit is unchanged and is restated as a
hard upper bound for every live account.

## Context

**Facts (relayed by the program director, 2026-10-25).** The owner topped up both IC Markets Raw GBP demo accounts "so
the system can learn": icm-demo now £10,960.55, icm-probe £11,000.00. The engines read the rise as an unconfirmed
balance increase above a high-water mark (HWM) of about £1,000. Config today (`config/accounts.yaml` at `caa79f4c`):
icm-demo `daily_loss_limit_abs: 50`, icm-probe `30` with `cost_probe.budget_share: 0.9`, `max_open: 4`,
`max_loss: 4.0`; both carry the ADR 0032 ceilings daily 0.05, weekly 0.10, `min_lot_risk_cap` 0.01. The per-trade hard
limit is 1% (`goldbot/risk/gate.py` `max_risk_per_trade`). The research plan: all 13 agents' own-stop minimum-lot
trades are 0.05-0.4% of £10k; pre-registered H-59 own-stop exploration has its own 60-trade / -10R budget; mirror
probes at native stops are skipped today when the stop's loss exceeds `max_loss` (£4).

**Price assumptions** (ADR 0004 table, not re-measured): XAUUSD $4,150/oz, GBPUSD 1.33, 0.01 lot = 1 oz, so **each $1
of stop at 0.01 lot costs £0.75**. 4h native stop at 0.01 lot ≈ £38 (relayed, about $50), 1h ≈ £16.5 ($21.9),
15m ≈ £8.2 ($10.9). Round trip on Raw ≈ $0.32/oz (spread ~$0.25 + commission $0.07), ≈ £0.24 per 0.01 lot. Every
threshold below scales linearly with ATR and the gold price, inversely with GBPUSD; the engine computes them live from
the broker's `loss_per_lot`, never from these figures. **Sample size behind every trading £ here: none measured;
arithmetic only. No profit is implied.**

**Findings in the code (read-only, `caa79f4c`).**

| # | Finding | Effect | Severity |
|---|---|---|---|
| F1 | While the HWM stays at ~£1,000 with equity ~£10,960 (`gate.py` `update_stage`: dd = 1 - equity / HWM), the 8% size-down fires only at equity ≤ £920 and the 12% kill switch at ≤ £880 | the drawdown stages are blind to ~£10,000 of loss; the daily and weekly caps still bind | **HIGH** until the mark is raised |
| F2 | `hwm.step` raises the mark to the **balance** when the deal history (deposits) or a second agreeing read explains it | a deposit after a loss forgives the loss: icm-demo's mark would be £10,960.55, not £1,000 + deposit | LOW (0.36% today), wrong in principle |
| F3 | A deposit during market hours leaves `day_start_equity` / `week_start_equity` at the pre-deposit level; the gate's day loss is `1 - equity / day_start` and the absolute projection uses `max(0, day_start - equity)` | the daily %, the £ absolute and the weekly caps are blind until the next roll (00:00 UTC; week Sunday 00:00 UTC): a silent loosening | **HIGH** in general; **not triggered today**: the top-ups landed with the market closed, and the first tick at the Sunday open crosses both the risk-day and risk-week boundaries, so both starts roll to post-deposit equity |
| F4 | `icm-live` and `vantage-live` carry no `daily_loss_limit_abs` | a live account would rely on the 2% percentage alone; ADR 0004's £50 is not enforced there | MEDIUM (inert: live disabled) |
| F5 | At £20 every 0.01-lot XAUUSD order fails three unrelated rails, not only the 1% cap: margin at 1:20 is £156 (level needs ≥ 300%, so equity ≥ £468) and combined notional ≤ 6 × equity needs equity ≥ £520 | "trade from £20" cannot be met by raising the per-trade cap alone | fact |

## Decision

### Part 1: demo limits at ~£11k (decided)

| Setting | icm-demo (champion trades) | icm-probe (probes, H-59, shadow host) | Why |
|---|---|---|---|
| `daily_loss_limit_abs` | 50 -> **220** | 30 -> **220** | 2% of £11,000, the design's own default daily cap (`risk.daily_cap: 0.02`): the £ limit adds no room beyond the percentage rule the design applies to any account. Room for 4 full losses at 0.5% (£55) or 5 native 4h probes |
| ceiling `daily` | 0.05 (unchanged) | 0.05 (unchanged) | the £220 binds first (2.0%) |
| ceiling `weekly` | 0.10 -> **0.06** | 0.10 -> **0.06** | a tightening. The weekly cap is checked on the loss so far, so a week can end one day past it: 6% + 2% = 8%, the size-down stage. 10% + 2% would cross the 8% stage and the 12% kill switch. Adaptive floor stays 5% (£548-550); ceiling 6% (£658-660) |
| ceiling `min_lot_risk_cap` | 0.01 (unchanged) | 0.01 (unchanged) | 1% = £110; every native stop fits |
| `cost_probe` | off (unchanged) | `budget_share` 0.9 (unchanged, £198), `max_open` 4 (unchanged), `max_loss` 4.0 -> **45.0** | native 1h (£16.5) and 4h (£38) mirror probes fit with 18% headroom; a 4h stop above ~$59 is still skipped (high-volatility days). Load check holds: 45 ≤ 0.9 × 220 = 198; 4 × 45 = 180 ≤ 198 |

Effective daily loss on icm-demo today: the supervisor's combined floor, 1.5% × £10,960 = **£164**, binds until ADR 0031
evidence lifts it (envelope 3.75%, then the £220). icm-probe is outside the combined sums (`probe_only`), so its own
£220 binds. Drawdown stages at £11,000: 8% = £880, 12% = £1,320 (once the mark is right, see deposits).

H-59 needs no extra £ cap: its -10R stop is pre-registered, and at its worst stop (0.4% of £10k, £44 a R) -10R is £440,
4% of the account, inside the weekly cap and below the 8% stage. It counts toward icm-probe's £220 day like every probe.

The ADR 0032 profile label stays `demo-2026-10-18` (evidence in R is scale-free; the deposit date splits the regime in
the ledger). ADR 0004's **£50 stays the owner's real-money daily limit**; this part changes demo accounts only.

**What would reverse it.** (a) Median realised probe loss above £150 a day, or the weekly cap hit two weeks running, in
the first 10 risk days -> `max_loss` back to £20 and review. (b) A withdrawal of the top-up -> the abs limits back to
£50 / £30 **before** the withdrawal. (c) The ADR 0037 amendment 3 correlation review (> 0.7) -> `max_open` 2.

### Part 1b: deposits and withdrawals (decided policy; code below)

| Rule | Decision |
|---|---|
| D1 Not P&L | A deposit or withdrawal is a capital flow (MT5 `DEAL_BALANCE`). Never in P&L, R, the phase gate, the adaptive or dynamic limits' evidence, or the north-star count. Monthly return stays the chained time-weighted return (`goldbot/ops/monthly_report.py`, deposits excluded) |
| D2 Ledger | The finance-auditor records both top-ups in the October ledger (`goldbot/ops/ledger.py` kind `deposit`, RC-9 tie-out), demo money, £0 real cost, amount from the deal history; the November review checks it |
| D3 High-water mark | On a confirmed balance operation the mark becomes `max(mark + net flow, balance)`, confirmed by the deal history only (never by a second read alone). A loss before a deposit is not forgiven. Example: icm-demo £1,000 + deposit -> mark £1,000 + deposit (drawdown 0.36% carried). A withdrawal lowers the mark by exactly the withdrawn amount (the only lowering besides the owner's re-arm). Until coded, ADR 0018 as written (mark = balance) is accepted as the interim: 0.36% difference today |
| D4 Day and week start | On a confirmed balance operation `day_start_equity` and `week_start_equity` move by the same net flow, so the loss so far is neither forgiven nor created (fixes F3) |
| D5 Today, before entries at the Sunday open | SRE verifies on both engines that the journal shows `hwm_raised` (mark ≥ balance) within 10 minutes of the first tick, and that the risk day and week rolled to post-deposit equity. If a mark is still ~£1,000 after that, stop that engine's service until it is confirmed (F1). Program director owns it; due 2026-10-25 22:15 UTC |
| D6 Withdrawals until D3/D4 ship | Withdraw only with the book flat and the market closed; a withdrawal otherwise reads as a drawdown (fail-safe: size-down or halt) |

### Part 2: small-account live policy, "fit or wait" (policy; owner-only items marked)

**Honest feasibility at £20 (IC Markets Raw, 0.01 lot).**

| Stop | £ at 0.01 lot | % of £20 | Passes the 1% cap? | Margin (≥ £468) and notional (≥ £520) rails? |
|---|---|---|---|---|
| $3 | £2.26 | 11.3% | no | no |
| $10.9 (15m) | £8.20 | 41% | no | no |
| $25 | £18.80 | 94% | no | no |
| ~$50 (4h) | £38 | 190% | no | no |
| round trip alone | £0.24 | 1.2% | costs alone exceed 1% | |

**Verdict: on IC Markets, real money cannot trade at £20 under any setting that keeps the rails.** Raising the
per-trade cap is rejected outright: a $3-stop scalp would need 11.3% a trade (nine losses ≈ -64%), it would also need
the margin and notional rails loosened, and it fails "never loosen RiskGate". The north star is irrelevant at this size.

**The policy (no RiskGate change at any size):**

1. **Rails fixed at every size.** 1% per-trade hard limit, margin ≥ 300% at 1:20, combined notional ≤ 6 × equity, daily
   `min(2% x day start, daily_loss_limit_abs ≤ £50)`, weekly 5%, 8% / 12% stages, owner confirmation of every real
   entry (90 s), exits never gated. No tier, account size or P&L raises any of them.
2. **Eligibility by fit, shown, never silent.** For each strategy on a live account the engine computes
   `E_min = max((min-lot loss at its own stop + round trip) / per-trade cap, notional / 6, 3 x margin at 1:20)` from the
   broker's `loss_per_lot` and margin, and publishes it (engine state, dashboard, Telegram on change): "£20; 15m box
   needs £820; nothing eligible; shadow continues". Strategies with `E_min` above equity are not proposed (no
   proposals the gate would refuse); the RiskGate stays the authority. This tightens nothing and loosens nothing.
3. **The ladder that results** (ICM Raw, today's ATR; recomputed live):

   | Live equity | Eligible (minimum-lot exception at ≤ 1%) | At the normal 0.5% sizing |
   |---|---|---|
   | < £520 | nothing (margin and notional rails) | nothing |
   | £520-£819 | only own stops ≤ ~$6.9 (scalps) | nothing |
   | £820+ | + 15m | 15m from £1,640 |
   | £1,650+ | + 1h | 1h from £3,300 |
   | £3,800+ | + 4h | 4h from £7,600 |

4. **OWNER-ONLY, recommendation: tiny-live by lot, not by 0.1%** (capital plan C2). Tiny-live risks 0.1% and never takes
   the minimum-lot exception, so it needs ~£6,850 for one 15m trade. Recommend: tiny-live = minimum lot only, the
   exception allowed at ≤ 1%, `daily_loss_limit_abs` = min(2%, £50). £ effect at £1,000: worst day ~£17 (two 15m
   losses), same as the demo plan; cost: none; break-even needs a measured net edge, none exists (0 strategies passed).
   Reverse if the tiny-live drawdown passes 1.5 × backtest. Deadline: before the paper -> tiny-live gate is recorded.
5. **OWNER-ONLY, recommendation: evaluate a micro-contract route, do not adopt yet.** A broker with MT5 gold at 0.001
   lot (0.1 oz) or a cent account (0.01 lot = 0.01 oz) moves every threshold down 10x or 100x:

   | Route (oz per minimum lot) | 15m `E_min` | 1h | 4h | Margin/notional floor |
   |---|---|---|---|---|
   | ICM Raw 0.01 lot (1 oz) | £820 | £1,650 | £3,800 | £520 |
   | 0.001 lot (0.1 oz), if offered | £82 | £165 | £380 | £52 |
   | cent account (0.01 oz) | £8.2 | £16.5 | £38 | £5.2 |

   Only the cent route makes £20 tradable (15m and 1h at ≤ 1%; 4h not). Expected money at £20: 1R = £0.20; at a
   hypothetical +0.1R net and 20 trades a month, **about £0.40 a month**: a live-pipe test (real fills, confirmations,
   reconciliation), not income. Unknowns to verify before any recommendation to open an account, each with its vendor
   page and date: regulation (FCA or offshore), MT5 availability, GBP or USC currency (USC needs adapter and FX work;
   mixed currencies halt the supervisor by design), spread and commission (cent accounts are usually standard-spread,
   where the 15m standard-account rules refuse most entries), and minimum deposit (IC Markets' own minimum may exceed
   £20: to verify). Owner: SRE and data-engineer research; opening an account is the owner's.
6. **Every live account carries `daily_loss_limit_abs` ≤ 50** (fixes F4; a tightening; a live account without it is
   refused at load once enabled).
7. **Deposits are the route up the ladder**, never size: an owner deposit moves a live account to a higher rung under
   D1-D4 with no cap change.

What would change this policy: a regulated MT5 broker with raw-like gold costs at 0.001 lot or below (adopt item 5);
a measured out-of-sample net edge per the capital plan (then the tiny-live recommendation, item 4, becomes urgent).

## Consequences

- Demo learning room rises on icm-probe from £30 to £220 a day, with native 1h and 4h mirror probes enabled, and the
  weekly ceiling cut to 6% so a bad week stops at or before the 8% stage.
- Deposits stop distorting the drawdown stages and the daily and weekly caps once D3 and D4 ship; until then D5 and D6
  are the operating controls.
- At £20 nothing trades on IC Markets, by design, and the owner sees why and at what equity each strategy becomes
  eligible. "Trading from £20" needs a micro-contract broker, an owner decision.

### Implementation (implementer; failing tests first)

| # | Change | Review |
|---|---|---|
| I1 | `config/accounts.yaml`: icm-demo `daily_loss_limit_abs: 220`, ceilings `weekly: 0.06`; icm-probe `daily_loss_limit_abs: 220`, ceilings `weekly: 0.06`, `cost_probe.max_loss: 45.0`; comments cite ADR 0042 | trading-safety (limits) |
| I2 | `goldbot/risk/hwm.py` `step`: on deal-history balance operations, mark = max(mark + net flow, balance); a confirmed withdrawal lowers it by the amount; a second read alone never confirms a flow | trading-safety |
| I3 | `goldbot/engine/runner.py` (`_update_hwm` path): on a confirmed balance operation, `day_start_equity` and `week_start_equity` += net flow, persisted with the risk snapshot; journal `capital_flow` (amount, no identifiers). Tests: deposit mid-day then a loss -> the daily %, £ and weekly caps fire at the same £ loss as without the deposit | trading-safety |
| I4 | `goldbot/ops/accounts.py`: an enabled `mode: live` account needs `daily_loss_limit_abs` ≤ 50; set it on icm-live and vantage-live | trading-safety |
| I5 | `E_min` per strategy per account in the engine state, API (`goldbot/api`, regenerate the contract), dashboard and Telegram; proposals skipped for strategies above equity (reason `below_min_equity`, journalled) | trading-safety, ui-ux, code |
| I6 | OWNER-ONLY, after the owner rules: tiny-live minimum-lot exception (`RiskLimits.from_settings`, `tiny_live=True` path); USC / cent-account support in the FX conversion | trading-safety (full), quant |
| I7 | SRE: D5 verification at the Sunday open; finance-auditor: D2 ledger rows | sre, finance-auditor |
