# Pre-registration 2026-10-31: XAUUSD247 weekend lane controls (WKS0, WKI0; family arms_weekend_247)

Research director, 2026-10-31, worktree branch `weekend-247` on `43ad25ee` (ADR 0062). Before this file was
committed, neither arm had a fill, the account `vantage-247` was `enabled: false`, and no XAUUSD247 quote, bar or tick
had been read by anyone in research. The git history of this file is the pre-registration proof. Any later change
needs a dated amendment.

## 1. Purpose: cost and plumbing measurement, nothing else

XAUUSD247 at the weekend is a **broker-made quote**: the underlying gold market is shut, so there is no external
order flow, no price discovery and no mechanism by which an entry rule could earn anything but the broker's own
quote-making behaviour. No edge claim is possible, and none is pre-registered. WKS0 and WKI0 are seeded random
brackets (`random_bracket`, side from the arm hash) on S0's and I0's bracket geometry, Saturday and Sunday UTC only
(`params.weekdays: [5, 6]`). Their expected gross is zero; their expected net R is minus the cost per R. They measure:

1. **Round-trip cost of XAUUSD247 at the weekend**: spread at send, slippage against the requested price, stop
   overshoot, commission (expected 0 on Standard STP), in USD/oz and per R at each arm's stop.
2. **Swap**: per lot-night at the weekend rollovers (mainly WKI0: a Saturday entry held up to 24 h crosses one).
3. **Plumbing**: fill/reject rates, order latency, the Sunday flat cut (`weekend_lane_flat`), health `weekend_247`.
4. **Reconvergence gap** (descriptive, from the tick record, not from trades: the lane is flat at Sunday 21:00 UTC,
   before gold reopens): XAUUSD247's last mid at the cut and at the main session's first quote, against vantage-demo's
   first XAUUSD+ mid of the week. Reported as a USD distribution per weekend.

## 2. Frozen specs

| id | geometry | days, window | hold | max_open | config_hash | registry row |
|---|---|---|---|---|---|---|
| WKS0 | 1m, stop $3.00, target 1R, minutes 0/10/20/30/40/50 | Sat-Sun UTC 00:00-23:50 | 30 bars | 3 | 643477cb6642a804 | 1 (family arms_weekend_247) |
| WKI0 | 15m, stop 1.5 x ATR of the lane's own 15m bars, target 2R, minute 0 | Sat-Sun UTC 00:00-23:45 | 96 bars | 3 | e0f7bd18d11d3947 | 1 (family arms_weekend_247) |

Both `forward_only` (never run by the history matrix, `goldbot/research/arms.py:467`), `role: control` (qualification
exempt, `arms.py:249`), no cumulative-R stop (`config/arm_stops.yaml` `none:`), account `vantage-247` only, broker
key `vantage247`, store symbol XAUUSD247.

Known effective windows (from the engine rule, `goldbot/engine/weekend_lane.py:56-61`: an entry whose hold reaches
the Sunday 21:00 UTC cut is refused):
- WKS0: decisions Saturday 00:00 to Sunday ~20:30 UTC. Expected 100-200 fills a weekend (max_open 3, 30-min holds).
- WKI0: decisions Saturday 00:00 to 20:45 UTC only (24 h hold). Expected 3-10 fills a weekend. Its stop is ATR of
  synthetic weekend bars, so **WKI0's R is not comparable with I0's R**; read it in USD and for swap.
- Friday 21:00-24:00 UTC is lost (whole UTC days), as ADR 0062 records.

## 3. Reading rule

**May be used for:**
- the vantage247 cost table (`state/costs_vantage-247.json`) and a cost read per (arm x exit type): mean and one-sided
  95% upper bound, at >= 100 fills (WKS0) for the cost read and >= 20 rollovers (WKI0) for swap;
- plumbing and health verdicts of the lane;
- the descriptive reconvergence-gap distribution (section 1 item 4);
- the owner-facing statement of what a weekend demo trade costs. A negative mean R is the expected result and is
  reported as cost, never as a finding about the control or the market.

**May not be used for (any such use is void):**
- edge, rank, promotion or scale of any arm or family (there is no promotion path from this file);
- the null of any weekday family's matched read (perf-report reads per account, and vantage-247 has no families;
  `goldbot/ops/perf_report.py:962-975`, `:1069-1072`);
- XAUUSD history, labels, costs, learner/bandit/H-62 weights or qualification of any other broker key
  (`goldbot/ops/accounts.py` `pooled_brokers` refuses a shared key; every learner reads by broker,
  `goldbot/research/exploration_data.py:279-293`);
- the per-weekday money lines of the account's perf-report (`pnl_per_day` divides by weekdays in the range): read the
  lane per weekend instead.

**No strategy arm on vantage-247 under this file.** A weekend strategy would need its own pre-registration, a
mechanism about the broker's weekend quote (for example reconvergence toward the Monday open), a screen on the lane's
own recorded bars with K counted, and its own null. Graded folklore today: no demo slots.

## 4. Stop and review rule

- **Read date:** after 4 weekends with the lane enabled, or WKS0 >= 300 fills, whichever is first; the
  microstructure-researcher writes the cost/swap read, the evaluator checks it against this file. Then the director
  decides: keep (plumbing value, cost still unstable) or stop (cost measured; the lane's demo slots are better spent
  elsewhere). Default without a written decision: stop.
- **Immediate stop (disable the account):** any position open 15 min after a cut (health FAIL), a fill on a weekday,
  any XAUUSD247 row found under symbol XAUUSD or broker vantage, or the £20 lane day limit hit twice in one weekend.
- **Void windows:** a quote frozen for > 10 min inside the window voids that window's fills for the cost read
  (counted and reported, never dropped silently).

## 5. Registry, K and budget

Precedent: every arm id that trades demo gets one registry row before its first fill, controls included (VI0, VW0,
VS0 and the wave 1/2 controls; `config/settings.yaml` trial_budget_overrides note; `prereg-2026-10-27-scalp-volume.md`
VS0 row). So **2 rows**, family `arms_weekend_247`, rule_ref this file, results never scored (no Sharpe, no
deflated-Sharpe read). K rises by 2 (about 1,256 -> 1,258; conservative, as these are not tested). The 2026Q4 budget
is raised **147 -> 149** by PD + PO under the owner's 10-18 "backtest as much as needed" and 10-31 weekend-lane
directives; the `config/settings.yaml` override edit ships with ADR 0062's PR, and the rows are recorded on the brain
registry before `enabled: true`.

## 6. Condition for `enabled: true`

All of: (1) the Vantage247 terminal installed and logged in, `verify_terminal_account` passing on the Standard STP
server; (2) `goldbot.ops.symbol_check` passing for XAUUSD247 (exists, contract size 1.0, trading enabled);
(3) bridge on 8766 answering and `weekend_247` health green outside the window (flat); (4) the 147 -> 149 override
merged and both registry rows recorded in 2026Q4 with the hashes above; (5) ADR 0062's quant and trading-safety
reviews APPROVE. Enable on a weekday, so the first window opens with the unit already healthy.
