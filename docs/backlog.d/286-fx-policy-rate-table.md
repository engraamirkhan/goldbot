---
number: 286
rank: 105
title: 'Data: point-in-time policy-rate table for 8 currencies (FX swap on history rows, U2 P3)'
stage: 'Data'
size: S
status: proposed
---
- **286. Data: point-in-time policy-rate table for 8 currencies (FX swap on history rows, U2 P3).** Value: the quant's swap ruling F9 (amendment `docs/research/prereg-amendment-2026-11-28-u2-fx-cost-probes.md` section 5 item 4) charges history-row swap from the as-of policy-rate differential; without this table P3 fails and U2's Stage 0 waits (kill 2027-01-31). Owner: data-engineer; quant countersigns; due 2026-12-23. Acceptance: step functions of policy decisions with `available_utc` = the decision announcement instant (UTC), never revised, from the last decision before 2010-01-01 to 2025-09-30 (holdout sealed): USD FRED DFEDTARU; EUR FRED ECBDFR; GBP BoE IUDBEDR; JPY BoJ decision table (-0.10 % announced 2016-01-29, effective 2016-02-16; then 0-0.1 %, 0.25 %, 0.5 %), cross-checked against STRDCLUCON; AUD RBA F1.1; NZD RBNZ B2 OCR; CAD BoC Valet V39079; CHF the midpoint of the SNB 3M CHF LIBOR target range until 2019-06-13, then the SNB policy rate. No OECD or FRED monthly averages. Joined only via `asof_join` on `available_utc`. Tests: truncation at 2025-09-30; a hand-computed 2015 EURUSD long and a 2023 USDJPY short; a credit row charges 0; any row on or after 2025-10 refused. Local data only; a sha256 manifest.
