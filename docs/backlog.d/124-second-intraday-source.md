---
number: 124
rank: 44
title: Second free intraday gold source and Dukascopy siblings
stage: Data · research input
size: M
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer'
---
- **124. Second free intraday gold source and Dukascopy siblings.** Value: an independent intraday source to cross-check Dukascopy (gap days, wrong prints) and intermarket series at the same timestamps. Target: HistData XAU/USD 1m from 2009-03 (terms to read; manual monthly zips, local only), and Dukascopy siblings on the same pipeline (EURUSD, USDJPY, Brent; XAGUSD is built: `data-dukascopy.yml` symbol xagusd, private repository only). Acceptance adds a cross-feed report: minutes where the two gold sources differ by more than 3x the median spread, per month. Licence condition: only public-domain or openly licensed data (CC BY, ODC-PDDL, US government) goes to a public release; anything personal-use-only stays local (brain or a private repository, ADR 0048) with only code, schema, row counts and sha256 in the repo. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.
