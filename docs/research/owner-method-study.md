# Owner-method study: from an MT5 trade-history export to hypotheses

Research director, 2026-10-11. Brief for the owner, the strategy researcher and the quant reviewer. The tool exists:
`python -m goldbot.ops.run owner-history [PATH]` (`goldbot/research/owner_history.py`, OWNER_GUIDE section on the
trade history). This note says what to export, what to analyse, and how to keep one trader's sample from being
mistaken for evidence. It spends no trial and changes no rule.

## 0. What the claim means, and what can transfer

The owner reports £30–50 a day trading XAUUSD by hand on a £50 account. That is 60–100% of equity a day. Under
goldbot's rails (1% per trade, £50 daily loss limit on £1,000, ADR 0004) a £50 account risks £0.50 a trade, so the
same day would be 60–100 R. Results of that size come mostly from **position size relative to equity**, which can
never transfer: the 1% cap and the daily limit are not research variables. What can transfer is the **trade stream
in R and in dollars per ounce**: where and when the owner enters, how far price moves for him against the round trip,
and how he exits. The study's first job is to separate the two:

  daily return % = trades per day × risk per trade (% of equity) × mean R per trade

Each factor is reported on its own. The headline goldbot number is the owner's R stream re-priced at 1% of £1,000
(£10 per R), which shows what his method would have made inside the rails. A record that is only strong because
risk per trade was 20–50% of equity has no research content. It carries a ruin risk that `owner_history` reports.

## 1. The export (stays local, never committed)

- **How:** MT5 desktop, History tab, period "All history", right-click, Report, then Open XML (xlsx) or HTML. CSV of
  deals or positions also works.
- **Where:** `~/Personal project/trade-history/`, outside every git work tree. The analyser refuses to write inside
  any work tree. Its outputs (`owner_history_*.md`, `.json`, `.trades.csv`) go next to the export with mode 0600.
- **Never in the repo:** the report holds the account number, name and broker, and the repo is public. The
  pre-commit hook `no-mt5-report` (`scripts/check_no_mt5_report.py`) rejects MT5 reports by name, by title and
  account label, and by shape. `.gitignore` ignores `*.mt5report.*`. Not in issues, PR text or chat either. The
  known gaps of the hook (tiny translated exports, xlsx with numeric dates) are listed in its docstring. Do not rely
  on it: just never copy the file into the repo.
- **What may enter the repo:** aggregates in R, %, counts, n and K only. No money amounts, no tickets, no account
  identifiers, no individual trade rows.
- **Complete, not curated:** the whole history, including losing periods, reset or blown accounts and every deposit
  and withdrawal. Picking a good month is the largest single bias this study can suffer, and nothing downstream can
  correct it.

## 2. Fields and what each is for

| Section | Fields | Used for |
|---|---|---|
| Header | account currency, Hedge/Netting, server (only to choose the time zone; never copied) | currency of P&L; netting changes how partial closes appear; server time → UTC (`--server-tz`, IC Markets Europe/Athens) |
| Positions | open time, symbol, type, volume, open price, S/L (final), T/P, close time, close price, commission, swap, profit | the trade list: side, size, holding time, net result, round trip paid |
| Orders | order type (market, limit, stop), placed and filled times, **initial** S/L and T/P | 1 R = the initial stop distance (the Positions S/L is the final stop after any trailing). Entry style: market vs resting orders at levels |
| Deals | direction in / out / in-out, partial volumes, comment (`sl`, `tp`, `so`), balance | scale-ins and partial closes (runner behaviour); exit reason; `so` = stop-out by margin, a risk finding in itself; deposits and withdrawals for time-weighted return |

