# Pre-registered reading rule: EVAL-1 gates in the catalogue screen (2026-11-07)

Status: binding for every catalogue card registered against EVAL-1. It does not re-gate the 79 units of WS-3 (EVAL-1
section 7: closed line). Source of the text: amendment EVAL-1 in `docs/research/evaluation-audit-2026-11-07.md`
section (b) (branch `research/eval-audit-1107`, commit 95a03b92), with the quant sign-off changes C1-C6
(`eval1_signoff_1107`). Code: `goldbot/research/catalogue/stats.py` (`explore_gates`, `confirm_gates`,
`test_old_gates`, `promotion_gates`); tests: `tests/test_catalogue_eval1_gates.py`. Adding this rule runs no trial;
K is unchanged.

No card may be registered against this rule before EVAL-1 preconditions 0 (i)-(iv) hold (harness D1-D4 fixed with
tests, the random-walk regression including the E9 drift-walk check of C1, the risk floor, `n_events` and trade n
reported apart).

## 1. The unit statistic (EVAL-1 section 2, C4)

Verbatim: "A unit is evaluated as a book: daily net return on equity at the pre-registered sizing (fixed fractional
risk per trade, or per-instrument vol targeting, stated in the card), all costs included, benchmark = cash (zero on a
margin account). Each unit has exactly one sizing rule in the card; any other sizing is a separate registry row; the
CONFIRM drawdown bound is read at that sizing and no later GBP projection or live sizing may exceed it (C4)."

As coded: the catalogue's one sizing rule is fixed fractional risk, `RISK_PER_TRADE` = 0.5 % of equity per 1 R. The
book's daily return is 0.5 % x the day's net R, marked to market on daily closes (`book_daily`): a multi-day trade
books side x (close - previous mark) / risk each day, the exit day books the rest of its gross and its whole cost.
Days with no position are 0 (cash).

## 2. Explore pass, ALL of (EVAL-1 section 3)

| Gate | Verbatim | Code |
|---|---|---|
| E1 | "Newey-West t of daily net book return >= 2, lag L = max(5, ceil(1.5 x median holding days)), fixed in the card (C3)" | `book["t"] >= T_MIN`; `eval1_lag`, `book_t` |
| E2 | "mean net > 0 at 1.0x" | `net > 0` (mean net R per trade) |
| E3 | "mean gross >= 2 x mean cost" | `gross >= 2 cost and gross > 0` |
| E4 | ">= 5 of 8 years net positive" | book net summed per calendar year, 2010-2017 |
| E6 | "n floors unchanged" | 300 when every cell is 1d/1w, else 1,000 |
| E7 | "learn_pre net same sign where n >= 60" | sign of learn_pre mean net = sign of explore mean net |
| E8 | "trimmed: net > 0 after removing the top 1 % of trades, and no single trade > 10 % of total net P&L; also, the book is net > 0 after removing the top 1 % of days (C6)" | `book["tail"]` |
| E9 | "drift guard: against a financed buy-and-hold book of the same instruments and side (equal risk, same cost model, always in the market), the unit's daily net book return has a Newey-West alpha (intercept) t >= 1.0, the alpha is >= 50 % of the unit's mean daily net return, and the unit beats that book scaled to its own average gross exposure on net P&L in >= 5 of 8 years" (C1) | `drift_guard`, `_e9_pass` |
| twin kill | Section 4: "a unit whose excess over its twin is t <= -2 is dropped (it is worse than random timing at the same exposure)" | `t_excess > -2` |

Reported, never gated at explore: the twin excess and its t (section 4: "a kill, not a pass gate"), drift_adj (old
E5), DSR at the registry K and at K_cells. The null-unit void rule and the buy-every-close sentinel stay (section 4).

## 3. CONFIRM, TEST-OLD and promotion (EVAL-1 sections 5 and 6)

- CONFIRM, read once, ALL of: "BH q = 0.10 over the m explore passers on the one-sided block-bootstrap p of the daily
  **net** book return; >= 5/7 years net positive; net > 0 at 1.5x cost stress; max drawdown at the registered sizing
  <= the account's size-down stage (8 % today)." Code: `run.cmd_confirm` (bootstrap on `book_daily`) +
  `confirm_gates`.
- TEST-OLD: "unchanged, on net (plus the E9 beta rule)"; E9: "at TEST-OLD (2006-02..2009, incl. 2008) a unit with
  beta > 0.3 must be net >= 0". Code: `test_old_gates` (n >= 60, net > 0, Newey-West t of the net book >= 1.28,
  `beta_net`).
