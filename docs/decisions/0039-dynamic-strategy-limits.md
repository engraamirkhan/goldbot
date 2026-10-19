# 0039: Dynamic per-strategy limits

Status: proposed (implementer, 2026-10-19; lane A of docs/design/dynamic-limits-2026-10-19.md)

## Context
The owner (2026-10-19): "loss per trade, daily loss, profits ... dynamic according to strategy ... always dynamically
calculated". Today every strategy risks the fixed 0.5% per trade (DESIGN "Sizing"), and the daily allowance is ADR
0031's ladder. ADR 0031's rules are frozen ("a change to any rule is a new ADR"), so the per-strategy risk and the
volatility step are this ADR. The ceilings of ADR 0004 / ADR 0032 (1% per trade, the £50 absolute daily limit, the
demo envelope) do not change.

## Decision
One new decided field in the nightly ADR 0031 decision (`goldbot/risk/adaptive.py`), no parallel path. Behind the
account's `dynamic_limits` flag (accounts.yaml, default off; off = ADR 0031 exactly):

1. Per strategy (model version) s, on demo fills and taken shadow trades after its freeze, closed before the decision
   day: f_s = ADR 0031 A1's quarter-Kelly per-trade fraction (min of the trade and day terms, before /3%);
   w = n_days/(n_days+20); r_s = base + w(f_s - base), base = the account's risk per trade (0.5%). Quantised DOWN to
   0.1% (rounding never raises risk), clamped to [0.25%, ceiling]. LCB <= 0 gives 0.25%; fewer than 2 trading days
   gives the base (`strategy_risk`).
2. The target is r_s on demo fills (at most the base with fewer than 20 demo fills of s), lowered by r_s on demo +
   shadow: shadow only tightens. Below the current value: lowered at once, any time. Above it by half a step: one
   0.1% step up at the risk-day roll after 3 consecutive qualifying days and 10 new demo fills of s since its last
   rise, never within 5 days of a tighten of s or of any account limit (ADR 0031's cooldown).
3. Ceilings: demo = the 1% per-trade hard limit (RiskSettings `le=0.01`); live/tiny-live = the base (tighten only;
   dynamic limits on live stay owner-only).
4. Daily allowance: one new tighten rule on ADR 0031's ladder: when the 20-day median ATR15 is >= its 1-year p90
   (the boundary included) the daily cap steps down one rung, once on entering the regime (no ratchet), and does not
   loosen while the regime holds. Unknown ATR is no regime.
5. Intraday factors for the engine (lane B applies them at each decision; they only lower r): k_vol = 0.5 in the
   regime; k_dd = 1 -> 0.5 linearly as the drawdown from the high-water mark goes 2% -> 4%; k_day = 0.5 once today's
   loss >= 50% of the allowance. r = r_s x k_vol x k_dd x k_day, then the multiplier and the 1% cap at sizing.
6. Fail closed: invalid or missing evidence, or ADR 0031's floors (drawdown, loss streak, costs, stage), give each
   strategy at most the base; a strategy already below it stays below (the strict side). An unreadable state gives
   the floors with no per-strategy entries, i.e. the base for every strategy (today's fixed values).
7. Published: `AdaptiveState.risk_terms[version] = RiskTerm{r, target, f, n_days, n_demo, lcb, w, k_vol, k_dd,
   k_day, reason, why}`; every change is an `adaptive_changes` row `strategy_risk:<version>` and the owner's FYI
   (no version ids in the Telegram text).

Readings (conservative side): quantisation is down, not nearest; the per-strategy loosen needs only the strategy's
own 20 demo fills (ADR 0031's account-wide 60 still gates the caps); the volatility step fires on entry to the regime
rather than every night, so a long regime does not walk the allowance to its floor.

Profit targets stay static: p is calibrated to the label-spec barrier and the gate's EV check uses target_atr, so an
MFE-based exit is a pre-registered research trial instead.

## Consequences
- The engine sizes per strategy once lane B reads `RiskLimits.strategy_risk` (`risk_for(version)`, unknown -> base);
  until then, and with the flag off, behaviour is today's. Rollback: `dynamic_limits: false` and restart.
- New fields are additive JSON in core.db `adaptive_state` (no migration); old rows read with the defaults.
- A strong strategy can reach 1% per trade on demo, which the CFO reviews as risk appetite (ADR 0004 still caps the
  day at £50). Quant review: the formula, the shrinkage constant 20 and the 0.25% floor are fixed here before any
  replay; changing any of them is a new ADR.
