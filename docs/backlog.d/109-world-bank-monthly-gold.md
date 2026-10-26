---
number: 109
rank: 42
title: World Bank Pink Sheet and DataHub monthly gold, 1833/1960 on
stage: 'Data · research input'
size: S
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer'
---
- **109. World Bank Pink Sheet and DataHub monthly gold, 1833/1960 on.** Value: a free monthly gold series from 1960 (World Bank CMO Pink Sheet, CC BY 4.0 per the Commodity Markets page's licensing link, attribution required) and 1833 (DataHub core/gold-prices, ODC-PDDL) for long seasonality and regime work. Target: a data-macro style workflow publishing monthly rows with available_utc = the Pink Sheet release date (monthly, about the 3rd business day; the next is 2026-11-03), vintage kept (values are revised), never joined intraday. DataHub's pre-1960 annual values repeated per month are flagged as annual. Licence condition: only public-domain or openly licensed data (CC BY, ODC-PDDL, US government) goes to a public release; anything personal-use-only stays local (brain or a private repository, ADR 0048) with only code, schema, row counts and sha256 in the repo. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.
