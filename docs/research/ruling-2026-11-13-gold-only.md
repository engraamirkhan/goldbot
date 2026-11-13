# Research-director ruling 2026-11-13: gold-only demo, no new 1m arms, A2 off for 1h+, MFE/MAE recorded

Status: **in force** from 2026-11-14 (R135; implemented on branch feat/gold-only-demo-mfe). Kill-only for items 1-3:
nothing here adds a route to continue, graduate or promote an arm.

## Evidence (live demo fills, all accounts, to 2026-11-13)

| Slice | Fills | Net | Net R a trade |
|---|---|---|---|
| All | 5,333 | -GBP 257 | |
| Non-gold (every instrument but XAUUSD/XAUUSD+) | 1,131 | -GBP 262 | -0.21 R |
| Gold | 4,202 | +GBP 5 | about 0 |

93% of the gold fills are 1m/5m. The A2 readability rule (prereg-amendment-2026-10-30-kill-rules.md) retired the slow
arms after four days with no P&L read.

## Rulings

1. **Gold only on demo.** Every non-gold demo arm and control stops new entries from 2026-11-14 (89 rows:
   S0-/I0-/LB-/LBC-<instrument>, the XAU crosses XAUEUR/XAUJPY included). Each row gets `retired_day` 2026-11-14 and
   `retired_rule` = `goldbot.research.arms.GOLD_ONLY_RULE`. The id, hash and K stay. The registrations in
   config/accounts.yaml stay, so open positions keep every exit (dropping them once flat is backlog 187). The gold arms
   and their matched controls keep running (LCZ*, GS1C). The gold execution/cost controls also keep running (VEX15,
   VEX1H, IEX1H). IEX1H was first retired here; the quant review of R135 reinstated it. It trades gold, and
   profitability-2026-11-12 section 3(a) kept it. It is also icm-demo's only 1h fill-vs-replay cost stream, which the
   A2/EVAL-2 history reads use.
2. **No new 1m arms.** The 1m bars give most of the gold fills and none of its edge. The 1m arms already in the spec
   keep running to their reads. A new 1m row loads only after a dated ruling adds its id to
   `goldbot.research.arms.ONE_MINUTE_ALLOWED`. That list holds the 55 1m rows of the spec on 2026-11-13. Retired rows
   are on it too, because a row is never removed and must keep loading. The research and CI loaders refuse a 1m row
   that is not on the list. The engine keeps such a row configured with its entries refused. Discovered arms
   (config/spec_arms.yaml) leave such a row out.
3. **A2 readability does not apply to arms of 1h or slower.** Those arms are read on history and on the 2018-25
   confirm window. Their demo fill rate says nothing about them. From 2026-11-13, any `retired_rule` that names A2 on a
   1h+ arm is refused (`goldbot.research.arms.ruling_1113_refusal`). The A2 retirements of 2026-10-30 stand, because a
   retirement is never undone.
4. **Record MFE and MAE.** Each closed `exploration_trades` row gets `mfe_r`/`mae_r` (in R of the initial stop) and
   `mfe_usd`/`mae_usd` (USD per oz), plus `excursion_approx`. They cover fill to exit and come from the tick track the
   engine keeps for its exits. Older rows have none of these fields, and readers treat that as missing, never as 0 R.
   No exit changes. Reading the fields in the reports is backlog 195.

No cap, RiskGate rule, loss limit or size changes.
