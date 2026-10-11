- **Owner history: shareable summary, in-repo input refused, hook covers every input format (2026-10-11; owner
  approved the owner-method study, M43).** `python -m goldbot.ops.run owner-history` now refuses a report inside any
  git work tree before opening it, and prints only the shareable summary (`render_share`, also written as
  `owner_history_*.share.md`): win rate, mean R or net USD/oz, holding-time quantiles, session/hour/weekday/side/
  holding histograms with each slice's verdict against the Bonferroni bar, winning and losing streaks, drawdown %,
  risk per trade and leverage relative to equity, and K. No account identifier, ticket or account-currency amount;
  the money figures stay in the private `.md`. The `no-mt5-report` hook now also rejects an English MT5 Positions /
  Orders / Deals header row in CSV, TSV, HTML or xlsx (any trade count, any stamp format) and the analyser's own
  outputs. Not built: a separate `scripts/owner_history.py` (the entry point already exists; a second one would
  drift). Needs quant and security review.
