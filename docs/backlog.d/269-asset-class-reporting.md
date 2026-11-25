---
number: 269
rank: 94
title: Trading performance by asset class and family x class
stage: Learning loop
size: M
status: done
---
- **269. Trading performance by asset class and family x class.** Value: the owner directive of 2026-11-25. Trading
  analysis is broken down by instrument category, because a strategy can perform differently on different
  instruments. Acceptance:
  - (a) `goldbot.ops.class_breakdown.asset_class_of` is the only mapping. It maps an instrument key, a store or broker symbol,
    or an arm id (suffix, or the spec row for a gold arm) to the `asset_class` in config/instruments.yaml. XAUUSD maps
    to metal, and an unknown name maps to "unknown", never to gold.
  - (b) The strategy ranking (each row and the view), the trades API page, the lab "by class" line in the daily and
    weekly messages, the ranking's Telegram family line and the performance report all give each asset class and each
    family x class: net money after costs, trades, win rate, money per trade, mean R and t.
  - (c) A family is flagged as split when its mean net R is positive in one class and negative in another, each class
    with at least 30 fills that have an R.
  - (d) A voided label (`exploration_label_void`) or a fill void as evidence for n never reaches a class figure
    (PR #442 rule).
  - (e) The web ranking table shows a Class column.
  - (f) ETF has no class because no ETF is listed. "Commodities" is split into metal, energy and agri.
