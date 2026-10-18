# Sizability of the 2026-10-18 batch at £1,000 and 0.01 lot (strategy-researcher, 2026-10-18)

**Question.** Of the 33 trials in `docs/research/batch-2026-10-18.md`, which could RiskGate place at 0.01 lot on the
£1,000 IC Markets account at today's volatility? Which fit `risk_per_trade` and the daily loss limit, and which are
unsizable whatever their verdict?

**Scope.** This is arithmetic on stops and limits only. No trade outcome was computed or read, so it is not a trial
and adds 0 to K. RiskGate limits and sizing are owner-only. Where this file quantifies an option that would loosen
them, it says what the option would mean. It does not propose the option as decided.

## Inputs (all stated, nothing assumed silently)

| Input | Value | Source |
|---|---|---|
| ATR(14), $/oz, last-90-day median (p90) | 15m 8.01 (11.54) · 1h 17.73 (21.36) · 4h 36.61 (41.19) · 1d 87.63 (94.67) | `docs/research/atr-measured-2026-10-18.md`, release `data-v1` 1m mid bars to 2026-10-05, Wilder ATR(14). These are volatility statistics only: no outcome is read from the holdout window |
| ATR15 by hour, last 12 months | calmest 22-00 UTC (median $7.60-8.10) and 10-12 UTC ($8.2-8.6); worst 14-17 UTC ($10.7-11.6) | `docs/research/tight-stop-entry-study-2026-10-18.md` section 3 |
| Stop multiple and ATR timeframe per cell | resolved with the specialist's own code: `preregistration.resolve_variant`, then `SPECIALISTS[fam](**ov).config["stop_atr"]` and `atr_tf`; pooled members from `pipeline.pooled_members` | code at `239d866` |
| Account | equity £1,000, GBP per USD 0.75 (GBPUSD ≈ 1.33) | `config/settings.yaml` `research.lot_fit_equity` / `lot_fit_acct_per_usd` |
| Lot | 0.01 lot = 1 oz, min 0.01, step 0.01 | `goldbot/research/min_lot.py:41-47` (`LotSpec`, IC Markets) |
| Risk per trade | 0.5 % (default, paper and demo); 0.1 % tiny-live | `config/settings.yaml:44-45` |
| Min-lot rule | refused when the 0.01 lot's realised risk > min(1.2 × target, 1 %) and the raw lot < 0.01 | `goldbot/risk/sizing.py:25,104-107`, called from `goldbot/risk/gate.py:636-641` |
| Min-lot exception | exactly 0.01 lot if realised risk ≤ `min_lot_risk_cap` (≤ 1 %, halved in size-down); **off in the repo** (`min_lot_risk_cap: null`), demo server only via `settings.local.yaml`; never in tiny-live | `gate.py:351-358`, `gate.py:642-651`, `gate.py:109`, `config/settings.yaml:69-75` |
| Daily loss limit | effective limit = min(`daily_cap` 2 % × day-start equity, `daily_loss_limit_abs` £50) = **£20 at £1,000**. Each entry is projected as loss so far + this trade's stop risk + its round-trip cost and swap | `gate.py:343-349`, `gate.py:570-577`, `config/settings.yaml:47`, `config/accounts.yaml:28` |
| Round trip for the daily projection | 0.37 $/oz (settings prior) + ≈ 0.30 $/oz spread = 0.67 $/oz | `batch-2026-10-18.md` section 0 and its sizing note |

Computation: `goldbot.risk.sizing.effective_risk` / `size_lots` and `RiskGate.min_lot_cap` (the gate's own
functions) for every cell. The script is a scratch file and is not committed.

Thresholds that follow from these inputs:
- The default gate places 0.01 lot while the stop ≤ **$8.00/oz** (£6.00 = 1.2 × £5).
- The demo exception at 1 % allows it up to **$13.33/oz** (£10).
- Tiny-live allows it only up to **$1.60/oz** (£1.20 = 1.2 × £1).