- Promotion to the live-candidate list: "DSR >= 0.95 at registry K_eff (`n_trials_effective`) on the CONFIRM net series
  (the explore+confirm DSR is reported only) (C5)". Code: `promotion_gates`, reported per unit in `confirm.json`. The
  demo kill rule against the matched control stays with the engine.

## 4. Readings (where EVAL-1 leaves a choice, the stricter one is taken)

1. **NW window.** The gated t (E1, TEST-OLD) is the smaller of the Bartlett t at lag L and at 2L; the E9 alpha uses the
   larger standard error of the two. EVAL-1 says "lag L"; the WS-3 card (section 14) measured that a window of L
   understates an MA(L) long-run variance by about 30 %.
2. **Holding days** are calendar days (t_exit - t_fill), not trading days: a larger L.
3. **Top 1 %** is ceil(1 %) of the trades and of every book day, flat days included: at least one trade and one day are
   always removed.
4. **"Same side" for a two-sided unit**: E9 is run against the buy-and-hold book of each side the unit trades, and must
   pass against every one of them. For a one-sided unit this is the text exactly.
5. **Equal risk and exposure.** Each buy-and-hold leg holds 1 R = the unit's own median order risk on that instrument as
   a fraction of price; the unit's average gross exposure = sum over trades of (rfrac x fill / risk) x calendar days
   held, over (calendar days of the book x instruments). The buy-and-hold book pays the frozen sim's round trip once and
   `sim.swap_rate` per calendar night: the same cost model as the unit.
6. **Side-0 trades** (both OCO legs on one bar, quant C8) are left out of the beta estimate and kept in the alpha (the
   intercept plus their mean daily net).
7. **A year with no data** is not a year beaten (E9) nor a positive year (E4).
8. **A missing twin t (NaN)** fails the kill; a traded instrument without daily closes in the window stops the run
   before any result is written; E9 is never computed against a partial book (amended, quant G1). A unit without any
   book fails E1, E4, E8 and E9.
9. **Drawdown at the registered sizing** is linear in R (0.5 % x max drawdown in R), not compounded: larger.
10. **E7** requires a non-zero sign: a learn_pre mean net of exactly 0 fails.

## 5. Costs, data and the random-walk guard

- Costs (section 1, C2) are in `goldbot/research/catalogue/costs.py` with the table `config/catalogue_costs.yaml`
  (version measured-2026-11-07-v1). Swap terms follow C2 with point-in-time benchmark rates. Spread and commission
  are used only from a sample of 20 or more trading days (quant ruling 2026-11-07); every instrument has 3-11 days
  today, so the frozen fallback round trip stays. E9's buy-and-hold book uses the same model.
- Cost model details after the quant and code reviews: the rollover is 17:00 New York (21:00 UTC in summer, 22:00 UTC
  in winter); a short spread sample never lowers the fallback round trip but can raise it; BTCUSD's unmeasured swap is
  20 %/yr per side (`SWAP_FALLBACK_CRYPTO`); the daily book marks a trade only on closes after its fill instant.
- Benchmark-rate vintage (code review LOW-3): the OECD IR3TIB01 3-month rates (EUR, JPY, GBP, AUD, CAD, CHF, NZD) are the
  2026-11-02 download vintage, not first release. Revisions to these monthly averages are small, but using them is a
  small look-ahead, in the cost model only (no signal or gate reads it). ALFRED first-release values are a backlog item.
  USD uses DTB3 (daily T-bill), which is not revised.
- Oil series (quant review 5): the Dukascopy XTIUSD 1d bars carry one source segment (`seg` = 0 on all 1,529 explore
  bars, no cutover). Nothing in the data marks a roll. The largest daily moves are known market days (2015-08-26,
  2016-02-02, 2016-11-29) plus two data gaps (2011-09-23..11-10 and 2013-02-22..05-23). Whether the series is
  roll-adjusted is therefore unverified, and it is treated as NOT roll-adjusted: futures carry is in neither its price
  nor its financing.
- Precondition (ii)'s random-walk regression, including the C1 check that the buy-every-close sentinel and every
  long-only control fail E9 on a drifting walk, lives in `tests/test_catalogue_random_walk.py`
  (`test_long_only_controls_and_the_sentinel_fail_e9_on_a_drifting_walk`).
  `tests/test_catalogue_lookahead.py::test_buy_every_close_null_fails_explore_on_the_eval1_drift_guard` covers the
  sentinel on one synthetic drifting walk.

## 6. Survivor conditions (quant review 5, verbatim)

- "Any XTIUSD card passes only if it also passes with both sides charged max(|long_pct|, |short_credit_pct|,
  two_sided/2) = 21.16 %/yr."
- "Any short-side UK100/US500/JP225 card must also pass with heavy ex-dividend weeks excluded."
