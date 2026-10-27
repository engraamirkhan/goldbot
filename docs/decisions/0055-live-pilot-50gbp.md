# 0055: Live pilot at £50 on IC Markets: 0.01 lot, £3.12 a trade, £6.25 a day, £25 or 30 trades

Status: accepted (program-director + cfo + trading-safety-reviewer under the owner's delegation, 2026-10-27).
This record stands in for the owner's signature on the pilot terms (readiness plan item L1). It does **not** replace
the owner's own unlock: live starts only through `unlock_live` and the typed phrase `ENABLE LIVE icm-live` (L19).

## Context
- **Owner instruction, 2026-10-27:** live on 2026-10-30 with about £50, on IC Markets Raw Spread.
  - Server `ICMarketsSC-MT5-3` (the Seychelles entity, IC Markets Global), leverage 1:5000, account currency GBP.
  - The login number is never recorded in the repo; it lives in the live box's keyring (L7).
- **Owner delegation, 2026-10-27 (verbatim):** "again you are asking me, i want it done, for decisions ask expert
  agents, i gave what i want".
- **CFO analysis:** `docs/finance/live-pilot-50gbp-2026-10-27.md` (branch `cfo/demo-volume-500-funding`, sha 8b7a42d6).
  - At £50 no 0.01-lot order fits the standing rails: the 1% per-trade limit, the 2% daily limit and the gate's 1:20
    margin and notional rails.
  - Three sizing options were simulated with 100,000 runs each for the pilot rows, assuming a 1.5 R payoff and 3 trades
    a day. Option (b), the owner's style at about £18-19 a trade, loses the account in 68% of runs within 5 trading
    days at zero edge.
  - Option (a), the conservative pilot, caps the loss at £25 by construction. At zero edge it reaches that stop in
    33% of runs; the median end balance is £44.7.
- **Evidence of edge: none.** No trial has passed the gates, and the demo record since 10-25 is flat within noise
  (readiness plan, section 2). This pilot is a paid test of the plumbing: fills, slippage, the 90 s confirmation,
  live exits and reconciliation. It is not a profit plan.
- **Inputs:** XAUUSD 4,014.84; GBP per USD 0.7508; ICM round trip $0.156 (£0.12) per 0.01 lot from 137 demo fills
  (daily review 2026-10-26). These were measured on 2026-10-27, and the limits below are in £ so that they do not move
  with price.

## Decision
Option (a) for account `icm-live` only. Every other account and every default is unchanged.

| Term | Value | Basis |
|---|---|---|
| Lot | 0.01 exactly (1 oz) | the minimum lot; there is no sizing-up |
| Stop distance | at most **$4.00** from entry. A longer stop is refused, never shrunk | $4 × 0.7508 = £3.00 |
| Risk per trade | at most **£3.12** at the stop, including the round trip (6.25% of £50) | £3.00 + £0.12 |
| Daily loss limit | **£6.25** (two full stops), absolute, account currency | 12.5% of £50; ADR 0004's £50 stays the ceiling |
| Pilot stop | after **£25 cumulative realised loss** or **30 filled entries**, whichever comes first: entries halt for good. Open positions keep their automatic exits | the £25 caps the downside |
| Open positions | at most 1 | |
| Timeframe | 15m families only, with stops ≤ $4 (scalp rules). The one family nominated by the research-director (L17) | the 15m natural stop of $9.90 is refused |
| Entries | owner-confirmed, card marked REAL MONEY, 90 s window, `approval_mode: propose` | ADR 0036; L8 |
| Exits | automatic, never gated | non-negotiable |
| Gate leverage | `leverage_cap` **500** for this account (not the broker's 1:5000) | see below |
| Dynamic or adaptive limits | off (`dynamic_limits: false`, no learnt caps) | tighten-only in any case |
| First order | 2026-10-30, if every go/no-go item in `docs/deploy/live-readiness-2026-10-30.md` is green at the 2026-10-29 18:00 UTC review (L18); otherwise the first day on which they all are | |

**Why `leverage_cap` is 500 and not the broker's 5000.**
- 500 is the smallest round value that lets one 0.01 lot through across the whole pilot range (£50 down to £25).
- **Margin.** The gate's figure is £3,014 / 500 = £6.03. Its margin level is 829% at £50 and 415% at £25, against the
  300% floor. The broker's own figure at 1:5000 is about $0.80 (£0.60).
- **Combined notional.** The cap is 0.30 × 500 × equity: £7,500 at £50 and £3,750 at £25, against a £3,014 notional.
  If gold reaches about $4,990 with the account at £25, that rail refuses. That is fail-closed and accepted.
- Using 5000 would let the gate rails stop binding at all. 500 keeps them meaningful.

**Rails changed for this account only, stated plainly.** Each is a loosening of a standing rail. Each is decided here
under the owner's delegation and is bounded by the £25 pilot stop:
1. The per-trade 1% hard limit becomes £3.12 absolute (6.25%).
2. The 2% daily cap becomes £6.25 absolute (12.5%). The £50 ADR 0004 cap remains the ceiling above it.
3. The gate's `leverage_cap` goes from 20 to 500.
4. The drawdown kill (12% of the high-water mark, £6 at £50) gives way to the £25 pilot stop as the hard halt.
   - The 8% size-down stage cannot shrink below 0.01 lot, so it is not applicable.
   - The £6.25 daily limit still ends each bad day.

Unchanged:
- RiskGate remains the only path to an order.
- The spread, stale-tick and news-blackout rails stay as they are.
- Max one order in flight (ADR 0052).
- BACKLOG 135 close-priority bridge (L2) is a go/no-go item.

**Config values for the pilot-mode implementer** (account `icm-live`; names to fit the L3 mechanism):

```yaml
icm-live:
  server: ICMarketsSC-MT5-3
  login: null                    # keyring only
  currency: GBP
  approval_mode: propose         # owner confirms each entry, 90 s
  dynamic_limits: false
  max_positions: 1
  daily_loss_limit_abs: 6.25     # GBP
  pilot:
    adr: 55
    lot: 0.01                    # fixed; refuse any other size
    max_stop_usd: 4.00           # refuse (never shrink) a wider stop
    max_risk_per_trade_abs: 3.12 # GBP, stop loss + round trip
    stop_loss_abs: 25.00         # GBP cumulative realised loss -> halt entries for good, exits continue
    max_entries: 30              # filled entries -> halt entries for good, exits continue
    timeframes: [15m]
    leverage_cap: 500            # gate margin and notional rails for this account
```

## Consequences
- **£ cost.**
  - Expected: about £3.60 in round trips over 30 trades (30 × £0.12), plus a trading result that is zero to negative
    because no edge is proven.
  - Worst case: £25 realised, plus the overshoot of one gap on the last open position.
  - Host: about £3.6 a month (L6, a spend approval already listed in the readiness plan).
- **Break-even.** None is claimed. The loss is booked as a cost of learning in the GBP ledger (L16), reconciled to the
  broker daily within £0.01.
- **Upside.** It cannot meet the £50-a-day north star at £50. That belongs to a £1,000+ account (ADR 0004).
  - The best day is two or three £4.5-wins at a 1.5 R payoff.
  - At p 0.45 the median end balance after the pilot is £54.
- **After the pilot stop or the 30th fill:** entries stay halted. The CFO and finance-auditor review the 30-trade
  record within 2 working days: slippage against the cost model, confirmation latency, exit behaviour and net £.
  - Any continuation, larger size, a second account or more capital needs a new recorded decision.
  - Capital follows evidence: a measured, out-of-sample net edge on at least 60 forward fills (the capital plan),
    sized at most at half-Kelly. A good streak is not evidence.
- **What would reverse this decision before or during the pilot:**
  - any go/no-go item red;
  - an exit that is late or not served;
  - an unreconciled ledger day;
  - slippage over 2 × the cost model on 5 or more fills;
  - the owner saying stop.
  Each of these halts entries; the exits stay automatic.
- **UK tax flag** (not tax advice): from the first fill, keep GBP records of every gain, loss, commission and swap.
  Spread-bet versus CFD treatment depends on the account type, and the owner should confirm it with an adviser.
- **Regulation:** the account is offshore (FSA Seychelles). The FCA's 1:20 cap, FOS and FSCS do not apply. Our rails
  are the protection that binds.

## Addendum (trading-safety review conditions and implementation, 2026-10-27)
- **Worst-case gap.** A stop is a market order once touched: a weekend or news gap can fill about **$20 beyond the
  stop** (about **£15** at 0.01 lot and 0.7508 GBP per USD). The worst single trade is then about £18 (£3 stop + £15
  gap), and the worst pilot about £25 + £15 = **£40**, not £25. The £25 stop bounds what the gate lets start, not
  what a gap can take.
- **Negative-balance protection must be confirmed with IC Markets** (support or the client agreement) for the
  Seychelles entity before the first order. Without it a gap on a £25-£50 account could leave a debt. Go/no-go item.
- **Implemented as icm-live config, not code bypasses** (`pilot:` in config/accounts.yaml, goldbot/risk/pilot.py
  bounds = these terms; the gate's pilot checks run only when that block is set; every demo rail unchanged):
  - the £25 stop is also checked before each entry: realised loss + open risk + this trade's stop and round trip
    must stay within £25 (`pilot_stop_projected`), as the daily limit is;
  - realised loss and filled entries are counted from the broker's deal history since `start_utc` (balance
    operations excluded, any magic counted), kept with the sticky stop in core.db `pilot_state`; a record that cannot
    be read refuses entries (`pilot_record_unknown`);
  - `leverage_cap` 500 for the gate's margin and combined-notional rails, lowered to the terminal's own leverage when
    that is smaller; the bridge's notional ceiling on this account is 0.30 x that (150x equity, £7,500 at £50)
    instead of 6x (£300 at £50, which refuses every 0.01 lot);
  - rails 1, 2 and 4 are `max_risk_frac: 0.125`, `daily_cap_frac: 0.25` and `drawdown_stages: false` on the pilot block,
    so £3.12 a trade and £6.25 a day bind over the whole £25-£50 range.
- **Open point for the trading-safety reviewer: the weekly cap.** The standing 5% weekly cap is not one of the four
  changed rails, so it still binds: £2.50 at £50, i.e. **one full stop ends the pilot's week**. It is left as is
  (never loosened without a decision); 30 trades are then unlikely within 4 weeks. Decide whether a fifth rail
  (weekly) is intended. **Ruled in the amendment below (weekly 25%, i.e. £12.50 at £50).**
- The supervisor's combined caps (1.5% / 4% of combined equity) are unchanged and see icm-live beside the demo accounts.

## Amendment (trading-safety rulings on the two open points, 2026-10-27)
Decided by the trading-safety reviewer under the owner's delegation. The unlock and typed phrase stay the owner's.

**(a) Weekly cap: a fifth changed rail, set to 25% of the week's starting equity (£12.50 at £50), not £25.**
- The standing 5% (£2.50 at £50) is below one full stop (£3.12), so one loss ends the week. The pilot then cannot
  collect its 30 fills in a useful time, which defeats its purpose.
- Setting it to £25 would remove the rail: the pilot stop already ends everything at £25. That would be a hidden
  loosening, not a weekly limit.
- £12.50 (four full stops, two bad days) means the pilot cannot spend its whole budget in one week. At least one weekly
  review (the ledger reconciliation, slippage against the cost model, the reversal triggers above) takes place before
  the second half is at risk. It never binds in a week that is not losing.
- As a fraction it tightens as equity falls (£8.75 at £35). The weekly check is on equity and has no projection, so a
  week can end at most one trade (£3.12) past it. This is accepted.
- **Implementation (config, not a code bypass):** `pilot.weekly_cap_frac: 0.25` in `config/accounts.yaml`, bounded at
  0.25 in `PilotRules` (`PILOT_MAX_WEEKLY_FRAC`). `engine_limits` maps it to `weekly_cap` for the pilot account only.
  The test `test_the_standing_weekly_5_percent_cap_still_binds_on_the_pilot_account` is replaced by one that proves
  25% on icm-live and 5% everywhere else.

**(b) Gap overshoot: acceptable only if the pilot holds nothing over a weekend. Stops are not tightened.**
- Where the £40 comes from: the weekend rule (`Engine._weekend_rule`) closes losers at the Friday cut but **keeps
  winners** with their stop moved to half the open profit. A weekend gap of about $20-30 goes through that stop. This
  is the realistic route to a single loss of £15-20.
- Tightening the $4 stop does not help. A gap's overshoot does not depend on the stop's width; a tighter stop only
  adds noise stop-outs.
- Moving the Friday cut earlier does not help on its own either, because winners are still held.
- **Ruling:** on the pilot account every position is closed at the Friday cut, winners too (`pilot.weekend_flat: true`,
  default false, so no other account changes). It is an exit, so it is automatic and never gated.
  - What remains is a midweek shock that is not in the calendar. The news blackout covers scheduled events. The daily
    break gap on gold is typically a few dollars.
  - Worst case kept in this record: about £25 + £15 in a rare midweek shock. The deposit (£50) bounds it, provided
    negative-balance protection applies.
- **Negative-balance protection stays a go/no-go item.** It could not be verified from the repo or this environment.
  `docs/finance/capital-plan.md` records it as unverified, and BACKLOG 27 has it open. Before the first order, the
  sre/finance-auditor records the following in BACKLOG 27:
  - IC Markets Global's (Seychelles FSA) client agreement and its negative-balance protection clause or policy page,
    with the version and date;
  - a written answer from IC Markets support to two questions: "Is negative balance protection applied to retail Raw
    Spread MT5 accounts of IC Markets (SC), automatically and per account? Is a negative balance from a gap reset to
    zero without a claim?";
  - the account's stop-out level, read from the terminal.
- **If IC Markets does not confirm it in writing, the pilot does not start.** A debt larger than the deposit is outside
  what the owner delegated.