## Per-cell table (risk of the 0.01 lot at the stop; spread not included)

Column key:
- **Default**: the repo settings.
- **Demo + exc.**: `min_lot_risk_cap: 0.01`.
- **Places while ATR ≤**: the ATR at which the default gate (and, after the slash, the demo exception) stops placing.
- **Daily**: the gate's first-trade projection, set against £20.
- *exception* means the trade is placed only through the minimum-lot exception.

| Cell | Family, tf (ATR tf) | stop_atr | Stop $ | £ at 0.01 | % of £1,000 | Default 0.5 % | Demo + exc. | Tiny-live 0.1 % | Places while ATR ≤ (default / demo) | Daily: projection, full losses before £20 |
|---|---|---|---|---|---|---|---|---|---|---|
| B01#4 | session_open 15m | 0.75 | 6.01 | 4.51 | 0.45 % | **fits** | fits | refused | $10.67 / $17.78 | £5.01, 3 |
| B01#1,#2,#3,#5 | session_open 15m | 1.0 | 8.01 | 6.01 | 0.60 % | refused (by £0.01 at the median) | exception | refused | $8.00 / $13.33 | £6.51, 3 |
| B02#1-#3 | intraday_momentum 15m | 1.0 | 8.01 | 6.01 | 0.60 % | refused (at the median) | exception | refused | $8.00 / $13.33 | £6.51, 3 |
| B03#1-#4 | breakout 15m | 1.0 | 8.01 | 6.01 | 0.60 % | refused (at the median) | exception | refused | $8.00 / $13.33 | £6.51, 3 |
| B04#1 | mean_reversion 15m | 1.0 | 8.01 | 6.01 | 0.60 % | refused (at the median) | exception | refused | $8.00 / $13.33 | £6.51, 3 |
| B12 member | session_open 15m | 1.0 | 8.01 | 6.01 | 0.60 % | refused (at the median) | exception | refused | $8.00 / $13.33 | £6.51, 3 |
| B12 member | mean_reversion 15m | 1.5 | 12.02 | 9.01 | 0.90 % | refused | exception | refused | $5.33 / $8.89 | £9.51, 2 |
| B12 member | intraday_momentum 15m | 2.0 | 16.02 | 12.02 | 1.20 % | refused | refused (> 1 %) | refused | $4.00 / $6.67 | £12.52, 1 |
| B06#2, B07#1-#2, B13 breakout | trend / breakout 1h | 1.0 | 17.73 | 13.30 | 1.33 % | **unsizable** | unsizable | unsizable | $8.00 / $13.33 (ATR1h; never in 2026) | £13.80, 1 |
| B06#1, B13 trend | trend 1h | 1.25 | 22.16 | 16.62 | 1.66 % | **unsizable** | unsizable | unsizable | $6.40 / $10.67 | £17.12, 1 |
| B04#2-#3, B05#1-#3, B06#3, B08, B13 tsmom | mean_reversion / tsmom / trend / asia_drift 1h | 1.5 | 26.59 | 19.95 | 1.99 % | **unsizable** | unsizable | unsizable | $5.33 / $8.89 | £20.45: **over £20, refused `daily_loss_limit_abs` even as the first trade of the day** |
| B09#1-#2 | tsmom 4h | 1.5 | 54.91 | 41.19 | 4.12 % | **unsizable** | unsizable | unsizable | — | £41.69: over £20 |
| B09#3 | tsmom 4h | 2.0 | 73.22 | 54.91 | 5.49 % | **unsizable** | unsizable | unsizable | — | over £20 and over £50 |
| B11#1 | tsmom 4h (1d) | 1.0 | 87.63 | 65.72 | 6.57 % | **unsizable** | unsizable | unsizable | — | over £50 |
| B10 (H-01), B11#2-#3 | tsmom 4h/1d (1d) | 1.5 | 131.44 | 98.58 | 9.86 % | **unsizable** | unsizable | unsizable | — | over £50 |

