---
number: 154
title: Convert mode 2/3/5/6 swaps from the profit currency into USD
stage: 'engine (follow-up of fix/swap-mode-5-6, quant review b)'
size: S
status: 'done (triage 2026-11-13): PR #379 (R130)'
---
- **154. Convert mode 2/3/5/6 swaps from the profit currency into USD.** Value: `instruments_db.swap_per_lot` stores swap in the symbol's profit currency, but `books.swap_spec` hands it to SwapSpec as USD per lot, so a non-USD profit currency (DE40 EUR, JP225 JPY) misprices the RiskGate's swap projection by that FX rate; mode 5/6 currently refuses any non-USD profit currency (`swap_unknown`) instead. Acceptance, tests first: the swap is converted into USD with the same point-in-time profit-to-USD rate the gate uses for other price-unit amounts (`acct_per_profit` / `acct_per_usd`); an unknown or non-finite rate leaves the swap unknown (entries refused, never 0); a EUR-profit mode 3 index and a JPY-profit mode 5 symbol give hand-worked USD figures; the mode 5/6 USD-only restriction is lifted only together with this conversion.