**Derived per trade** (the analyser computes most of these; marked † where bars are needed and the tool has no price
path today):
- entry and exit in UTC; session (Tokyo / London / New York on each date's DST clock); minutes after the session
  open; minutes to or from the nearest tier-1 release;
- holding time; R multiple; stop moved to break-even or into profit (`sl_moved`, kept out of the R statistics);
  risk at entry as % of equity; notional ÷ equity (leverage);
- distance to the nearest $10 level;
- † ATR(15m) and ATR(1d) at entry, the day's range ÷ ATR20 (was the trade on an opportunity day?), MFE and MAE,
  distance to the prior-day and Asian extremes.

**Derived per day:** net % of start-of-day equity, number of trades, consecutive losses, re-entry within N minutes
of a loss, and whether the day hit £30 / £50 (`--targets`).

## 3. Analysis plan, in this order

1. **Integrity.** Period covered, trades per month, share of trades with a stop (without a stop R is undefined, so
   report $/oz instead), deposits, any stop-outs. If fewer than about 100 closed trades with a stop exist, stop at
   description: no slice can clear the bar.
2. **Decomposition** (section 0): trades per day, risk per trade, mean R, hit rate, payoff ratio, the R stream at
   £10 per R, and ruin risk at the owner's own sizing.
3. **Edge in price terms (the transferable number).** Mean net $/oz per trade, and the same divided by goldbot's
   round trip (Dukascopy spread + 0.37 $/oz) and by ATR(15m) at entry. A method whose average capture is under about
   3 round trips cannot survive goldbot's cost gate (H-13), whatever it did on the owner's account.
4. **Where it comes from.** Slices by session, hour, weekday, side, holding-time band, news proximity, near-round-
   number and near-session-open entries. Every slice is reported, not only the winners. K = the number of slices
   examined, and a slice "clears" only at Bonferroni 5%/K with n ≥ 10 (`slice_bar`).
5. **Exits.** Holding time of winners vs losers, share of partial closes and of break-even moves, R of the best 10%
   of trades. This is the comparison with the runner research (H-17 V1, BACKLOG 31): does the owner make his days by
   holding a few winners on trend days?
6. **Codifiability.** Write the entry as a rule using only data known at the entry (one sentence). Report how many
   of the owner's entries the rule reproduces, and how often it fires when he did not trade. If no simple rule
   reproduces a clear majority, record "discretion not codifiable from this record". That is a valid result.
7. **Overlap with the queue.** Map each candidate to an existing hypothesis before proposing a new one: London-open
   sweeps → H-08 inside H-14; round numbers → H-05; trend days → H-17; news → H-18 / H-11; Asian hours → H-04. An
   overlap adds a diagnostic to that trial's report (for example, "does the rule also fire on the owner's entry
   days?"), not a new trial.

## 4. Not overfitting to one trader's sample

- **Seeds, not evidence.** Every pattern is an in-sample observation of one discretionary record (evidence grade
  "owner record, in-sample, n = …, K = …"). It becomes a hypothesis row with a mechanism, a pre-registration written
  before its data are seen, and a trial charged to the quarter's budget, read by its rule and the unchanged gates.
- **Regime control.** The record probably covers 2025–26, a strong bull market for gold. A mostly-long record must
  beat simply being long: report long trades' mean $/oz minus the unconditional mean move over windows of the same
  length at the same hours, and the long and short sides apart.
- **The holdout problem.** The research holdout is 2025-10-01 .. 2026-09-30, and the owner's trades probably fall
  inside it. A hypothesis seeded from that period can be tested honestly on the research window 2010-01 .. 2025-09,
  which is out of sample with respect to the seed. But the holdout is then **not independent** for it. Rule
  (decided here): every owner-seeded configuration is tagged `seed: owner-history`; its confirmation is a forward
  shadow period that starts after the seed date, not a holdout scoring. Bar-based context for the owner's trades (†
  above) is computed only in the private folder, describes his trades only, and is never used to evaluate or tune a
  goldbot configuration.
- **Multiple testing.** K counts every slice and every rule tried during the study, and is reported with every seed.
  At most **two** owner seeds become pre-registered trials in any quarter, ranked against the agenda like any other
  hypothesis (north-star KPIs first) and funded from the free reserve, never from reserved slots.
- **No hindsight tags.** If the owner labels trades ("method" vs "gamble"), the labels count only if written before
  the analysis is seen; otherwise the whole record is used.
- **Costs as the owner paid them.** Note his account type (Raw or Standard) and the spread and commission he
  actually paid, so a difference from goldbot's costs is explained rather than discovered later.
- **Sizing never transfers.** No seed may propose a higher risk per trade, looser daily limits or gated exits. Those
  are not research variables.

## 5. Steps and owners

| Step | Owner | When |
|---|---|---|
| Export the complete history to `~/Personal project/trade-history/` | owner | when convenient |
| Run `python -m goldbot.ops.run owner-history` locally (or a Claude session on the Mac with the owner, data staying local) | owner | after the export |
| Share the summary in R, %, counts and K only (no amounts) with the research director, via the program director | owner | after the run |
| Turn seeds into hypothesis rows (mechanism, evidence grade, overlap with sections A and A2) | strategy researcher | within a week of the summary |
| Rule on K, the regime control and the forward-shadow rule for each seed | quant reviewer | before any pre-registration |
| Rank seeds into the next quarter's draft (at most two trials) | research director | at the next draft |

**Optional extension (not scheduled):** the † fields need a price path. That means joining the owner's trades to 1m
bars in the private folder with a holdout-safe flag. Build it only if steps 1–6 find a codifiable pattern worth the
context.

## Not verified / left out

- The analyser was read, not run. No export exists in this environment, by design.
- Whether the owner's period overlaps the holdout is an assumption until the export's dates are seen.