What share of the time a cell places depends on ATR15. Over the last 90 days ATR15 had p25 $6.95, median $8.01, p75
$9.52 and p90 $11.54. The shares below are interpolated between those quantiles, so they are approximate:
- 0.75 × ATR15 under the default gate: about 85 % of bars.
- 1.0 × ATR15 under the default gate: about 50 %.
- 1.0 × ATR15 with the demo exception: over 90 %.
- 1.5 × ATR15 with the demo exception: about 60 %.

## Ranking of the sizable cells

Ranked by how robust the fit is under the repo's default gate. The cost per trade in R (0.71 $/oz ÷ stop) is the
gross edge needed just to break even, quoted from the tight-stop study.

| Rank | Cells (trials) | Fit | Break-even gross edge | Comment |
|---|---|---|---|---|
| 1 | B01#4 (1) | default gate, ~85 % of bars; demo exception up to ATR15 $17.8 | 0.118 R | The only cell that fits the repo's own 0.5 % rule at the median. It has the tightest stop, so it carries the highest cost in R |
| 2 | 1.0 × ATR15 cells: B01#1,#2,#3,#5, B02#1-#3, B03#1-#4, B04#1 (12) | default gate only on below-median ATR15 bars (~50 %); demo exception almost always | 0.089 R | The live fit is a volatility filter, so the traded set is the calmer half of the candidates (finding F3) |
| 3 | B12 pooled 15m (1) | session_open member as rank 2; mean_reversion member only with the exception, ~60 %; intraday_momentum member never | 0.059-0.089 R | Partly sizable: a third of the pool's members can never be placed |
| — | All 1h, 4h and 1d cells (19 trials: B04#2-#3, B05-B11, B13) | **unsizable** at £1,000 under every setting, including the demo exception | — | Shadow only. Every 1.5 × ATR1h cell is also refused by the £20 effective daily limit |

Totals: 1 trial fits under the default gate at the median, 13 fit only partially, and 19 are unsizable. **No trial
is sizable in tiny-live**: it needs a stop of $1.60/oz or less, a fifth of one ATR15.

## Check of H-27 lot_fit and min_lot.py: is a trade above the cap reported as unsizable, or rounded up?

**lot_fit: correct.** A refused trade is reported as unfit and earns no P&L. It is not rounded up.
- `size_lots` raises the lot to 0.01 but flags `refused` (`goldbot/risk/sizing.py:104-107`).
- `lot_fit_overlay` keeps such a trade only through the exception, and only when the exception is configured and
  realised risk ≤ cap (`goldbot/research/lot_fit.py:127-132`). This is the same test as `gate.py:644-651`. Every
  other trade is `fit=False`, with P&L 0 (`lot_fit.py:133`).
- Verified on three trades with stops $6.01, $8.01 and $12.02, and fx 0.75:
  - Default: 1 fit, 2 unfit.
  - With `min_lot_risk_cap: 0.01`: 3 fit, 2 of them through the exception.
- `tests/test_lot_fit_overlay.py` pins parity with `RiskGate.check`.

**min_lot.py: never rounds up either, but two findings (reporting only, not edited):**
- **F1 (MEDIUM): no account-currency conversion.**
  - Where: `goldbot/research/min_lot.py:109` computes `min_lot_risk_usd` in USD, and `:114-115` divides it by the
    target to give `min_equity_*`. `size_lots` is called without `acct_per_usd` (`:117-118`, so fx = 1).
  - Effect on a GBP account: the equity thresholds are in USD, and `--equity 1000` is read as $1,000. Realised risk
    is overstated by 1.33 ×.
  - Reproduced: `python -m goldbot.research.min_lot --price 4150 --atr 8.01 --stop-atr 0.75 --equity 1000` prints
    `0.60% refused`. The gate, with the account's fx 0.75, places this trade at 0.45 %.
  - The error is on the conservative side, but the tool then contradicts the gate and lot_fit for the real account.
  - Fix (implementer): add `acct_per_usd` (default from `research.lot_fit_acct_per_usd`) and label the currency.
- **F2 (LOW): `allowed` ignores the min-lot exception.**
  - Where: `min_lot.py:116-120`. It reports "refused" on a server where `min_lot_risk_cap` would place the trade.
  - The docstring says "sizing rule only" (`:13-14`), so this is documented, but it differs from lot_fit.
  - Fix: read `RiskLimits.min_lot_risk_cap` and report `min_lot_exception` the way lot_fit does.
- **F3 (MEDIUM, research process): the batch commands do not request the lot-fit overlay.**
  - Where: every command in `batch-2026-10-18.md` section 1 omits `-f lot_fit=true`, and the input defaults to
    false (`.github/workflows/research.yml:25`).
  - Why it matters: for the 1.0 × ATR15 cells the traded population is the below-median-ATR half. Without the
    overlay, the reports give no "net R (fit)" for the trades that could actually be placed.
  - The overlay is reporting only and is not part of the registry identity (`goldbot/research/cells.py:172`).
  - Fix: add `-f lot_fit=true` before dispatch. It costs 0 trials and does not change the reading rule. The
    research-director decides, since that file is theirs.
- **F4 (MEDIUM, risk communication): the £50 limit is not the binding daily limit at £1,000.** The binding limit is
  `daily_cap` 2 % = £20 (`gate.py:347-349`). Each entry projects its full stop risk plus cost (`gate.py:576`), so:
  - a 1.5 × ATR1h trade (£20.45) is refused even as the first trade of the day;
  - 15m 1.0 × ATR15 trades allow 3 full losses a day.

  The supervisor's combined cap is 1.5 % = £15 (`config/settings.yaml:49`; not traced further here), so the
  combined figure may bind first. Documents that quote "£50 a day" for this account overstate the room.

## Options within today's limits, as testable hypotheses

Constraint from evidence, not opinion: **the binding limit is gross edge, not stop width.** The tight-stop study is
pre-trial and design sample 2024-01..2025-09 (net of 0.71 $/oz). It found:
- signal gross edge vs a random-side control of +0.05 to +0.07 R whatever the entry;
- net R of −0.11 R at a 1.5 × ATR15 stop, falling to **−0.45 R** with a pullback-reclaim 1m entry;
- net R of −0.10 to −0.15 R with the stop held at $3-6.65.

Any option that shrinks the stop multiplies cost in R. The options are ranked by expected information per trial.

| ID (proposed) | Hypothesis | Mechanism | Test and trial cost | Reading rule (fixed now, before any data) | Prior |
|---|---|---|---|---|---|
| S-1 | **Low-ATR gating does not destroy the 15m edge.** Gross mean R of a 15m batch cell, restricted to candidates whose 0.01 lot fits the default gate (ATR15 ≤ $8.00 for 1.0×), is ≥ its all-candidate gross R − 0.03 R. | Lower vol means a smaller $ stop, so a fixed $ cost is a larger share of R (0.71/8 = 0.089 R). The edge must not be concentrated in high-vol bars, which are the ones the gate drops | 0 trials. A diagnostic on the batch's own out-of-fold labels via the lot-fit overlay, after each verdict. It cannot pass anything | Continue (feeds S-3) if the fit-subset gross R ≥ the all-candidate value − 0.03 R and the fit subset has ≥ 300 trades. Otherwise record "edge sits in high-vol bars: unsizable at £1,000" against that cell | Weak. Vol-scaled edges usually scale with vol, so the low-vol half probably earns less in $ while paying the same $ cost |
| S-2 | **Partial session / time of day.** Restricting 15m entries to the calm hours (22-00 and 10-12 UTC) raises the fit fraction without lowering gross R | Calmer hours give a smaller ATR15. But the study found hour explains less than the overall ATR level: in the last 12 months no hour has median ATR15 < $7.60, so the gain is small | 0 trials. Add `hour_utc` and a session dummy as point-in-time features to H-14 discovery (fold-internal selection) | Spend an individual trial only if H-14's stability selection keeps an hour or session feature in ≥ 60 % of folds; otherwise record "hour filter: no selection" | Weak. Overlaps S-1; probably subsumed by it |
| S-3 | **Stop-at-cap (cost-aware stop).** stop = min(1.0 × ATR15, $8.00), target unchanged in $. This is the widest stop the default gate places, so it is the cheapest in R that fits | It minimises cost per R subject to fitting. Above ATR15 $8 it becomes a sub-ATR stop, which the study's $3-6.65 band says loses (−0.10 to −0.15 R) | 1 trial, written into the Q1 2027 file only as a neighbour of a 15m batch PASS or LEAD, and only if S-1 continued for that cell. Not dispatched in Q4 (batch rule: no neighbours after results) | Same rule as batch section 2, at the K in force. Additionally FAIL if net R on the ATR15 > $8 subset is < −0.05 R | Low. Run only if a parent shows gross ≥ 0.12 R |
| S-4 | **1m-deferred entry (H-25).** | — | **Known-failed as specified** (pre-trial study, −0.45 R / −0.46 R for T1/T2). Do not spend O-11 on T1/T2 rules without new evidence: a parent signal with gross edge ≥ 0.36 R (break-even at a $2 stop) | — | Recommend that the research-director withdraw or rewrite O-11 |
| S-5 | **Fixed-$ stop / sub-ATR structural stop.** | — | **Known-failed** (same study, $3-6.65 band) | — | Record in `hypotheses.md` B so it is not re-tested |
| S-6 | **1h+ trades at 0.01 lot with a stop shrunk to fit ($8 = 0.45 × ATR1h).** | — | Equivalent to S-5 on a slower signal: cost 0.089 R against R-06's gross +0.061 R | — | Expected fail; not proposed |

## Owner-only options (quantified, not recommended or assumed)

| Option | What it would mean | Research view |
|---|---|---|
| Keep paper and shadow at £1,000 | No real-money trade from any batch cell (tiny-live places nothing) | Recommended (same as `batch-2026-10-18.md` section 3 (a)) |
| Larger account | Equity at which the cell passes the default 1.2 × 0.5 % rule: 15m 1.0 × ATR15 ≈ £1,000 (median ATR only), 0.75 × ATR15 £751, 15m 1.5 × ATR15 £1,502, 1h 1.0 × £2,216, 1h 1.5 × £3,324, 4h 1.5 × £6,864, 1d 1.5 × £16,431. At tiny-live (0.12 %): 15m 1.0 × £5,006, 1h 1.5 × £16,622 | Spending decision. The thresholds rise in proportion to ATR |
| `min_lot_risk_cap: 0.01` on the demo server | Places the 15m cells at up to 1 % realised risk (£10, half of the £20 effective daily limit, per trade) | Demo only. The design forbids it in tiny-live (`gate.py:109`) |
| A smaller contract or another broker or instrument (e.g. a 0.001-lot gold CFD) | Would divide every figure above by 10 *(availability, costs and FCA status unverified)* | Instrument/broker change: owner decision; needs a cost measurement first |
| Raise `daily_cap` above 2 % | Lets a 1.5 × ATR1h first trade through the £20 limit | Loosens a risk limit: not recommended |

## Definition-of-done check

- Factual claims cite a file and line, or a doc with its sample. The only external claim, the availability of
  0.001-lot gold CFDs, is marked unverified.
- The table separates robust results from single-study ones. The known-failed options (S-4, S-5, S-6) come from one
  pre-trial study, the tight-stop study, and are flagged as such.
- No trial was proposed for dispatch. S-1 and S-2 cost 0. S-3 is conditional and costs 1 trial in Q1 2027. K is
  unchanged at 53 (batch), and the holdout was not touched: the ATR inputs are volatility only.
- Owner-only items are in the last table, flagged.
- Not done here:
  - `hypotheses.md` rows for S-1..S-6. These should go in together with the batch verdicts, so the ledger is
    written once.
  - Fixes for F1 and F2 (implementer) and F3 (research-director).
